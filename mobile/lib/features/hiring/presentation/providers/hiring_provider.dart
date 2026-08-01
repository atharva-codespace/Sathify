import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../data/models/hiring_models.dart';
import '../../data/repositories/hiring_repository.dart';

final hiringRepositoryProvider =
    Provider<HiringRepository>((ref) => HiringRepository());

/// The filters currently applied on the search screen.
///
/// Held separately from the results so that changing a filter re-runs the
/// query without the screen having to orchestrate it — [workerSearchProvider]
/// watches this and refetches on its own.
final workerFiltersProvider = StateProvider.autoDispose<WorkerSearchFilters>(
  (ref) => const WorkerSearchFilters(),
);

/// Module 4.1 — ranked search results for the current filters.
final workerSearchProvider =
    FutureProvider.autoDispose<List<WorkerSearchResult>>(
  (ref) {
    final filters = ref.watch(workerFiltersProvider);
    return ref.read(hiringRepositoryProvider).searchWorkers(filters);
  },
);

/// Module 4.2 — one worker's full profile.
final workerDetailProvider =
    FutureProvider.autoDispose.family<WorkerDetail, int>(
  (ref, workerId) => ref.read(hiringRepositoryProvider).fetchWorker(workerId),
);

/// Every hire request the caller is party to. The server decides whether that
/// means "sent by me" or "addressed to me" from the caller's role.
final hireRequestsProvider = FutureProvider.autoDispose<List<HireRequest>>(
  (ref) => ref.read(hiringRepositoryProvider).fetchHireRequests(),
);

/// Just the open ones — what a worker actually needs to act on.
final pendingHireRequestsProvider =
    FutureProvider.autoDispose<List<HireRequest>>(
  (ref) =>
      ref.read(hiringRepositoryProvider).fetchHireRequests(status: 'pending'),
);

/// Module 4.5 — engagements the caller is party to.
final engagementsProvider = FutureProvider.autoDispose<List<Engagement>>(
  (ref) => ref.read(hiringRepositoryProvider).fetchEngagements(),
);

/// Refreshes everything a hire or lifecycle action could have changed.
///
/// Accepting a request creates an engagement and empties an inbox row, so the
/// two lists are never invalidated independently — doing so left one screen
/// showing a request that had already become an engagement.
///
/// Takes a [WidgetRef] rather than a [Ref] because every caller is a widget
/// reacting to a button press; the two are distinct types in Riverpod.
void invalidateHiring(WidgetRef ref) {
  ref.invalidate(hireRequestsProvider);
  ref.invalidate(pendingHireRequestsProvider);
  ref.invalidate(engagementsProvider);
}
