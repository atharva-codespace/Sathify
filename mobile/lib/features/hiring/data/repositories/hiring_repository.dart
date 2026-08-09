import '../../../../core/config/api_endpoints.dart';
import '../../../../core/network/api_client.dart';
import '../../../bookings/data/models/booking_models.dart' show formatWireDate;
import '../models/hiring_models.dart';

/// All Module 4 endpoints — discovery, hire requests, and engagements.
class HiringRepository {
  HiringRepository({ApiClient? client}) : _client = client ?? ApiClient();

  final ApiClient _client;

  // --- 4.1 / 4.2 Discovery ---------------------------------------------------

  /// Ranked worker search. The server applies the Module 4.3 score, so the list
  /// arrives already ordered and each row carries its `match_percentage`.
  Future<List<WorkerSearchResult>> searchWorkers(
    WorkerSearchFilters filters, {
    int page = 1,
  }) async {
    final response = await _client.get(
      ApiEndpoints.searchWorkers,
      query: {...filters.toQuery(), 'page': page},
    ) as Map<String, dynamic>;

    return (response['results'] as List)
        .map((row) => WorkerSearchResult.fromJson(row as Map<String, dynamic>))
        .toList();
  }

  Future<WorkerDetail> fetchWorker(int workerId) async {
    final response = await _client.get(ApiEndpoints.workerProfile(workerId))
        as Map<String, dynamic>;
    return WorkerDetail.fromJson(response);
  }

  // --- 4.4 Hire requests -----------------------------------------------------

  /// Sends a hire request. Only the flat's primary account holder may do this
  /// (Module 2.4); the server answers 403 for anyone else in the household.
  Future<HireRequest> sendHireRequest({
    required int workerId,
    required int serviceTypeId,
    required RecurringTerms terms,
    String message = '',
  }) async {
    final response = await _client.post(
      ApiEndpoints.hireRequests,
      data: {
        'worker': workerId,
        'service_type': serviceTypeId,
        ...terms.toJson(),
        if (message.isNotEmpty) 'message': message,
      },
    ) as Map<String, dynamic>;

    return HireRequest.fromJson(response['request'] as Map<String, dynamic>);
  }

  /// Requests the caller is party to. Residents get the ones they sent, workers
  /// the ones addressed to them — the server decides which from the role.
  Future<List<HireRequest>> fetchHireRequests({String? status}) async {
    final response = await _client.get(
      ApiEndpoints.hireRequests,
      query: {
        if (status != null) 'status': status,
        'page_size': 100,
      },
    ) as Map<String, dynamic>;

    return (response['results'] as List)
        .map((row) => HireRequest.fromJson(row as Map<String, dynamic>))
        .toList();
  }

  /// Declines a request. Returns the updated request.
  Future<HireRequest> declineHireRequest(
    int requestId, {
    String note = '',
  }) async {
    final response = await _client.post(
      ApiEndpoints.respondToHireRequest(requestId),
      data: {'accept': false, if (note.isNotEmpty) 'note': note},
    ) as Map<String, dynamic>;

    return HireRequest.fromJson(response['request'] as Map<String, dynamic>);
  }

  /// Accepts a request. This is what creates the engagement, so the server
  /// returns the new engagement rather than the request.
  Future<Engagement> acceptHireRequest(
    int requestId, {
    String note = '',
  }) async {
    final response = await _client.post(
      ApiEndpoints.respondToHireRequest(requestId),
      data: {'accept': true, if (note.isNotEmpty) 'note': note},
    ) as Map<String, dynamic>;

    return Engagement.fromJson(response['engagement'] as Map<String, dynamic>);
  }

  Future<HireRequest> withdrawHireRequest(
    int requestId, {
    String reason = '',
  }) async {
    final response = await _client.post(
      ApiEndpoints.withdrawHireRequest(requestId),
      data: {if (reason.isNotEmpty) 'reason': reason},
    ) as Map<String, dynamic>;

    return HireRequest.fromJson(response['request'] as Map<String, dynamic>);
  }

