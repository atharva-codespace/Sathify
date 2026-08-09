import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../hiring/data/models/hiring_models.dart' show WorkerSearchResult;
import '../../data/models/booking_models.dart';
import '../../data/repositories/booking_repository.dart';

final bookingRepositoryProvider =
    Provider<BookingRepository>((ref) => BookingRepository());

/// Module 5.1 — the bookable catalogue.
///
/// Not `autoDispose`: it is short, changes rarely, and is read on every entry
/// into the booking flow, so keeping it for the session avoids a round trip
/// each time on a connection that may be poor.
final serviceCategoriesProvider = FutureProvider<List<ServiceCategory>>(
  (ref) => ref.read(bookingRepositoryProvider).fetchCategories(),
);

/// Module 5.3 — workers free for a slot, ranked by Module 4.3's score.
final matchedWorkersProvider =
    FutureProvider.autoDispose.family<List<WorkerSearchResult>, BookingSlot>(
  (ref, slot) => ref.read(bookingRepositoryProvider).matchWorkers(slot),
);

/// A specific worker's open dates, for the resident's date picker.
final workerAvailabilityProvider =
    FutureProvider.autoDispose.family<List<DayAvailability>, int>(
  (ref, workerId) =>
      ref.read(bookingRepositoryProvider).fetchWorkerAvailability(workerId),
);

/// The signed-in worker's own upcoming availability (Module 5.3).
final myAvailabilityProvider =
    FutureProvider.autoDispose<List<DayAvailability>>(
  (ref) => ref.read(bookingRepositoryProvider).fetchMyAvailability(),
);

/// Every booking the caller is party to.
final bookingsProvider = FutureProvider.autoDispose<List<Booking>>(
  (ref) => ref.read(bookingRepositoryProvider).fetchBookings(),
);

// --- Module 5.5: emergency broadcast ----------------------------------------

/// The live emergency picture for whoever is signed in.
///
/// -----------------------------------------------------------------------
/// WHY THIS IS POLLED AND NOT PUSHED
/// -----------------------------------------------------------------------
/// A claimed job has to vanish from seven other dashboards within seconds, and
/// the household has to see who is coming without reopening anything. The
/// obvious mechanism is a socket, and this deployment cannot have one: the
/// backend is a single free Render web service with no Channels, no Redis and
/// no second process to run them in (`docs/free-tier-constraints.md` §7).
///
/// So the server exposes one very small endpoint and this polls it — but only
/// while something is actually happening. [EmergencyLiveRefresher] starts a
/// timer when there is live work and stops the moment there is not, so the cost
/// is bounded to the few minutes an emergency is in flight rather than being a
/// permanent background drip.
final emergencyLiveProvider = FutureProvider<EmergencyLiveState>(
  (ref) => ref.read(bookingRepositoryProvider).fetchEmergencyLive(),
);

/// Just the worker's open offers, for the cards at the top of her dashboard.
final myEmergencyOffersProvider = Provider<List<EmergencyOffer>>((ref) {
  return ref.watch(emergencyLiveProvider).maybeWhen(
        data: (state) => state.offers,
        orElse: () => const <EmergencyOffer>[],
      );
});

/// The resident's own open and just-claimed emergency requests.
final myEmergencyRequestsProvider = Provider<List<Booking>>((ref) {
  return ref.watch(emergencyLiveProvider).maybeWhen(
        data: (state) => state.requests,
        orElse: () => const <Booking>[],
      );
});

/// Module 5.5 — what raising an emergency today would cost.
final surchargeQuoteProvider = FutureProvider.autoDispose<SurchargeQuote>(
  (ref) => ref.read(bookingRepositoryProvider).fetchSurchargeQuote(),
);

/// Refreshes everything a booking action could have changed.
///
/// Availability is invalidated alongside the bookings themselves: confirming or
/// cancelling changes which slots a worker still has free, and refreshing only
/// the booking list left the date picker offering a slot that had just been
/// taken.
void invalidateBookings(WidgetRef ref) {
  ref.invalidate(bookingsProvider);
  ref.invalidate(myAvailabilityProvider);
  ref.invalidate(emergencyLiveProvider);
}
