import 'package:flutter_test/flutter_test.dart';
import 'package:sathify/features/scheduling/data/models/schedule_models.dart';

/// Module 6.5 — urgent leave, client side.
///
/// The parsing tests matter more than they look: a schedule item that loses its
/// leave flags renders as an ordinary visit, and a household is then told
/// somebody is coming who is not.
void main() {
  group('LeaveStatus', () {
    test('there is no pending state to parse', () {
      // Leave is approved the instant it is asked for. If a "pending" value ever
      // appears on the wire, something has gone wrong in the workflow, not here.
      expect(
        LeaveStatus.values.map((s) => s.wireValue),
        isNot(contains('pending')),
      );
    });

    test('only an approved request is waiting on the household', () {
      expect(LeaveStatus.approved.awaitsHousehold, isTrue);
      expect(LeaveStatus.waived.awaitsHousehold, isFalse);
      expect(LeaveStatus.replacementConfirmed.awaitsHousehold, isFalse);
    });

    test('an unrecognised status degrades rather than throwing', () {
      // A newer server adding a status must not crash an older app.
      expect(LeaveStatus.fromWire('something_new'), LeaveStatus.unknown);
      expect(LeaveStatus.fromWire(null), LeaveStatus.unknown);
    });
  });

  group('LeaveRequest', () {
    Map<String, dynamic> payload({Map<String, dynamic> overrides = const {}}) => {
          'id': 7,
          'engagement': 3,
          'leave_date': '2026-04-14',
          'status': 'replacement_confirmed',
          'reason': 'Child unwell',
          'worker': 11,
          'worker_name': 'Priya Sharma',
          'resident_name': 'Anita Desai',
          'flat_label': 'A-301',
          'start_time': '09:00:00',
          'replacement': 12,
          'replacement_name': 'Sunita Rao',
          'summary': 'Sunita Rao is covering this visit.',
          'day_rate_paise': 23077,
          'forgone_paise': 23077,
          'replacement_paise': 23077,
          'replacement_display': '₹230.77',
          'needs_resident_response': false,
          'can_withdraw': false,
          'is_covered': true,
          'is_settled': true,
          ...overrides,
        };

    test('parses a confirmed replacement', () {
      final leave = LeaveRequest.fromJson(payload());

      expect(leave.id, 7);
      expect(leave.status, LeaveStatus.replacementConfirmed);
      expect(leave.replacementId, 12);
      expect(leave.replacementName, 'Sunita Rao');
      expect(leave.isCovered, isTrue);
      expect(leave.leaveDate, DateTime(2026, 4, 14));
    });

    test('money arrives in paise with a formatted copy', () {
      // The app must never do currency arithmetic; it prints what it is given.
      final leave = LeaveRequest.fromJson(payload());

      expect(leave.replacementPaise, 23077);
      expect(leave.replacementDisplay, '₹230.77');
    });

    test('a missing reason is empty, not null', () {
      final leave = LeaveRequest.fromJson(payload(overrides: {'reason': null}));
      expect(leave.reason, '');
    });

    test('an unanswered request knows it is waiting', () {
      final leave = LeaveRequest.fromJson(
        payload(overrides: {
          'status': 'approved',
          'replacement': null,
          'needs_resident_response': true,
          'is_covered': false,
        },),
      );

      expect(leave.needsResidentResponse, isTrue);
      expect(leave.replacementId, isNull);
      expect(leave.isCovered, isFalse);
    });

    test('the start time is formatted for display', () {
      expect(LeaveRequest.fromJson(payload()).startTimeLabel, '09:00');
    });
  });

  group('ScheduleItem leave flags', () {
    Map<String, dynamic> item({Map<String, dynamic> overrides = const {}}) => {
          'source': 'engagement',
          'source_id': 3,
          'date': '2026-04-14',
          'start_time': '09:00:00',
          'duration_minutes': 90,
          'worker_id': 11,
          'worker_name': 'Priya Sharma',
          ...overrides,
        };

    test('an ordinary visit carries no leave marks', () {
      final parsed = ScheduleItem.fromJson(item());

      expect(parsed.onLeave, isFalse);
      expect(parsed.isCover, isFalse);
      expect(parsed.leaveRequestId, 0);
      expect(parsed.isUncovered, isFalse);
    });

    test('leave with nobody covering reads as uncovered', () {
      final parsed = ScheduleItem.fromJson(
        item(overrides: {
          'on_leave': true,
          'leave_status': 'replacement_requested',
          'leave_request_id': 7,
        },),
      );

      expect(parsed.onLeave, isTrue);
      expect(parsed.isUncovered, isTrue);
      expect(parsed.leaveRequestId, 7);
    });

    test('leave with cover arranged is not uncovered', () {
      final parsed = ScheduleItem.fromJson(
        item(overrides: {
          'on_leave': true,
          'leave_status': 'replacement_confirmed',
          'cover_worker_name': 'Sunita Rao',
        },),
      );

      expect(parsed.isUncovered, isFalse);
      expect(parsed.coverWorkerName, 'Sunita Rao');
    });

    test('a cover visit on the replacement own schedule', () {
      final parsed = ScheduleItem.fromJson(
        item(overrides: {
          'worker_id': 12,
          'worker_name': 'Sunita Rao',
          'is_cover': true,
          'covering_for_name': 'Priya Sharma',
          'leave_request_id': 7,
        },),
      );

      expect(parsed.isCover, isTrue);
      expect(parsed.coveringForName, 'Priya Sharma');
      // The gate matches an arrival against the engagement being served, so the
      // source id stays the engagement's even on somebody else's calendar.
      expect(parsed.sourceId, 3);
    });
  });

  group('ReplacementCandidate', () {
    test('parses a ranked candidate', () {
      final candidate = ReplacementCandidate.fromJson({
        'worker_id': 12,
        'name': 'Sunita Rao',
        'photo_url': 'https://example.test/p.jpg',
        'trust_score': 82.5,
        'average_rating': 4.6,
        'rating_count': 9,
        'match_score': 0.87,
        'match_percentage': 87,
      });

      expect(candidate.workerId, 12);
      expect(candidate.matchPercentage, 87);
      expect(candidate.averageRating, closeTo(4.6, 0.001));
    });

    test('a candidate with no history parses at zero rather than failing', () {
      final candidate = ReplacementCandidate.fromJson({
        'worker_id': 13,
        'name': 'New Worker',
      });

      expect(candidate.ratingCount, 0);
      expect(candidate.matchScore, 0);
      expect(candidate.photoUrl, '');
    });
  });
}
