import 'package:dio/dio.dart';

import '../../../../core/config/api_endpoints.dart';
import '../../../../core/errors/api_exception.dart';
import '../../../../core/network/api_client.dart';
import '../models/worker_models.dart';

/// All Module 3 endpoints — profile, KYC, consent, and the admin review queue.
class WorkerRepository {
  WorkerRepository({ApiClient? client}) : _client = client ?? ApiClient();

  final ApiClient _client;

  // --- Catalogue -------------------------------------------------------------

  Future<List<ServiceType>> fetchServiceTypes() async {
    final response = await _client.get(ApiEndpoints.serviceTypes) as List;
    return response
        .map((row) => ServiceType.fromJson(row as Map<String, dynamic>))
        .toList();
  }

  // --- 3.1 Profile -----------------------------------------------------------

  /// The worker's own profile, or null if they have not created one yet.
  ///
  /// A 404 is a normal onboarding state, not an error worth surfacing — the
  /// worker simply has not started. Only that case is swallowed; anything else
  /// still throws.
  Future<WorkerProfile?> fetchMyProfile() async {
    try {
      final response = await _client.get(ApiEndpoints.myWorkerProfile)
          as Map<String, dynamic>;
      return WorkerProfile.fromJson(response);
    } on ApiException catch (error) {
      if (error.statusCode == 404) return null;
      rethrow;
    }
  }

  Future<WorkerProfile> createProfile(
    WorkerProfileDraft draft, {
    String? photoPath,
  }) async {
    final response = await _client.upload(
      ApiEndpoints.myWorkerProfile,
      formData: await _profileFormData(draft, photoPath),
    ) as Map<String, dynamic>;

    return WorkerProfile.fromJson(response['profile'] as Map<String, dynamic>);
  }

  Future<WorkerProfile> updateProfile(
    WorkerProfileDraft draft, {
    String? photoPath,
  }) async {
    final response = await _client.patch(
      ApiEndpoints.myWorkerProfile,
      data: await _profileFormData(draft, photoPath),
    ) as Map<String, dynamic>;

    return WorkerProfile.fromJson(response['profile'] as Map<String, dynamic>);
  }

  /// Builds the multipart body.
  ///
  /// ``service_types`` is a many-to-many, which over multipart means the key
  /// repeats once per id rather than carrying a JSON array — hence the explicit
  /// [MapEntry] list instead of a plain map.
  Future<FormData> _profileFormData(
    WorkerProfileDraft draft,
    String? photoPath,
  ) async {
    final form = FormData();

    draft.toFields().forEach((key, value) {
      form.fields.add(MapEntry(key, '$value'));
    });

    for (final id in draft.serviceTypeIds) {
      form.fields.add(MapEntry('service_types', '$id'));
    }

    if (photoPath != null) {
      form.files.add(
        MapEntry('photo', await MultipartFile.fromFile(photoPath)),
      );
    }

    return form;
  }

  // --- 3.2 / 3.6 KYC ---------------------------------------------------------

  /// Uploads an Aadhaar document and runs it through OCR.
  ///
  /// [consent] is required and travels in this same request: the DPDP Act wants
  /// it captured at the point of collection, and the server refuses — storing
  /// nothing — if it is not given.
  ///
  /// [formName]/[formDob] are what the worker typed at registration; supplying
  /// them enables the server's cross-check of the card against the form.
  ///
  /// Never throws on an unreadable document. A failed read comes back as a
  /// [KycDocument] with `failed` set, which is the cue to offer manual entry.
  Future<KycUploadResult> uploadAadhaar({
    required String documentPath,
    required bool consent,
    String formName = '',
    String formDob = '',
  }) async {
    final form = FormData.fromMap({
      'document': await MultipartFile.fromFile(documentPath),
      'consent': '$consent',
      if (formName.isNotEmpty) 'form_name': formName,
      if (formDob.isNotEmpty) 'form_dob': formDob,
    });

    final response = await _client.upload(
      ApiEndpoints.uploadAadhaar,
      formData: form,
    ) as Map<String, dynamic>;

    return KycUploadResult.fromJson(response);
  }

