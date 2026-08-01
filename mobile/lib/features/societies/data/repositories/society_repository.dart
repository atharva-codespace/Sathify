import 'package:dio/dio.dart';

import '../../../../core/config/api_endpoints.dart';
import '../../../../core/network/api_client.dart';
import '../models/society_models.dart';

/// All Module 2 endpoints.
class SocietyRepository {
  SocietyRepository({ApiClient? client}) : _client = client ?? ApiClient();

  final ApiClient _client;

  /// Societies available to register into. Requires no authentication, since a
  /// prospective resident must choose one before they have an account.
  Future<List<SocietySummary>> fetchPublicSocieties({String? search}) async {
    final response = await _client.get(
      ApiEndpoints.publicSocieties,
      query: {
        if (search != null && search.isNotEmpty) 'search': search,
        'page_size': 100,
      },
    ) as Map<String, dynamic>;

    return (response['results'] as List)
        .map((row) => SocietySummary.fromJson(row as Map<String, dynamic>))
        .toList();
  }

  Future<Society> fetchMySociety() async {
    final response =
        await _client.get(ApiEndpoints.mySociety) as Map<String, dynamic>;
    return Society.fromJson(response);
  }

  Future<List<Tower>> fetchTowers() async {
    final response = await _client.get(
      ApiEndpoints.towers,
      query: {'page_size': 100},
    ) as Map<String, dynamic>;

    return (response['results'] as List)
        .map((row) => Tower.fromJson(row as Map<String, dynamic>))
        .toList();
  }

  /// Flats in the caller's society, optionally narrowed to one tower or to
  /// vacant flats only.
  Future<List<Flat>> fetchFlats({int? towerId, bool vacantOnly = false}) async {
    final response = await _client.get(
      ApiEndpoints.flats,
      query: {
        if (towerId != null) 'tower': towerId,
        if (vacantOnly) 'vacant': 'true',
        'page_size': 200,
      },
    ) as Map<String, dynamic>;

    return (response['results'] as List)
        .map((row) => Flat.fromJson(row as Map<String, dynamic>))
        .toList();
  }

  /// Claims a flat, optionally attaching proof of residence.
  ///
  /// Multipart is used only when a document is supplied — sending an empty
  /// multipart body for the common case would be wasteful.
  Future<ResidentProfile> claimFlat({
    required int flatId,
    required ResidentRelationship relationship,
    String? proofDocumentPath,
    DateTime? moveInDate,
  }) async {
    final Map<String, dynamic> response;

    if (proofDocumentPath != null) {
      final formData = FormData.fromMap({
        'flat': flatId,
        'relationship': relationship.wireValue,
        if (moveInDate != null) 'move_in_date': _formatDate(moveInDate),
        'proof_document': await MultipartFile.fromFile(proofDocumentPath),
      });
      response = await _client.upload(
        ApiEndpoints.claimFlat,
        formData: formData,
      ) as Map<String, dynamic>;
    } else {
      response = await _client.post(
        ApiEndpoints.claimFlat,
        data: {
          'flat': flatId,
          'relationship': relationship.wireValue,
          if (moveInDate != null) 'move_in_date': _formatDate(moveInDate),
        },
      ) as Map<String, dynamic>;
    }

    return ResidentProfile.fromJson(
      response['resident'] as Map<String, dynamic>,
    );
  }

  Future<ResidentProfile> fetchMyResidentProfile() async {
    final response = await _client.get(ApiEndpoints.myResidentProfile)
        as Map<String, dynamic>;
    return ResidentProfile.fromJson(response);
  }

  /// The administrator's approval queue (Module 2.3).
  Future<List<ResidentProfile>> fetchPendingResidents() async {
    final response = await _client.get(
      ApiEndpoints.pendingResidents,
      query: {'page_size': 100},
    ) as Map<String, dynamic>;

    return (response['results'] as List)
        .map((row) => ResidentProfile.fromJson(row as Map<String, dynamic>))
        .toList();
  }

  /// Approves or rejects a resident. A rejection must carry a reason so the
  /// resident knows what to correct.
  Future<ResidentProfile> decideResident({
    required int residentId,
    required bool approve,
    String? rejectionReason,
  }) async {
    final response = await _client.post(
      ApiEndpoints.residentDecision(residentId),
      data: {
        'approve': approve,
        if (rejectionReason != null) 'rejection_reason': rejectionReason,
      },
    ) as Map<String, dynamic>;

    return ResidentProfile.fromJson(
      response['resident'] as Map<String, dynamic>,
    );
  }

  String _formatDate(DateTime date) =>
      '${date.year.toString().padLeft(4, '0')}-'
      '${date.month.toString().padLeft(2, '0')}-'
      '${date.day.toString().padLeft(2, '0')}';
}
