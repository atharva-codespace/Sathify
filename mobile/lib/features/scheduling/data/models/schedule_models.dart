/// Data models for Module 6 — Scheduling & Task Management.
///
/// -----------------------------------------------------------------------
/// A SCHEDULE ITEM IS NOT A RECORD
/// -----------------------------------------------------------------------
/// The server derives the schedule on read by merging recurring engagements
/// with one-day bookings; there is no calendar table. So a [ScheduleItem] has
/// no id of its own — it carries [source] and [sourceId], which is how the app
/// navigates back to whichever engagement or booking produced it. Never cache
/// one as though it were a row: pausing an engagement makes it vanish, which is
/// the point.
library;

import '../../../bookings/data/models/booking_models.dart' show formatWireDate;

/// Formats an `HH:MM:SS` wire time as `HH:MM`.
String formatScheduleTime(String? value) {
  if (value == null || value.isEmpty) return '';
  final parts = value.split(':');
  return parts.length >= 2 ? '${parts[0]}:${parts[1]}' : value;
}

/// Where a scheduled visit came from.
enum ScheduleSource {
  engagement('engagement', 'Regular'),
  booking('booking', 'One-day');

  const ScheduleSource(this.wireValue, this.label);

  final String wireValue;
  final String label;

  static ScheduleSource fromWire(String? value) =>
      ScheduleSource.values.firstWhere(
        (s) => s.wireValue == value,
        orElse: () => ScheduleSource.engagement,
      );
}

/// One expected visit (Module 6.1).
class ScheduleItem {
  const ScheduleItem({
    required this.source,
    required this.sourceId,
    required this.date,
    required this.startTime,
    required this.durationMinutes,
    this.endTime = '',
    this.title = '',
    this.workerId = 0,
    this.workerName = '',
    this.residentId = 0,
    this.residentName = '',
    this.flatLabel = '',
    this.status = '',
    this.isConfirmed = true,
    this.canRespond = false,
    this.expectedArrival,
    this.graceMinutes = 0,
    this.taskNotes = '',
    this.onLeave = false,
    this.leaveStatus = '',
    this.leaveRequestId = 0,
    this.coverWorkerName = '',
    this.isCover = false,
    this.coveringForName = '',
    this.visitStatus = 'pending',
    this.completedAt,
    this.completionNote = '',
    this.canMarkDone = false,
    this.settlement = 'app',
  });

  final ScheduleSource source;

  /// The engagement or booking id, depending on [source].
  final int sourceId;

  final DateTime date;
  final String startTime;
  final String endTime;
  final int durationMinutes;

  final String title;
  final int workerId;
  final String workerName;
  final int residentId;
  final String residentName;
  final String flatLabel;
  final String status;

  /// Bookings need the worker's confirmation; engagements are already agreed.
  final bool isConfirmed;

  /// Whether the worker may accept or decline this visit right now.
  ///
  /// Not the same question as [needsResponse], and conflating them is what left
  /// a maid staring at "Awaiting your confirmation" with no way to confirm: a
  /// request whose answering deadline has passed still *needs* an answer it can
  /// no longer be given. The server owns the deadline (`Booking.is_actionable`)
  /// and this is its answer.
  final bool canRespond;

  /// The resident's expected arrival, which may differ from [startTime] when
  /// Module 6.2 timing has been set.
  final String? expectedArrival;
  final int graceMinutes;
  final String taskNotes;

  // --- 6.5 urgent leave ------------------------------------------------------
  //
  // A visit the regular worker is away for stays on the schedule rather than
  // vanishing from it. Everyone involved needs to see that the slot exists and
  // who — if anyone — is filling it.

  /// The regular worker has taken this day off.
  final bool onLeave;
  final String leaveStatus;
  final int leaveRequestId;

  /// Who is covering, on the regular worker's and the household's views.
  final String coverWorkerName;

  /// True on the *replacement's* own schedule: somebody else's visit that they
  /// agreed to take for one day.
  final bool isCover;
  final String coveringForName;

