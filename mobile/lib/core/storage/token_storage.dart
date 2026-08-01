import 'dart:convert';

import 'package:flutter_secure_storage/flutter_secure_storage.dart';

/// Persists JWTs in platform-encrypted storage (Android Keystore / iOS
/// Keychain).
///
/// Deliberately NOT SharedPreferences: that is plain XML on disk and readable
/// on a rooted device. Tokens carry the user's role and society, so leaking one
/// would mean impersonating a guard or an administrator.
class TokenStorage {
  TokenStorage({FlutterSecureStorage? storage})
      : _storage = storage ??
            const FlutterSecureStorage(
              aOptions: AndroidOptions(encryptedSharedPreferences: true),
            );

  final FlutterSecureStorage _storage;

  static const String _accessKey = 'sathify_access_token';
  static const String _refreshKey = 'sathify_refresh_token';
  static const String _userKey = 'sathify_user';

  Future<void> saveTokens({
    required String accessToken,
    required String refreshToken,
  }) async {
    await _storage.write(key: _accessKey, value: accessToken);
    await _storage.write(key: _refreshKey, value: refreshToken);
  }

  Future<String?> readAccessToken() => _storage.read(key: _accessKey);

  Future<String?> readRefreshToken() => _storage.read(key: _refreshKey);

  /// Caches the profile block returned alongside the tokens at login, so the
  /// app can render the right dashboard offline without hitting `/auth/me/`.
  Future<void> saveUser(Map<String, dynamic> user) =>
      _storage.write(key: _userKey, value: jsonEncode(user));

  Future<Map<String, dynamic>?> readUser() async {
    final raw = await _storage.read(key: _userKey);
    if (raw == null) return null;
    return jsonDecode(raw) as Map<String, dynamic>;
  }

  /// Wipes all credentials. Called on logout and whenever a refresh attempt is
  /// rejected, which means the session is no longer recoverable.
  Future<void> clear() async {
    await _storage.delete(key: _accessKey);
    await _storage.delete(key: _refreshKey);
    await _storage.delete(key: _userKey);
  }

  Future<bool> get hasSession async => (await readRefreshToken()) != null;
}
