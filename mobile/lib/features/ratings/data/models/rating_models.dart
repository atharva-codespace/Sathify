/// Data models for Module 9 — Ratings, Reviews & Trust Score.
///
/// -----------------------------------------------------------------------
/// A TRUST SCORE NEVER TRAVELS WITHOUT ITS REASONS
/// -----------------------------------------------------------------------
/// [TrustScore] requires its [components]. That mirrors the server, which has
/// no endpoint returning a bare number: the modspec makes explainability the
/// key requirement, because this score decides whether someone gets hired and a
/// number nobody can justify gets disputed. Do not add a constructor that makes
/// the breakdown optional — the first screen to use it would show the number
/// alone.
library;

/// Which way a rating runs.
enum RatingDirection {
  residentToWorker('resident_to_worker', 'Resident rated the worker'),
  workerToResident('worker_to_resident', 'Worker rated the resident');

  const RatingDirection(this.wireValue, this.label);

  final String wireValue;
  final String label;

  static RatingDirection fromWire(String? value) =>
      RatingDirection.values.firstWhere(
        (d) => d.wireValue == value,
        orElse: () => RatingDirection.residentToWorker,
      );
}

enum SentimentLabel {
  positive('positive', 'Positive'),
  neutral('neutral', 'Mixed'),
  negative('negative', 'Negative'),
  unknown('unknown', 'Not analysed');

  const SentimentLabel(this.wireValue, this.label);

  final String wireValue;
  final String label;

  static SentimentLabel fromWire(String? value) =>
      SentimentLabel.values.firstWhere(
        (s) => s.wireValue == value,
        orElse: () => SentimentLabel.unknown,
      );
}

/// Module 9.2 — what a model made of a review's text.
class ReviewSentiment {
  const ReviewSentiment({
    required this.label,
    required this.isReliable,
    this.polarity = 0,
    this.confidence = 0,
    this.themes = const {},
    this.detectedLanguage = '',
    this.engine = '',
  });

  final SentimentLabel label;

  /// False when the engine was not confident enough to be worth showing. The
  /// built-in lexicon is a stopgap for mixed Hindi/Hinglish/English text and
  /// says so through this flag — a guess presented as a finding is worse than
  /// showing nothing.
  final bool isReliable;

  final double polarity;
  final double confidence;

  /// Per-theme verdicts: punctuality, hygiene, behaviour, quality. Only themes
  /// the review actually touched appear.
  final Map<String, String> themes;
  final String detectedLanguage;
  final String engine;

  factory ReviewSentiment.fromJson(Map<String, dynamic> json) =>
      ReviewSentiment(
        label: SentimentLabel.fromWire(json['label'] as String?),
        isReliable: json['is_reliable'] as bool? ?? false,
        polarity: (json['polarity'] as num?)?.toDouble() ?? 0,
        confidence: (json['confidence'] as num?)?.toDouble() ?? 0,
        themes: ((json['themes'] as Map?) ?? const {})
            .map((key, value) => MapEntry(key.toString(), value.toString())),
        detectedLanguage: json['detected_language'] as String? ?? '',
        engine: json['engine'] as String? ?? '',
      );
}

/// One rating (Module 9.1).
class Rating {
  const Rating({
    required this.id,
    required this.stars,
    required this.direction,
    this.review = '',
    this.raterName = '',
    this.workerName = '',
    this.residentName = '',
    this.subjectIsWorker = true,
    this.sentiment,
    this.isFlagged = false,
    this.isWithheld = false,
    this.createdAt,
  });

  final int id;
  final int stars;
  final RatingDirection direction;
  final String review;
  final String raterName;
  final String workerName;
  final String residentName;
  final bool subjectIsWorker;
  final ReviewSentiment? sentiment;

  /// Matched a suspicious pattern and is awaiting an administrator. Shown to
  /// the person who wrote it rather than hidden — a rating that silently
  /// vanished would be indistinguishable from a bug.
  final bool isFlagged;

  /// Excluded from scoring until reviewed. Not deleted.
  final bool isWithheld;

  final DateTime? createdAt;

  bool get hasReview => review.trim().isNotEmpty;

  factory Rating.fromJson(Map<String, dynamic> json) => Rating(
        id: json['id'] as int,
        stars: json['stars'] as int? ?? 0,
        direction: RatingDirection.fromWire(json['direction'] as String?),
        review: json['review'] as String? ?? '',
        raterName: json['rater_name'] as String? ?? '',
        workerName: json['worker_name'] as String? ?? '',
        residentName: json['resident_name'] as String? ?? '',
        subjectIsWorker: json['subject_is_worker'] as bool? ?? true,
        sentiment: json['sentiment'] is Map<String, dynamic>
            ? ReviewSentiment.fromJson(
                json['sentiment'] as Map<String, dynamic>,
              )
            : null,
        isFlagged: json['is_flagged'] as bool? ?? false,
        isWithheld: json['is_withheld'] as bool? ?? false,
        createdAt: DateTime.tryParse(json['created_at'] as String? ?? ''),
      );
}

/// A completed job the caller has not rated yet (Module 9.1).
class RateableJob {
  const RateableJob({
    required this.kind,
    required this.id,
    required this.title,
    this.counterpartyName = '',
    this.flatLabel = '',
    this.finishedOn,
  });

  /// `engagement` or `booking` — which the submission must reference.
  final String kind;
  final int id;
  final String title;
  final String counterpartyName;
  final String flatLabel;
  final DateTime? finishedOn;

  bool get isEngagement => kind == 'engagement';

