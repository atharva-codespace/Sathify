import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../bookings/data/models/booking_models.dart' show Booking;
import '../../../bookings/presentation/providers/booking_provider.dart'
    show bookingsProvider;
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

/// One row of the unified request list: a hire request **or** a booking.
///
/// A sum type, hand-rolled, because Dart has no sealed union that survives a
/// `List<T>` cleanly here. Exactly one field is ever set; [when] is the only
/// intended way to read it, so nothing has to remember to check for null.
class RequestEntry {
  const RequestEntry.hire(HireRequest request)
      : hireRequest = request,
        booking = null;

  const RequestEntry.job(Booking job)
      : hireRequest = null,
        booking = job;

  final HireRequest? hireRequest;
  final Booking? booking;

  /// Most-recent-first sort key. The only thing the two shapes must agree on.
  DateTime get sortedOn =>
      hireRequest?.createdAt ?? booking?.scheduledDate ?? DateTime(1970);
}

/// Everything the caller has asked for, in one list, newest first.
///
/// -----------------------------------------------------------------------
/// WHY THIS EXISTS
/// -----------------------------------------------------------------------
/// "My requests" showed only [hireRequestsProvider] — recurring-hire proposals.
/// One-day bookings and every emergency live in a different model on a
/// different endpoint, so a household that had just raised and paid for an
/// emergency opened this screen and found nothing. This joins the two sources
/// the screen should always have had.
///
/// Both are watched rather than read, so invalidating either refreshes the
/// merged list without this provider knowing which action caused it.
final combinedRequestsProvider =
    FutureProvider.autoDispose<List<RequestEntry>>((ref) async {
  final hires = await ref.watch(hireRequestsProvider.future);
  final jobs = await ref.watch(bookingsProvider.future);

  final entries = [
    ...hires.map(RequestEntry.hire),
    ...jobs.map(RequestEntry.job),
  ]..sort((a, b) => b.sortedOn.compareTo(a.sortedOn));

  return entries;
});

/// Refreshes the merged list from both of its sources.
void invalidateCombinedRequests(WidgetRef ref) {
  ref.invalidate(hireRequestsProvider);
  ref.invalidate(bookingsProvider);
}

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
