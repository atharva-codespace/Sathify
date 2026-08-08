import 'package:flutter_test/flutter_test.dart';
import 'package:sathify/features/hiring/data/models/hiring_models.dart';

/// Module 4.6 — the ten-day notice rule, client side.
///
/// The server is the authority and refuses anything shorter with
/// `notice_too_short`. These tests pin the client's mirror of that rule, which
/// exists so a date picker cannot offer a day the server is going to reject.
void main() {
  group('NoticePeriod', () {
    test('mirrors the server constant', () {
      // NOTICE_PERIOD_DAYS in apps/hiring/models.py. If one moves, both must.
      expect(NoticePeriod.days, 10);
    });

    test('the earliest last day is ten days out', () {
      final today = DateTime(2026, 8, 3);
      expect(NoticePeriod.earliestLastDay(today), DateTime(2026, 8, 13));
    });

    test('it crosses a month boundary correctly', () {
      // Date arithmetic via DateTime(y, m, d + n) rather than Duration, so
      // month lengths are the calendar's problem and not ours.
      expect(
        NoticePeriod.earliestLastDay(DateTime(2026, 8, 25)),
        DateTime(2026, 9, 4),
      );
    });

    test('it crosses a leap day correctly', () {
      expect(
        NoticePeriod.earliestLastDay(DateTime(2028, 2, 25)),
        DateTime(2028, 3, 6),
      );
    });

    test('a day inside the window is refused', () {
      final today = DateTime(2026, 8, 3);
      expect(
        NoticePeriod.isPermitted(DateTime(2026, 8, 12), today: today),
        isFalse,
      );
    });

    test('the tenth day itself is permitted', () {
      // Inclusive. Off by one here is a day of somebody's wage.
      final today = DateTime(2026, 8, 3);
      expect(
        NoticePeriod.isPermitted(DateTime(2026, 8, 13), today: today),
        isTrue,
      );
    });

    test('a longer notice is permitted', () {
      final today = DateTime(2026, 8, 3);
      expect(
        NoticePeriod.isPermitted(DateTime(2026, 9, 30), today: today),
        isTrue,
      );
    });

    test('the summary counts visits, not days', () {
      // Ten days of notice on a Tuesday-only engagement is one more visit.
      expect(NoticePeriod.summary(visitsRemaining: 1), contains('One more'));
      expect(NoticePeriod.summary(visitsRemaining: 8), contains('8 more'));
      // Whatever the count, it says the days are paid — that is the promise.
      expect(NoticePeriod.summary(visitsRemaining: 8), contains('paid'));
    });
  });

  group('Engagement notice fields', () {
    Map<String, dynamic> payload({Map<String, dynamic> overrides = const {}}) => {
          'id': 5,
          'status': 'active',
          'days_of_week': [0, 2, 4],
          'start_time': '09:00:00',
          'expected_duration_minutes': 90,
          'monthly_rate': 4000,
          'worker': 11,
          'worker_name': 'Priya Sharma',
          'resident_name': 'Anita Desai',
          'resident_flat': 'A-301',
          ...overrides,
        };

    test('an engagement without notice parses clean', () {
      final engagement = Engagement.fromJson(payload());

      expect(engagement.isServingNotice, isFalse);
      expect(engagement.lastWorkingDay, isNull);
      expect(engagement.visitsRemaining, 0);
    });

    test('an engagement serving notice is still active', () {
      // The load-bearing one. Anything that reads isActive to decide whether
      // work is happening must keep saying yes while notice runs — otherwise
      // the worker's remaining visits disappear from the app.
      final engagement = Engagement.fromJson(
        payload(
          overrides: {
            'last_working_day': '2026-08-13',
            'is_serving_notice': true,
            'notice_days_remaining': 10,
            'visits_remaining': 4,
          },
        ),
      );

      expect(engagement.isActive, isTrue);
      expect(engagement.isLive, isTrue);
      expect(engagement.isServingNotice, isTrue);
      expect(engagement.lastWorkingDay, DateTime(2026, 8, 13));
      expect(engagement.visitsRemaining, 4);
    });

    test('visits still occur on working days while notice runs', () {
      final engagement = Engagement.fromJson(
        payload(
          overrides: {
            'last_working_day': '2026-08-13',
            'is_serving_notice': true,
          },
        ),
      );

      // 3 Aug 2026 is a Monday, which this engagement calls for.
      expect(engagement.occursOn(DateTime(2026, 8, 3)), isTrue);
    });
  });
}
