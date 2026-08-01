import 'package:flutter_test/flutter_test.dart';
import 'package:sathify/features/scheduling/data/models/schedule_models.dart';

/// Wire-format tests for Module 6 — Scheduling & Task Management.
///
/// NOT YET EXECUTED — the Flutter SDK is not installed on this machine. Run
/// `flutter test` once it is.
void main() {
  group('ScheduleSource', () {
    test('parses both sources', () {
      expect(ScheduleSource.fromWire('engagement'), ScheduleSource.engagement);
      expect(ScheduleSource.fromWire('booking'), ScheduleSource.booking);
    });

    test('falls back rather than throwing on something new', () {
      expect(ScheduleSource.fromWire('workshop'), ScheduleSource.engagement);
    });
  });

  group('ScheduleItem', () {
    Map<String, dynamic> engagementPayload() => {
          'source': 'engagement',
          'source_id': 12,
          'date': '2026-08-03',
          'start_time': '09:00:00',
          'end_time': '10:30:00',
          'duration_minutes': 90,
          'title': 'Maid',
          'worker_id': 7,
          'worker_name': 'Rahul Sharma',
          'resident_id': 3,
          'resident_name': 'Anita Desai',
          'flat_label': 'A-301',
          'status': 'active',
          'is_confirmed': true,
          'expected_arrival': '08:45:00',
          'grace_minutes': 15,
          'task_notes': 'Start with the kitchen.',
        };

    test('parses a recurring visit', () {
      final item = ScheduleItem.fromJson(engagementPayload());

      expect(item.source, ScheduleSource.engagement);
      expect(item.sourceId, 12);
      expect(item.isRecurring, isTrue);
      expect(item.date, DateTime(2026, 8, 3));
      expect(item.flatLabel, 'A-301');
      expect(item.taskNotes, 'Start with the kitchen.');
    });

    test('renders the time range', () {
      expect(ScheduleItem.fromJson(engagementPayload()).timeRangeLabel, '09:00 – 10:30');
    });

    test('falls back to the start time when no end is given', () {
      final item = ScheduleItem.fromJson({...engagementPayload(), 'end_time': ''});
      expect(item.timeRangeLabel, '09:00');
    });

    test('computes minutes since midnight for timeline layout', () {
      expect(ScheduleItem.fromJson(engagementPayload()).startMinutes, 540);
    });

    test('carries the resident timing override separately from the start time', () {
      /// The engagement starts at 09:00 but the resident expects 08:45.
      final item = ScheduleItem.fromJson(engagementPayload());
      expect(item.startTime, '09:00:00');
      expect(item.expectedArrival, '08:45:00');
      expect(item.graceMinutes, 15);
    });

    test('a confirmed engagement never needs a response', () {
      expect(ScheduleItem.fromJson(engagementPayload()).needsResponse, isFalse);
    });

    test('an unconfirmed booking needs a response', () {
      final item = ScheduleItem.fromJson({
        ...engagementPayload(),
        'source': 'booking',
        'is_confirmed': false,
      });

      expect(item.isRecurring, isFalse);
      expect(item.needsResponse, isTrue);
    });

    test('a confirmed booking does not need a response', () {
      final item = ScheduleItem.fromJson({
        ...engagementPayload(),
        'source': 'booking',
        'is_confirmed': true,
      });
      expect(item.needsResponse, isFalse);
    });
  });

  group('TaskTiming', () {
    Map<String, dynamic> payload() => {
          'expected_arrival': '09:00:00',
          'arrival_grace_minutes': 15,
          'expected_departure': '10:30:00',
          'departure_grace_minutes': 10,
          'task_notes': 'Kitchen first.',
          'reminders_enabled': true,
          'reminder_lead_minutes': 60,
          'is_customised': true,
        };

    test('parses the expectations in force', () {
      final timing = TaskTiming.fromJson(payload());

      expect(timing.arrivalLabel, '09:00');
      expect(timing.departureLabel, '10:30');
      expect(timing.windowLabel, '09:00 – 10:30');
      expect(timing.isCustomised, isTrue);
    });

    test('distinguishes engagement defaults from a resident choice', () {
      final timing = TaskTiming.fromJson({...payload(), 'is_customised': false});
      expect(timing.isCustomised, isFalse);
    });

    test('shows the grace window alongside the arrival time', () {
      expect(
        TaskTiming.fromJson(payload()).arrivalWithGraceLabel,
        '09:00 (15 min grace)',
      );
    });

    test('omits the grace note when arrival is exact', () {
      final timing = TaskTiming.fromJson({...payload(), 'arrival_grace_minutes': 0});
      expect(timing.arrivalWithGraceLabel, '09:00');
    });

    test('round-trips through the wire format', () {
      final parsed = TaskTiming.fromJson(TaskTiming.fromJson(payload()).toJson());

      expect(parsed.expectedArrival, '09:00:00');
      expect(parsed.arrivalGraceMinutes, 15);
      expect(parsed.reminderLeadMinutes, 60);
    });
  });

  group('ConflictReport', () {
    test('parses a clean check', () {
      final report = ConflictReport.fromJson({
        'has_conflict': false,
        'summary': 'No conflicts.',
        'clashes': const [],
      });

      expect(report.hasConflict, isFalse);
      expect(report.clashes, isEmpty);
    });

    test('carries the colliding items so a clash can be resolved', () {
      final report = ConflictReport.fromJson({
        'has_conflict': true,
        'summary': 'Already committed: Maid at 09:00–10:30 (A-301)',
        'clashes': [
          {
            'source': 'engagement',
            'source_id': 12,
            'date': '2026-08-03',
            'start_time': '09:00:00',
            'end_time': '10:30:00',
            'duration_minutes': 90,
            'title': 'Maid',
          },
        ],
      });

      expect(report.hasConflict, isTrue);
      expect(report.clashes.single.sourceId, 12);
      expect(report.summary, contains('Already committed'));
    });
  });

  group('AgendaRange', () {
    test('today is a single day', () {
      final range = AgendaRange.today();
      expect(range.from, range.to);
    });

    test('a week spans seven days inclusive', () {
      final range = AgendaRange.week();
      expect(range.to.difference(range.from).inDays, 6);
    });

    test('serialises to from/to query parameters', () {
      final range = AgendaRange(
        from: DateTime(2026, 8, 3),
        to: DateTime(2026, 8, 9),
      );

      expect(range.toQuery(), {'from': '2026-08-03', 'to': '2026-08-09'});
    });

    test('compares by value so the provider refetches only on real changes', () {
      final a = AgendaRange(from: DateTime(2026, 8, 3), to: DateTime(2026, 8, 9));
      final b = AgendaRange(from: DateTime(2026, 8, 3), to: DateTime(2026, 8, 9));
      final c = AgendaRange(from: DateTime(2026, 8, 4), to: DateTime(2026, 8, 9));

      expect(a, equals(b));
      expect(a.hashCode, b.hashCode);
      expect(a, isNot(equals(c)));
    });
  });

  group('Reminder', () {
    test('parses a queued reminder', () {
      final reminder = Reminder.fromJson({
        'id': 5,
        'kind': 'upcoming_engagement',
        'kind_display': 'Recurring visit due',
        'title': 'Maid at A-301',
        'body': 'You are expected at A-301 at 09:00 today.',
        'event_at': '2026-08-03T09:00:00+05:30',
        'status': 'scheduled',
      });

      expect(reminder.id, 5);
      expect(reminder.kindDisplay, 'Recurring visit due');
      expect(reminder.status, 'scheduled');
    });
  });
}