  factory RateableJob.fromJson(Map<String, dynamic> json) => RateableJob(
        kind: json['kind'] as String? ?? 'booking',
        id: json['id'] as int? ?? 0,
        title: json['title'] as String? ?? '',
        counterpartyName: json['counterparty_name'] as String? ?? '',
        flatLabel: json['flat_label'] as String? ?? '',
        finishedOn: DateTime.tryParse(json['finished_on'] as String? ?? ''),
      );
}

/// One weighted input behind a trust score (Module 9.3).
class TrustComponent {
  const TrustComponent({
    required this.key,
    required this.label,
    required this.weight,
    required this.score,
    required this.contribution,
    this.detail = '',
  });

  final String key;
  final String label;
  final double weight;

  /// This component on 0–1.
  final double score;

  /// How many points of the final score this contributed.
  final double contribution;

  /// A sentence a person can read. "attendance: 0.72" explains nothing to a
  /// worker asking why their score fell, so the server sends prose.
  final String detail;

  int get scorePercentage => (score * 100).round();

  factory TrustComponent.fromJson(Map<String, dynamic> json) => TrustComponent(
        key: json['key'] as String? ?? '',
        label: json['label'] as String? ?? '',
        weight: (json['weight'] as num?)?.toDouble() ?? 0,
        score: (json['score'] as num?)?.toDouble() ?? 0,
        contribution: (json['contribution'] as num?)?.toDouble() ?? 0,
        detail: json['detail'] as String? ?? '',
      );
}

/// Module 9.3 — a score and the reasons behind it.
class TrustScore {
  const TrustScore({
    required this.subjectType,
    required this.subjectId,
    required this.score,
    required this.components,
    this.subjectName = '',
    this.averageRating = 0,
    this.ratingCount = 0,
    this.weakest,
  });

  final String subjectType;
  final int subjectId;
  final String subjectName;

  /// 0–100.
  final double score;
  final double averageRating;
  final int ratingCount;

  /// Required, not optional. See the file header.
  final List<TrustComponent> components;

  /// What is costing the most, so "how do I improve this?" has an answer.
  final TrustComponent? weakest;

  bool get hasRatings => ratingCount > 0;

  /// A coarse band for colour and wording. Deliberately not shown as "bad" at
  /// the low end: a new worker sits there through having no history, not
  /// through having a poor one.
  String get band {
    if (score >= 80) return 'strong';
    if (score >= 60) return 'good';
    if (score >= 40) return 'building';
    return 'new';
  }

  factory TrustScore.fromJson(Map<String, dynamic> json) => TrustScore(
        subjectType: json['subject_type'] as String? ?? '',
        subjectId: json['subject_id'] as int? ?? 0,
        subjectName: json['subject_name'] as String? ?? '',
        score: (json['score'] as num?)?.toDouble() ?? 0,
        averageRating: (json['average_rating'] as num?)?.toDouble() ?? 0,
        ratingCount: json['rating_count'] as int? ?? 0,
        components: ((json['components'] as List?) ?? const [])
            .map((row) => TrustComponent.fromJson(row as Map<String, dynamic>))
            .toList(),
        weakest: json['weakest'] is Map<String, dynamic>
            ? TrustComponent.fromJson(json['weakest'] as Map<String, dynamic>)
            : null,
      );
}

/// One entry in the trust score audit trail (Module 9.3).
class TrustScoreLog {
  const TrustScoreLog({
    required this.id,
    required this.previousScore,
    required this.newScore,
    required this.delta,
    this.trigger = '',
    this.components = const [],
    this.createdAt,
  });

  final int id;
  final double previousScore;
  final double newScore;
  final double delta;

  /// What caused the recomputation.
  final String trigger;

  /// The breakdown **as it was at the time**. Not recomputed — that would give
  /// today's answer rather than the one that was acted on.
  final List<TrustComponent> components;

  final DateTime? createdAt;

  bool get improved => delta > 0;

  factory TrustScoreLog.fromJson(Map<String, dynamic> json) => TrustScoreLog(
        id: json['id'] as int,
        previousScore:
            double.tryParse(json['previous_score']?.toString() ?? '') ?? 0,
        newScore: double.tryParse(json['new_score']?.toString() ?? '') ?? 0,
        delta: double.tryParse(json['delta']?.toString() ?? '') ?? 0,
        trigger: json['trigger'] as String? ?? '',
        components: ((json['components'] as List?) ?? const [])
            .map((row) => TrustComponent.fromJson(row as Map<String, dynamic>))
            .toList(),
        createdAt: DateTime.tryParse(json['created_at'] as String? ?? ''),
      );
}

/// Module 9.4 — a rating flagged for an administrator.
class ReviewFlag {
  const ReviewFlag({
    required this.id,
    required this.reason,
    required this.reasonDisplay,
    required this.status,
    this.detail = '',
    this.rating,
    this.isOpen = true,
    this.reviewNote = '',
    this.createdAt,
  });

  final int id;
  final String reason;
  final String reasonDisplay;
  final String status;

  /// Why the heuristic fired, in plain language.
  final String detail;
  final Rating? rating;
  final bool isOpen;
  final String reviewNote;
  final DateTime? createdAt;

  factory ReviewFlag.fromJson(Map<String, dynamic> json) => ReviewFlag(
        id: json['id'] as int,
        reason: json['reason'] as String? ?? '',
        reasonDisplay: json['reason_display'] as String? ?? '',
        status: json['status'] as String? ?? 'open',
        detail: json['detail'] as String? ?? '',
        rating: json['rating_detail'] is Map<String, dynamic>
            ? Rating.fromJson(json['rating_detail'] as Map<String, dynamic>)
            : null,
        isOpen: json['is_open'] as bool? ?? true,
        reviewNote: json['review_note'] as String? ?? '',
        createdAt: DateTime.tryParse(json['created_at'] as String? ?? ''),
      );
}