  // --- 6.6 how far through the day's work this visit is ----------------------

  /// `pending`, `in_progress` or `complete`, composed server-side from the gate
  /// log and the completion mark. Never derived locally — the gate is the
  /// authority on arrival and a second opinion here would disagree with it.
  final String visitStatus;
  final DateTime? completedAt;
  final String completionNote;

  /// Whether this worker may mark this visit done right now.
  ///
  /// -----------------------------------------------------------------------
  /// THE SERVER DECIDES THIS. THE APP USED TO, AND THAT WAS THE BUG.
  /// -----------------------------------------------------------------------
  /// The card previously worked out for itself whether to draw "Mark as done" —
  /// from the visit date, the leave flags and whether a booking was confirmed —
  /// while the server applied a different rule when the request arrived. Two
  /// rules that were never going to stay in step, and they did not: the button
  /// appeared on visits the server would refuse and was hidden on visits it
  /// would have accepted, which is how a maid ended up with no way to close out
  /// an emergency job at all.
  ///
  /// It is now one rule, computed where the refusal is also decided
  /// (`Booking.can_be_completed`), and this field is that answer. Do not
  /// re-derive it here — that is precisely the mistake being undone.
  final bool canMarkDone;

  /// `app` or `cash`. An emergency job's fee is paid hand to hand, so the card
  /// must not tell her the household is about to be asked for money.
  final String settlement;

  /// Paid directly in cash, outside the app.
  bool get isCashSettled => settlement == 'cash';

  /// The worker has marked the day's work done.
  bool get isComplete => visitStatus == 'complete' || completedAt != null;

  /// Somebody has arrived but not yet finished.
  bool get isInProgress => visitStatus == 'in_progress';

  /// Nobody is coming: leave was taken and no cover was arranged.
  bool get isUncovered => onLeave && coverWorkerName.isEmpty;

  bool get isRecurring => source == ScheduleSource.engagement;

  String get startTimeLabel => formatScheduleTime(startTime);

  /// What should actually be shown to the user: the resident's expected
  /// arrival where Module 6.2 timing has been set, falling back to the
  /// engagement's own start time otherwise. [startTime] itself never changes
  /// when a resident edits the timing sheet — it stays the raw engagement
  /// slot, used for sorting and conflict checks — so any card that shows a
  /// clock time to a person should read this instead of [startTimeLabel].
  String get displayTimeLabel =>
      formatScheduleTime(expectedArrival ?? startTime);

  String get timeRangeLabel {
    final end = formatScheduleTime(endTime);
    return end.isEmpty ? startTimeLabel : '$startTimeLabel – $end';
  }

  /// Minutes since midnight, for laying items out on a timeline.
  int get startMinutes {
    final parts = startTime.split(':');
    if (parts.length < 2) return 0;
    return (int.tryParse(parts[0]) ?? 0) * 60 + (int.tryParse(parts[1]) ?? 0);
  }

  /// A booking still awaiting the worker's answer — needs action, not just
  /// attendance.
  bool get needsResponse => !isRecurring && !isConfirmed;

  /// Awaiting an answer that can no longer be given: the deadline has passed.
  ///
  /// Worth its own name because the card must say something different here. A
  /// row that keeps insisting "Awaiting your confirmation" with nothing to tap
  /// is the exact complaint this fixes.
  bool get responseLapsed => needsResponse && !canRespond;

