import 'package:flutter_test/flutter_test.dart';
import 'package:sathify/features/societies/data/models/society_models.dart';

/// Wire-format tests for Module 2.
///
/// NOT YET EXECUTED — the Flutter SDK is not installed on this machine. Run
/// `flutter test` once it is.
void main() {
  group('SocietySummary', () {
    test('parses the public list payload', () {
      final society = SocietySummary.fromJson({
        'id': 3,
        'name': 'Green Meadows',
        'city': 'Pune',
        'state': 'Maharashtra',
        'pincode': '411045',
      });

      expect(society.id, 3);
      expect(society.name, 'Green Meadows');
      expect(society.subtitle, 'Pune, Maharashtra, 411045');
    });

    test('subtitle omits blank parts rather than leaving stray commas', () {
      const society = SocietySummary(id: 1, name: 'X', city: 'Pune');
      expect(society.subtitle, 'Pune');
    });
  });

  group('Society', () {
    test('distinguishes active from pending verification', () {
      final active = Society.fromJson({'id': 1, 'name': 'A', 'status': 'active'});
      final pending = Society.fromJson({'id': 2, 'name': 'B', 'status': 'pending'});

      expect(active.isActive, isTrue);
      expect(active.isPendingVerification, isFalse);
      expect(pending.isPendingVerification, isTrue);
      expect(pending.isActive, isFalse);
    });

    test('mapped flat count is separate from the declared total', () {
      // The declared count is what the admin typed at registration; the mapped
      // count is what actually exists. Conflating them would hide gaps.
      final society = Society.fromJson({
        'id': 1,
        'name': 'A',
        'status': 'active',
        'total_flats': 180,
        'mapped_flat_count': 96,
      });

      expect(society.totalFlats, 180);
      expect(society.mappedFlatCount, 96);
    });

    test('missing optional fields fall back to safe defaults', () {
      final society = Society.fromJson({'id': 1, 'name': 'A', 'status': 'active'});

      expect(society.gateCount, 1);
      expect(society.bookingNoticeHours, 12);
      expect(society.allowResidentSelfCheckin, isTrue);
    });
  });

  group('ResidentRelationship', () {
    test('wire values match the Django TextChoices', () {
      expect(ResidentRelationship.owner.wireValue, 'owner');
      expect(ResidentRelationship.tenant.wireValue, 'tenant');
      expect(ResidentRelationship.familyMember.wireValue, 'family_member');
    });

    test('an unrecognised value falls back to owner rather than throwing', () {
      expect(ResidentRelationship.fromWire('landlord'), ResidentRelationship.owner);
      expect(ResidentRelationship.fromWire(null), ResidentRelationship.owner);
    });
  });

  group('ResidentProfile', () {
    Map<String, dynamic> payload({
      bool isApproved = false,
      String rejectionReason = '',
    }) =>
        {
          'id': 11,
          'full_name': 'Anita Desai',
          'phone_number': '9800000001',
          'flat': 5,
          'flat_label': 'A-301',
          'relationship': 'tenant',
          'is_primary': true,
          'is_approved': isApproved,
          'household_size': 3,
          'rejection_reason': rejectionReason,
        };

    test('parses the approval-queue payload', () {
      final resident = ResidentProfile.fromJson(payload());

      expect(resident.flatLabel, 'A-301');
      expect(resident.relationship, ResidentRelationship.tenant);
      expect(resident.isPrimary, isTrue);
      expect(resident.householdSize, 3);
    });

    test('a rejection is distinguishable from simply not yet reviewed', () {
      // Both are unapproved; only one has something for the resident to fix.
      final notReviewed = ResidentProfile.fromJson(payload());
      final rejected = ResidentProfile.fromJson(
        payload(rejectionReason: 'Proof of residence is unreadable.'),
      );

      expect(notReviewed.wasRejected, isFalse);
      expect(rejected.wasRejected, isTrue);
      expect(rejected.rejectionReason, 'Proof of residence is unreadable.');
    });

    test('an approved resident is never treated as rejected', () {
      final approved = ResidentProfile.fromJson(payload(isApproved: true));
      expect(approved.wasRejected, isFalse);
    });

    test('missing proof document is represented as null, not an empty string', () {
      final resident = ResidentProfile.fromJson(payload());
      expect(resident.proofDocumentUrl, isNull);
    });
  });

  group('Flat', () {
    test('uses the server-rendered label rather than re-deriving it', () {
      final flat = Flat.fromJson({
        'id': 9,
        'tower': 2,
        'tower_name': 'A',
        'number': '301',
        'floor': 3,
        'label': 'A-301',
        'is_occupied': true,
      });

      expect(flat.label, 'A-301');
      expect(flat.isOccupied, isTrue);
    });
  });
}
