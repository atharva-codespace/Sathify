import 'dart:convert';

import 'package:flutter/foundation.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';

import '../../features/auth/data/models/saved_account.dart';

/// Persists the list of accounts this device has signed into.
///
/// Uses the same Keystore-backed secure storage as [TokenStorage] rather than
/// SharedPreferences, and for the same reason: these records carry refresh
/// tokens for signed-out accounts. On a rooted device SharedPreferences is
/// plain XML, and one of these tokens is enough to resume somebody's session.
///
/// Stored as a single JSON array under one key. The list is short — a shared
/// family phone might hold three or four accounts — so rewriting the whole
/// array on every change is cheaper and far less error-prone than maintaining
/// an index plus per-account keys that could drift out of sync.
class SavedAccountsStorage {
  SavedAccountsStorage({FlutterSecureStorage? storage})
      : _storage = storage ??
            const FlutterSecureStorage(
              aOptions: AndroidOptions(encryptedSharedPreferences: true),
            );

  final FlutterSecureStorage _storage;

  static const String _key = 'sathify_saved_accounts';

  /// Guards against a shared device accumulating an unbounded list — and
  /// against the storage entry growing large enough to be slow to decode.
  static const int _maxAccounts = 8;

  /// Most recently used first.
  Future<List<SavedAccount>> readAll() async {
    final raw = await _storage.read(key: _key);
    if (raw == null || raw.isEmpty) return const [];

    try {
      final decoded = jsonDecode(raw) as List<dynamic>;
      final accounts = decoded
          .map((e) => SavedAccount.fromJson(e as Map<String, dynamic>))
          .toList()
        ..sort((a, b) => b.lastUsedAt.compareTo(a.lastUsedAt));
      return accounts;
    } catch (error) {
      // A corrupt entry must never brick the login screen. Dropping the list
      // costs the user one re-entry of their password; throwing here would
      // leave them with no way in at all.
      debugPrint('Saved accounts unreadable, discarding: $error');
      await _storage.delete(key: _key);
      return const [];
    }
  }

  /// Adds [account], or replaces the existing record with the same user id.
  Future<void> upsert(SavedAccount account) async {
    final accounts = (await readAll())
        .where((a) => a.userId != account.userId)
        .toList()
      ..insert(0, account);

    await _write(accounts.take(_maxAccounts).toList());
  }

  Future<void> remove(int userId) async {
    final accounts = await readAll();
    await _write(accounts.where((a) => a.userId != userId).toList());
  }

  Future<SavedAccount?> find(int userId) async {
    final accounts = await readAll();
    for (final account in accounts) {
      if (account.userId == userId) return account;
    }
    return null;
  }

  /// Parks a refresh token against a signed-out account so it can be resumed
  /// in one tap.
  Future<void> stashToken(int userId, String refreshToken) async {
    final account = await find(userId);
    if (account == null) return;
    await upsert(account.copyWith(refreshToken: refreshToken));
  }

  /// Hands back the stashed token **and clears it in the same step**.
  ///
  /// Deliberately destructive: the backend blacklists a refresh token the
  /// moment it is spent, so leaving a copy behind would guarantee a dead token
  /// in storage and a confusing failure on the next switch. Callers that fail
  /// to complete the sign-in simply fall back to asking for the password.
  Future<String?> takeToken(int userId) async {
    final account = await find(userId);
    final token = account?.refreshToken;
    if (account == null || token == null) return null;

    await upsert(account.copyWith(clearRefreshToken: true));
    return token;
  }

  Future<void> clearAll() => _storage.delete(key: _key);

  Future<void> _write(List<SavedAccount> accounts) async {
    if (accounts.isEmpty) {
      await _storage.delete(key: _key);
      return;
    }
    await _storage.write(
      key: _key,
      value: jsonEncode(accounts.map((a) => a.toJson()).toList()),
    );
  }
}
