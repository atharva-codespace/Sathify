/// The four roles defined by `apps.accounts.models.Role` on the server.
///
/// The wire values must match Django exactly — they arrive both in the `role`
/// field of `/auth/me/` and as a claim inside the JWT.
enum UserRole {
  resident('resident'),
  worker('worker'),
  guard('guard'),
  societyAdmin('society_admin'),
  unknown('');

  const UserRole(this.wireValue);

  final String wireValue;

  static UserRole fromWire(String? value) {
    return UserRole.values.firstWhere(
      (role) => role.wireValue == value,
      orElse: () => UserRole.unknown,
    );
  }
}

/// The signed-in user, as returned by `/auth/login/` and `/auth/me/`.
class UserModel {
  const UserModel({
    required this.id,
    required this.phoneNumber,
    required this.role,
    required this.isApproved,
    this.firstName = '',
    this.lastName = '',
    this.email = '',
    this.societyId,
    this.societyName,
    this.isPhoneVerified = false,
    this.preferredLanguage = 'en',
  });

  final int id;
  final String phoneNumber;
  final String firstName;
  final String lastName;
  final String email;
  final UserRole role;

  /// Null for a society administrator who has not yet registered a society.
  final int? societyId;
  final String? societyName;

  /// False until an administrator approves the account. The user can sign in
  /// either way, but an unapproved account cannot transact — so the app shows
  /// a pending-approval screen rather than a dashboard.
  final bool isApproved;
  final bool isPhoneVerified;
  final String preferredLanguage;

  String get fullName {
    final name = '$firstName $lastName'.trim();
    return name.isEmpty ? phoneNumber : name;
  }

  factory UserModel.fromJson(Map<String, dynamic> json) {
    return UserModel(
      id: json['id'] as int,
      phoneNumber: json['phone_number'] as String? ?? '',
      firstName: json['first_name'] as String? ?? '',
      lastName: json['last_name'] as String? ?? '',
      email: json['email'] as String? ?? '',
      role: UserRole.fromWire(json['role'] as String?),
      societyId: json['society'] as int?,
      societyName: json['society_name'] as String?,
      isApproved: json['is_approved'] as bool? ?? false,
      isPhoneVerified: json['is_phone_verified'] as bool? ?? false,
      preferredLanguage: json['preferred_language'] as String? ?? 'en',
    );
  }

  Map<String, dynamic> toJson() => {
        'id': id,
        'phone_number': phoneNumber,
        'first_name': firstName,
        'last_name': lastName,
        'email': email,
        'role': role.wireValue,
        'society': societyId,
        'society_name': societyName,
        'is_approved': isApproved,
        'is_phone_verified': isPhoneVerified,
        'preferred_language': preferredLanguage,
      };

  UserModel copyWith({
    bool? isApproved,
    bool? isPhoneVerified,
    String? preferredLanguage,
  }) {
    return UserModel(
      id: id,
      phoneNumber: phoneNumber,
      firstName: firstName,
      lastName: lastName,
      email: email,
      role: role,
      societyId: societyId,
      societyName: societyName,
      isApproved: isApproved ?? this.isApproved,
      isPhoneVerified: isPhoneVerified ?? this.isPhoneVerified,
      preferredLanguage: preferredLanguage ?? this.preferredLanguage,
    );
  }
}
