import 'package:flutter_test/flutter_test.dart';
import 'package:sathify/features/attendance/data/models/attendance_models.dart';
import 'package:sathify/features/attendance/presentation/screens/face_check_sheet.dart';

/// Module 7.3 — what the guard is told, not just what the server computed.
///
/// `attendance_models_test.dart` already covers the model: that a low score and
/// an unavailable engine both set `needsGuardReview`. That is necessary and not
/// sufficient. The consequence for a real worker lives in the *wording* the
/// guard reads at the gate, under time pressure, on a cheap screen — and that
/// wording is what these assert.
///
/// The rule the backend states in `apps/attendance/face.py` and this file
/// enforces on the client: face recognition is measurably less accurate for
/// darker skin tones, older cameras and poor lighting, so a below-threshold
/// score is a prompt to look at the person, never a refusal. A regression here
/// costs somebody a day's wages for a model's error, which is why it is worth
/// a test rather than a code comment.
void main() {
  FaceCheckResult matched() => FaceCheckResult.fromJson({
        'available': true,
        'verified': true,
        'score': 0.91,
        'engine': 'deepface',
      });

  FaceCheckResult belowThreshold() => FaceCheckResult.fromJson({
        'available': true,
        'verified': false,
        'score': 0.31,
        'engine': 'deepface',
      });

  FaceCheckResult unavailable() => FaceCheckResult.fromJson({
        'available': false,
        'verified': false,
        'reason': 'No face recognition engine is installed on this server.',
      });

  group('FaceCheckPresentation', () {
    test('a match reads as a match, and reports the score', () {
      final presentation = FaceCheckPresentation.of(matched());

      expect(presentation.tone, FaceCheckTone.matched);
      expect(presentation.body, contains('91%'));
    });

    test('a low score never reads as a refusal', () {
      final presentation = FaceCheckPresentation.of(belowThreshold());

      expect(presentation.tone, FaceCheckTone.review);
      // The load-bearing sentence. If a future edit drops it, the guard is left
      // to infer from a big red-ish number that the system turned someone away.
      expect(presentation.body, contains('has not been refused'));
      expect(presentation.body, contains('31%'));
    });

    test('a low score asks the guard to look, rather than announcing a verdict',
        () {
      final presentation = FaceCheckPresentation.of(belowThreshold());

      expect(presentation.title.toLowerCase(), contains('check'));
      // "Denied"/"refused entry"/"rejected" must never appear as the outcome.
      // Only the reassurance "has not been refused" may mention refusal at all.
      expect(presentation.title.toLowerCase(), isNot(contains('denied')));
      expect(presentation.title.toLowerCase(), isNot(contains('rejected')));
      expect(presentation.body.toLowerCase(), isNot(contains('denied')));
    });

    test('unavailable is a distinct tone from a low score', () {
      final notRun = FaceCheckPresentation.of(unavailable());
      final low = FaceCheckPresentation.of(belowThreshold());

      expect(notRun.tone, FaceCheckTone.notChecked);
      expect(notRun.tone, isNot(low.tone));
    });

    test('unavailable states nothing was measured and carries the reason', () {
      final presentation = FaceCheckPresentation.of(unavailable());

      expect(presentation.title, 'Not checked');
      expect(presentation.body, contains('No face recognition engine'));
      expect(presentation.body, contains('verify visually'));
      // Nothing ran, so there is no score to quote and none must be invented.
      expect(presentation.body, isNot(contains('%')));
    });

    test('an unavailable result with no reason still tells the guard what to do',
        () {
      final presentation = FaceCheckPresentation.of(
        FaceCheckResult.fromJson({'available': false, 'verified': false}),
      );

      expect(presentation.tone, FaceCheckTone.notChecked);
      expect(presentation.body, contains('verify visually'));
    });

    test('a transport failure is presented as not-checked, not as a mismatch',
        () {
      // Offline, a 404, or a 500. The comparison never happened, so this must
      // land in the same bucket as "no engine" rather than looking like a
      // failed match.
      final presentation =
          FaceCheckPresentation.notRun('No internet connection.');

      expect(presentation.tone, FaceCheckTone.notChecked);
      expect(presentation.body, contains('No internet connection.'));
    });

    test('a match with no score does not fabricate a percentage', () {
      final presentation = FaceCheckPresentation.of(
        FaceCheckResult.fromJson({'available': true, 'verified': true}),
      );

      expect(presentation.tone, FaceCheckTone.matched);
      expect(presentation.body, isNot(contains('%')));
    });
  });
}
