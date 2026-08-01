import 'package:flutter/foundation.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../../core/errors/api_exception.dart';
import '../../../notifications/presentation/providers/notification_provider.dart';
import '../../data/models/saved_account.dart';
import '../../data/models/user_model.dart';
import '../../data/repositories/auth_repository.dart';

/// Where the user is in the authentication lifecycle.
enum AuthStatus {
  /// Restoring a cached session on cold start.
  checking,
  unauthenticated,

  /// Signed in, but an administrator has not approved the account yet. The
  /// user reaches a pending screen rather than a dashboard.
  pendingApproval,
  authenticated,
}

class AuthState {
  const AuthState({
    this.status = AuthStatus.checking,
    this.user,
    this.errorMessage,
    this.fieldErrors = const {},
    this.isSubmitting = false,
  });

  final AuthStatus status;
  final UserModel? user;
  final String? errorMessage;

  /// Per-field messages from the server, keyed by field name, so a form can
  /// show the error under the offending input rather than in a generic banner.
  final Map<String, String> fieldErrors;
  final bool isSubmitting;

  AuthState copyWith({
    AuthStatus? status,
    UserModel? user,
    String? errorMessage,
    Map<String, String>? fieldErrors,
    bool? isSubmitting,
    bool clearError = false,
  }) {
    return AuthState(
      status: status ?? this.status,
      user: user ?? this.user,
      errorMessage: clearError ? null : (errorMessage ?? this.errorMessage),
      fieldErrors: clearError ? const {} : (fieldErrors ?? this.fieldErrors),
      isSubmitting: isSubmitting ?? this.isSubmitting,
    );
  }
}

final authRepositoryProvider =
    Provider<AuthRepository>((ref) => AuthRepository());

final authProvider =
    NotifierProvider<AuthNotifier, AuthState>(AuthNotifier.new);

/// Accounts previously signed into on this device, most recent first.
///
/// Read by the login screen and the account switcher. Invalidated by
/// [AuthNotifier] whenever the list can have changed, so both stay in step
/// without either screen refetching on every build.
final savedAccountsProvider = FutureProvider<List<SavedAccount>>((ref) {
  return ref.watch(authRepositoryProvider).savedAccounts();
});

/// Owns sign-in, registration and sign-out.
class AuthNotifier extends Notifier<AuthState> {
  @override
  AuthState build() {
    // Kick off session restoration; the splash screen watches for the result.
    //
    // The catch-all is load-bearing, not defensive padding. The router holds
    // the user on the splash screen for exactly as long as the status is
    // `checking`, so any escape from this future that leaves the status
    // untouched strands them on a spinner with no error and no way forward.
    // restoreSession itself only handles ApiException; a PlatformException out
    // of the Keystore-backed secure storage would otherwise never surface.
    // Failing to read a stored session is indistinguishable from not having
    // one, so signed-out is the correct fallback.
    Future.microtask(() async {
      try {
        await restoreSession();
      } catch (error, stackTrace) {
        debugPrint(
          'Session restore failed, continuing signed out: $error\n$stackTrace',
        );
        state = const AuthState(status: AuthStatus.unauthenticated);
      }
    });
    return const AuthState();
  }

  AuthRepository get _repository => ref.read(authRepositoryProvider);

  /// Restores a cached session on cold start.
  ///
  /// Reads the cached profile first and only then refreshes from the server,
  /// so the correct dashboard appears immediately even when the backend is
  /// asleep on Render's free tier.
  Future<void> restoreSession() async {
    if (!await _repository.hasSession()) {
      state = state.copyWith(status: AuthStatus.unauthenticated);
      return;
    }

    final cached = await _repository.cachedUser();
    if (cached != null) {
      state = state.copyWith(status: _statusFor(cached), user: cached);
    }

    try {
      final fresh = await _repository.fetchMe();
      state = state.copyWith(status: _statusFor(fresh), user: fresh);
    } on ApiException catch (error) {
      // Offline with a cached profile is a perfectly usable state; only a
      // rejected session forces the user back to the login screen.
      if (error.isAuthFailure) {
        await _repository.logout();
        state = const AuthState(status: AuthStatus.unauthenticated);
      } else if (cached == null) {
        state = state.copyWith(
          status: AuthStatus.unauthenticated,
          errorMessage: error.message,
        );
      }
    }
  }

  Future<void> login({
    required String phoneNumber,
    required String password,
  }) async {
    state = state.copyWith(isSubmitting: true, clearError: true);
    try {
      final user = await _repository.login(
        phoneNumber: phoneNumber,
        password: password,
      );
      state = AuthState(status: _statusFor(user), user: user);
      ref.invalidate(savedAccountsProvider);
    } on ApiException catch (error) {
      state = state.copyWith(
        status: AuthStatus.unauthenticated,
        isSubmitting: false,
        errorMessage: _loginMessageFor(error),
        fieldErrors: _extractFieldErrors(error),
      );
    }
  }

