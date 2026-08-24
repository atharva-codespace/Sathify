/// Data models for Module 7.7 — work sessions, and Module 8.10 — invoices.
///
/// -------------------------------------------------------------------------
/// A SESSION IS PER FLAT, NOT PER DAY
/// -------------------------------------------------------------------------
/// A worker enters the society once and works four homes. The gate log holds
/// two events for that whole trip, which is why it cannot say what any one
/// household owes. A [WorkSession] is one engagement's day, and it is what both
/// apps read: her Today screen is a stack of these, and the resident's screen
/// is the same row seen from the other side.
///
/// -------------------------------------------------------------------------
/// MONEY IS PAISE, AS INTEGERS
/// -------------------------------------------------------------------------
/// Same rule as Module 8: integers for arithmetic, and never a `double`. The
/// visit fee is carried as its own field rather than folded into the hourly
/// total, because the resident is shown it as its own line and the two numbers
/// have to agree.
library;

import '../../../payments/data/models/payment_models.dart' show formatPaise;

export '../../../payments/data/models/payment_models.dart' show formatPaise;

/// How a session's boundaries were captured, best first.
///
/// Carried into the UI because it is the first thing anyone should look at when
/// a visit is disputed — and because tier 1 and 2 mean *somebody observed this*,
/// while 4 and 5 mean somebody inferred it.
enum SessionSource {
  self('self', 'Her phone', 1),
  residentScan('resident_scan', 'Scanned at your door', 2),
  residentConfirm('resident_confirm', 'Confirmed by the resident', 3),
  derived('derived', 'Worked out from the gate log', 4),
  manual('manual', 'Entered by the office', 5);

  const SessionSource(this.wire, this.label, this.tier);

  final String wire;
  final String label;
  final int tier;

  /// Tier 1 and 2 rest on an observation rather than an inference.
  bool get isTrusted => tier <= 2;

  static SessionSource parse(String? value) => SessionSource.values.firstWhere(
        (source) => source.wire == value,
        orElse: () => SessionSource.manual,
      );
}

enum SessionStatus {
  open('open', 'Working now'),
  closed('closed', 'Done'),

  /// Closed by the nightly job because nobody tapped Stop. Billed at the
  /// scheduled hours — never open-ended — and always flagged for a human.
  autoClosed('auto_closed', 'Closed for you'),
  cancelledAtDoor('cancelled_at_door', 'Cancelled at the door'),
  noShow('no_show', 'Did not attend');

  const SessionStatus(this.wire, this.label);

  final String wire;
  final String label;

  bool get isOpen => this == SessionStatus.open;
  bool get isFinished =>
      this == SessionStatus.closed || this == SessionStatus.autoClosed;

  static SessionStatus parse(String? value) => SessionStatus.values.firstWhere(
        (status) => status.wire == value,
        orElse: () => SessionStatus.closed,
      );
}

/// One engagement's work on one day.
class WorkSession {
  const WorkSession({
    required this.id,
    required this.engagementId,
    required this.visitDate,
    required this.source,
    required this.status,
    this.startedAt,
    this.endedAt,
    this.flat = '',
    this.residentName = '',
    this.workerName = '',
    this.scheduledStart = '',
    this.scheduledEnd = '',
    this.needsReview = false,
    this.reviewNote = '',
    this.approvedOvertimeMinutes = 0,
    this.billableMinutes = 0,
    this.overtimeMinutes = 0,
    this.unbilledExtraMinutes = 0,
    this.timePaise = 0,
    this.overtimePaise = 0,
    this.visitFeePaise = 0,
    this.totalPaise = 0,
    this.pricedAt,
    this.canRequestOvertime = false,
  });

