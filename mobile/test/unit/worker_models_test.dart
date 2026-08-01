import 'package:flutter_test/flutter_test.dart';
import 'package:sathify/features/workers/data/models/worker_models.dart';

/// Wire-format tests for Module 3 — Worker Onboarding & KYC.
///
/// NOT YET EXECUTED — the Flutter SDK is not installed on this machine. Run
/// `flutter test` once it is.
///
/// The privacy group is the one that matters most: the server never sends a
/// full Aadhaar number, and nothing in these models should be able to hold one.
void main() {
  group('KycStatus', () {
    test('parses every state', () {
      expect(KycStatus.fromWire('pending'), KycStatus.pending);
      expect(KycStatus.fromWire('processing'), KycStatus.processing);
      expect(KycStatus.fromWire('completed'), KycStatus.completed);
      expect(KycStatus.fromWire('failed'), KycStatus.failed);
    });

    test('falls back rather than throwing on something new', () {
      expect(KycStatus.fromWire('quarantined'), KycStatus.pending);
    });
  });

  group('KycDocument', () {
    Map<String, dynamic> payload() => {
          'id': 4,
          'status': 'completed',
          'error_message': '',
          'extracted_name': 'Rahul Sharma',
          'extracted_dob': '01/01/1990',
          'extracted_gender': 'Male',
          'masked_aadhaar': 'XXXX XXXX 2346',
          'aadhaar_checksum_valid': true,
          'extracted_age': 36,
          'is_minor': false,
          'ocr_engine': 'paddleocr',
          'mean_confidence': 0.93,
          'low_confidence_fields': <String>[],
          'needs_manual_confirmation': false,
          'has_mismatch': false,
        };

    test('parses a successful read', () {
      final kyc = KycDocument.fromJson(payload());

      expect(kyc.id, 4);
      expect(kyc.status, KycStatus.completed);
      expect(kyc.extractedName, 'Rahul Sharma');
      expect(kyc.aadhaarChecksumValid, isTrue);
      expect(kyc.meanConfidence, 0.93);
      expect(kyc.needsAttention, isFalse);
    });

    test('only ever holds a masked Aadhaar', () {
      final kyc = KycDocument.fromJson(payload());

      expect(kyc.maskedAadhaar, 'XXXX XXXX 2346');
      expect(kyc.maskedAadhaar.replaceAll(RegExp(r'[^0-9]'), '').length, 4);
    });

    test('a processing document reports as such', () {
      final kyc = KycDocument.fromJson({...payload(), 'status': 'processing'});
      expect(kyc.isProcessing, isTrue);
      expect(kyc.failed, isFalse);
    });

    test('a failed read carries its reason and needs attention', () {
      final kyc = KycDocument.fromJson({
        ...payload(),
        'status': 'failed',
        'error_message': 'No OCR engine available',
      });

      expect(kyc.failed, isTrue);
      expect(kyc.errorMessage, 'No OCR engine available');
      expect(kyc.needsAttention, isTrue);
    });

    test('a failed checksum needs attention even when everything else is fine', () {
      final kyc =
          KycDocument.fromJson({...payload(), 'aadhaar_checksum_valid': false});
      expect(kyc.needsAttention, isTrue);
    });

    test('low-confidence fields are addressable individually', () {
      final kyc = KycDocument.fromJson({
        ...payload(),
        'low_confidence_fields': ['aadhaar', 'dob'],
        'needs_manual_confirmation': true,
      });

      expect(kyc.isLowConfidence('aadhaar'), isTrue);
      expect(kyc.isLowConfidence('dob'), isTrue);
      expect(kyc.isLowConfidence('name'), isFalse);
      expect(kyc.needsAttention, isTrue);
    });

    test('a mismatch against the registration form needs attention', () {
      final kyc = KycDocument.fromJson({...payload(), 'has_mismatch': true});
      expect(kyc.needsAttention, isTrue);
    });

    test('parses a confidence sent as a decimal string', () {
      final kyc = KycDocument.fromJson({...payload(), 'mean_confidence': '0.87'});
      expect(kyc.meanConfidence, 0.87);
    });
  });

  group('KycUploadResult', () {
    test('parses a normal upload', () {
      final result = KycUploadResult.fromJson({
        'kyc': {'id': 1, 'status': 'completed', 'masked_aadhaar': 'XXXX XXXX 2346'},
        'auto_rejected': false,
        'message': 'Document read successfully. Please confirm your details.',
      });

      expect(result.autoRejected, isFalse);
      expect(result.document.id, 1);
      expect(result.message, contains('confirm'));
    });

    test('surfaces the Module 3.4 automatic rejection', () {
      /// A hard block — the UI must not offer a retry from this.
      final result = KycUploadResult.fromJson({
        'kyc': {'id': 2, 'status': 'completed', 'is_minor': true},
        'auto_rejected': true,
        'message': 'The document shows an age under 18…',
      });

      expect(result.autoRejected, isTrue);
      expect(result.document.isMinor, isTrue);
    });
  });

  group('WorkerProfile', () {
    Map<String, dynamic> payload() => {
          'id': 7,
          'full_name': 'Rahul Sharma',
          'phone_number': '9800000002',
          'photo': 'https://example.test/p.jpg',
          'service_types': [
            {'id': 1, 'name': 'Maid', 'slug': 'maid'},
          ],
          'years_of_experience': 5,
          'bio': 'Ten years in this area.',
          'languages_spoken': 'Hindi, Marathi',
          'expected_monthly_rate': 4000,
          'is_available': true,
          'trust_score': 70.0,
          'average_rating': 4.5,
          'completed_engagements': 12,
          'is_approved': true,
          'is_searchable': true,
          'kyc_status': 'completed',
          'rejection_reason': '',
        };

    test('parses a complete profile', () {
      final profile = WorkerProfile.fromJson(payload());

      expect(profile.id, 7);
      expect(profile.serviceTypes.single.name, 'Maid');
      expect(profile.isApproved, isTrue);
      expect(profile.isSearchable, isTrue);
      expect(profile.hasPhoto, isTrue);
      expect(profile.remainingSteps, isEmpty);
    });

    test('parses scores sent as decimal strings', () {
      final profile = WorkerProfile.fromJson(
          {...payload(), 'trust_score': '70.00', 'average_rating': '4.50'},);

      expect(profile.trustScore, 70.0);
      expect(profile.averageRating, 4.5);
    });

    test('lists a missing photo as a remaining step', () {
      final profile = WorkerProfile.fromJson({...payload(), 'photo': null});

      expect(profile.hasPhoto, isFalse);
      expect(profile.remainingSteps, contains('Add a profile photo'));
    });

    test('lists missing services and document as remaining steps', () {
      final profile = WorkerProfile.fromJson({
        ...payload(),
        'photo': null,
        'service_types': const [],
        'kyc_status': null,
      });

      expect(profile.remainingSteps.length, 3);
    });

    test('approved but unavailable is approved yet not searchable', () {
      /// The distinction the onboarding banner depends on.
      final profile = WorkerProfile.fromJson(
          {...payload(), 'is_available': false, 'is_searchable': false},);

      expect(profile.isApproved, isTrue);
      expect(profile.isSearchable, isFalse);
    });

    test('a rejection is detectable and carries its reason', () {
      final profile = WorkerProfile.fromJson({
        ...payload(),
        'is_approved': false,
        'rejection_reason': 'The photo of the card is unreadable.',
      });

      expect(profile.wasRejected, isTrue);
      expect(profile.rejectionReason, contains('unreadable'));
    });

    test('an unapproved worker with no reason has not been rejected', () {
      final profile = WorkerProfile.fromJson({...payload(), 'is_approved': false});
      expect(profile.wasRejected, isFalse);
    });
  });

  group('WorkerProfileDraft', () {
    test('omits optional fields it has nothing for', () {
      const draft = WorkerProfileDraft(yearsOfExperience: 3);
      final fields = draft.toFields();

      expect(fields['years_of_experience'], 3);
      expect(fields.containsKey('bio'), isFalse);
      expect(fields.containsKey('expected_monthly_rate'), isFalse);
    });

    test('sends an availability window only when both ends are set', () {
      const half = WorkerProfileDraft(availableFrom: '09:00');
      expect(half.toFields().containsKey('available_from'), isFalse);

      const whole =
          WorkerProfileDraft(availableFrom: '09:00', availableUntil: '18:00');
      expect(whole.toFields()['available_from'], '09:00');
      expect(whole.toFields()['available_until'], '18:00');
    });

    test('service types are carried separately from the form fields', () {
      /// They repeat as multipart keys rather than travelling as a value.
      const draft = WorkerProfileDraft(serviceTypeIds: [1, 2]);
      expect(draft.toFields().containsKey('service_types'), isFalse);
      expect(draft.serviceTypeIds, [1, 2]);
    });
  });

  group('ConsentPurpose', () {
    test('parses each purpose', () {
      expect(ConsentPurpose.fromWire('kyc_aadhaar'), ConsentPurpose.kycAadhaar);
      expect(
        ConsentPurpose.fromWire('face_biometric'),
        ConsentPurpose.faceBiometric,
      );
    });

    test('falls back rather than throwing', () {
      expect(ConsentPurpose.fromWire('telepathy'), ConsentPurpose.dataProcessing);
    });

    test('a withdrawn consent is not active', () {
      final consent = ConsentRecord.fromJson({
        'id': 1,
        'purpose': 'face_biometric',
        'is_active': false,
        'granted_at': '2026-07-01T10:00:00Z',
        'withdrawn_at': '2026-07-20T10:00:00Z',
      });

      expect(consent.isActive, isFalse);
      expect(consent.withdrawnAt, isNotNull);
    });
  });

  group('WorkerReview', () {
    Map<String, dynamic> payload() => {
          'id': 7,
          'full_name': 'Rahul Sharma',
          'phone_number': '9800000002',
          'photo': 'https://example.test/p.jpg',
          'service_types': const [],
          'is_approved': false,
          'latest_kyc': {
            'id': 4,
            'status': 'completed',
            'masked_aadhaar': 'XXXX XXXX 2346',
            'aadhaar_checksum_valid': true,
          },
          'approval_blockers': const [],
          'duplicate_of': null,
        };

    test('parses the review payload', () {
      final review = WorkerReview.fromJson(payload());

      expect(review.latestKyc?.maskedAadhaar, 'XXXX XXXX 2346');
      expect(review.canApprove, isTrue);
      expect(review.duplicateOf, isNull);
    });

    test('blockers disable approval', () {
      final review = WorkerReview.fromJson({
        ...payload(),
        'approval_blockers': ['No profile photo.', 'No service types selected.'],
      });

      expect(review.canApprove, isFalse);
      expect(review.approvalBlockers.length, 2);
    });

    test('a duplicate is surfaced but does not itself block approval', () {
      /// The same person moving societies looks identical to a fraud; only a
      /// human can tell, so it is a warning rather than a blocker.
      final review = WorkerReview.fromJson({
        ...payload(),
        'duplicate_of': {
          'worker_id': 3,
          'name': 'R. Sharma',
          'society': 'Blue Ridge',
        },
      });

      expect(review.duplicateOf!.workerId, 3);
      expect(review.duplicateOf!.society, 'Blue Ridge');
      expect(review.canApprove, isTrue);
    });

    test('a worker with no document parses with a null kyc', () {
      final review = WorkerReview.fromJson({...payload(), 'latest_kyc': null});
      expect(review.latestKyc, isNull);
    });
  });
}
