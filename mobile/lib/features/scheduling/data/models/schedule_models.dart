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
    this.expectedArrival,
    this.graceMinutes = 0,
    this.taskNotes = '',
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

  /// The resident's expected arrival, which may differ from [startTime] when
  /// Module 6.2 timing has been set.
  final String? expectedArrival;
  final int graceMinutes;
  final String taskNotes;

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
        expectedArrival: json['expected_arrival'] as String?,
        graceMinutes: json['grace_minutes'] as int? ?? 0,
        taskNotes: json['task_notes'] as String? ?? '',
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
