/// Data models for Module 12 — the AI layer.
///
/// -----------------------------------------------------------------------
/// EVERY MODEL CARRIES THE ENGINE THAT PRODUCED IT
/// -----------------------------------------------------------------------
/// [ReviewSummary.engine], [ComplaintSuggestion.engine] and
/// [ChatReply.intentSource] all say where the answer came from. That mirrors
/// the server, where `Degraded.engine` and `ReviewSentiment.engine` exist for
/// the same reason: an answer from a model and an answer from a keyword pass
/// are both valid, but conflating them lets a weak guess be read as a finding.
///
/// The screens use these to label rather than to hide. A resident reading a
/// review summary is entitled to know whether it was written by a model or
/// assembled from counts.
library;

double _toDouble(dynamic value) {
  if (value is num) return value.toDouble();
  if (value is String) return double.tryParse(value) ?? 0;
  return 0;
}

List<String> _toStrings(dynamic value) =>
    ((value as List?) ?? const []).map((item) => item.toString()).toList();

/// What the deployment can do. Read once at startup.
class AiStatus {
  const AiStatus({
    this.enabled = false,
    this.providersConfigured = const [],
    this.chatAvailable = false,
    this.faceAvailable = false,
    this.ocrAvailable = false,
    this.recommendationEngine = '',
  });

  final bool enabled;

  /// Which provider tiers have keys. Names only — never the keys themselves.
  final List<String> providersConfigured;

  /// True even with no provider configured: the keyword intent pass and the
  /// database lookups behind it need nothing external.
  final bool chatAvailable;

  final bool faceAvailable;
  final bool ocrAvailable;
  final String recommendationEngine;

  /// Whether any provider can actually be reached. Distinct from
  /// [chatAvailable]: the assistant still works without one, it just falls back
  /// to keywords for understanding the question.
  bool get hasProvider => enabled && providersConfigured.isNotEmpty;

  factory AiStatus.fromJson(Map<String, dynamic> json) => AiStatus(
        enabled: json['enabled'] as bool? ?? false,
        providersConfigured: _toStrings(json['providers_configured']),
        chatAvailable: json['chat_available'] as bool? ?? false,
        faceAvailable: json['face_available'] as bool? ?? false,
        ocrAvailable: json['ocr_available'] as bool? ?? false,
        recommendationEngine: json['recommendation_engine'] as String? ?? '',
      );
}

/// What the assistant was asked about.
///
/// A closed set, matching the server's. Every intent maps to a lookup the
/// platform already runs on a screen — there is deliberately no intent whose
/// answer would have to be composed by a model.
enum ChatIntent {
  schedule('schedule'),
  payments('payments'),
  bookings('bookings'),
  availability('availability'),
  complaints('complaints'),
  help('help'),
  unknown('unknown');

  const ChatIntent(this.wireValue);

  final String wireValue;

  static ChatIntent fromWire(String? value) => ChatIntent.values.firstWhere(
        (intent) => intent.wireValue == value,
        orElse: () => ChatIntent.unknown,
      );
}

/// One reply from the assistant.
class ChatReply {
  const ChatReply({
    required this.intent,
    required this.text,
    this.facts = const [],
    this.intentSource = 'keywords',
    this.suggestions = const [],
  });

  final ChatIntent intent;
  final String text;

  /// The same answer, structured. Shape depends on the intent — the server
  /// returns the fields each lookup actually has, and the screen renders per
  /// intent rather than pretending one shape fits all.
  final List<Map<String, dynamic>> facts;

  /// "ai" or "keywords" — where the *intent* came from. Never where the data
  /// came from: the data is always read from the caller's own records.
  final String intentSource;

  final List<String> suggestions;

  bool get hasFacts => facts.isNotEmpty;

  factory ChatReply.fromJson(Map<String, dynamic> json) => ChatReply(
        intent: ChatIntent.fromWire(json['intent'] as String?),
        text: json['text'] as String? ?? '',
        facts: ((json['facts'] as List?) ?? const [])
            .map((row) => Map<String, dynamic>.from(row as Map))
            .toList(),
        intentSource: json['intent_source'] as String? ?? 'keywords',
        suggestions: _toStrings(json['suggestions']),
      );
}

/// One turn in the on-screen conversation.
///
/// Held in memory only. The server does not store transcripts — see the note in
/// `apps/ai_services/models.py` on why — so neither does the app, and the
/// history goes when the screen does.
class ChatTurn {
  const ChatTurn({
    required this.text,
    required this.isUser,
    this.reply,
    this.isPending = false,
    this.isError = false,
  });

  final String text;
  final bool isUser;

  /// Present on assistant turns that carried structured facts.
  final ChatReply? reply;

  final bool isPending;
  final bool isError;

  factory ChatTurn.user(String text) => ChatTurn(text: text, isUser: true);

  factory ChatTurn.pending() =>
      const ChatTurn(text: '', isUser: false, isPending: true);

  factory ChatTurn.assistant(ChatReply reply) =>
      ChatTurn(text: reply.text, isUser: false, reply: reply);

  factory ChatTurn.failure(String message) =>
      ChatTurn(text: message, isUser: false, isError: true);
}

/// Module 12.5 — a worker's reviews, condensed.
class ReviewSummary {
  const ReviewSummary({
    this.headline = '',
    this.strengths = const [],
    this.concerns = const [],
    this.reviewCount = 0,
    this.engine = '',
    this.isAi = false,
  });

  final String headline;
  final List<String> strengths;
  final List<String> concerns;
  final int reviewCount;

  /// A provider name, or "fallback". Surfaced so a summary assembled from
  /// keyword counts is not read as an assessment.
  final String engine;

  final bool isAi;

  bool get isEmpty => headline.isEmpty && strengths.isEmpty && concerns.isEmpty;

  factory ReviewSummary.fromJson(Map<String, dynamic> json) => ReviewSummary(
        headline: json['headline'] as String? ?? '',
        strengths: _toStrings(json['strengths']),
        concerns: _toStrings(json['concerns']),
        reviewCount: json['review_count'] as int? ?? 0,
        engine: json['engine'] as String? ?? '',
        isAi: json['is_ai'] as bool? ?? false,
      );
}

/// Module 12.5 — a suggested category for a complaint.
class ComplaintSuggestion {
  const ComplaintSuggestion({
    this.category = 'other',
    this.confidence = 0,
    this.rationale = '',
    this.isConfident = false,
    this.engine = '',
  });

  final String category;
  final double confidence;
  final String rationale;

  /// Whether the server considers this worth acting on. The form only offers
  /// the suggestion when true — a lone keyword hit must not be able to nudge
  /// somebody into filing a safety complaint, or out of one.
  final bool isConfident;

  final String engine;

  factory ComplaintSuggestion.fromJson(Map<String, dynamic> json) =>
      ComplaintSuggestion(
        category: json['category'] as String? ?? 'other',
        confidence: _toDouble(json['confidence']),
        rationale: json['rationale'] as String? ?? '',
        isConfident: json['is_confident'] as bool? ?? false,
        engine: json['engine'] as String? ?? '',
      );
}
