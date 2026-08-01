import 'package:flutter_test/flutter_test.dart';
import 'package:sathify/features/attendance/data/models/self_checkin_models.dart';

/// Wire-format tests for Module 13.3 tier 2 — worker self check-in.
///
/// NOT YET EXECUTED — the Flutter SDK is not installed on this machine. Run
/// `flutter test` once it is.
///
/// The property worth defending hardest is that a check-in survives having no
/// position. A worker with location switched off who did the job must still end
/// up with a record; making the position mandatory would let a settings toggle
/// cost somebody a day's wages.
void main() {
  group('SelfCheckInDraft', () {
    test('round-trips through the queue payload', () {
      // The queue stores the JSON and reads it back, so anything the encoder
      // drops is silently lost during an outage — which is exactly when it
      // matters.
      final draft = SelfCheckInDraft(
        id: 'c0ffee00-0000-4000-8000-000000000001',
        occurredAt: DateTime.utc(2026, 3, 14, 9, 5),
        direction: 'entry',
        position: const DevicePosition(
          latitude: 18.5591,
          longitude: 73.7801,
          accuracyMetres: 22.5,
        ),
        deviceId: 'phone-1',
        wasOffline: true,
      );

      final restored = SelfCheckInDraft.fromJson(draft.toJson());

      expect(restored.id, draft.id);
      expect(restored.direction, 'entry');
      expect(restored.position!.latitude, 18.5591);
      expect(restored.position!.accuracyMetres, 22.5);
      expect(restored.deviceId, 'phone-1');
      expect(restored.wasOffline, isTrue);
      expect(
        restored.occurredAt.toUtc(),
        DateTime.utc(2026, 3, 14, 9, 5),
      );
    });

    test('a check-in with no position is still a valid check-in', () {
      final draft = SelfCheckInDraft(
        id: 'c0ffee00-0000-4000-8000-000000000002',
        occurredAt: DateTime.utc(2026, 3, 14, 9, 5),
      );

      final json = draft.toJson();

      expect(json.containsKey('latitude'), isFalse);
      expect(json.containsKey('longitude'), isFalse);
      expect(SelfCheckInDraft.fromJson(json).position, isNull);
    });

    test('accuracy is sent when the device reports it', () {
      // The server widens its allowance to match, so an honest phone reporting
      // a poor fix helps the worker rather than hurting them. Dropping the
      // field would turn that into a rejection.
      final draft = SelfCheckInDraft(
        id: 'x',
        occurredAt: DateTime.utc(2026, 3, 14),
        position: const DevicePosition(
          latitude: 1,
          longitude: 2,
          accuracyMetres: 180,
        ),
      );

      expect(draft.toJson()['accuracy_metres'], 180);
    });

    test('the timestamp is sent in UTC', () {
      // The server stores UTC and reasons about local time itself. Sending a
      // device-local string with no offset would put a 9am arrival at 3:30am.
      final draft = SelfCheckInDraft(
        id: 'x',
        occurredAt: DateTime.utc(2026, 3, 14, 9),
      );

      expect(draft.toJson()['occurred_at'], endsWith('Z'));
    });

    test('copyWith clears the offline marker without losing the id', () {
      // The id is what makes the retry idempotent. A copy that minted a new one
      // would create a second record on every push.
      final draft = SelfCheckInDraft(
        id: 'keep-me',
        occurredAt: DateTime.utc(2026, 3, 14),
        wasOffline: true,
      );

      final online = draft.copyWith(wasOffline: false);

      expect(online.id, 'keep-me');
      expect(online.wasOffline, isFalse);
    });
  });

  group('SelfCheckInResult', () {
    Map<String, dynamic> payload({
      String decision = 'allowed',
      bool created = true,
    }) =>
        {
          'id': 'c0ffee00-0000-4000-8000-000000000001',
          'created': created,
          'decision': decision,
          'decision_reason': '',
          'was_expected': true,
          'needs_review': decision != 'allowed',
          'distance_metres': 41.2,
          'location_checked': true,
        };

    test('parses an accepted check-in', () {
      final result = SelfCheckInResult.fromJson(payload());

      expect(result.isAllowed, isTrue);
      expect(result.needsReview, isFalse);
      expect(result.distanceMetres, 41.2);
      expect(result.locationChecked, isTrue);
    });

    test('a review outcome is not a refusal', () {
      // There is no `denied` state to parse, because the server never produces
      // one from this tier. A GPS fix between two towers is routinely 150 m
      // out, and that must not cost anybody a day's wages.
      final result = SelfCheckInResult.fromJson(
        payload(decision: 'pending_review'),
      );

      expect(result.isAllowed, isFalse);
      expect(result.needsReview, isTrue);
    });

    test('a replayed check-in comes back as not created', () {
      // The expected outcome of a device that pushed and lost the response.
      // Not an error — the record exists, which is all the worker needs.
      final result = SelfCheckInResult.fromJson(payload(created: false));

      expect(result.created, isFalse);
      expect(result.isAllowed, isTrue);
    });

    test('an unmeasured location is distinct from a distant one', () {
      // Three states, not two: nothing was measured, so nothing may be
      // concluded — the same distinction the face check makes.
      final result = SelfCheckInResult.fromJson({
        'id': 'x',
        'decision': 'pending_review',
        'location_checked': false,
        'distance_metres': null,
      });

      expect(result.locationChecked, isFalse);
      expect(result.distanceMetres, isNull);
    });

    test('a missing decision defaults to review rather than to allowed', () {
      // Defaulting the other way would let a truncated response read as a clean
      // entry, which Module 8 would then bill from.
      expect(SelfCheckInResult.fromJson({}).decision, 'pending_review');
      expect(SelfCheckInResult.fromJson({}).isAllowed, isFalse);
    });
  });
}
