/// Data models for Module 2 — Society & Resident Onboarding.
library;

/// A society as returned by the PUBLIC list endpoint.
///
/// Deliberately narrow: the picker on the registration screen needs only enough
/// to identify the right society, and this endpoint is unauthenticated.
class SocietySummary {
  const SocietySummary({
    required this.id,
    required this.name,
    required this.city,
    this.state = '',
    this.pincode = '',
  });

  final int id;
  final String name;
  final String city;
  final String state;
  final String pincode;

  /// Disambiguates societies that share a name across cities.
  String get subtitle =>
      [city, state, pincode].where((s) => s.isNotEmpty).join(', ');

  factory SocietySummary.fromJson(Map<String, dynamic> json) => SocietySummary(
        id: json['id'] as int,
        name: json['name'] as String? ?? '',
        city: json['city'] as String? ?? '',
        state: json['state'] as String? ?? '',
        pincode: json['pincode'] as String? ?? '',
      );
}

/// Full society record, available to members.
class Society {
  const Society({
    required this.id,
    required this.name,
    required this.status,
    this.city = '',
    this.addressLine = '',
    this.totalTowers = 0,
    this.totalFlats = 0,
    this.mappedFlatCount = 0,
    this.gateCount = 1,
    this.bookingNoticeHours = 12,
    this.allowResidentSelfCheckin = true,
  });

  final int id;
  final String name;
  final String status;
  final String city;
  final String addressLine;
  final int totalTowers;
  final int totalFlats;

  /// Flats actually mapped, as opposed to the declared [totalFlats].
  final int mappedFlatCount;
  final int gateCount;
  final int bookingNoticeHours;
  final bool allowResidentSelfCheckin;

  bool get isActive => status == 'active';
  bool get isPendingVerification => status == 'pending';

  factory Society.fromJson(Map<String, dynamic> json) => Society(
        id: json['id'] as int,
        name: json['name'] as String? ?? '',
        status: json['status'] as String? ?? 'pending',
        city: json['city'] as String? ?? '',
        addressLine: json['address_line'] as String? ?? '',
        totalTowers: json['total_towers'] as int? ?? 0,
        totalFlats: json['total_flats'] as int? ?? 0,
        mappedFlatCount: json['mapped_flat_count'] as int? ?? 0,
        gateCount: json['gate_count'] as int? ?? 1,
        bookingNoticeHours: json['booking_notice_hours'] as int? ?? 12,
        allowResidentSelfCheckin:
            json['allow_resident_self_checkin'] as bool? ?? true,
      );
}

class Tower {
  const Tower({
    required this.id,
    required this.name,
    this.floors = 1,
    this.flatCount = 0,
  });

  final int id;
  final String name;
  final int floors;
  final int flatCount;

  factory Tower.fromJson(Map<String, dynamic> json) => Tower(
        id: json['id'] as int,
        name: json['name'] as String? ?? '',
        floors: json['floors'] as int? ?? 1,
        flatCount: json['flat_count'] as int? ?? 0,
      );
}

class Flat {
  const Flat({
    required this.id,
    required this.number,
    required this.label,
    this.towerId = 0,
    this.towerName = '',
    this.floor = 0,
    this.isOccupied = false,
  });

  final int id;
  final String number;

  /// Server-rendered "A-301" form, so the client never re-derives it.
  final String label;
  final int towerId;
  final String towerName;
  final int floor;
  final bool isOccupied;

  factory Flat.fromJson(Map<String, dynamic> json) => Flat(
        id: json['id'] as int,
        number: json['number'] as String? ?? '',
        label: json['label'] as String? ?? '',
        towerId: json['tower'] as int? ?? 0,
        towerName: json['tower_name'] as String? ?? '',
        floor: json['floor'] as int? ?? 0,
        isOccupied: json['is_occupied'] as bool? ?? false,
      );
}

enum ResidentRelationship {
  owner('owner', 'Owner'),
  tenant('tenant', 'Tenant'),
  familyMember('family_member', 'Family member');

  const ResidentRelationship(this.wireValue, this.label);

  final String wireValue;
  final String label;

  static ResidentRelationship fromWire(String? value) =>
      ResidentRelationship.values.firstWhere(
        (r) => r.wireValue == value,
        orElse: () => ResidentRelationship.owner,
      );
}

/// A resident's flat linkage — what an administrator reviews before approving.
class ResidentProfile {
  const ResidentProfile({
    required this.id,
    required this.fullName,
    required this.phoneNumber,
    required this.flatLabel,
    required this.isApproved,
    this.flatId = 0,
    this.relationship = ResidentRelationship.owner,
    this.isPrimary = false,
    this.householdSize = 1,
    this.proofDocumentUrl,
    this.rejectionReason = '',
  });

  final int id;
  final String fullName;
  final String phoneNumber;
  final int flatId;
  final String flatLabel;
  final ResidentRelationship relationship;

  /// Only the primary account holder may create or edit hires and schedules,
  /// which stops two people in one household issuing conflicting bookings.
  final bool isPrimary;
  final bool isApproved;
  final int householdSize;
  final String? proofDocumentUrl;
  final String rejectionReason;

  bool get wasRejected => !isApproved && rejectionReason.isNotEmpty;

  factory ResidentProfile.fromJson(Map<String, dynamic> json) =>
      ResidentProfile(
        id: json['id'] as int,
        fullName: json['full_name'] as String? ?? '',
        phoneNumber: json['phone_number'] as String? ?? '',
        flatId: json['flat'] as int? ?? 0,
        flatLabel: json['flat_label'] as String? ?? '',
        relationship:
            ResidentRelationship.fromWire(json['relationship'] as String?),
        isPrimary: json['is_primary'] as bool? ?? false,
        isApproved: json['is_approved'] as bool? ?? false,
        householdSize: json['household_size'] as int? ?? 1,
        proofDocumentUrl: json['proof_document'] as String?,
        rejectionReason: json['rejection_reason'] as String? ?? '',
      );
}
