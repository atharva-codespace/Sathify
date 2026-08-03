import 'package:dio/dio.dart';
import 'package:uuid/uuid.dart';

import '../../../../core/config/api_endpoints.dart';
import '../../../../core/errors/api_exception.dart';
import '../../../../core/network/api_client.dart';
import '../local/attendance_queue.dart';
import '../local/self_checkin_queue.dart';
import '../models/attendance_models.dart';
import '../models/self_checkin_models.dart';

/// All Module 7 endpoints, with the offline queue in front of the write path.
///
/// -----------------------------------------------------------------------
/// A GATE DECISION NEVER WAITS FOR THE NETWORK
/// -----------------------------------------------------------------------
/// [recordDecision] writes to the local queue **first**, then tries to push.
/// The guard's screen is free the moment the local write returns, so a worker
/// is never held at the gate by a timing-out request. If the push succeeds the
/// event is settled immediately; if not it stays queued for [syncPending].
///
/// Writing after a successful push instead would lose every decision made
/// during an outage — which is exactly when the queue is needed.
class AttendanceRepository {
  AttendanceRepository({
    ApiClient? client,
    AttendanceQueue? queue,
    SelfCheckInQueue? checkInQueue,
  })  : _client = client ?? ApiClient(),
        _queue = queue ?? AttendanceQueue(),
        _checkInQueue = checkInQueue ?? SelfCheckInQueue();

  final ApiClient _client;
  final AttendanceQueue _queue;

  /// The worker's queue (13.1). Separate from the guard's: they never run on
  /// the same account, and the guard's holds a roster of every expected visit
  /// in the society — data a worker has no business carrying.
  final SelfCheckInQueue _checkInQueue;

  static const _uuid = Uuid();

  AttendanceQueue get queue => _queue;

  /// A fresh client-generated id. See attendance_models.dart on why the server
  /// must never assign this.
  String newEventId() => _uuid.v4();

  // --- 7.1 Gate pass ---------------------------------------------------------

  Future<GatePass> fetchMyGatePass() async {
    final response =
        await _client.get(ApiEndpoints.myGatePass) as Map<String, dynamic>;
    return GatePass.fromJson(response);
  }

  Future<GatePass> rotateMyGatePass() async {
    final response =
        await _client.post(ApiEndpoints.rotateGatePass) as Map<String, dynamic>;
    return GatePass.fromJson(response['pass'] as Map<String, dynamic>);
  }

  // --- 7.2 / 7.4 Roster ------------------------------------------------------

  /// Fetches the day's roster and caches it for offline scanning.
  Future<List<RosterEntry>> refreshRoster({DateTime? day}) async {
    final target = day ?? DateTime.now();
    final response = await _client.get(
      ApiEndpoints.gateRoster,
      query: {'date': _dayParam(target)},
    ) as Map<String, dynamic>;

    final roster = ((response['results'] as List?) ?? const [])
        .map((row) => RosterEntry.fromJson(row as Map<String, dynamic>))
        .toList();

    await _queue.cacheRoster(target, roster);
    return roster;
  }

  Future<List<RosterEntry>> cachedRoster({DateTime? day}) =>
      _queue.cachedRoster(day ?? DateTime.now());

  // --- 7.2 Scanning ----------------------------------------------------------

  /// Resolves a scanned code, falling back to the cached roster when offline.
  ///
  /// The offline answer is deliberately the same shape as the online one
  /// ([RosterEntry.toScanResult]), so the guard's screen looks and behaves
  /// identically either way — only [ScanResult.fromCache] differs, so the UI
  /// can say where the answer came from.
  ///
  /// A code that is not in the cache while offline is genuinely unknown to this
  /// device, and is reported as such rather than guessed at.
  Future<ScanResult?> resolveScan(String code, {DateTime? day}) async {
    try {
      final response = await _client.post(
        ApiEndpoints.scanPass,
        data: {'code': code},
      ) as Map<String, dynamic>;
      return ScanResult.fromJson(response);
    } on ApiException catch (error) {
      if (!error.isConnectionFailure) rethrow;
      final entry = await _queue.findByCode(day ?? DateTime.now(), code);
      return entry?.toScanResult();
    }
  }

  // --- 7.2 / 7.5 / 7.6 Recording --------------------------------------------

  /// Logs a decision. Queues locally first, then attempts to push.
  ///
  /// Returns true if it reached the server, false if it is waiting in the
  /// queue. Either way the decision is durable before this returns.
  Future<bool> recordDecision(AttendanceEventDraft event) async {
    await _queue.enqueue(event);

    try {
      await _client.post(
        ApiEndpoints.attendanceEvents,
        data: event.copyWith(wasOffline: false).toJson(),
      );
      await _queue.removeSettled([event.id]);
      return true;
    } on ApiException {
      // Left queued on purpose, including for a server error — the guard has
      // already moved on and the event must not be lost because of a 500.
      await _queue.recordAttempt([event.id]);
      return false;
    }
  }

