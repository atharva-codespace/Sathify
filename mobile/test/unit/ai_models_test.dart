import 'package:flutter_test/flutter_test.dart';
import 'package:sathify/features/ai/data/models/ai_models.dart';

/// Wire-format tests for Module 12 — the AI layer.
///
/// NOT YET EXECUTED — the Flutter SDK is not installed on this machine. Run
/// `flutter test` once it is.
///
/// The [ChatReply] group carries the most weight. The server's whole design
/// rests on the model choosing the question while the database answers it, and
/// the client half of that property is that `facts` survives parsing intact —
/// those are the receipt numbers and amounts a user checks the sentence
/// against. Dropping them would leave only the prose, which is the one part
/// nobody should have to take on trust.
void main() {
  group('AiStatus', () {
    test('parses a configured deployment', () {
      final status = AiStatus.fromJson({
        'enabled': true,
        'providers_configured': ['gemini', 'groq'],
        'chat_available': true,
        'face_available': false,
        'ocr_available': false,
        'recommendation_engine': 'rule_based_v1',
      });

      expect(status.enabled, isTrue);
      expect(status.providersConfigured, ['gemini', 'groq']);
      expect(status.hasProvider, isTrue);
      expect(status.recommendationEngine, 'rule_based_v1');
    });

    test('chat stays available with no provider configured', () {
      // The keyword intent pass and the database lookups behind it need nothing
      // external, so the assistant is never hidden — only the note under it
      // changes.
      final status = AiStatus.fromJson({
        'enabled': true,
        'providers_configured': [],
        'chat_available': true,
      });

      expect(status.chatAvailable, isTrue);
      expect(status.hasProvider, isFalse);
    });

    test('a missing field parses as unavailable rather than throwing', () {
      final status = AiStatus.fromJson({});

      expect(status.enabled, isFalse);
      expect(status.faceAvailable, isFalse);
      expect(status.providersConfigured, isEmpty);
    });
  });

  group('ChatReply', () {
    Map<String, dynamic> paymentsPayload() => {
          'intent': 'payments',
          'text': '₹4,500 paid across 1 payment(s) in the last 30 days.',
          'facts': [
            {
              'receipt_number': 'SATH-202603-A1B2C3D4',
              'date': '2026-03-14',
              'amount': '₹4,500',
              'kind': 'One-day booking',
            },
          ],
          'intent_source': 'ai',
          'suggestions': ['Who is coming today?'],
        };

    test('parses a reply with its facts', () {
      final reply = ChatReply.fromJson(paymentsPayload());

      expect(reply.intent, ChatIntent.payments);
      expect(reply.hasFacts, isTrue);
      expect(reply.facts.first['receipt_number'], 'SATH-202603-A1B2C3D4');
      expect(reply.intentSource, 'ai');
    });

    test('facts survive as maps rather than being flattened to text', () {
      // The receipt number is what lets a user check the sentence against their
      // own records. Losing the structure would leave only the prose.
      final reply = ChatReply.fromJson(paymentsPayload());

      expect(reply.facts.first, isA<Map<String, dynamic>>());
      expect(reply.facts.first.keys, contains('amount'));
    });

    test('an unknown intent parses rather than throwing', () {
      // The server's intent set is closed, but a version skew must not crash a
      // conversation — the sentence is still readable.
      final json = paymentsPayload()..['intent'] = 'launch_rockets';
      expect(ChatReply.fromJson(json).intent, ChatIntent.unknown);
    });

    test('every wire value round-trips', () {
      for (final intent in ChatIntent.values) {
        expect(ChatIntent.fromWire(intent.wireValue), intent);
      }
    });

    test('a reply with no facts is honest about it', () {
      final json = paymentsPayload()..['facts'] = [];
      expect(ChatReply.fromJson(json).hasFacts, isFalse);
    });

    test('intent source defaults to keywords, not to ai', () {
      // Defaulting the other way would label a keyword match as a model's
      // understanding on any server that stopped sending the field.
      final json = paymentsPayload()..remove('intent_source');
      expect(ChatReply.fromJson(json).intentSource, 'keywords');
    });
  });

  group('ChatTurn', () {
    test('a pending turn carries no text', () {
      final turn = ChatTurn.pending();

      expect(turn.isPending, isTrue);
      expect(turn.isUser, isFalse);
      expect(turn.text, isEmpty);
    });

    test('a failure is a turn in the thread, not a lost message', () {
      // Rendered inline so somebody scrolling back can see which question went
      // unanswered, rather than a snackbar that has already gone.
      final turn = ChatTurn.failure('You are offline.');

      expect(turn.isError, isTrue);
      expect(turn.isUser, isFalse);
      expect(turn.text, 'You are offline.');
    });

    test('an assistant turn keeps the structured reply', () {
      final reply = ChatReply.fromJson({
        'intent': 'schedule',
        'text': '2 visits today.',
        'facts': [
          {'date': '2026-03-14', 'start_time': '09:00', 'worker_name': 'Rahul'},
        ],
      });
      final turn = ChatTurn.assistant(reply);

      expect(turn.reply, isNotNull);
      expect(turn.reply!.facts.length, 1);
      expect(turn.text, '2 visits today.');
    });
  });

  group('ReviewSummary', () {
    Map<String, dynamic> payload({bool isAi = true}) => {
          'headline': 'Consistently punctual, occasional gaps in cleaning.',
          'strengths': ['always on time', 'polite'],
          'concerns': ['kitchen sometimes missed'],
          'review_count': 12,
          'engine': isAi ? 'gemini' : 'fallback',
          'is_ai': isAi,
        };

    test('parses a summary', () {
      final summary = ReviewSummary.fromJson(payload());

      expect(summary.reviewCount, 12);
      expect(summary.strengths.length, 2);
      expect(summary.concerns.first, 'kitchen sometimes missed');
      expect(summary.isEmpty, isFalse);
    });

    test('says which engine produced it', () {
      // A summary written by a model and one assembled from keyword counts read
      // very differently, and a resident deciding whether to hire somebody is
      // entitled to know which they are looking at.
      expect(ReviewSummary.fromJson(payload()).isAi, isTrue);
      expect(ReviewSummary.fromJson(payload(isAi: false)).engine, 'fallback');
    });

    test('a worker with no written reviews summarises to empty', () {
      final summary = ReviewSummary.fromJson({
        'headline': '',
        'strengths': [],
        'concerns': [],
        'review_count': 0,
      });

      expect(summary.isEmpty, isTrue);
    });
  });

  group('ComplaintSuggestion', () {
    test('parses a confident suggestion', () {
      final suggestion = ComplaintSuggestion.fromJson({
        'category': 'safety',
        'confidence': 0.95,
        'rationale': 'mentions a threat at the gate',
        'is_confident': true,
        'engine': 'gemini',
      });

      expect(suggestion.category, 'safety');
      expect(suggestion.confidence, 0.95);
      expect(suggestion.isConfident, isTrue);
    });

    test('a weak match is not confident', () {
      // Module 11 routes safety complaints to the front of the queue. A lone
      // keyword hit must not be able to nudge somebody into filing one, or out.
      final suggestion = ComplaintSuggestion.fromJson({
        'category': 'safety',
        'confidence': 0.4,
        'is_confident': false,
        'engine': 'fallback',
      });

      expect(suggestion.isConfident, isFalse);
    });

    test('defaults to other rather than to a real category', () {
      // A missing category must not be filed as something specific.
      expect(ComplaintSuggestion.fromJson({}).category, 'other');
      expect(ComplaintSuggestion.fromJson({}).isConfident, isFalse);
    });

    test('confidence survives arriving as a string', () {
      final suggestion = ComplaintSuggestion.fromJson({
        'category': 'payment',
        'confidence': '0.65',
        'is_confident': true,
      });

      expect(suggestion.confidence, 0.65);
    });
  });
}
