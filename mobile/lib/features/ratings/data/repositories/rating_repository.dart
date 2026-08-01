import '../../../../core/config/api_endpoints.dart';
import '../../../../core/network/api_client.dart';
import '../models/rating_models.dart';

/// All Module 9 endpoints.
class RatingRepository {
  RatingRepository({ApiClient? client}) : _client = client ?? ApiClient();

  final ApiClient _client;

  // --- 9.1 Rating ------------------------------------------------------------

  /// Completed jobs the caller has not rated yet.
  ///
  /// Driven from what is actually rateable rather than from a notification, so
  /// somebody who dismissed a prompt can still find the job.
  Future<List<RateableJob>> fetchPending() async {
    final response =
        await _client.get(ApiEndpoints.pendingRatings) as Map<String, dynamic>;

    return ((response['results'] as List?) ?? const [])
        .map((row) => RateableJob.fromJson(row as Map<String, dynamic>))
        .toList();
  }

  /// Submits a rating. The direction is decided server-side from the caller's
  /// role, so the app cannot rate on the wrong side by mistake.
  Future<Rating> submit({
    required RateableJob job,
    required int stars,
    String review = '',
  }) async {
    final response = await _client.post(
      ApiEndpoints.ratings,
      data: {
        'stars': stars,
        if (review.trim().isNotEmpty) 'review': review.trim(),
        if (job.isEngagement) 'engagement': job.id else 'booking': job.id,
      },
    ) as Map<String, dynamic>;

    return Rating.fromJson(response['rating'] as Map<String, dynamic>);
  }

  Future<List<Rating>> fetchMyRatings() async {
    final response = await _client.get(
      ApiEndpoints.ratings,
      query: {'page_size': 100},
    ) as Map<String, dynamic>;

    return (response['results'] as List)
        .map((row) => Rating.fromJson(row as Map<String, dynamic>))
        .toList();
  }

  /// A worker's public reviews. Withheld ones are excluded server-side.
  Future<List<Rating>> fetchWorkerRatings(int workerId) async {
    final response = await _client.get(
      ApiEndpoints.workerRatings(workerId),
      query: {'page_size': 100},
    ) as Map<String, dynamic>;

    return (response['results'] as List)
        .map((row) => Rating.fromJson(row as Map<String, dynamic>))
        .toList();
  }

  // --- 9.3 Trust -------------------------------------------------------------

  Future<TrustScore> fetchMyTrustScore() async {
    final response =
        await _client.get(ApiEndpoints.myTrustScore) as Map<String, dynamic>;
    return TrustScore.fromJson(response);
  }

  Future<TrustScore> fetchWorkerTrustScore(int workerId) async {
    final response = await _client.get(ApiEndpoints.workerTrustScore(workerId))
        as Map<String, dynamic>;
    return TrustScore.fromJson(response);
  }

  /// Every change, with the breakdown frozen as it was.
  Future<List<TrustScoreLog>> fetchTrustHistory() async {
    final response = await _client.get(
      ApiEndpoints.trustHistory,
      query: {'page_size': 50},
    ) as Map<String, dynamic>;

    return (response['results'] as List)
        .map((row) => TrustScoreLog.fromJson(row as Map<String, dynamic>))
        .toList();
  }

  // --- 9.4 Flags -------------------------------------------------------------

  Future<List<ReviewFlag>> fetchFlags({String? status}) async {
    final response = await _client.get(
      ApiEndpoints.reviewFlags,
      query: {
        if (status != null) 'status': status,
        'page_size': 100,
      },
    ) as Map<String, dynamic>;

    return (response['results'] as List)
        .map((row) => ReviewFlag.fromJson(row as Map<String, dynamic>))
        .toList();
  }

  /// Uphold or dismiss a flag.
  ///
  /// Dismissing restores the rating to scoring and recomputes the subject's
  /// score server-side; the note is required either way, because suppressing
  /// somebody's rating should be answerable for.
  Future<ReviewFlag> resolveFlag(
    int flagId, {
    required bool upheld,
    required String note,
  }) async {
    final response = await _client.post(
      ApiEndpoints.resolveReviewFlag(flagId),
      data: {'upheld': upheld, 'note': note},
    ) as Map<String, dynamic>;

    return ReviewFlag.fromJson(response['flag'] as Map<String, dynamic>);
  }
}
