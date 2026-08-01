import '../../../../core/config/api_endpoints.dart';
import '../../../../core/device/device_identity.dart';
import '../../../../core/errors/api_exception.dart';
import '../../../../core/network/api_client.dart';
import '../../../../core/storage/saved_accounts_storage.dart';
import '../../../../core/storage/token_storage.dart';
import '../models/saved_account.dart';
import '../models/user_model.dart';

/// Every call to the Module 1 auth endpoints goes through here.
///
/// Screens never touch [ApiClient] directly: keeping the HTTP shape in one
/// place means a server-side field rename is a one-file change, and it lets
/// tests substitute a fake repository without mocking HTTP.
class AuthRepository {
  AuthRepository({
    ApiClient? client,
    TokenStorage? tokenStorage,
    SavedAccountsStorage? savedAccounts,
  })  : _client = client ?? ApiClient(),
        _tokenStorage = tokenStorage ?? TokenStorage(),
        _savedAccounts = savedAccounts ?? SavedAccountsStorage();

  final ApiClient _client;
  final TokenStorage _tokenStorage;
  final SavedAccountsStorage _savedAccounts;

  /// Signs in and persists the token pair.
  ///
  /// Sends a device block so the server can open a session row (Module 1.5).
  /// For guards this is what makes "one active gate terminal" enforceable.
  Future<UserModel> login({
    required String phoneNumber,
    required String password,
  }) async {
    final response = await _client.post(
      ApiEndpoints.login,
      data: {
        'phone_number': phoneNumber,
        'password': password,
        'device': await _deviceInfo(),
      },
    ) as Map<String, dynamic>;

    await _tokenStorage.saveTokens(
      accessToken: response['access'] as String,
      refreshToken: response['refresh'] as String,
    );

    final user = UserModel.fromJson(response['user'] as Map<String, dynamic>);
    await _tokenStorage.saveUser(user.toJson());

    // Remember the account for the switcher. No refresh token is stored here:
    // this account is now the active one, and its token lives in
    // [TokenStorage]. See [SavedAccount] for why it may only live in one place.
    await _savedAccounts.upsert(SavedAccount.fromUser(user));
    return user;
  }

  /// Registers a resident. The account lands unapproved by design.
  Future<UserModel> registerResident({
    required String phoneNumber,
    required String password,
    required String firstName,
    required String lastName,
    required int societyId,
    String email = '',
  }) =>
      _register(ApiEndpoints.registerResident, {
        'phone_number': phoneNumber,
        'password': password,
        'password_confirm': password,
        'first_name': firstName,
        'last_name': lastName,
        'email': email,
        'society': societyId,
      });

  /// Registers a domestic worker. KYC (Module 3) follows before they are
  /// visible in search or admissible at the gate.
  Future<UserModel> registerWorker({
    required String phoneNumber,
    required String password,
    required String firstName,
    required String lastName,
    required int societyId,
  }) =>
      _register(ApiEndpoints.registerWorker, {
        'phone_number': phoneNumber,
        'password': password,
        'password_confirm': password,
        'first_name': firstName,
        'last_name': lastName,
        'society': societyId,
      });

  Future<UserModel> _register(String path, Map<String, dynamic> body) async {
    final response =
        await _client.post(path, data: body) as Map<String, dynamic>;
    return UserModel.fromJson(response['user'] as Map<String, dynamic>);
  }

  /// Fetches the current profile from the server.
  Future<UserModel> fetchMe() async {
    final response = await _client.get(ApiEndpoints.me) as Map<String, dynamic>;
    final user = UserModel.fromJson(response);
    await _tokenStorage.saveUser(user.toJson());
    return user;
  }

  /// Returns the cached profile without a network call.
  ///
  /// Used on cold start so the app can render the right dashboard immediately,
  /// which matters when the backend is asleep on Render's free tier and the
  /// first request may take ~50 seconds.
  Future<UserModel?> cachedUser() async {
    final json = await _tokenStorage.readUser();
    return json == null ? null : UserModel.fromJson(json);
  }

  Future<bool> hasSession() => _tokenStorage.hasSession;

  /// Signs out, blacklisting the refresh token server-side.
  ///
  /// Local credentials are cleared even if the network call fails — the user
  /// asked to sign out, and leaving tokens on the device would be worse than
  /// leaving a token live on the server until it expires.
  Future<void> logout() async {
    final refreshToken = await _tokenStorage.readRefreshToken();
    try {
      if (refreshToken != null) {
        await _client
            .post(ApiEndpoints.logout, data: {'refresh': refreshToken});
      }
    } on ApiException {
      // Intentionally swallowed; see above.
    } finally {
      await _tokenStorage.clear();
    }
  }