  Future<bool> registerResident({
    required String phoneNumber,
    required String password,
    required String firstName,
    required String lastName,
    required int societyId,
  }) async {
    return _register(
      () => _repository.registerResident(
        phoneNumber: phoneNumber,
        password: password,
        firstName: firstName,
        lastName: lastName,
        societyId: societyId,
      ),
    );
  }

  Future<bool> registerWorker({
    required String phoneNumber,
    required String password,
    required String firstName,
    required String lastName,
    required int societyId,
  }) async {
    return _register(
      () => _repository.registerWorker(
        phoneNumber: phoneNumber,
        password: password,
        firstName: firstName,
        lastName: lastName,
        societyId: societyId,
      ),
    );
  }

  Future<bool> _register(Future<UserModel> Function() call) async {
    state = state.copyWith(isSubmitting: true, clearError: true);
    try {
      await call();
      state = state.copyWith(isSubmitting: false);
      return true;
    } on ApiException catch (error) {
      state = state.copyWith(
        isSubmitting: false,
        errorMessage: error.message,
        fieldErrors: _extractFieldErrors(error),
      );
      return false;
    }
  }

  /// Signs out.
  ///
  /// [forget] distinguishes "sign out" from "sign out and forget this account".
  /// The default keeps the account in the switcher with a parked refresh token
  /// so it can be resumed in one tap; see [AuthRepository.signOut].
  Future<void> logout({bool forget = false}) async {
    // Clear the push token *before* the tokens go, while the call is still
    // authenticated. Otherwise the next person to hold this phone keeps
    // receiving the previous user's gate alerts and payment notifications.
    // This applies to both flavours: a parked account must not keep receiving
    // pushes on a device nobody is signed into.
    await ref.read(pushServiceProvider).stop();
    await _repository.signOut(forget: forget);
    state = const AuthState(status: AuthStatus.unauthenticated);
    ref.invalidate(savedAccountsProvider);
  }

  /// Signs in as a previously used account without a password.
  ///
  /// Returns false when the shortcut is no longer available — the parked token
  /// expired, was revoked, or was already spent — so the caller can fall back
  /// to asking for the password. That is not an error state: the account is
  /// still valid, only the shortcut lapsed, and [state] is deliberately left
  /// without an `errorMessage` so the login screen does not show a red banner
  /// for something the user did nothing wrong to cause.
  Future<bool> signInWithSavedAccount(SavedAccount account) async {
    state = state.copyWith(isSubmitting: true, clearError: true);
    try {
      final user = await _repository.resumeSavedAccount(account);
      state = AuthState(status: _statusFor(user), user: user);
      ref.invalidate(savedAccountsProvider);
      return true;
    } on ApiException {
      state = state.copyWith(
        status: AuthStatus.unauthenticated,
        isSubmitting: false,
      );
      ref.invalidate(savedAccountsProvider);
      return false;
    }
  }

  /// Drops an account from the switcher and revokes its parked token.
  Future<void> forgetAccount(SavedAccount account) async {
    await _repository.forgetAccount(account);
    ref.invalidate(savedAccountsProvider);
  }

  /// Signs out of the current account and into [target] in one step.
  ///
  /// Crucially, [state] is never set to `unauthenticated` in between. Doing so
  /// would drive the router's redirect back to the login screen for the
  /// fraction of a second the swap takes, and the user would watch the app
  /// bounce through a screen they did not ask for.
  ///
  /// Returns false when [target] could not be resumed, in which case the
  /// session really has ended and the login screen is the correct destination.
  Future<bool> switchTo(SavedAccount target) async {
    state = state.copyWith(isSubmitting: true, clearError: true);

    // Same reasoning as [logout]: the outgoing user's push token must go while
    // the call is still authenticated as them.
    await ref.read(pushServiceProvider).stop();
    await _repository.signOut();

    try {
      final user = await _repository.resumeSavedAccount(target);
      state = AuthState(status: _statusFor(user), user: user);
      ref.invalidate(savedAccountsProvider);
      return true;
    } on ApiException {
      state = const AuthState(status: AuthStatus.unauthenticated);
      ref.invalidate(savedAccountsProvider);
      return false;
    }
  }

  void clearError() => state = state.copyWith(clearError: true);

  AuthStatus _statusFor(UserModel user) =>
      user.isApproved ? AuthStatus.authenticated : AuthStatus.pendingApproval;

  /// The server returns a generic 401 for bad credentials. Translate it into
  /// something a user can act on, without confirming whether the account exists.
  String _loginMessageFor(ApiException error) {
    if (error.isAuthFailure || error.statusCode == 401) {
      return 'Incorrect phone number or password.';
    }
    return error.message;
  }

  Map<String, String> _extractFieldErrors(ApiException error) {
    return error.details.map((field, messages) {
      final text = messages is List && messages.isNotEmpty
          ? messages.first.toString()
          : messages.toString();
      return MapEntry(field, text);
    });
  }
}