  factory ScheduleItem.fromJson(Map<String, dynamic> json) => ScheduleItem(
        source: ScheduleSource.fromWire(json['source'] as String?),
        sourceId: json['source_id'] as int? ?? 0,
        date: DateTime.parse(json['date'] as String),
        startTime: json['start_time'] as String? ?? '',
        endTime: json['end_time'] as String? ?? '',
        durationMinutes: json['duration_minutes'] as int? ?? 0,
        title: json['title'] as String? ?? '',
        workerId: json['worker_id'] as int? ?? 0,
        workerName: json['worker_name'] as String? ?? '',
        residentId: json['resident_id'] as int? ?? 0,
        residentName: json['resident_name'] as String? ?? '',
        flatLabel: json['flat_label'] as String? ?? '',
        status: json['status'] as String? ?? '',
        isConfirmed: json['is_confirmed'] as bool? ?? true,
        canRespond: json['can_respond'] as bool? ?? false,
        expectedArrival: json['expected_arrival'] as String?,
        graceMinutes: json['grace_minutes'] as int? ?? 0,
        taskNotes: json['task_notes'] as String? ?? '',
        onLeave: json['on_leave'] as bool? ?? false,
        leaveStatus: json['leave_status'] as String? ?? '',
        leaveRequestId: json['leave_request_id'] as int? ?? 0,
        coverWorkerName: json['cover_worker_name'] as String? ?? '',
        isCover: json['is_cover'] as bool? ?? false,
        coveringForName: json['covering_for_name'] as String? ?? '',
        visitStatus: json['visit_status'] as String? ?? 'pending',
        completedAt: DateTime.tryParse(json['completed_at'] as String? ?? ''),
        completionNote: json['completion_note'] as String? ?? '',
        canMarkDone: json['can_mark_done'] as bool? ?? false,
        settlement: json['settlement'] as String? ?? 'app',
      );
}

/// Where a leave request has got to (Module 6.5).
///
/// There is deliberately no "pending": leave is approved the instant it is
/// asked for. The household is never asked whether to allow it, only whether
/// they need somebody else that day.
enum LeaveStatus {
  approved('approved', 'Approved'),
  waived('waived', 'No cover needed'),
  replacementRequested('replacement_requested', 'Finding cover'),
  replacementConfirmed('replacement_confirmed', 'Cover arranged'),
  unfilled('unfilled', 'No cover found'),
  withdrawn('withdrawn', 'Withdrawn'),
  unknown('', '');

  const LeaveStatus(this.wireValue, this.label);

  final String wireValue;
  final String label;

  static LeaveStatus fromWire(String? value) => LeaveStatus.values.firstWhere(
        (s) => s.wireValue == value,
        orElse: () => LeaveStatus.unknown,
      );

  /// Waiting on the household to say whether they need somebody.
  bool get awaitsHousehold => this == LeaveStatus.approved;
}

/// Module 6.5 — one day of urgent leave ("chutti").
class LeaveRequest {
  const LeaveRequest({
    required this.id,
    required this.engagementId,
    required this.leaveDate,
    required this.status,
    this.reason = '',
    this.workerId = 0,
    this.workerName = '',
    this.residentName = '',
    this.flatLabel = '',
    this.startTime = '',
    this.replacementId,
    this.replacementName = '',
    this.summary = '',
    this.dayRatePaise = 0,
    this.forgonePaise = 0,
    this.replacementPaise = 0,
    this.replacementDisplay = '',
    this.needsResidentResponse = false,
    this.canWithdraw = false,
    this.isCovered = false,
    this.isSettled = false,
  });

  final int id;
  final int engagementId;
  final DateTime leaveDate;
  final LeaveStatus status;

  /// Always optional. A worker should not have to describe a private emergency
  /// in order to be believed.
  final String reason;

  final int workerId;
  final String workerName;
  final String residentName;
  final String flatLabel;
  final String startTime;

  final int? replacementId;
  final String replacementName;

  /// The server's one-line account of where this has got to. Preferred over
  /// re-deriving a sentence from [status] on the client, so both sides say the
  /// same thing.
  final String summary;

  /// Money, in paise, like everywhere else on the platform. [replacementDisplay]
  /// is the formatted copy — the app should never do currency arithmetic.
  final int dayRatePaise;
  final int forgonePaise;
  final int replacementPaise;
  final String replacementDisplay;

  final bool needsResidentResponse;
  final bool canWithdraw;
  final bool isCovered;
  final bool isSettled;