  // --- 4.5 Engagements -------------------------------------------------------

  Future<List<Engagement>> fetchEngagements({bool liveOnly = false}) async {
    final response = await _client.get(
      ApiEndpoints.engagements,
      query: {
        if (liveOnly) 'live': 'true',
        'page_size': 100,
      },
    ) as Map<String, dynamic>;

    return (response['results'] as List)
        .map((row) => Engagement.fromJson(row as Map<String, dynamic>))
        .toList();
  }

  Future<Engagement> pauseEngagement(int engagementId, {String note = ''}) =>
      _transition(engagementId, action: 'pause', note: note);

  Future<Engagement> resumeEngagement(int engagementId) =>
      _transition(engagementId, action: 'resume');

  /// Terminating is final — the server refuses a later resume. A reason is
  /// required, which is why it is not optional here either.
  Future<Engagement> terminateEngagement(
    int engagementId, {
    required EngagementEndReason reason,
    String note = '',
  }) =>
      _transition(
        engagementId,
        action: 'terminate',
        reason: reason.wireValue,
        note: note,
      );

  // --- 4.6 Notice period -----------------------------------------------------

  /// Ends the arrangement with notice. The engagement stays **active** and its
  /// visits keep appearing on both schedules until [lastWorkingDay].
  ///
  /// Omitting [lastWorkingDay] takes the earliest the server permits, which is
  /// what most people want. Anything shorter than [NoticePeriod.days] is
  /// refused with `notice_too_short`, and the error details carry the earliest
  /// date back so the caller can correct itself rather than guess.
  Future<Engagement> giveNotice(
    int engagementId, {
    required EngagementEndReason reason,
    DateTime? lastWorkingDay,
    String note = '',
  }) async {
    final response = await _client.post(
      ApiEndpoints.giveNotice(engagementId),
      data: {
        'reason': reason.wireValue,
        if (lastWorkingDay != null)
          'last_working_day': formatWireDate(lastWorkingDay),
        if (note.isNotEmpty) 'note': note,
      },
    ) as Map<String, dynamic>;

    return Engagement.fromJson(response['engagement'] as Map<String, dynamic>);
  }

  /// Module 4.6 — what this month's worked days come to, before notice.
  ///
  /// Always fetched and shown before the resident confirms, for the same reason
  /// the cancellation quote is: a charge that appears only after the fact is
  /// the kind of surprise that costs an app its users — and this one arrives at
  /// the moment a working relationship is ending.
  Future<NoticeSettlement> fetchNoticeSettlement(int engagementId) async {
    final response = await _client.get(ApiEndpoints.noticeSettlement(engagementId))
        as Map<String, dynamic>;
    return NoticeSettlement.fromJson(response);
  }

  /// Opens the ledger row for that settlement. Returns the **payment id** the
  /// caller takes through the pay sheet.
  ///
  /// Idempotent on the engagement and month: re-opening the screen or retrying
  /// on a poor connection resumes the same row rather than raising a second
  /// demand for the same wages.
  Future<String> openNoticeSettlement(int engagementId) async {
    final response = await _client.post(ApiEndpoints.noticeSettlement(engagementId))
        as Map<String, dynamic>;
    return (response['payment'] as Map<String, dynamic>)['id'] as String;
  }

  /// Both sides changed their mind before the last working day.
  Future<Engagement> withdrawNotice(int engagementId) async {
    final response = await _client.post(ApiEndpoints.withdrawNotice(engagementId))
        as Map<String, dynamic>;
    return Engagement.fromJson(response['engagement'] as Map<String, dynamic>);
  }

  Future<Engagement> _transition(
    int engagementId, {
    required String action,
    String? reason,
    String note = '',
  }) async {
    final response = await _client.post(
      ApiEndpoints.engagementTransition(engagementId),
      data: {
        'action': action,
        if (reason != null) 'reason': reason,
        if (note.isNotEmpty) 'note': note,
      },
    ) as Map<String, dynamic>;

    return Engagement.fromJson(response['engagement'] as Map<String, dynamic>);
  }
}
