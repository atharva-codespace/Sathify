/// Data models for Module 3 — Worker Onboarding & KYC.
///
/// -----------------------------------------------------------------------
/// THERE IS NO FULL AADHAAR NUMBER IN THIS FILE
/// -----------------------------------------------------------------------
/// The server never stores or returns one — only the last four digits and a
/// keyed hash. So [KycDocument] carries [maskedAadhaar] and nothing else, and
/// the number a worker types to correct a misread is sent write-only and never
/// read back. If a field ever appears here that holds twelve digits, something
/// has gone wrong upstream.
library;

import '../../../hiring/data/models/hiring_models.dart'
    show ServiceType, toDoubleOrZero;

export '../../../hiring/data/models/hiring_models.dart' show ServiceType;

/// Where a KYC attempt has got to (Module 3.2).
enum KycStatus {
  pending('pending', 'Waiting to be read'),
  processing('processing', 'Reading your document…'),
  completed('completed', 'Read successfully'),
  failed('failed', 'Could not be read');

  const KycStatus(this.wireValue, this.label);

  final String wireValue;
  final String label;

  static KycStatus fromWire(String? value) => KycStatus.values.firstWhere(
        (s) => s.wireValue == value,
        orElse: () => KycStatus.pending,
      );
}

/// One Aadhaar upload and everything the OCR pipeline made of it.
class KycDocument {
  const KycDocument({
    required this.id,
    required this.status,
    this.errorMessage = '',
    this.extractedName = '',
    this.extractedDob = '',
    this.extractedGender = '',
    this.maskedAadhaar = '',
    this.aadhaarChecksumValid = false,
    this.extractedAge,
    this.isMinor = false,
    this.ocrEngine = '',
    this.meanConfidence = 0,
    this.lowConfidenceFields = const [],
    this.needsManualConfirmation = false,
    this.hasMismatch = false,
  });

  final int id;
  final KycStatus status;

  /// Why OCR failed, when it did. Shown so the worker knows whether to retake
  /// the photo or just type the details in.
  final String errorMessage;

  final String extractedName;
  final String extractedDob;
  final String extractedGender;

  /// "XXXX XXXX 9012". The only Aadhaar representation the client ever sees.
  final String maskedAadhaar;

  /// Whether the Verhoeff checksum passed (Module 3.3).
  final bool aadhaarChecksumValid;

  final int? extractedAge;

  /// Module 3.4 — an automatic, non-overridable rejection.
  final bool isMinor;

  final String ocrEngine;
  final double meanConfidence;

  /// Fields read too poorly to trust. The form asks the worker to confirm these
  /// rather than auto-filling them.
  final List<String> lowConfidenceFields;
  final bool needsManualConfirmation;

  /// The extracted fields disagree with what the worker typed at registration.
  final bool hasMismatch;

  bool get isProcessing =>
      status == KycStatus.pending || status == KycStatus.processing;

  bool get failed => status == KycStatus.failed;

  /// Whether a given field needs the worker's eye before it can be trusted.
  bool isLowConfidence(String field) => lowConfidenceFields.contains(field);

  /// Whether anything at all needs attention before this can go to review.
  bool get needsAttention =>
      failed || needsManualConfirmation || hasMismatch || !aadhaarChecksumValid;

  factory KycDocument.fromJson(Map<String, dynamic> json) => KycDocument(
        id: json['id'] as int,
        status: KycStatus.fromWire(json['status'] as String?),
        errorMessage: json['error_message'] as String? ?? '',
        extractedName: json['extracted_name'] as String? ?? '',
        extractedDob: json['extracted_dob'] as String? ?? '',
        extractedGender: json['extracted_gender'] as String? ?? '',
        maskedAadhaar: json['masked_aadhaar'] as String? ?? '',
        aadhaarChecksumValid: json['aadhaar_checksum_valid'] as bool? ?? false,
        extractedAge: json['extracted_age'] as int?,
        isMinor: json['is_minor'] as bool? ?? false,
        ocrEngine: json['ocr_engine'] as String? ?? '',
        meanConfidence: toDoubleOrZero(json['mean_confidence']),
        lowConfidenceFields:
            ((json['low_confidence_fields'] as List?) ?? const [])
                .map((f) => f.toString())
                .toList(),
        needsManualConfirmation:
            json['needs_manual_confirmation'] as bool? ?? false,
        hasMismatch: json['has_mismatch'] as bool? ?? false,
      );
}

/// The result of an upload: the document, plus whether the age gate fired.
class KycUploadResult {
  const KycUploadResult({
    required this.document,
    required this.autoRejected,
    this.message = '',
  });

  final KycDocument document;

  /// Module 3.4 — the document showed an age under 18 and the registration was
  /// rejected outright. Not a warning; there is no path forward from here.
  final bool autoRejected;

  /// The server's own wording, shown verbatim so the app never invents a
  /// different explanation from the one the server logged.
  final String message;

