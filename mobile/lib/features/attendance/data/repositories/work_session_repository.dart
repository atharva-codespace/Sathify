import 'package:uuid/uuid.dart';

import '../../../../core/config/api_endpoints.dart';
import '../../../../core/network/api_client.dart';
import '../models/work_session_models.dart';

/// Module 7.7 — work sessions, and the invoices they add up to.
///
/// -------------------------------------------------------------------------
/// THE CLIENT MINTS THE SESSION ID
/// -------------------------------------------------------------------------
/// [startSession] generates the UUID before the request leaves the phone, for
/// the same reason `AttendanceEvent` does: she taps Start in a stairwell with
/// no signal, and when the request finally lands — possibly twice, possibly on
/// a retry the user never saw — it must not open a second session for the same
/// day. The server treats a repeat as "this day is already under way, here it
/// is" rather than as an error.
///
/// -------------------------------------------------------------------------
/// NOTHING HERE FAILS IN A WAY THAT COSTS HER THE DAY
/// -------------------------------------------------------------------------
/// A missing GPS fix is sent as `null` rather than withheld: the server lowers
/// the capture tier and flags the session for a human, and she starts work
/// either way. An app that refused to start the clock without a location would
/// be charging a worker for her phone's hardware.
class WorkSessionRepository {
  WorkSessionRepository({ApiClient? client, Uuid? uuid})
      : _client = client ?? ApiClient(),
        _uuid = uuid ?? const Uuid();

  final ApiClient _client;
  final Uuid _uuid;

  // --- The worker's day ------------------------------------------------------

  /// Everything the Today screen renders, in one call.
  Future<TodayBoard> fetchToday({DateTime? date}) async {
    final response = await _client.get(
      ApiEndpoints.sessionsToday,
      query: {if (date != null) 'date': _isoDate(date)},
    ) as Map<String, dynamic>;
    return TodayBoard.fromJson(response);
  }

  /// Open a session for one flat.
  ///
  /// [latitude] and [longitude] are optional on purpose — see the class note.
  Future<WorkSession> startSession({
    required int engagementId,
    double? latitude,
    double? longitude,
  }) async {
    final response = await _client.post(
      ApiEndpoints.sessionStart,
      data: {
        'id': _uuid.v4(),
        'engagement': engagementId,
        'latitude': latitude,
        'longitude': longitude,
      },
    ) as Map<String, dynamic>;
    return WorkSession.fromJson(response);
  }

  /// Stop the clock. Safe to call twice.
  Future<WorkSession> stopSession(String sessionId) async {
    final response = await _client.post(
      ApiEndpoints.sessionStop(sessionId),
      data: const <String, dynamic>{},
    ) as Map<String, dynamic>;
    return WorkSession.fromJson(response);
  }

  /// Ask the resident for extra time. Returns what is currently approved.
  ///
  /// Deliberately does not change what is billed: unapproved extra time is not
  /// paid, and the app must not let her work it believing otherwise.
  Future<int> requestOvertime(String sessionId, {required int minutes}) async {
    final response = await _client.post(
      ApiEndpoints.sessionRequestOvertime(sessionId),
      data: {'minutes': minutes},
    ) as Map<String, dynamic>;
    return (response['approved_minutes'] as int?) ?? 0;
  }

  /// Her answer to a session the nightly job closed for her.
  Future<WorkSession> confirmSession(
    String sessionId, {
    required bool correct,
    String note = '',
  }) async {
    final response = await _client.post(
      ApiEndpoints.sessionConfirm(sessionId),
      data: {'correct': correct, if (note.isNotEmpty) 'note': note},
    ) as Map<String, dynamic>;
    return WorkSession.fromJson(response);
  }

  // --- The resident's side ---------------------------------------------------

  Future<List<WorkSession>> fetchSessions({
    DateTime? from,
    DateTime? to,
    int? engagementId,
  }) async {
    final response = await _client.get(
      ApiEndpoints.sessions,
      query: {
        if (from != null) 'from': _isoDate(from),
        if (to != null) 'to': _isoDate(to),
        if (engagementId != null) 'engagement': engagementId,
      },
    ) as List;
    return response
        .map((row) => WorkSession.fromJson(row as Map<String, dynamic>))
        .toList();
  }

  /// The resident approving extra time. This is the only thing that makes it
  /// billable — the worker's request alone does not.
  Future<WorkSession> approveOvertime(
    String sessionId, {
    required int minutes,
  }) async {
    final response = await _client.post(
      ApiEndpoints.sessionApproveOvertime(sessionId),
      data: {'minutes': minutes},
    ) as Map<String, dynamic>;
    return WorkSession.fromJson(response);
  }

  // --- Invoices --------------------------------------------------------------

  Future<List<Invoice>> fetchInvoices({int? engagementId}) async {
    final response = await _client.get(
      ApiEndpoints.invoices,
      query: {if (engagementId != null) 'engagement': engagementId},
    ) as List;
    return response
        .map((row) => Invoice.fromJson(row as Map<String, dynamic>))
        .toList();
  }

  Future<Invoice> fetchInvoice(int invoiceId) async {
    final response = await _client.get(ApiEndpoints.invoice(invoiceId))
        as Map<String, dynamic>;
    return Invoice.fromJson(response);
  }

  /// Query one line. Holds that amount and leaves the rest payable.
  Future<Invoice> raiseQuery(
    int invoiceId, {
    required String sessionId,
    required String reason,
    String description = '',
  }) async {
    final response = await _client.post(
      ApiEndpoints.invoiceQuery(invoiceId),
      data: {
        'session': sessionId,
        'reason': reason,
        if (description.isNotEmpty) 'description': description,
      },
    ) as Map<String, dynamic>;
    return Invoice.fromJson(response);
  }

  /// Accept the other party's version, releasing the hold.
  Future<Invoice> acceptQuery(int queryId) async {
    final response = await _client.post(
      ApiEndpoints.acceptQuery(queryId),
      data: const <String, dynamic>{},
    ) as Map<String, dynamic>;
    return Invoice.fromJson(response);
  }

  static String _isoDate(DateTime date) =>
      '${date.year.toString().padLeft(4, '0')}-'
      '${date.month.toString().padLeft(2, '0')}-'
      '${date.day.toString().padLeft(2, '0')}';
}
