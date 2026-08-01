import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../data/models/rating_models.dart';
import '../../data/repositories/rating_repository.dart';

final ratingRepositoryProvider =
    Provider<RatingRepository>((ref) => RatingRepository());

/// Module 9.1 — jobs the caller can still rate.
///
/// Watched by the home screens to show a prompt, so a finished job is not
/// silently forgotten.
final pendingRatingsProvider = FutureProvider.autoDispose<List<RateableJob>>(
  (ref) => ref.read(ratingRepositoryProvider).fetchPending(),
);

/// Ratings the caller gave, and ratings about them.
final myRatingsProvider = FutureProvider.autoDispose<List<Rating>>(
  (ref) => ref.read(ratingRepositoryProvider).fetchMyRatings(),
);

/// A worker's public reviews, for their profile.
final workerRatingsProvider =
    FutureProvider.autoDispose.family<List<Rating>, int>(
  (ref, workerId) =>
      ref.read(ratingRepositoryProvider).fetchWorkerRatings(workerId),
);

/// Module 9.3 — the caller's own score, always with its breakdown.
final myTrustScoreProvider = FutureProvider.autoDispose<TrustScore>(
  (ref) => ref.read(ratingRepositoryProvider).fetchMyTrustScore(),
);

/// A worker's score, for a resident asking why they rank where they do.
final workerTrustScoreProvider =
    FutureProvider.autoDispose.family<TrustScore, int>(
  (ref, workerId) =>
      ref.read(ratingRepositoryProvider).fetchWorkerTrustScore(workerId),
);

/// Module 9.3 — the audit trail.
final trustHistoryProvider = FutureProvider.autoDispose<List<TrustScoreLog>>(
  (ref) => ref.read(ratingRepositoryProvider).fetchTrustHistory(),
);

/// Module 9.4 — the administrator's flag queue.
final reviewFlagsProvider = FutureProvider.autoDispose<List<ReviewFlag>>(
  (ref) => ref.read(ratingRepositoryProvider).fetchFlags(),
);

/// Refreshes everything a rating action could have changed.
///
/// The trust score is invalidated alongside the ratings because submitting one
/// moves it immediately — and a stale score shown next to a fresh review is the
/// kind of inconsistency that makes people distrust the number.
void invalidateRatings(WidgetRef ref) {
  ref.invalidate(pendingRatingsProvider);
  ref.invalidate(myRatingsProvider);
  ref.invalidate(myTrustScoreProvider);
  ref.invalidate(trustHistoryProvider);
  ref.invalidate(reviewFlagsProvider);
}
