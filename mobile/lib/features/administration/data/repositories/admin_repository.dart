import 'package:dio/dio.dart';

import '../../../../core/config/api_endpoints.dart';
import '../../../../core/network/api_client.dart';
import '../models/admin_models.dart';

/// All Module 11 endpoints.
class AdminRepository {
  AdminRepository({ApiClient? client}) : _client = client ?? ApiClient();

  final ApiClient _client;

  // --- 11.1 Directory --------------------------------------------------------

  Future<List<DirectoryWorker>> fetchWorkers({
    String search = '',
    String? service,
    bool? approved,
    bool? available,
  }) async {
    final response = await _client.get(
      ApiEndpoints.adminWorkerDirectory,
      query: {
        if (search.trim().isNotEmpty) 'search': search.trim(),
        if (service != null) 'service': service,
        if (approved != null) 'approved': approved.toString(),
        if (available != null) 'available': available.toString(),
        'page_size': 100,
      },
    ) as Map<String, dynamic>;

    return ((response['results'] as List?) ?? const [])
        .map((row) => DirectoryWorker.fromJson(row as Map<String, dynamic>))
        .toList();
  }

  Future<List<DirectoryResident>> fetchResidents({
    String search = '',
    bool? approved,
  }) async {
    final response = await _client.get(
      ApiEndpoints.adminResidentDirectory,
      query: {
        if (search.trim().isNotEmpty) 'search': search.trim(),
        if (approved != null) 'approved': approved.toString(),
        'page_size': 100,
      },
    ) as Map<String, dynamic>;

    return ((response['results'] as List?) ?? const [])
        .map((row) => DirectoryResident.fromJson(row as Map<String, dynamic>))
        .toList();
  }

  // --- 11.2 Reports ----------------------------------------------------------

  /// One report as JSON. `kind` is `attendance`, `payments` or `complaints`.
  Future<AdminReport> fetchReport(
    String kind, {
    DateTime? start,
    DateTime? end,
  }) async {
    final response = await _client.get(
      ApiEndpoints.adminReport(kind),
      query: {
        if (start != null) 'start': _isoDate(start),
        if (end != null) 'end': _isoDate(end),
      },
    ) as Map<String, dynamic>;

    return AdminReport.fromJson(response);
  }

  // --- 11.3 Complaints -------------------------------------------------------

  /// The society queue for an administrator; the caller's own for everyone else.
  Future<List<Complaint>> fetchComplaints({
    bool openOnly = false,
    bool overdueOnly = false,
    ComplaintCategory? category,
    ComplaintStatus? status,
  }) async {
    final response = await _client.get(
      ApiEndpoints.complaints,
      query: {
        if (openOnly) 'open': 'true',
        if (overdueOnly) 'overdue': 'true',
        if (category != null) 'category': category.wireValue,
        if (status != null) 'status': status.wireValue,
        'page_size': 100,
      },
    ) as Map<String, dynamic>;

    return ((response['results'] as List?) ?? const [])
        .map((row) => Complaint.fromJson(row as Map<String, dynamic>))
        .toList();
  }

  Future<Complaint> fetchComplaint(int complaintId) async {
    final response = await _client.get(ApiEndpoints.complaint(complaintId))
        as Map<String, dynamic>;
    return Complaint.fromJson(response);
  }

