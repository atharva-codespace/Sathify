/// Data models for Module 7 — Attendance & Gate Verification.
///
/// -----------------------------------------------------------------------
/// THE ID IS GENERATED HERE, NOT BY THE SERVER
/// -----------------------------------------------------------------------
/// [AttendanceEventDraft.id] is a UUID this device mints the moment the guard
/// makes a decision — before the server has ever heard of it. That is what
/// makes `/attendance/sync/` idempotent: a queue replayed after a dropped
/// connection cannot log the same person through the gate twice. Never let the
/// server assign it, and never regenerate one for an event already queued.
///
/// -----------------------------------------------------------------------
/// TWO TIMESTAMPS, DELIBERATELY
/// -----------------------------------------------------------------------
/// [AttendanceEventDraft.occurredAt] is when the person actually walked
/// through. The server separately records when it heard about it. A batch that
/// syncs at 6pm must not look like forty people arriving at 6pm, which is what
/// would happen if the client left the time to the server.
library;

import 'dart:convert';

/// Which way through the gate.
enum GateDirection {
  entry('entry', 'In'),
  exit('exit', 'Out');

  const GateDirection(this.wireValue, this.label);

  final String wireValue;
  final String label;

  static GateDirection fromWire(String? value) =>
      GateDirection.values.firstWhere(
        (d) => d.wireValue == value,
        orElse: () => GateDirection.entry,
      );
}

/// How the worker was identified.
enum VerificationMethod {
  qr('qr', 'QR scanned'),
  face('face', 'Face verified'),
  manual('manual', 'Logged by hand'),
  selfCheckin('self_checkin', 'Self check-in'),
  register('register', 'From the register');

  const VerificationMethod(this.wireValue, this.label);

  final String wireValue;
  final String label;

  static VerificationMethod fromWire(String? value) =>
      VerificationMethod.values.firstWhere(
        (m) => m.wireValue == value,
        orElse: () => VerificationMethod.qr,
      );
}

/// What was decided at the gate.
enum GateDecision {
  allowed('allowed', 'Allowed'),
  denied('denied', 'Refused'),

  /// A face check came back below threshold, or could not run. NOT a refusal —
  /// the guard decides. See the backend's face.py for why this is separate.
  pendingReview('pending_review', 'Needs your decision');

  const GateDecision(this.wireValue, this.label);

  final String wireValue;
  final String label;

  static GateDecision fromWire(String? value) => GateDecision.values.firstWhere(
        (d) => d.wireValue == value,
        orElse: () => GateDecision.allowed,
      );
}

/// One visit a worker is expected for, as the guard sees it.
class ExpectedVisit {
  const ExpectedVisit({
    required this.source,
    required this.sourceId,
    this.title = '',
    this.startTime = '',
    this.endTime = '',
    this.flatLabel = '',
    this.isConfirmed = true,
  });

  final String source;
  final int sourceId;
  final String title;
  final String startTime;
  final String endTime;
  final String flatLabel;
  final bool isConfirmed;

  String get timeLabel {
    String trim(String value) {
      final parts = value.split(':');
      return parts.length >= 2 ? '${parts[0]}:${parts[1]}' : value;
    }

    final start = trim(startTime);
    final end = trim(endTime);
    return end.isEmpty ? start : '$start – $end';
  }

  Map<String, dynamic> toJson() => {
        'source': source,
        'source_id': sourceId,
        'title': title,
        'start_time': startTime,
        'end_time': endTime,
        'flat_label': flatLabel,
        'is_confirmed': isConfirmed,
      };

  factory ExpectedVisit.fromJson(Map<String, dynamic> json) => ExpectedVisit(
        source: json['source'] as String? ?? '',
        sourceId: json['source_id'] as int? ?? 0,
        title: json['title'] as String? ?? '',
        startTime: json['start_time'] as String? ?? '',
        endTime: json['end_time'] as String? ?? '',
        flatLabel: json['flat_label'] as String? ?? '',
        isConfirmed: json['is_confirmed'] as bool? ?? true,
      );
}

/// What a scan tells the guard (Module 7.2). Resolving a code creates nothing.
class ScanResult {
  const ScanResult({
    required this.workerId,
    required this.workerName,
    required this.isUsable,
    required this.recommendation,
    this.workerPhoto,
    this.reason = '',
    this.isExpected = false,
    this.expectedVisits = const [],
    this.fromCache = false,
  });

  final int workerId;
  final String workerName;
  final String? workerPhoto;
  final bool isUsable;
  final String reason;
  final bool isExpected;

  /// The server's suggestion. SRS 3.7 gives the guard the actual call, so this
  /// pre-selects a button — it never decides on its own.
  final GateDecision recommendation;
  final List<ExpectedVisit> expectedVisits;

  /// Resolved from the cached roster rather than the server, i.e. offline.
  final bool fromCache;