  factory WorkSession.fromJson(Map<String, dynamic> json) => WorkSession(
        id: json['id'] as String,
        engagementId: json['engagement'] as int,
        visitDate: DateTime.parse(json['visit_date'] as String),
        source: SessionSource.parse(json['source'] as String?),
        status: SessionStatus.parse(json['status'] as String?),
        startedAt: _parseTime(json['started_at']),
        endedAt: _parseTime(json['ended_at']),
        flat: (json['flat'] as String?) ?? '',
        residentName: (json['resident_name'] as String?) ?? '',
        workerName: (json['worker_name'] as String?) ?? '',
        scheduledStart: (json['scheduled_start'] as String?) ?? '',
        scheduledEnd: (json['scheduled_end'] as String?) ?? '',
        needsReview: (json['needs_review'] as bool?) ?? false,
        reviewNote: (json['review_note'] as String?) ?? '',
        approvedOvertimeMinutes: (json['approved_ot_minutes'] as int?) ?? 0,
        billableMinutes: (json['billable_minutes'] as int?) ?? 0,
        overtimeMinutes: (json['overtime_minutes'] as int?) ?? 0,
        unbilledExtraMinutes: (json['unbilled_extra_minutes'] as int?) ?? 0,
        timePaise: (json['time_paise'] as int?) ?? 0,
        overtimePaise: (json['overtime_paise'] as int?) ?? 0,
        visitFeePaise: (json['visit_fee_paise'] as int?) ?? 0,
        totalPaise: (json['total_paise'] as int?) ?? 0,
        pricedAt: _parseTime(json['priced_at']),
        canRequestOvertime: (json['can_request_overtime'] as bool?) ?? false,
      );

  final String id;
  final int engagementId;
  final DateTime visitDate;
  final SessionSource source;
  final SessionStatus status;
  final DateTime? startedAt;
  final DateTime? endedAt;

  final String flat;
  final String residentName;
  final String workerName;

  /// Wall-clock `HH:MM:SS` from the server, not an instant. The schedule
  /// belongs to the society's clock; converting it to a local `DateTime` here
  /// is how an evening visit ends up rendered as the following morning.
  final String scheduledStart;
  final String scheduledEnd;

  final bool needsReview;
  final String reviewNote;

  final int approvedOvertimeMinutes;
  final int billableMinutes;
  final int overtimeMinutes;

  /// Worked past the schedule without approval. Recorded and shown to both
  /// sides, and never charged — she should see that the app noticed, and the
  /// resident should see goodwill they did not pay for.
  final int unbilledExtraMinutes;

  final int timePaise;
  final int overtimePaise;
  final int visitFeePaise;
  final int totalPaise;

  /// Set once, when the visit was priced. After this the arithmetic is frozen:
  /// a config change must not rewrite what somebody was already paid.
  final DateTime? pricedAt;
  final bool canRequestOvertime;

  bool get isPriced => pricedAt != null;
  bool get isRunning => status.isOpen;

  /// Minutes elapsed since she started, for the live counter.
  int get elapsedMinutes {
    if (startedAt == null) return 0;
    final end = endedAt ?? DateTime.now();
    final minutes = end.difference(startedAt!).inMinutes;
    return minutes < 0 ? 0 : minutes;
  }

  int get billedMinutes => billableMinutes + overtimeMinutes;

  String get totalDisplay => formatPaise(totalPaise);
  String get visitFeeDisplay => formatPaise(visitFeePaise);

  /// `09:00:00` -> `9:00 am`. Kept here so every screen renders a schedule the
  /// same way.
  static String prettyTime(String wallClock) {
    if (wallClock.isEmpty) return '';
    final parts = wallClock.split(':');
    if (parts.length < 2) return wallClock;
    final hour = int.tryParse(parts[0]) ?? 0;
    final minute = parts[1];
    final suffix = hour < 12 ? 'am' : 'pm';
    final display = hour % 12 == 0 ? 12 : hour % 12;
    return '$display:$minute $suffix';
  }

  static DateTime? _parseTime(Object? value) =>
      value == null ? null : DateTime.parse(value as String).toLocal();
}

/// One card on the worker's Today screen: the engagement, plus its session if
/// the day has started.
class TodayCard {
  const TodayCard({
    required this.engagementId,
    required this.flat,
    required this.residentName,
    required this.scheduledStart,
    required this.scheduledEnd,
    required this.isHourly,
    required this.hourlyRate,
    required this.visitFee,
    this.session,
  });

  factory TodayCard.fromJson(Map<String, dynamic> json) => TodayCard(
        engagementId: json['engagement'] as int,
        flat: (json['flat'] as String?) ?? '',
        residentName: (json['resident_name'] as String?) ?? '',
        scheduledStart: (json['scheduled_start'] as String?) ?? '',
        scheduledEnd: (json['scheduled_end'] as String?) ?? '',
        isHourly: (json['is_hourly'] as bool?) ?? false,
        hourlyRate: (json['hourly_rate'] as int?) ?? 0,
        visitFee: (json['visit_fee'] as int?) ?? 0,
        session: json['session'] == null
            ? null
            : WorkSession.fromJson(json['session'] as Map<String, dynamic>),
      );

