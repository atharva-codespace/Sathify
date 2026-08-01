import 'package:flutter_test/flutter_test.dart';
import 'package:sathify/features/attendance/data/models/attendance_models.dart';

/// Wire-format tests for Module 7 — Attendance & Gate Verification.
///
/// NOT YET EXECUTED — the Flutter SDK is not installed on this machine. Run
/// `flutter test` once it is.
///
/// The draft round-trip and [SyncResult.settledIds] groups matter most: they are
/// what the offline queue depends on. An event that loses its id in the queue,
/// or a settled id the queue fails to clear, costs someone a day's attendance.
void main() {
  group('GateDecision', () {
    test('parses each decision', () {
      expect(GateDecision.fromWire('allowed'), GateDecision.allowed);
      expect(GateDecision.fromWire('denied'), GateDecision.denied);
      expect(
        GateDecision.fromWire('pending_review'),
        GateDecision.pendingReview,
      );
    });

    test('pending review is distinct from denied', () {
      /// The whole point of Module 7.3: a failed face check is not a refusal.
      expect(GateDecision.pendingReview, isNot(GateDecision.denied));
      expect(GateDecision.pendingReview.label, isNot(contains('Refused')));
    });

    test('falls back rather than throwing', () {
      expect(GateDecision.fromWire('quarantined'), GateDecision.allowed);
    });
  });

  group('AttendanceEventDraft', () {
    AttendanceEventDraft draft() => AttendanceEventDraft(
          id: 'a3f1c2d4-0000-4000-8000-000000000001',
          workerId: 7,
          occurredAt: DateTime.utc(2026, 8, 3, 9, 15),
          direction: GateDirection.entry,
          method: VerificationMethod.qr,
          decision: GateDecision.allowed,
          deviceId: 'guard-tablet-01',
        );

    test('serialises the client-generated id', () {
      /// Without this the sync endpoint cannot be idempotent.
      expect(draft().toJson()['id'], 'a3f1c2d4-0000-4000-8000-000000000001');
    });

    test('round-trips through the queue without losing anything', () {
      final original = draft();
      final restored = AttendanceEventDraft.decode(original.encode());

      expect(restored.id, original.id);
      expect(restored.workerId, original.workerId);
      expect(restored.occurredAt, original.occurredAt);
      expect(restored.direction, original.direction);
      expect(restored.method, original.method);
      expect(restored.decision, original.decision);
      expect(restored.deviceId, original.deviceId);
    });

    test('sends occurred_at, never leaving the time to the server', () {
      /// Otherwise a batch synced at 6pm looks like everyone arriving at 6pm.
      final json = draft().toJson();
      expect(json['occurred_at'], '2026-08-03T09:15:00.000Z');
    });

    test('omits an empty reason rather than sending a blank string', () {
      expect(draft().toJson().containsKey('decision_reason'), isFalse);
    });

    test('includes a reason when one was given', () {
      final refused = AttendanceEventDraft(
        id: 'x',
        workerId: 7,
        occurredAt: DateTime.utc(2026, 8, 3),
        decision: GateDecision.denied,
        decisionReason: 'Pass was cancelled.',
      );
      expect(refused.toJson()['decision_reason'], 'Pass was cancelled.');
    });

    test('copyWith flags an event as offline without altering its id', () {
      final original = draft();
      final queued = original.copyWith(wasOffline: true);

      expect(queued.id, original.id);
      expect(queued.wasOffline, isTrue);
      expect(original.wasOffline, isFalse);
    });
  });

  group('SyncResult', () {
    test('treats duplicates as accepted, not rejected', () {
      /// A duplicate is the expected outcome of a device that retried after
      /// losing its connection. Retrying it forever would stall the queue.
      final result = SyncResult.fromJson({
        'created': ['a'],
        'duplicates': ['b', 'c'],
        'rejected': const [],
      });

      expect(result.acceptedCount, 3);
      expect(result.rejected, isEmpty);
    });

    test('settledIds covers created, duplicate and rejected', () {
      /// All three are safe to clear. Keeping rejected rows would block the
      /// queue on one malformed event forever.
      final result = SyncResult.fromJson({
        'created': ['a'],
        'duplicates': ['b'],
        'rejected': [
          {'id': 'c', 'reason': 'Unknown worker for this society.'},
        ],
      });

      expect(result.settledIds, containsAll(['a', 'b', 'c']));
      expect(result.settledIds.length, 3);
    });

    test('an empty response settles nothing', () {
      final result = SyncResult.fromJson(const {});
      expect(result.settledIds, isEmpty);
      expect(result.acceptedCount, 0);
    });
  });

  group('RosterEntry', () {
    Map<String, dynamic> payload() => {
          'worker_id': 7,
          'worker_name': 'Rahul Sharma',
          'pass_code': 'a3f1c2d4-0000-4000-8000-000000000001',
          'visits': [
            {
              'source': 'engagement',
              'source_id': 12,
              'title': 'Maid',
              'start_time': '09:00:00',
              'end_time': '10:30:00',
              'flat_label': 'A-301',
              'is_confirmed': true,
            },
          ],
        };

    test('round-trips through the cache', () {
      final restored = RosterEntry.fromJson(RosterEntry.fromJson(payload()).toJson());

      expect(restored.workerId, 7);
      expect(restored.passCode, 'a3f1c2d4-0000-4000-8000-000000000001');
      expect(restored.visits.single.flatLabel, 'A-301');
    });

    test('an offline hit produces the same shape as an online scan', () {
      /// The guard's screen must look identical with or without signal.
      final scan = RosterEntry.fromJson(payload()).toScanResult();

      expect(scan.workerId, 7);
      expect(scan.workerName, 'Rahul Sharma');
      expect(scan.isUsable, isTrue);
      expect(scan.isExpected, isTrue);
      expect(scan.recommendation, GateDecision.allowed);
      expect(scan.fromCache, isTrue);
    });

    test('a worker with no visits is recommended for review, not refusal', () {
      final entry = RosterEntry.fromJson({...payload(), 'visits': const []});
      expect(entry.toScanResult().recommendation, GateDecision.pendingReview);
    });

    test('a worker with no pass code is not usable offline', () {
      final entry = RosterEntry.fromJson({...payload(), 'pass_code': null});
      expect(entry.toScanResult().isUsable, isFalse);
    });
  });

  group('ScanResult', () {
    test('parses a server response', () {
      final scan = ScanResult.fromJson({
        'worker_id': 7,
        'worker_name': 'Rahul Sharma',
        'worker_photo': 'https://example.test/p.jpg',
        'is_usable': true,
        'reason': '',
        'is_expected': true,
        'recommendation': 'allowed',
        'expected_visits': const [],
      });

      expect(scan.isUsable, isTrue);
      expect(scan.recommendation, GateDecision.allowed);
      expect(scan.fromCache, isFalse);
    });

    test('a revoked pass carries its reason', () {
      final scan = ScanResult.fromJson({
        'worker_id': 7,
        'worker_name': 'Rahul Sharma',
        'is_usable': false,
        'reason': 'This pass was cancelled. Card reported lost.',
        'recommendation': 'denied',
      });

      expect(scan.isUsable, isFalse);
      expect(scan.reason, contains('cancelled'));
      expect(scan.recommendation, GateDecision.denied);
    });
  });

  group('FaceCheckResult', () {
    test('a match needs no review', () {
      final result = FaceCheckResult.fromJson({
        'available': true,
        'verified': true,
        'score': 0.91,
        'engine': 'deepface',
      });

      expect(result.needsGuardReview, isFalse);
    });

    test('a failed match needs review', () {
      final result = FaceCheckResult.fromJson({
        'available': true,
        'verified': false,
        'score': 0.31,
      });

      expect(result.needsGuardReview, isTrue);
    });

    test('unavailable is distinct from a failed match', () {
      /// Nothing was measured, so nothing can be concluded — the UI must not
      /// show this as "did not match".
      final result = FaceCheckResult.fromJson({
        'available': false,
        'verified': false,
        'reason': 'No face recognition engine is installed.',
      });

      expect(result.available, isFalse);
      expect(result.score, isNull);
      expect(result.needsGuardReview, isTrue);
    });
  });

  group('AttendanceEvent', () {
    Map<String, dynamic> payload() => {
          'id': 'a3f1c2d4-0000-4000-8000-000000000001',
          'worker': 7,
          'worker_name': 'Rahul Sharma',
          'direction': 'entry',
          'decision': 'allowed',
          'method': 'qr',
          'occurred_at': '2026-08-03T09:15:00Z',
          'was_expected': true,
          'face_checked': false,
          'was_offline': false,
        };

    test('parses a logged entry', () {
      final event = AttendanceEvent.fromJson(payload());

      expect(event.id, 'a3f1c2d4-0000-4000-8000-000000000001');
      expect(event.decision, GateDecision.allowed);
      expect(event.needsReview, isFalse);
      expect(event.wasOverridden, isFalse);
    });

    test('a pending entry needs review', () {
      final event =
          AttendanceEvent.fromJson({...payload(), 'decision': 'pending_review'});
      expect(event.needsReview, isTrue);
    });

    test('an overridden entry names who decided it', () {
      final event = AttendanceEvent.fromJson({
        ...payload(),
        'decision': 'allowed',
        'face_checked': true,
        'face_verified': false,
        'face_match_score': 0.35,
        'overridden_by_name': 'Vikram Singh',
        'override_reason': 'Verified visually.',
      });

      expect(event.wasOverridden, isTrue);
      expect(event.overriddenByName, 'Vikram Singh');
      expect(event.faceMatchScore, 0.35);
    });
  });
}