  factory ScanResult.fromJson(
    Map<String, dynamic> json, {
    bool fromCache = false,
  }) =>
      ScanResult(
        workerId: json['worker_id'] as int? ?? 0,
        workerName: json['worker_name'] as String? ?? '',
        workerPhoto: json['worker_photo'] as String?,
        isUsable: json['is_usable'] as bool? ?? false,
        reason: json['reason'] as String? ?? '',
        isExpected: json['is_expected'] as bool? ?? false,
        recommendation:
            GateDecision.fromWire(json['recommendation'] as String?),
        expectedVisits: ((json['expected_visits'] as List?) ?? const [])
            .map((row) => ExpectedVisit.fromJson(row as Map<String, dynamic>))
            .toList(),
        fromCache: fromCache,
      );
}

/// One worker on the cached day roster (Module 7.2/7.4).
class RosterEntry {
  const RosterEntry({
    required this.workerId,
    required this.workerName,
    this.passCode,
    this.visits = const [],
  });

  final int workerId;
  final String workerName;

  /// What their QR encodes. Null when they have no active pass.
  final String? passCode;
  final List<ExpectedVisit> visits;

  Map<String, dynamic> toJson() => {
        'worker_id': workerId,
        'worker_name': workerName,
        'pass_code': passCode,
        'visits': visits.map((v) => v.toJson()).toList(),
      };

  factory RosterEntry.fromJson(Map<String, dynamic> json) => RosterEntry(
        workerId: json['worker_id'] as int? ?? 0,
        workerName: json['worker_name'] as String? ?? '',
        passCode: json['pass_code'] as String?,
        visits: ((json['visits'] as List?) ?? const [])
            .map((row) => ExpectedVisit.fromJson(row as Map<String, dynamic>))
            .toList(),
      );

  /// Turns a cached roster hit into the same shape an online scan returns, so
  /// the guard's screen renders identically whether or not there is signal.
  ScanResult toScanResult() => ScanResult(
        workerId: workerId,
        workerName: workerName,
        isUsable: passCode != null,
        isExpected: visits.isNotEmpty,
        recommendation:
            visits.isEmpty ? GateDecision.pendingReview : GateDecision.allowed,
        expectedVisits: visits,
        fromCache: true,
      );
}

/// A decision the guard made, ready to send or to queue (Modules 7.2–7.6).
class AttendanceEventDraft {
  const AttendanceEventDraft({
    required this.id,
    required this.workerId,
    required this.occurredAt,
    this.direction = GateDirection.entry,
    this.method = VerificationMethod.qr,
    this.decision = GateDecision.allowed,
    this.decisionReason = '',
    this.gateId,
    this.deviceId = '',
    this.wasOffline = false,
  });

  /// Generated on this device before the server sees it. See the file header.
  final String id;
  final int workerId;
  final DateTime occurredAt;
  final GateDirection direction;
  final VerificationMethod method;
  final GateDecision decision;
  final String decisionReason;
  final int? gateId;
  final String deviceId;
  final bool wasOffline;

  Map<String, dynamic> toJson() => {
        'id': id,
        'worker': workerId,
        'direction': direction.wireValue,
        'method': method.wireValue,
        'decision': decision.wireValue,
        if (decisionReason.isNotEmpty) 'decision_reason': decisionReason,
        'occurred_at': occurredAt.toUtc().toIso8601String(),
        if (gateId != null) 'gate': gateId,
        if (deviceId.isNotEmpty) 'device_id': deviceId,
        'was_offline': wasOffline,
      };

  /// Round-trips through the SQLite queue.
  String encode() => jsonEncode(toJson());

  factory AttendanceEventDraft.decode(String raw) =>
      AttendanceEventDraft.fromJson(jsonDecode(raw) as Map<String, dynamic>);

  factory AttendanceEventDraft.fromJson(Map<String, dynamic> json) =>
      AttendanceEventDraft(
        id: json['id'] as String,
        workerId: json['worker'] as int,
        occurredAt: DateTime.parse(json['occurred_at'] as String),
        direction: GateDirection.fromWire(json['direction'] as String?),
        method: VerificationMethod.fromWire(json['method'] as String?),
        decision: GateDecision.fromWire(json['decision'] as String?),
        decisionReason: json['decision_reason'] as String? ?? '',
        gateId: json['gate'] as int?,
        deviceId: json['device_id'] as String? ?? '',
        wasOffline: json['was_offline'] as bool? ?? false,
      );

  AttendanceEventDraft copyWith({bool? wasOffline}) => AttendanceEventDraft(
        id: id,
        workerId: workerId,
        occurredAt: occurredAt,
        direction: direction,
        method: method,
        decision: decision,
        decisionReason: decisionReason,
        gateId: gateId,
        deviceId: deviceId,
        wasOffline: wasOffline ?? this.wasOffline,
      );
}