  // ---------------------------------------------------------------------------
  // Saved accounts (login history / quick switching)
  // ---------------------------------------------------------------------------

  /// Every account this device has signed into, most recent first.
  Future<List<SavedAccount>> savedAccounts() => _savedAccounts.readAll();

  /// Signs out of the active session.
  ///
  /// Two flavours, because "sign out" and "forget me" are different intentions
  /// and conflating them is what makes quick sign-in impossible:
  ///
  /// * `forget: false` (the default action in the UI) keeps this account in the
  ///   switcher and **moves** its refresh token out of [TokenStorage] into the
  ///   saved record, so it can be resumed in one tap. It deliberately does not
  ///   call the server's logout endpoint — that blacklists the refresh token,
  ///   which would destroy the very credential being parked.
  ///
  /// * `forget: true` is the full sign-out that existed before: it blacklists
  ///   the refresh token server-side and drops the account from the switcher.
  ///
  /// The device keeps no *password* either way — only a rotating, revocable
  /// token in the Keystore.
  Future<void> signOut({bool forget = false}) async {
    final cached = await cachedUser();

    if (forget) {
      if (cached != null) await _savedAccounts.remove(cached.id);
      await logout();
      return;
    }

    final refreshToken = await _tokenStorage.readRefreshToken();
    if (cached != null && refreshToken != null) {
      await _savedAccounts.stashToken(cached.id, refreshToken);
    }
    // Clears the active session locally without revoking the parked token.
    await _tokenStorage.clear();
  }

  /// Resumes a previously used account without a password.
  ///
  /// Spends the parked refresh token to mint a fresh pair. Throws an
  /// [ApiException] when there is no token or the server rejects it — expired,
  /// already blacklisted, or revoked because the device was reported lost. The
  /// login screen treats that as "ask for the password" rather than as an
  /// error, which is the only sane outcome: the account is still real, the
  /// shortcut just expired.
  Future<UserModel> resumeSavedAccount(SavedAccount account) async {
    final refreshToken = await _savedAccounts.takeToken(account.userId);
    if (refreshToken == null) {
      throw const ApiException(
        code: 'quick_sign_in_unavailable',
        message: 'Enter your password to sign in.',
        statusCode: 401,
      );
    }

    final response = await _client.post(
      ApiEndpoints.refresh,
      data: {'refresh': refreshToken},
    ) as Map<String, dynamic>;

    final access = response['access'] as String?;
    if (access == null) {
      throw const ApiException(
        code: 'quick_sign_in_unavailable',
        message: 'Enter your password to sign in.',
        statusCode: 401,
      );
    }

    await _tokenStorage.saveTokens(
      accessToken: access,
      // Rotation is on, so a replacement normally comes back. Falling back to
      // the token just spent would store a blacklisted value, so prefer the
      // new one and accept the old only if the server chose not to rotate.
      refreshToken: response['refresh'] as String? ?? refreshToken,
    );

    // `/auth/refresh/` returns tokens only, so the profile still has to be
    // fetched. This also re-syncs approval state, which may have changed while
    // the account was signed out — the common case for a pending worker.
    final user = await fetchMe();
    await _savedAccounts.upsert(SavedAccount.fromUser(user));
    return user;
  }

  /// Removes an account from the switcher.
  ///
  /// Revokes its parked token server-side first, on a best-effort basis: the
  /// user asked to forget this account, and leaving a resumable token alive
  /// after that would defeat the point. A network failure must not block the
  /// local removal, for the same reason [logout] swallows its own.
  Future<void> forgetAccount(SavedAccount account) async {
    final token = await _savedAccounts.takeToken(account.userId);
    if (token != null) {
      try {
        await _client.post(ApiEndpoints.logout, data: {'refresh': token});
      } on ApiException {
        // Intentionally swallowed; see above.
      }
    }
    await _savedAccounts.remove(account.userId);
  }

  Future<void> requestOtp({
    required String phoneNumber,
    String purpose = 'registration',
  }) =>
      _client.post(
        ApiEndpoints.requestOtp,
        data: {'phone_number': phoneNumber, 'purpose': purpose},
      );

  Future<bool> verifyOtp({
    required String phoneNumber,
    required String code,
    String purpose = 'registration',
  }) async {
    final response = await _client.post(
      ApiEndpoints.verifyOtp,
      data: {'phone_number': phoneNumber, 'code': code, 'purpose': purpose},
    ) as Map<String, dynamic>;
    return response['verified'] as bool? ?? false;
  }

  /// Identifies this installation so the server can manage device sessions.
  ///
  /// Shared with Module 10.1's push registration — see [DeviceIdentity] for why
  /// both must send the same id.
  Future<Map<String, String>> _deviceInfo() async =>
      (await DeviceIdentity.current()).toJson();
}