  String get startTimeLabel => formatScheduleTime(startTime);

  factory LeaveRequest.fromJson(Map<String, dynamic> json) => LeaveRequest(
        id: json['id'] as int,
        engagementId: json['engagement'] as int? ?? 0,
        leaveDate: DateTime.parse(json['leave_date'] as String),
        status: LeaveStatus.fromWire(json['status'] as String?),
        reason: json['reason'] as String? ?? '',
        workerId: json['worker'] as int? ?? 0,
        workerName: json['worker_name'] as String? ?? '',
        residentName: json['resident_name'] as String? ?? '',
        flatLabel: json['flat_label'] as String? ?? '',
        startTime: json['start_time'] as String? ?? '',
        replacementId: json['replacement'] as int?,
        replacementName: json['replacement_name'] as String? ?? '',
        summary: json['summary'] as String? ?? '',
        dayRatePaise: json['day_rate_paise'] as int? ?? 0,
        forgonePaise: json['forgone_paise'] as int? ?? 0,
        replacementPaise: json['replacement_paise'] as int? ?? 0,
        replacementDisplay: json['replacement_display'] as String? ?? '',
        needsResidentResponse: json['needs_resident_response'] as bool? ?? false,
        canWithdraw: json['can_withdraw'] as bool? ?? false,
        isCovered: json['is_covered'] as bool? ?? false,
        isSettled: json['is_settled'] as bool? ?? false,
      );
}

/// A worker who could cover a visit, with the score that put them there.
///
/// The breakdown travels with the suggestion because Module 4.3 established
/// that a ranking a resident cannot account for is a ranking they will not
/// trust — and this one is being read in a hurry.
class ReplacementCandidate {
  const ReplacementCandidate({
    required this.workerId,
    required this.name,
    this.photoUrl = '',
    this.trustScore = 0,
    this.averageRating = 0,
    this.ratingCount = 0,
    this.matchScore = 0,
    this.matchPercentage = 0,
  });

  final int workerId;
  final String name;
  final String photoUrl;
  final double trustScore;
  final double averageRating;
  final int ratingCount;
  final double matchScore;
  final int matchPercentage;

  factory ReplacementCandidate.fromJson(Map<String, dynamic> json) =>
      ReplacementCandidate(
        workerId: json['worker_id'] as int? ?? 0,
        name: json['name'] as String? ?? '',
        photoUrl: json['photo_url'] as String? ?? '',
        trustScore: (json['trust_score'] as num?)?.toDouble() ?? 0,
        averageRating: (json['average_rating'] as num?)?.toDouble() ?? 0,
        ratingCount: json['rating_count'] as int? ?? 0,
        matchScore: (json['match_score'] as num?)?.toDouble() ?? 0,
        matchPercentage: json['match_percentage'] as int? ?? 0,
      );
}

/// Module 6.2 — the arrival and departure expectations in force.
///
/// The server always answers, whether or not the resident customised anything;
/// [isCustomised] distinguishes "the resident chose this" from "these are the
/// engagement's own times". That keeps the fallback logic on the server rather
/// than duplicated here.
class TaskTiming {
  const TaskTiming({
    required this.expectedArrival,
    required this.expectedDeparture,
    this.arrivalGraceMinutes = 0,
    this.departureGraceMinutes = 0,
    this.taskNotes = '',
    this.remindersEnabled = true,
    this.reminderLeadMinutes = 60,
    this.isCustomised = false,
  });

  final String expectedArrival;
  final String expectedDeparture;
  final int arrivalGraceMinutes;
  final int departureGraceMinutes;
  final String taskNotes;
  final bool remindersEnabled;
  final int reminderLeadMinutes;

  /// False means these are the engagement's own times, not a resident's choice.
  final bool isCustomised;

  String get arrivalLabel => formatScheduleTime(expectedArrival);
  String get departureLabel => formatScheduleTime(expectedDeparture);
  String get windowLabel => '$arrivalLabel – $departureLabel';

