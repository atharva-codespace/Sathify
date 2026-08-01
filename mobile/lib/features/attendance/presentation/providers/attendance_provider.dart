import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../data/models/attendance_models.dart';
import '../../data/repositories/attendance_repository.dart';

/// Held for the session rather than autoDispose: the repository owns the SQLite
/// queue handle, and reopening the database on every scan would be both slow
/// and a good way to lose a write mid-flight.
final attendanceRepositoryProvider =
    Provider<AttendanceRepository>((ref) => AttendanceRepository());

/// Module 7.1 — the worker's own QR credential.
final myGatePassProvider = FutureProvider.autoDispose<GatePass>(
  (ref) => ref.read(attendanceRepositoryProvider).fetchMyGatePass(),
);

/// Module 7.2/7.4 — the day's roster, refreshed from the server and cached.
///
/// Falls back to whatever is cached when the fetch fails. A guard opening the
/// app at a gate with no signal still needs yesterday's answer more than they
/// need an error.
final gateRosterProvider = FutureProvider.autoDispose<List<RosterEntry>>(
  (ref) async {
    final repository = ref.read(attendanceRepositoryProvider);
    try {
      return await repository.refreshRoster();
    } on Exception {
      return repository.cachedRoster();
    }
  },
);

/// How many decisions are still waiting to reach the server (Module 7.4).
///
/// Surfaced prominently in the guard's UI: a number that keeps climbing is the
/// only visible sign that a day's attendance is not landing.
final pendingSyncCountProvider = FutureProvider.autoDispose<int>(
  (ref) => ref.read(attendanceRepositoryProvider).pendingCount(),
);

/// Module 7.6 — today's gate log.
final gateLogProvider = FutureProvider.autoDispose<List<AttendanceEvent>>(
  (ref) =>
      ref.read(attendanceRepositoryProvider).fetchEvents(day: DateTime.now()),
);

/// Entries where a face check did not clear and a guard has yet to decide.
final pendingReviewsProvider =
    FutureProvider.autoDispose<List<AttendanceEvent>>(
  (ref) =>
      ref.read(attendanceRepositoryProvider).fetchEvents(needsReviewOnly: true),
);

/// Module 13.1 — check-ins the worker's device has not managed to push yet.
///
/// Surfaced on the check-in screen rather than kept quiet: a worker is relying
/// on the tap having landed, and a queue that silently never drains is worse
/// than no queue at all.
final pendingCheckInCountProvider = FutureProvider.autoDispose<int>(
  (ref) => ref.read(attendanceRepositoryProvider).pendingCheckInCount(),
);

/// Refreshes everything a gate decision could have changed.
void invalidateAttendance(WidgetRef ref) {
  ref.invalidate(gateLogProvider);
  ref.invalidate(pendingReviewsProvider);
  ref.invalidate(pendingSyncCountProvider);
  ref.invalidate(pendingCheckInCountProvider);
}