  final int engagementId;
  final String flat;
  final String residentName;
  final String scheduledStart;
  final String scheduledEnd;
  final bool isHourly;
  final int hourlyRate;
  final int visitFee;
  final WorkSession? session;

  bool get notStarted => session == null;
  bool get isRunning => session?.isRunning ?? false;
  bool get isDone => session?.status.isFinished ?? false;
}

/// The whole Today screen in one payload.
///
/// One request rather than three: the screen is a stack of flats whose state
/// depends on both the roster and the day's sessions, and resolving that join
/// on the phone over a patchy connection is how a home screen ends up
/// half-rendered.
class TodayBoard {
  const TodayBoard({
    required this.date,
    required this.earnedPaise,
    required this.billedMinutes,
    required this.flatsTotal,
    required this.flatsDone,
    required this.cards,
  });

  factory TodayBoard.fromJson(Map<String, dynamic> json) => TodayBoard(
        date: DateTime.parse(json['date'] as String),
        earnedPaise: (json['earned_paise'] as int?) ?? 0,
        billedMinutes: (json['billed_minutes'] as int?) ?? 0,
        flatsTotal: (json['flats_total'] as int?) ?? 0,
        flatsDone: (json['flats_done'] as int?) ?? 0,
        cards: ((json['cards'] as List?) ?? const [])
            .map((row) => TodayCard.fromJson(row as Map<String, dynamic>))
            .toList(),
      );

  final DateTime date;
  final int earnedPaise;
  final int billedMinutes;
  final int flatsTotal;
  final int flatsDone;
  final List<TodayCard> cards;

  String get earnedDisplay => formatPaise(earnedPaise);

  TodayCard? get running => cards.where((card) => card.isRunning).firstOrNull;

  List<TodayCard> get done => cards.where((card) => card.isDone).toList();
  List<TodayCard> get upcoming =>
      cards.where((card) => card.notStarted).toList();

  /// Sessions the nightly job closed and she has not yet confirmed.
  List<WorkSession> get needingConfirmation => cards
      .map((card) => card.session)
      .whereType<WorkSession>()
      .where((session) =>
          session.needsReview && session.status == SessionStatus.autoClosed,)
      .toList();
}

extension _FirstOrNull<T> on Iterable<T> {
  T? get firstOrNull => isEmpty ? null : first;
}

// ---------------------------------------------------------------------------
// Invoices
// ---------------------------------------------------------------------------

enum InvoiceLineKind {
  time('time', 'Time worked'),
  overtime('overtime', 'Approved extra time'),
  visitFee('visit_fee', 'Visit fee'),
  adjustment('adjustment', 'Adjustment');

  const InvoiceLineKind(this.wire, this.label);

  final String wire;
  final String label;

  static InvoiceLineKind parse(String? value) =>
      InvoiceLineKind.values.firstWhere(
        (kind) => kind.wire == value,
        orElse: () => InvoiceLineKind.adjustment,
      );
}

class InvoiceLine {
  const InvoiceLine({
    required this.id,
    required this.kind,
    required this.description,
    required this.minutes,
    required this.amountPaise,
    required this.amountDisplay,
    required this.isHeld,
    this.sessionId,
  });

  factory InvoiceLine.fromJson(Map<String, dynamic> json) => InvoiceLine(
        id: json['id'] as int,
        kind: InvoiceLineKind.parse(json['kind'] as String?),
        description: (json['description'] as String?) ?? '',
        minutes: (json['minutes'] as int?) ?? 0,
        amountPaise: (json['amount_paise'] as int?) ?? 0,
        amountDisplay: (json['amount_display'] as String?) ?? '',
        isHeld: (json['is_held'] as bool?) ?? false,
        sessionId: json['session'] as String?,
      );

  final int id;
  final InvoiceLineKind kind;
  final String description;
  final int minutes;
  final int amountPaise;
  final String amountDisplay;

  /// Withheld from this bill's payment while a query is open against it. The
  /// rest of the invoice still pays on time.
  final bool isHeld;
  final String? sessionId;
}

/// An open question about one visit on a bill.
///
/// [canAccept] is decided by the server, not here: only the party who did *not*
/// raise it may accept, and a household with two residents would get that wrong
/// if the client tried to work it out from its own user id.
class OpenQuery {
  const OpenQuery({
    required this.id,
    required this.sessionId,
    required this.reason,
    required this.raisedByName,
    required this.canAccept,
    this.description = '',
  });

