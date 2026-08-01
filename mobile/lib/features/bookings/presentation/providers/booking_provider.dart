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

/// Refreshes everything a booking action could have changed.
///
/// Availability is invalidated alongside the bookings themselves: confirming or
/// cancelling changes which slots a worker still has free, and refreshing only
/// the booking list left the date picker offering a slot that had just been
/// taken.
void invalidateBookings(WidgetRef ref) {
  ref.invalidate(bookingsProvider);
  ref.invalidate(myAvailabilityProvider);
}
