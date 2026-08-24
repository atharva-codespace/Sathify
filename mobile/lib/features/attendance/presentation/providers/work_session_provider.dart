import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:geolocator/geolocator.dart';

import '../../data/models/work_session_models.dart';
import '../../data/repositories/work_session_repository.dart';

/// Module 7.7 — providers for the worker's day and the resident's view of it.

final workSessionRepositoryProvider = Provider<WorkSessionRepository>(
  (ref) => WorkSessionRepository(),
);

/// Which day the worker or resident is looking at. Today, unless they scroll.
final sessionDayProvider = StateProvider<DateTime>((ref) {
  final now = DateTime.now();
  return DateTime(now.year, now.month, now.day);
});

/// The worker's Today screen.
final todayBoardProvider = FutureProvider.autoDispose<TodayBoard>((ref) {
  final day = ref.watch(sessionDayProvider);
  return ref.read(workSessionRepositoryProvider).fetchToday(date: day);
});

/// Sessions over a range — the month view, and the resident's history.
final sessionHistoryProvider =
    FutureProvider.autoDispose.family<List<WorkSession>, SessionRange>(
  (ref, range) => ref.read(workSessionRepositoryProvider).fetchSessions(
        from: range.from,
        to: range.to,
        engagementId: range.engagementId,
      ),
);

/// A month of sessions, keyed so two screens asking for the same month share
/// one request.
class SessionRange {
  const SessionRange({required this.from, required this.to, this.engagementId});

  factory SessionRange.month(DateTime anchor, {int? engagementId}) =>
      SessionRange(
        from: DateTime(anchor.year, anchor.month),
        to: DateTime(anchor.year, anchor.month + 1, 0),
        engagementId: engagementId,
      );

  final DateTime from;
  final DateTime to;
  final int? engagementId;

  @override
  bool operator ==(Object other) =>
      other is SessionRange &&
      other.from == from &&
      other.to == to &&
      other.engagementId == engagementId;

  @override
  int get hashCode => Object.hash(from, to, engagementId);
}

/// The resident's bills, newest first.
final invoicesProvider = FutureProvider.autoDispose<List<Invoice>>(
  (ref) => ref.read(workSessionRepositoryProvider).fetchInvoices(),
);

final invoiceProvider = FutureProvider.autoDispose.family<Invoice, int>(
  (ref, id) => ref.read(workSessionRepositoryProvider).fetchInvoice(id),
);

/// A best-effort location fix for check-in.
///
/// Returns `null` rather than throwing when permission is refused, the service
/// is off, or the fix times out. Every one of those is a reason to open the
/// session at a lower capture tier — never a reason to refuse to start her day.
/// The server flags it and a human confirms; she works either way.
Future<({double? latitude, double? longitude})> bestEffortFix() async {
  try {
    if (!await Geolocator.isLocationServiceEnabled()) {
      return (latitude: null, longitude: null);
    }
    var permission = await Geolocator.checkPermission();
    if (permission == LocationPermission.denied) {
      permission = await Geolocator.requestPermission();
    }
    if (permission == LocationPermission.denied ||
        permission == LocationPermission.deniedForever) {
      return (latitude: null, longitude: null);
    }
    final position = await Geolocator.getCurrentPosition(
      locationSettings: const LocationSettings(
        accuracy: LocationAccuracy.medium,
        // A first fix indoors can take a long time. Waiting longer than this
        // makes her stand in a stairwell holding the phone up; the fallback
        // tier is a better outcome than a slow one.
        timeLimit: Duration(seconds: 8),
      ),
    );
    return (latitude: position.latitude, longitude: position.longitude);
  } on Exception {
    return (latitude: null, longitude: null);
  }
}

/// Refreshes everything a session change could have altered.
void invalidateSessions(WidgetRef ref) {
  ref.invalidate(todayBoardProvider);
  ref.invalidate(sessionHistoryProvider);
  ref.invalidate(invoicesProvider);
  ref.invalidate(invoiceProvider);
}