  factory KycUploadResult.fromJson(Map<String, dynamic> json) =>
      KycUploadResult(
        document: KycDocument.fromJson(json['kyc'] as Map<String, dynamic>),
        autoRejected: json['auto_rejected'] as bool? ?? false,
        message: json['message'] as String? ?? '',
      );
}

/// A worker's own profile (Module 3.1).
class WorkerProfile {
  const WorkerProfile({
    required this.id,
    required this.fullName,
    this.phoneNumber = '',
    this.photoUrl,
    this.serviceTypes = const [],
    this.yearsOfExperience = 0,
    this.bio = '',
    this.languagesSpoken = '',
    this.expectedMonthlyRate,
    this.isAvailable = true,
    this.availableFrom,
    this.availableUntil,
    this.trustScore = 0,
    this.averageRating = 0,
    this.completedEngagements = 0,
    this.isApproved = false,
    this.isSearchable = false,
    this.kycStatus,
    this.rejectionReason = '',
  });

  final int id;
  final String fullName;
  final String phoneNumber;
  final String? photoUrl;
  final List<ServiceType> serviceTypes;
  final int yearsOfExperience;
  final String bio;
  final String languagesSpoken;
  final int? expectedMonthlyRate;

  final bool isAvailable;
  final String? availableFrom;
  final String? availableUntil;

  final double trustScore;
  final double averageRating;
  final int completedEngagements;

  /// An administrator has admitted this worker to the platform.
  final bool isApproved;

  /// Approved AND available AND has a photo — the full Module 4 search rule.
  /// Approved but not searchable means something is still missing.
  final bool isSearchable;

  final String? kycStatus;
  final String rejectionReason;

  bool get wasRejected => !isApproved && rejectionReason.isNotEmpty;

  bool get hasPhoto => photoUrl != null && photoUrl!.isNotEmpty;

  /// What the worker still has to do before an administrator can approve them.
  /// Mirrors the server's own blockers, so the app can prompt rather than
  /// leaving someone waiting on a queue they will never clear.
  List<String> get remainingSteps {
    final steps = <String>[];
    if (!hasPhoto) steps.add('Add a profile photo');
    if (serviceTypes.isEmpty) steps.add('Choose the work you do');
    if (kycStatus == null) steps.add('Upload your Aadhaar document');
    return steps;
  }

  factory WorkerProfile.fromJson(Map<String, dynamic> json) => WorkerProfile(
        id: json['id'] as int,
        fullName: json['full_name'] as String? ?? '',
        phoneNumber: json['phone_number'] as String? ?? '',
        photoUrl: json['photo'] as String?,
        serviceTypes: ((json['service_types'] as List?) ?? const [])
            .map((row) => ServiceType.fromJson(row as Map<String, dynamic>))
            .toList(),
        yearsOfExperience: json['years_of_experience'] as int? ?? 0,
        bio: json['bio'] as String? ?? '',
        languagesSpoken: json['languages_spoken'] as String? ?? '',
        expectedMonthlyRate: json['expected_monthly_rate'] as int?,
        isAvailable: json['is_available'] as bool? ?? true,
        availableFrom: json['available_from'] as String?,
        availableUntil: json['available_until'] as String?,
        trustScore: toDoubleOrZero(json['trust_score']),
        averageRating: toDoubleOrZero(json['average_rating']),
        completedEngagements: json['completed_engagements'] as int? ?? 0,
        isApproved: json['is_approved'] as bool? ?? false,
        isSearchable: json['is_searchable'] as bool? ?? false,
        kycStatus: json['kyc_status'] as String?,
        rejectionReason: json['rejection_reason'] as String? ?? '',
      );
}

/// Purposes a worker consents to separately (Module 3.6).
///
/// Separate rows, never one blanket flag: withdrawing face-verification consent
/// must not silently revoke the identity verification an approval rests on.
enum ConsentPurpose {
  kycAadhaar('kyc_aadhaar', 'Identity verification using Aadhaar'),
  faceBiometric('face_biometric', 'Face verification at the society gate'),
  dataProcessing('data_processing', 'General platform data processing');

  const ConsentPurpose(this.wireValue, this.label);

  final String wireValue;
  final String label;

  static ConsentPurpose fromWire(String? value) =>
      ConsentPurpose.values.firstWhere(
        (p) => p.wireValue == value,
        orElse: () => ConsentPurpose.dataProcessing,
      );
}

class ConsentRecord {
  const ConsentRecord({
    required this.id,
    required this.purpose,
    required this.isActive,
    this.grantedAt,
    this.withdrawnAt,
    this.policyVersion = '1.0',
  });

  final int id;
  final ConsentPurpose purpose;
  final bool isActive;
  final DateTime? grantedAt;
  final DateTime? withdrawnAt;
  final String policyVersion;

