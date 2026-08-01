import 'package:flutter_test/flutter_test.dart';
import 'package:sathify/features/ratings/data/models/rating_models.dart';

/// Wire-format tests for Module 9 — Ratings, Reviews & Trust Score.
///
/// NOT YET EXECUTED — the Flutter SDK is not installed on this machine. Run
/// `flutter test` once it is.
///
/// The [TrustScore] group carries the most weight. The modspec makes
/// explainability the key requirement, so the breakdown must survive parsing
/// intact — a score that arrived without its reasons would be a score nobody
/// could defend.
void main() {
  group('TrustScore', () {
    Map<String, dynamic> payload() => {
          'subject_type': 'worker',
          'subject_id': 7,
          'subject_name': 'Rahul Sharma',
          'score': 78.5,
          'average_rating': 4.4,
          'rating_count': 12,
          'components': [
            {
              'key': 'ratings',
              'label': 'Ratings from residents',
              'weight': 0.35,
              'score': 0.86,
              'contribution': 30.1,
              'detail': '4.4 out of 5 from 12 rating(s)',
            },
            {
              'key': 'verification',
              'label': 'Identity verified',
              'weight': 0.2,
              'score': 1.0,
              'contribution': 20.0,
              'detail': 'Approved, ID verified, photo on file',
            },
          ],
          'weakest': {
            'key': 'attendance',
            'label': 'Turns up as agreed',
            'weight': 0.3,
            'score': 0.4,
            'contribution': 12.0,
            'detail': 'Attended 8 of 20 expected visits',
          },
        };

    test('parses a score with its breakdown', () {
      final trust = TrustScore.fromJson(payload());

      expect(trust.score, 78.5);
      expect(trust.ratingCount, 12);
      expect(trust.components.length, 2);
      expect(trust.subjectName, 'Rahul Sharma');
    });

    test('every component carries a readable reason', () {
      /// "attendance: 0.72" explains nothing to a worker asking why.
      final trust = TrustScore.fromJson(payload());
      for (final component in trust.components) {
        expect(component.detail, isNotEmpty);
      }
    });

    test('the weakest component is available for "how do I improve?"', () {
      final trust = TrustScore.fromJson(payload());

      expect(trust.weakest, isNotNull);
      expect(trust.weakest!.key, 'attendance');
      expect(trust.weakest!.detail, contains('8 of 20'));
    });

    test('a component reports its share as a percentage', () {
      final trust = TrustScore.fromJson(payload());
      expect(trust.components.first.scorePercentage, 86);
    });

    test('a new subject bands as new, not as bad', () {
      /// They sit low through having no history, not a poor one, and the
      /// wording a resident sees must not imply otherwise.
      final trust = TrustScore.fromJson({
        ...payload(),
        'score': 30.0,
        'rating_count': 0,
      });

      expect(trust.band, 'new');
      expect(trust.hasRatings, isFalse);
    });

    test('bands cover the range', () {
      expect(TrustScore.fromJson({...payload(), 'score': 90.0}).band, 'strong');
      expect(TrustScore.fromJson({...payload(), 'score': 70.0}).band, 'good');
      expect(TrustScore.fromJson({...payload(), 'score': 45.0}).band, 'building');
      expect(TrustScore.fromJson({...payload(), 'score': 20.0}).band, 'new');
    });

    test('an empty breakdown still parses rather than throwing', () {
      /// Defensive: a screen showing an unexplained number is bad, but a crash
      /// on a profile is worse.
      final trust = TrustScore.fromJson({...payload(), 'components': const []});
      expect(trust.components, isEmpty);
    });
  });

  group('TrustScoreLog', () {
    test('parses a change with its frozen breakdown', () {
      final log = TrustScoreLog.fromJson({
        'id': 4,
        'previous_score': '60.00',
        'new_score': '72.50',
        'delta': '12.50',
        'trigger': 'rating submitted',
        'components': [
          {
            'key': 'ratings',
            'label': 'Ratings from residents',
            'weight': 0.35,
            'score': 0.9,
            'contribution': 31.5,
            'detail': '4.5 out of 5 from 10 rating(s)',
          },
        ],
        'created_at': '2026-08-10T09:00:00Z',
      });

      expect(log.previousScore, 60.0);
      expect(log.newScore, 72.5);
      expect(log.delta, 12.5);
      expect(log.improved, isTrue);
      expect(log.components.single.detail, contains('4.5 out of 5'));
    });

    test('decimals arriving as strings are parsed', () {
      /// DRF renders DecimalField as a string; the score must not become zero.
      final log = TrustScoreLog.fromJson({
        'id': 1,
        'previous_score': '10.00',
        'new_score': '5.00',
        'delta': '-5.00',
      });

      expect(log.newScore, 5.0);
      expect(log.improved, isFalse);
    });
  });

  group('Rating', () {
    Map<String, dynamic> payload() => {
          'id': 3,
          'stars': 5,
          'direction': 'resident_to_worker',
          'review': 'Very good work, always punctual',
          'rater_name': 'Anita Desai',
          'worker_name': 'Rahul Sharma',
          'subject_is_worker': true,
          'is_flagged': false,
          'is_withheld': false,
          'created_at': '2026-08-10T09:00:00Z',
          'sentiment': {
            'label': 'positive',
            'is_reliable': true,
            'polarity': 0.8,
            'confidence': 0.55,
            'themes': {'punctuality': 'positive'},
            'detected_language': 'en-or-hinglish',
            'engine': 'lexicon',
          },
        };

    test('parses a rating with its sentiment', () {
      final rating = Rating.fromJson(payload());

      expect(rating.stars, 5);
      expect(rating.direction, RatingDirection.residentToWorker);
      expect(rating.hasReview, isTrue);
      expect(rating.sentiment!.label, SentimentLabel.positive);
      expect(rating.sentiment!.themes['punctuality'], 'positive');
    });

    test('a stars-only rating has no sentiment', () {
      final rating = Rating.fromJson({...payload(), 'review': '', 'sentiment': null});

      expect(rating.hasReview, isFalse);
      expect(rating.sentiment, isNull);
    });

    test('an unreliable sentiment is marked as such', () {
      /// The built-in lexicon is a stopgap; a guess must not be shown as a
      /// verdict on somebody's work.
      final rating = Rating.fromJson({
        ...payload(),
        'sentiment': {
          'label': 'unknown',
          'is_reliable': false,
          'confidence': 0.0,
        },
      });

      expect(rating.sentiment!.isReliable, isFalse);
    });

    test('a flagged rating is visible, not hidden', () {
      /// A rating that silently vanished would be indistinguishable from a bug.
      final rating = Rating.fromJson({
        ...payload(),
        'is_flagged': true,
        'is_withheld': true,
      });

      expect(rating.isFlagged, isTrue);
      expect(rating.isWithheld, isTrue);
      expect(rating.stars, 5);
    });

    test('falls back rather than throwing on an unknown direction', () {
      final rating = Rating.fromJson({...payload(), 'direction': 'sideways'});
      expect(rating.direction, RatingDirection.residentToWorker);
    });
  });

  group('RateableJob', () {
    test('an engagement is distinguishable from a booking', () {
      /// The submission must reference the right one.
      final engagement = RateableJob.fromJson({
        'kind': 'engagement',
        'id': 12,
        'title': 'Maid',
        'counterparty_name': 'Rahul Sharma',
        'flat_label': 'A-301',
        'finished_on': '2026-08-09',
      });
      final booking = RateableJob.fromJson({
        'kind': 'booking',
        'id': 5,
        'title': 'Deep cleaning',
      });

      expect(engagement.isEngagement, isTrue);
      expect(booking.isEngagement, isFalse);
      expect(engagement.finishedOn, DateTime(2026, 8, 9));
    });

    test('a missing finish date parses as null rather than throwing', () {
      final job = RateableJob.fromJson({'kind': 'booking', 'id': 1, 'title': 'x'});
      expect(job.finishedOn, isNull);
    });
  });

  group('ReviewFlag', () {
    test('parses a flag with the rating it concerns', () {
      final flag = ReviewFlag.fromJson({
        'id': 2,
        'reason': 'burst',
        'reason_display': 'Many ratings from one person in a short window',
        'status': 'open',
        'detail': '6 ratings from this person within 1 hour(s).',
        'is_open': true,
        'rating_detail': {
          'id': 9,
          'stars': 5,
          'direction': 'resident_to_worker',
          'review': 'good work',
        },
      });

      expect(flag.reason, 'burst');
      expect(flag.isOpen, isTrue);
      expect(flag.detail, contains('6 ratings'));
      expect(flag.rating!.stars, 5);
    });

    test('a resolved flag carries its note', () {
      final flag = ReviewFlag.fromJson({
        'id': 2,
        'reason': 'uniform',
        'reason_display': 'Suspiciously uniform ratings',
        'status': 'dismissed',
        'is_open': false,
        'review_note': 'Genuine — catching up on a month of bookings.',
      });

      expect(flag.isOpen, isFalse);
      expect(flag.reviewNote, contains('Genuine'));
    });
  });
}