  Future<List<KycDocument>> fetchMyKycAttempts() async {
    final response = await _client.get(
      ApiEndpoints.myKycAttempts,
      query: {'page_size': 20},
    ) as Map<String, dynamic>;

    return (response['results'] as List)
        .map((row) => KycDocument.fromJson(row as Map<String, dynamic>))
        .toList();
  }

  Future<KycDocument> fetchKycAttempt(int kycId) async {
    final response = await _client.get(ApiEndpoints.kycAttempt(kycId))
        as Map<String, dynamic>;
    return KycDocument.fromJson(response);
  }

  /// Confirms or corrects the extracted fields (Module 3.2/3.3).
  ///
  /// Also the manual-entry fallback: when OCR could not read the document at
  /// all, this is how a worker completes onboarding by typing the fields in.
  ///
  /// [aadhaarNumber] is write-only. The server validates it against the Verhoeff
  /// checksum, re-hashes it, and never returns it.
  Future<KycDocument> confirmKyc(
    int kycId, {
    String name = '',
    String dob = '',
    String gender = '',
    String aadhaarNumber = '',
  }) async {
    final response = await _client.post(
      ApiEndpoints.confirmKyc(kycId),
      data: {
        if (name.isNotEmpty) 'name': name,
        if (dob.isNotEmpty) 'dob': dob,
        if (gender.isNotEmpty) 'gender': gender,
        if (aadhaarNumber.isNotEmpty) 'aadhaar_number': aadhaarNumber,
      },
    ) as Map<String, dynamic>;

    return KycDocument.fromJson(response['kyc'] as Map<String, dynamic>);
  }

  // --- 3.6 Consent -----------------------------------------------------------

  Future<List<ConsentRecord>> fetchConsents() async {
    final response = await _client.get(
      ApiEndpoints.consents,
      query: {'page_size': 50},
    ) as Map<String, dynamic>;

    return (response['results'] as List)
        .map((row) => ConsentRecord.fromJson(row as Map<String, dynamic>))
        .toList();
  }

  Future<ConsentRecord> grantConsent(ConsentPurpose purpose) async {
    final response = await _client.post(
      ApiEndpoints.consents,
      data: {'purpose': purpose.wireValue},
    ) as Map<String, dynamic>;

    return ConsentRecord.fromJson(response);
  }

  Future<ConsentRecord> withdrawConsent(int consentId) async {
    final response = await _client.post(
      ApiEndpoints.withdrawConsent(consentId),
    ) as Map<String, dynamic>;

    return ConsentRecord.fromJson(response['consent'] as Map<String, dynamic>);
  }

  // --- 3.5 Admin review ------------------------------------------------------

  Future<List<WorkerReview>> fetchPendingWorkers() async {
    final response = await _client.get(
      ApiEndpoints.pendingWorkers,
      query: {'page_size': 100},
    ) as Map<String, dynamic>;

    return (response['results'] as List)
        .map((row) => WorkerReview.fromJson(row as Map<String, dynamic>))
        .toList();
  }

  Future<WorkerReview> fetchWorkerReview(int workerId) async {
    final response = await _client.get(ApiEndpoints.workerReview(workerId))
        as Map<String, dynamic>;
    return WorkerReview.fromJson(response);
  }

  /// Approves or rejects. A rejection must carry a reason the worker can act on;
  /// the server rejects an empty one.
  Future<WorkerReview> decideWorker({
    required int workerId,
    required bool approve,
    String rejectionReason = '',
  }) async {
    final response = await _client.post(
      ApiEndpoints.decideWorker(workerId),
      data: {
        'approve': approve,
        if (rejectionReason.isNotEmpty) 'rejection_reason': rejectionReason,
      },
    ) as Map<String, dynamic>;

    return WorkerReview.fromJson(response['worker'] as Map<String, dynamic>);
  }
}