  factory ConsentRecord.fromJson(Map<String, dynamic> json) => ConsentRecord(
        id: json['id'] as int,
        purpose: ConsentPurpose.fromWire(json['purpose'] as String?),
        isActive: json['is_active'] as bool? ?? false,
        grantedAt: DateTime.tryParse(json['granted_at'] as String? ?? ''),
        withdrawnAt: DateTime.tryParse(json['withdrawn_at'] as String? ?? ''),
        policyVersion: json['policy_version'] as String? ?? '1.0',
      );
}

/// Another worker already registered with the same Aadhaar (Module 3.5).
class DuplicateWorker {
  const DuplicateWorker({
    required this.workerId,
    this.name = '',
    this.society = '',
  });

  final int workerId;
  final String name;
  final String society;

  factory DuplicateWorker.fromJson(Map<String, dynamic> json) =>
      DuplicateWorker(
        workerId: json['worker_id'] as int? ?? 0,
        name: json['name'] as String? ?? '',
        society: json['society'] as String? ?? '',
      );
}

/// What an administrator sees in the approval queue (Module 3.5).
class WorkerReview {
  const WorkerReview({
    required this.id,
    required this.fullName,
    this.phoneNumber = '',
    this.photoUrl,
    this.serviceTypes = const [],
    this.yearsOfExperience = 0,
    this.bio = '',
    this.languagesSpoken = '',
    this.isApproved = false,
    this.latestKyc,
    this.approvalBlockers = const [],
    this.duplicateOf,
    this.rejectionReason = '',
  });

  final int id;
  final String fullName;
  final String phoneNumber;
  final String? photoUrl;
  final List<ServiceType> serviceTypes;
  final int yearsOfExperience;
  final String bio;
  final String languagesSpoken;
  final bool isApproved;

  final KycDocument? latestKyc;

  /// Every reason approval is blocked, in plain language. The server returns
  /// them all at once so the screen can show them together rather than
  /// revealing them one refused tap at a time.
  final List<String> approvalBlockers;

  /// The same person moving societies looks identical to a fraudulent double
  /// registration, so this is a warning for a human, never an automatic block.
  final DuplicateWorker? duplicateOf;

  final String rejectionReason;

  bool get canApprove => approvalBlockers.isEmpty;

  factory WorkerReview.fromJson(Map<String, dynamic> json) => WorkerReview(
        id: json['id'] as int,
        fullName: json['full_name'] as String? ?? '',
        phoneNumber: json['phone_number'] as String? ?? '',
        photoUrl: json['photo'] as String?,
        serviceTypes: ((json['service_types'] as List?) ?? const [])
            .map((row) => ServiceType.fromJson(row as Map<String, dynamic>))
            .toList(),
        yearsOfExperience: json['years_of_experience'] as int? ?? 0,
        bio: json['bio'] as String? ?? '',
        languagesSpoken: json['languages_spoken'] as String? ?? '',
        isApproved: json['is_approved'] as bool? ?? false,
        latestKyc: json['latest_kyc'] is Map<String, dynamic>
            ? KycDocument.fromJson(json['latest_kyc'] as Map<String, dynamic>)
            : null,
        approvalBlockers: ((json['approval_blockers'] as List?) ?? const [])
            .map((b) => b.toString())
            .toList(),
        duplicateOf: json['duplicate_of'] is Map<String, dynamic>
            ? DuplicateWorker.fromJson(
                json['duplicate_of'] as Map<String, dynamic>,
              )
            : null,
        rejectionReason: json['rejection_reason'] as String? ?? '',
      );
}

/// The fields a worker submits when creating or updating their profile (3.1).
class WorkerProfileDraft {
  const WorkerProfileDraft({
    this.serviceTypeIds = const [],
    this.yearsOfExperience = 0,
    this.bio = '',
    this.languagesSpoken = '',
    this.expectedMonthlyRate,
    this.isAvailable = true,
    this.availableFrom,
    this.availableUntil,
  });

  final List<int> serviceTypeIds;
  final int yearsOfExperience;
  final String bio;
  final String languagesSpoken;
  final int? expectedMonthlyRate;
  final bool isAvailable;
  final String? availableFrom;
  final String? availableUntil;

  /// Multipart fields. The photo is attached separately by the repository,
  /// since it is a file rather than a form value.
  Map<String, dynamic> toFields() => {
        'years_of_experience': yearsOfExperience,
        'is_available': isAvailable,
        if (bio.isNotEmpty) 'bio': bio,
        if (languagesSpoken.isNotEmpty) 'languages_spoken': languagesSpoken,
        if (expectedMonthlyRate != null)
          'expected_monthly_rate': expectedMonthlyRate,
        // Both or neither — the server rejects half a window.
        if (availableFrom != null && availableUntil != null) ...{
          'available_from': availableFrom,
          'available_until': availableUntil,
        },
      };
}
