import '../../../../core/config/api_endpoints.dart';
import '../../../../core/device/device_identity.dart';
import '../../../../core/errors/api_exception.dart';
import '../../../../core/network/api_client.dart';
import '../../../../core/storage/saved_accounts_storage.dart';
import '../../../../core/storage/token_storage.dart';
import '../models/saved_account.dart';
import '../models/user_model.dart';

/// Why a one-time code was requested.
///
/// Mirrors `OtpPurpose` on the server, which scopes codes by purpose: a code
/// issued to verify a new account is refused on the password-reset route, and
/// vice versa. That scoping is load-bearing — a registration code can be
/// triggered by anyone who knows a phone number — which is why this is an enum
/// rather than a loose string a typo could silently corrupt.
///
/// There is no `login` purpose. Signing in uses a password.
enum OtpPurpose {
  /// Verifying the number given while creating an account.
  registration('registration'),

  /// Proving the phone before setting a new password ("forgot password").
  passwordReset('password_reset');

  const OtpPurpose(this.wireValue);

  final String wireValue;
}

/// What creating an account returns: the new profile, and whether the
/// verification code actually went out.
class RegistrationResult {
  const RegistrationResult({required this.user, required this.otpSent});

  final UserModel user;

  /// False when the server's send throttle bit. The account exists regardless,
  /// so the flow continues to the code prompt either way.
  final bool otpSent;
}

/// Every call to the Module 1 auth endpoints goes through here.
///
/// Screens never touch [ApiClient] directly: keeping the HTTP shape in one
/// place means a server-side field rename is a one-file change, and it lets
/// tests substitute a fake repository without mocking HTTP.
///
/// Sign-in is [login] — phone number and password. The OTP methods serve the
/// two flows that need to prove a phone number is real: [verifyOtp] finishes
/// sign-up, and [resetPassword] answers "I forgot my password".
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

    return _persistSession(response);
  }

  /// Asks the server to text a one-time code to [phoneNumber].
  ///
  /// [purpose] must match the purpose the code is later redeemed under — the
  /// server scopes codes so one issued for registration cannot be spent on a
  /// password reset. Throws [ApiException] with code `throttled` when the
  /// resend cooldown or the hourly ceiling is hit; `details.retry_after_seconds`
  /// says how long to wait, which is what the resend countdown reads.
  Future<void> requestOtp({
    required String phoneNumber,
    OtpPurpose purpose = OtpPurpose.registration,
  }) =>
      _client.post(
        ApiEndpoints.requestOtp,
        data: {'phone_number': phoneNumber, 'purpose': purpose.wireValue},
      );

  /// Finishes sign-up: verifies the phone number and signs the new user in.
  ///
  /// Only used once per account, straight after registration. Every sign-in
  /// after this one goes through [login] with the password chosen at sign-up.
  Future<UserModel> verifyOtp({
    required String phoneNumber,
    required String code,
  }) async {
    final response = await _client.post(
      ApiEndpoints.verifyOtp,
      data: {
        'phone_number': phoneNumber,
        'code': code,
        'device': await _deviceInfo(),
      },
    ) as Map<String, dynamic>;

    return _persistSession(response);
  }

  /// Sets a new password against a reset code, and signs the user in.
  ///
  /// The "forgot password" endpoint. No current password is asked for — the
  /// user is here precisely because they do not have it — so the code is the
  /// proof. Succeeding revokes every other session server-side.
  Future<UserModel> resetPassword({
    required String phoneNumber,
    required String code,
    required String newPassword,
  }) async {
    final response = await _client.post(
      ApiEndpoints.resetPassword,
      data: {
        'phone_number': phoneNumber,
        'code': code,
        'new_password': newPassword,
        'device': await _deviceInfo(),
      },
    ) as Map<String, dynamic>;

    return _persistSession(response);
  }

  /// Stores the tokens and profile from any endpoint that opens a session.
  ///
  /// Shared by sign-in, sign-up verification and password reset, which return
  /// an identical body. Keeping one implementation is what stops a flow from
  /// quietly forgetting to save the refresh token and leaving the user signed
  /// in until their first app restart.
  Future<UserModel> _persistSession(Map<String, dynamic> response) async {
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
  ///
  /// The server sends a verification code as part of creating the account, so
  /// the caller's next screen is the code prompt — not a sign-in form.
  Future<RegistrationResult> registerResident({
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
  Future<RegistrationResult> registerWorker({
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

  Future<RegistrationResult> _register(
    String path,
    Map<String, dynamic> body,
  ) async {
    final response =
        await _client.post(path, data: body) as Map<String, dynamic>;
    return RegistrationResult(
      user: UserModel.fromJson(response['user'] as Map<String, dynamic>),
      // False when the server was throttled mid-registration. The account still
      // exists; the code prompt simply opens with resend already available.
      otpSent: response['otp_sent'] as bool? ?? true,
    );
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
  /// The device stores no credential either way — only a rotating, revocable
  /// token in the Keystore. There is no password to leave behind, and a lapsed
  /// token costs the user one SMS rather than a forgotten secret.
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

  /// Resumes a previously used account without a new code.
  ///
  /// Spends the parked refresh token to mint a fresh pair. Throws an
  /// [ApiException] when there is no token or the server rejects it — expired,
  /// already blacklisted, or revoked because the device was reported lost. The
  /// login screen treats that as "send a fresh code" rather than as an error,
  /// which is the only sane outcome: the account is still real, the shortcut
  /// just expired.
  Future<UserModel> resumeSavedAccount(SavedAccount account) async {
    final refreshToken = await _savedAccounts.takeToken(account.userId);
    if (refreshToken == null) {
      throw const ApiException(
        code: 'quick_sign_in_unavailable',
        message: 'Sign in with a code to continue.',
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
        message: 'Sign in with a code to continue.',
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

  /// Identifies this installation so the server can manage device sessions.
  ///
  /// Shared with Module 10.1's push registration — see [DeviceIdentity] for why
  /// both must send the same id.
  Future<Map<String, String>> _deviceInfo() async =>
      (await DeviceIdentity.current()).toJson();
}