  /// Raises a complaint.
  ///
  /// Multipart whenever a photo is attached, JSON otherwise — a `FormData` body
  /// with no file would still force every field through as a string, and the
  /// server's integer target ids would arrive as text.
  ///
  /// Priority is deliberately not sent. The server derives it from the
  /// category, because a field labelled "how urgent is this?" makes everything
  /// urgent within a week.
  Future<Complaint> raiseComplaint({
    required ComplaintCategory category,
    required String subject,
    required String description,
    int? againstWorker,
    int? againstResident,
    String? photoPath,
  }) async {
    final fields = <String, dynamic>{
      'category': category.wireValue,
      'subject': subject.trim(),
      'description': description.trim(),
      if (againstWorker != null) 'against_worker': againstWorker,
      if (againstResident != null) 'against_resident': againstResident,
    };

    final dynamic response;
    if (photoPath != null && photoPath.isNotEmpty) {
      response = await _client.upload(
        ApiEndpoints.complaints,
        formData: FormData.fromMap({
          ...fields,
          'photo': await MultipartFile.fromFile(photoPath),
        }),
      );
    } else {
      response = await _client.post(ApiEndpoints.complaints, data: fields);
    }

    final body = response as Map<String, dynamic>;
    return Complaint.fromJson(body['complaint'] as Map<String, dynamic>);
  }

  /// Adds a note. Returns the complaint with its refreshed history.
  ///
  /// `isInternal` is only honoured for administrators — the server drops it for
  /// anybody else, so a resident cannot hide a comment from the one person who
  /// needs to read it.
  Future<Complaint> addNote(
    int complaintId, {
    required String note,
    bool isInternal = false,
  }) async {
    final response = await _client.post(
      ApiEndpoints.complaintUpdates(complaintId),
      data: {'note': note.trim(), 'is_internal': isInternal},
    ) as Map<String, dynamic>;

    return Complaint.fromJson(response);
  }

  Future<Complaint> startComplaint(int complaintId) async {
    final response = await _client
        .post(ApiEndpoints.startComplaint(complaintId)) as Map<String, dynamic>;
    return Complaint.fromJson(response);
  }

  /// Resolve or reject. A resolution note is required either way — a rejection
  /// with no explanation is the outcome most likely to be disputed.
  Future<Complaint> closeComplaint(
    int complaintId, {
    required ComplaintStatus status,
    required String resolution,
  }) async {
    final response = await _client.post(
      ApiEndpoints.closeComplaint(complaintId),
      data: {'status': status.wireValue, 'resolution': resolution.trim()},
    ) as Map<String, dynamic>;

    return Complaint.fromJson(response);
  }

  Future<Complaint> withdrawComplaint(
    int complaintId, {
    String reason = '',
  }) async {
    final response = await _client.post(
      ApiEndpoints.withdrawComplaint(complaintId),
      data: {if (reason.trim().isNotEmpty) 'reason': reason.trim()},
    ) as Map<String, dynamic>;

    return Complaint.fromJson(response);
  }

  /// Runs the overdue sweep. Loading the queue already triggers it server-side;
  /// this is the explicit button, for the day somebody wants to be sure.
  Future<int> escalateOverdue() async {
    final response = await _client.post(ApiEndpoints.escalateComplaints)
        as Map<String, dynamic>;
    return response['escalated'] as int? ?? 0;
  }

  // --- 11.4 Analytics --------------------------------------------------------

  Future<AdminDashboard> fetchDashboard({
    DateTime? since,
    DateTime? until,
  }) async {
    final response = await _client.get(
      ApiEndpoints.adminDashboard,
      query: {
        if (since != null) 'since': _isoDate(since),
        if (until != null) 'until': _isoDate(until),
      },
    ) as Map<String, dynamic>;

    return AdminDashboard.fromJson(response);
  }

  Future<List<UnmetDemandEntry>> fetchUnmetDemand({String? kind}) async {
    final response = await _client.get(
      ApiEndpoints.unmetDemand,
      query: {
        if (kind != null) 'kind': kind,
        'page_size': 100,
      },
    ) as Map<String, dynamic>;

    return ((response['results'] as List?) ?? const [])
        .map((row) => UnmetDemandEntry.fromJson(row as Map<String, dynamic>))
        .toList();
  }

  static String _isoDate(DateTime value) =>
      '${value.year.toString().padLeft(4, '0')}-'
      '${value.month.toString().padLeft(2, '0')}-'
      '${value.day.toString().padLeft(2, '0')}';
}