  /// Module 7.4 — drains the queue.
  ///
  /// Only ids the server actually reported back are cleared, so a truncated
  /// response leaves the events queued for the next attempt rather than
  /// silently dropping them.
  Future<SyncResult?> syncPending() async {
    final pending = await _queue.pending();
    if (pending.isEmpty) return null;

    final ids = pending.map((event) => event.id).toList();

    try {
      final response = await _client.post(
        ApiEndpoints.attendanceSync,
        data: {'events': pending.map((event) => event.toJson()).toList()},
      ) as Map<String, dynamic>;

      final result = SyncResult.fromJson(response);
      await _queue.removeSettled(result.settledIds);
      return result;
    } on ApiException {
      await _queue.recordAttempt(ids);
      rethrow;
    }
  }

  Future<int> pendingCount() => _queue.pendingCount();

  // --- 13.3 tier 2: the worker's own check-in --------------------------------

  /// Records an arrival, queue-first, exactly like a guard's decision.
  ///
  /// Returns the server's verdict when it reached the server, and null when it
  /// is waiting in the queue. Null is not a failure — the check-in is durable
  /// before this returns, and [syncPendingCheckIns] will land it. Blocking on
  /// the network here would mean a worker in a stairwell with no signal has
  /// nothing to show for having turned up.
  Future<SelfCheckInResult?> selfCheckIn(SelfCheckInDraft draft) async {
    await _checkInQueue.enqueue(draft);

    try {
      final response = await _client.post(
        ApiEndpoints.selfCheckIn,
        data: draft.copyWith(wasOffline: false).toJson(),
      ) as Map<String, dynamic>;

      await _checkInQueue.removeSettled([draft.id]);
      return SelfCheckInResult.fromJson(response);
    } on ApiException {
      await _checkInQueue.recordAttempt([draft.id]);
      return null;
    }
  }

  /// Drains the worker's queue.
  ///
  /// One request per row rather than a batch: the self check-in endpoint is
  /// idempotent on the same client id, so replaying it is safe, and a worker's
  /// queue is a handful of rows a day rather than a shift's worth of scans. A
  /// batch endpoint would be machinery for a problem this side does not have.
  Future<int> syncPendingCheckIns() async {
    final pending = await _checkInQueue.pending();
    if (pending.isEmpty) return 0;

    var settled = 0;
    for (final draft in pending) {
      try {
        await _client.post(ApiEndpoints.selfCheckIn, data: draft.toJson());
        await _checkInQueue.removeSettled([draft.id]);
        settled++;
      } on ApiException {
        // Stop at the first failure. Continuing would burn the rest of the
        // queue against the same outage, and the order matters for a worker
        // reading their own history back.
        await _checkInQueue.recordAttempt([draft.id]);
        break;
      }
    }
    return settled;
  }

  Future<int> pendingCheckInCount() => _checkInQueue.pendingCount();

  Future<List<AttendanceEvent>> fetchEvents({
    DateTime? day,
    bool needsReviewOnly = false,
  }) async {
    final response = await _client.get(
      ApiEndpoints.attendanceEvents,
      query: {
        if (day != null) 'date': _dayParam(day),
        if (needsReviewOnly) 'needs_review': 'true',
        'page_size': 100,
      },
    ) as Map<String, dynamic>;

    return (response['results'] as List)
        .map((row) => AttendanceEvent.fromJson(row as Map<String, dynamic>))
        .toList();
  }

  // --- 7.3 Face verification -------------------------------------------------

  /// Submits a live gate photo.
  ///
  /// Only meaningful online — a comparison needs the server. When offline the
  /// guard verifies visually, which is the same fallback a below-threshold
  /// match produces anyway.
  Future<FaceCheckResult> verifyFace(String eventId, String photoPath) async {
    final form = FormData.fromMap({
      'photo': await MultipartFile.fromFile(photoPath),
    });

    final response = await _client.upload(
      ApiEndpoints.verifyFace(eventId),
      formData: form,
    ) as Map<String, dynamic>;

    return FaceCheckResult.fromJson(response['result'] as Map<String, dynamic>);
  }

  /// The guard resolves a below-threshold match. A reason is always required.
  Future<AttendanceEvent> resolveEvent(
    String eventId, {
    required bool allow,
    required String reason,
  }) async {
    final response = await _client.post(
      ApiEndpoints.resolveEvent(eventId),
      data: {'allow': allow, 'reason': reason},
    ) as Map<String, dynamic>;

    return AttendanceEvent.fromJson(response['event'] as Map<String, dynamic>);
  }

  // --- 7.5 Register digitisation ---------------------------------------------

  Future<void> uploadRegisterScan({
    required String imagePath,
    required DateTime forDate,
    int? gateId,
    String note = '',
  }) async {
    final form = FormData.fromMap({
      'image': await MultipartFile.fromFile(imagePath),
      'for_date': _dayParam(forDate),
      if (gateId != null) 'gate': gateId,
      if (note.isNotEmpty) 'note': note,
    });

    await _client.upload(ApiEndpoints.registerScans, formData: form);
  }

  String _dayParam(DateTime day) => '${day.year.toString().padLeft(4, '0')}-'
      '${day.month.toString().padLeft(2, '0')}-'
      '${day.day.toString().padLeft(2, '0')}';
}