  /// "09:00 (15 min grace)" — what the worker is actually held to.
  String get arrivalWithGraceLabel => arrivalGraceMinutes > 0
      ? '$arrivalLabel ($arrivalGraceMinutes min grace)'
      : arrivalLabel;

  Map<String, dynamic> toJson() => {
        'expected_arrival': expectedArrival,
        'arrival_grace_minutes': arrivalGraceMinutes,
        'expected_departure': expectedDeparture,
        'departure_grace_minutes': departureGraceMinutes,
        'task_notes': taskNotes,
        'reminders_enabled': remindersEnabled,
        'reminder_lead_minutes': reminderLeadMinutes,
      };

  factory TaskTiming.fromJson(Map<String, dynamic> json) => TaskTiming(
        expectedArrival: json['expected_arrival'] as String? ?? '',
        expectedDeparture: json['expected_departure'] as String? ?? '',
        arrivalGraceMinutes: json['arrival_grace_minutes'] as int? ?? 0,
        departureGraceMinutes: json['departure_grace_minutes'] as int? ?? 0,
        taskNotes: json['task_notes'] as String? ?? '',
        remindersEnabled: json['reminders_enabled'] as bool? ?? true,
        reminderLeadMinutes: json['reminder_lead_minutes'] as int? ?? 60,
        isCustomised: json['is_customised'] as bool? ?? false,
      );
}

/// Module 6.3 — what a proposed visit would collide with.
class ConflictReport {
  const ConflictReport({
    required this.hasConflict,
    this.summary = '',
    this.clashes = const [],
  });

  final bool hasConflict;

  /// The server's plain-language summary, shown verbatim.
  final String summary;

  /// The colliding items. Carried so a conflict can be resolved, not just
  /// refused — modspec 6.3 allows flagging for manual resolution.
  final List<ScheduleItem> clashes;

  factory ConflictReport.fromJson(Map<String, dynamic> json) => ConflictReport(
        hasConflict: json['has_conflict'] as bool? ?? false,
        summary: json['summary'] as String? ?? '',
        clashes: ((json['clashes'] as List?) ?? const [])
            .map((row) => ScheduleItem.fromJson(row as Map<String, dynamic>))
            .toList(),
      );
}

/// A date range for an agenda query.
class AgendaRange {
  const AgendaRange({required this.from, required this.to});

  /// Today only — what the app opens on.
  factory AgendaRange.today() {
    final now = DateTime.now();
    final today = DateTime(now.year, now.month, now.day);
    return AgendaRange(from: today, to: today);
  }

  /// The coming week, the default agenda view.
  factory AgendaRange.week() {
    final now = DateTime.now();
    final today = DateTime(now.year, now.month, now.day);
    return AgendaRange(from: today, to: today.add(const Duration(days: 6)));
  }

  final DateTime from;
  final DateTime to;

  Map<String, dynamic> toQuery() => {
        'from': formatWireDate(from),
        'to': formatWireDate(to),
      };

  @override
  bool operator ==(Object other) =>
      other is AgendaRange && other.from == from && other.to == to;

  @override
  int get hashCode => Object.hash(from, to);
}

/// Module 6.4 — a queued notification job.
class Reminder {
  const Reminder({
    required this.id,
    required this.title,
    required this.body,
    required this.eventAt,
    this.kind = '',
    this.kindDisplay = '',
    this.status = 'scheduled',
  });

  final int id;
  final String kind;
  final String kindDisplay;
  final String title;
  final String body;
  final DateTime eventAt;
  final String status;

  factory Reminder.fromJson(Map<String, dynamic> json) => Reminder(
        id: json['id'] as int,
        kind: json['kind'] as String? ?? '',
        kindDisplay: json['kind_display'] as String? ?? '',
        title: json['title'] as String? ?? '',
        body: json['body'] as String? ?? '',
        eventAt: DateTime.parse(json['event_at'] as String),
        status: json['status'] as String? ?? 'scheduled',
      );
}