/// A recorded gate decision as the server holds it (Module 7.6).
class AttendanceEvent {
  const AttendanceEvent({
    required this.id,
    required this.workerName,
    required this.direction,
    required this.decision,
    required this.method,
    required this.occurredAt,
    this.workerId = 0,
    this.workerPhoto,
    this.gateName,
    this.recordedByName,
    this.decisionReason = '',
    this.wasExpected = false,
    this.faceChecked = false,
    this.faceVerified = false,
    this.faceMatchScore,
    this.overriddenByName,
    this.overrideReason = '',
    this.wasOffline = false,
  });

  final String id;
  final int workerId;
  final String workerName;
  final String? workerPhoto;
  final GateDirection direction;
  final GateDecision decision;
  final VerificationMethod method;
  final DateTime occurredAt;
  final String? gateName;
  final String? recordedByName;
  final String decisionReason;
  final bool wasExpected;

  final bool faceChecked;
  final bool faceVerified;
  final double? faceMatchScore;
  final String? overriddenByName;
  final String overrideReason;

  final bool wasOffline;

  bool get needsReview => decision == GateDecision.pendingReview;
  bool get wasOverridden => overriddenByName != null;

  factory AttendanceEvent.fromJson(Map<String, dynamic> json) =>
      AttendanceEvent(
        id: json['id'].toString(),
        workerId: json['worker'] as int? ?? 0,
        workerName: json['worker_name'] as String? ?? '',
        workerPhoto: json['worker_photo'] as String?,
        direction: GateDirection.fromWire(json['direction'] as String?),
        decision: GateDecision.fromWire(json['decision'] as String?),
        method: VerificationMethod.fromWire(json['method'] as String?),
        occurredAt: DateTime.parse(json['occurred_at'] as String),
        gateName: json['gate_name'] as String?,
        recordedByName: json['recorded_by_name'] as String?,
        decisionReason: json['decision_reason'] as String? ?? '',
        wasExpected: json['was_expected'] as bool? ?? false,
        faceChecked: json['face_checked'] as bool? ?? false,
        faceVerified: json['face_verified'] as bool? ?? false,
        faceMatchScore: (json['face_match_score'] as num?)?.toDouble(),
        overriddenByName: json['overridden_by_name'] as String?,
        overrideReason: json['override_reason'] as String? ?? '',
        wasOffline: json['was_offline'] as bool? ?? false,
      );
}

/// The worker's own QR credential (Module 7.1).
class GatePass {
  const GatePass({
    required this.code,
    required this.isUsable,
    this.isActive = true,
    this.rotationCount = 0,
    this.revokedReason = '',
  });

  /// The QR payload.
  final String code;
  final bool isUsable;
  final bool isActive;
  final int rotationCount;
  final String revokedReason;

  factory GatePass.fromJson(Map<String, dynamic> json) => GatePass(
        code: json['code'].toString(),
        isUsable: json['is_usable'] as bool? ?? false,
        isActive: json['is_active'] as bool? ?? true,
        rotationCount: json['rotation_count'] as int? ?? 0,
        revokedReason: json['revoked_reason'] as String? ?? '',
      );
}

/// What a sync accomplished (Module 7.4).
class SyncResult {
  const SyncResult({
    this.created = const [],
    this.duplicates = const [],
    this.rejected = const [],
  });

  final List<String> created;

  /// Already on the server. A success — the device should clear these, not
  /// retry them.
  final List<String> duplicates;

  /// The server refused these. Dropping them is the only way to stop the queue
  /// retrying forever.
  final List<Map<String, dynamic>> rejected;

  /// Everything safe to remove from the local queue.
  List<String> get settledIds => [
        ...created,
        ...duplicates,
        ...rejected.map((row) => row['id'].toString()),
      ];

  int get acceptedCount => created.length + duplicates.length;

  factory SyncResult.fromJson(Map<String, dynamic> json) => SyncResult(
        created: ((json['created'] as List?) ?? const [])
            .map((id) => id.toString())
            .toList(),
        duplicates: ((json['duplicates'] as List?) ?? const [])
            .map((id) => id.toString())
            .toList(),
        rejected: ((json['rejected'] as List?) ?? const [])
            .map((row) => Map<String, dynamic>.from(row as Map))
            .toList(),
      );
}

/// The outcome of a face comparison (Module 7.3).
class FaceCheckResult {
  const FaceCheckResult({
    required this.available,
    required this.verified,
    this.score,
    this.engine = '',
    this.reason = '',
  });

  /// Whether a comparison actually ran. False means nothing was measured, which
  /// is not the same as a failed match and must not be shown as one.
  final bool available;
  final bool verified;
  final double? score;
  final String engine;
  final String reason;

  bool get needsGuardReview => !verified;

  factory FaceCheckResult.fromJson(Map<String, dynamic> json) =>
      FaceCheckResult(
        available: json['available'] as bool? ?? false,
        verified: json['verified'] as bool? ?? false,
        score: (json['score'] as num?)?.toDouble(),
        engine: json['engine'] as String? ?? '',
        reason: json['reason'] as String? ?? '',
      );
}
