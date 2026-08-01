import 'user_model.dart';

/// An account this device has successfully signed into before.
///
/// Powers the saved-accounts list on the login screen and the switcher on the
/// profile screen. Holds only what is needed to *identify* the account in a
/// list, plus — when the account is not the active one — the refresh token that
/// lets it be resumed in a single tap.
///
/// -----------------------------------------------------------------------
/// WHY THE TOKEN MOVES INSTEAD OF BEING COPIED
/// -----------------------------------------------------------------------
/// The backend runs SimpleJWT with `ROTATE_REFRESH_TOKENS` and
/// `BLACKLIST_AFTER_ROTATION` both on, so spending a refresh token mints a
/// replacement and blacklists the one just used. A refresh token is therefore
/// single-use, and two copies of it cannot both be valid.
///
/// So [refreshToken] is non-null for exactly the accounts that are *not* signed
/// in right now. Signing out moves the token out of [TokenStorage] and into
/// here; tapping the account moves it back out and spends it. At no point does
/// the same token exist in two places, which is what stops a switch-back from
/// failing months later against a blacklisted copy.
class SavedAccount {
  const SavedAccount({
    required this.userId,
    required this.phoneNumber,
    required this.firstName,
    required this.lastName,
    required this.role,
    required this.isApproved,
    required this.lastUsedAt,
    this.societyName,
    this.refreshToken,
  });

  final int userId;
  final String phoneNumber;
  final String firstName;
  final String lastName;
  final UserRole role;
  final bool isApproved;

  /// Drives the ordering of the list — most recently used first, like Gmail.
  final DateTime lastUsedAt;

  final String? societyName;

  /// Null while this account is the active session. See the class note.
  final String? refreshToken;

  /// True when this account can be resumed without typing a password.
  bool get canQuickSignIn => refreshToken != null && refreshToken!.isNotEmpty;

  String get displayName {
    final name = '$firstName $lastName'.trim();
    return name.isEmpty ? phoneNumber : name;
  }

  /// The disambiguating line under the name.
  ///
  /// Role and society rather than the phone number, because the same person may
  /// hold a resident account in one society and an admin account in another —
  /// and those two rows would otherwise look identical.
  String get subtitle {
    final label = switch (role) {
      UserRole.resident => 'Resident',
      UserRole.worker => 'Worker',
      UserRole.guard => 'Security guard',
      UserRole.societyAdmin => 'Administrator',
      UserRole.unknown => 'Account',
    };
    final society = societyName;
    if (society == null || society.isEmpty) return '$label · $phoneNumber';
    return '$label · $society';
  }

  factory SavedAccount.fromUser(
    UserModel user, {
    String? refreshToken,
    DateTime? lastUsedAt,
  }) {
    return SavedAccount(
      userId: user.id,
      phoneNumber: user.phoneNumber,
      firstName: user.firstName,
      lastName: user.lastName,
      role: user.role,
      isApproved: user.isApproved,
      societyName: user.societyName,
      lastUsedAt: lastUsedAt ?? DateTime.now(),
      refreshToken: refreshToken,
    );
  }

  SavedAccount copyWith({
    DateTime? lastUsedAt,
    String? refreshToken,
    bool clearRefreshToken = false,
  }) {
    return SavedAccount(
      userId: userId,
      phoneNumber: phoneNumber,
      firstName: firstName,
      lastName: lastName,
      role: role,
      isApproved: isApproved,
      societyName: societyName,
      lastUsedAt: lastUsedAt ?? this.lastUsedAt,
      refreshToken:
          clearRefreshToken ? null : (refreshToken ?? this.refreshToken),
    );
  }

  factory SavedAccount.fromJson(Map<String, dynamic> json) {
    return SavedAccount(
      userId: json['user_id'] as int,
      phoneNumber: json['phone_number'] as String? ?? '',
      firstName: json['first_name'] as String? ?? '',
      lastName: json['last_name'] as String? ?? '',
      role: UserRole.fromWire(json['role'] as String?),
      isApproved: json['is_approved'] as bool? ?? false,
      societyName: json['society_name'] as String?,
      lastUsedAt: DateTime.tryParse(json['last_used_at'] as String? ?? '') ??
          DateTime(2020),
      refreshToken: json['refresh_token'] as String?,
    );
  }

  Map<String, dynamic> toJson() => {
        'user_id': userId,
        'phone_number': phoneNumber,
        'first_name': firstName,
        'last_name': lastName,
        'role': role.wireValue,
        'is_approved': isApproved,
        'society_name': societyName,
        'last_used_at': lastUsedAt.toIso8601String(),
        'refresh_token': refreshToken,
      };
}