  factory OpenQuery.fromJson(Map<String, dynamic> json) => OpenQuery(
        id: json['id'] as int,
        sessionId: (json['session'] as String?) ?? '',
        reason: (json['reason'] as String?) ?? '',
        raisedByName: (json['raised_by_name'] as String?) ?? 'Someone',
        canAccept: (json['can_accept'] as bool?) ?? false,
        description: (json['description'] as String?) ?? '',
      );

  final int id;
  final String sessionId;
  final String reason;
  final String raisedByName;
  final bool canAccept;
  final String description;
}

class Invoice {
  const Invoice({
    required this.id,
    required this.number,
    required this.status,
    required this.inReview,
    required this.periodStart,
    required this.periodEnd,
    required this.workerName,
    required this.flat,
    required this.totalPaise,
    required this.payablePaise,
    required this.heldPaise,
    required this.timePaise,
    required this.overtimePaise,
    required this.visitFeePaise,
    required this.adjustmentPaise,
    this.totalDisplay = '',
    this.payableDisplay = '',
    this.reviewClosesAt,
    this.paymentId,
    this.lines = const [],
    this.unbilledExtraMinutes = 0,
    this.daysBilled = 0,
    this.openQueries = const [],
  });

  factory Invoice.fromJson(Map<String, dynamic> json) {
    final days = (json['days'] as Map<String, dynamic>?) ?? const {};
    return Invoice(
      id: json['id'] as int,
      number: (json['number'] as String?) ?? '',
      status: (json['status'] as String?) ?? 'draft',
      inReview: (json['in_review'] as bool?) ?? false,
      periodStart: DateTime.parse(json['period_start'] as String),
      periodEnd: DateTime.parse(json['period_end'] as String),
      workerName: (json['worker_name'] as String?) ?? '',
      flat: (json['flat'] as String?) ?? '',
      totalPaise: (json['total_paise'] as int?) ?? 0,
      payablePaise: (json['payable_paise'] as int?) ?? 0,
      heldPaise: (json['held_paise'] as int?) ?? 0,
      timePaise: (json['time_paise'] as int?) ?? 0,
      overtimePaise: (json['overtime_paise'] as int?) ?? 0,
      visitFeePaise: (json['visit_fee_paise'] as int?) ?? 0,
      adjustmentPaise: (json['adjustment_paise'] as int?) ?? 0,
      totalDisplay: (json['total_display'] as String?) ?? '',
      payableDisplay: (json['payable_display'] as String?) ?? '',
      reviewClosesAt: json['review_closes_at'] == null
          ? null
          : DateTime.parse(json['review_closes_at'] as String).toLocal(),
      paymentId: json['payment'] as String?,
      lines: ((json['lines'] as List?) ?? const [])
          .map((row) => InvoiceLine.fromJson(row as Map<String, dynamic>))
          .toList(),
      unbilledExtraMinutes: (json['unbilled_extra_minutes'] as int?) ?? 0,
      daysBilled: (days['billed'] as int?) ?? 0,
      openQueries: ((json['open_queries'] as List?) ?? const [])
          .map((row) => OpenQuery.fromJson(row as Map<String, dynamic>))
          .toList(),
    );
  }

  final int id;
  final String number;
  final String status;

  /// While true, either party may query a line and nothing has been charged.
  final bool inReview;

  final DateTime periodStart;
  final DateTime periodEnd;
  final String workerName;
  final String flat;

  final int totalPaise;

  /// The total less anything under query. This is what the Pay button charges,
  /// and the gap between the two is what makes raising a query safe.
  final int payablePaise;
  final int heldPaise;

  final int timePaise;
  final int overtimePaise;
  final int visitFeePaise;
  final int adjustmentPaise;

  final String totalDisplay;
  final String payableDisplay;
  final DateTime? reviewClosesAt;
  final String? paymentId;
  final List<InvoiceLine> lines;
  final int unbilledExtraMinutes;
  final int daysBilled;

  /// Questions still open on this bill, from either side.
  final List<OpenQuery> openQueries;

  bool get hasHeldAmount => heldPaise > 0;
  bool get isPayable => paymentId != null && payablePaise > 0;

  List<InvoiceLine> linesOf(InvoiceLineKind kind) =>
      lines.where((line) => line.kind == kind).toList();
}
