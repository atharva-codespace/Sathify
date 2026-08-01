import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../data/models/admin_models.dart';
import '../../data/repositories/admin_repository.dart';

final adminRepositoryProvider =
    Provider<AdminRepository>((ref) => AdminRepository());

/// Which slice of the complaint queue is on screen.
///
/// Held in a provider rather than screen state so the list, the badge on the
/// dashboard and a refresh after closing one all agree on what is being shown.
class ComplaintFilters {
  const ComplaintFilters({
    this.openOnly = true,
    this.overdueOnly = false,
    this.category,
  });

  /// Defaults to open. A queue that opens on "everything ever raised" buries
  /// the handful of things actually waiting.
  final bool openOnly;
  final bool overdueOnly;
  final ComplaintCategory? category;

  ComplaintFilters copyWith({
    bool? openOnly,
    bool? overdueOnly,
    ComplaintCategory? category,
    bool clearCategory = false,
  }) {
    return ComplaintFilters(
      openOnly: openOnly ?? this.openOnly,
      overdueOnly: overdueOnly ?? this.overdueOnly,
      category: clearCategory ? null : (category ?? this.category),
    );
  }
}

final complaintFiltersProvider =
    StateProvider<ComplaintFilters>((ref) => const ComplaintFilters());

/// Module 11.3 — the queue, filtered.
///
/// Loading this triggers the server's escalation sweep, which is the free
/// tier's substitute for a scheduled job.
final complaintsProvider = FutureProvider.autoDispose<List<Complaint>>((ref) {
  final filters = ref.watch(complaintFiltersProvider);
  return ref.read(adminRepositoryProvider).fetchComplaints(
        openOnly: filters.openOnly,
        overdueOnly: filters.overdueOnly,
        category: filters.category,
      );
});

/// One complaint with its full history.
final complaintProvider = FutureProvider.autoDispose.family<Complaint, int>(
  (ref, complaintId) =>
      ref.read(adminRepositoryProvider).fetchComplaint(complaintId),
);

/// Module 11.1 — directory search text, debounced by the screen.
final workerDirectorySearchProvider = StateProvider<String>((ref) => '');
final residentDirectorySearchProvider = StateProvider<String>((ref) => '');

final workerDirectoryProvider =
    FutureProvider.autoDispose<List<DirectoryWorker>>((ref) {
  final search = ref.watch(workerDirectorySearchProvider);
  return ref.read(adminRepositoryProvider).fetchWorkers(search: search);
});

final residentDirectoryProvider =
    FutureProvider.autoDispose<List<DirectoryResident>>((ref) {
  final search = ref.watch(residentDirectorySearchProvider);
  return ref.read(adminRepositoryProvider).fetchResidents(search: search);
});

/// Module 11.4 — every panel in one call.
final adminDashboardProvider = FutureProvider.autoDispose<AdminDashboard>(
  (ref) => ref.read(adminRepositoryProvider).fetchDashboard(),
);

final unmetDemandProvider = FutureProvider.autoDispose<List<UnmetDemandEntry>>(
  (ref) => ref.read(adminRepositoryProvider).fetchUnmetDemand(),
);

/// Module 11.2 — which report is selected, and over what period.
class ReportRequest {
  const ReportRequest({this.kind = 'complaints', this.start, this.end});

  final String kind;
  final DateTime? start;
  final DateTime? end;

  ReportRequest copyWith({String? kind, DateTime? start, DateTime? end}) =>
      ReportRequest(
        kind: kind ?? this.kind,
        start: start ?? this.start,
        end: end ?? this.end,
      );
}

final reportRequestProvider =
    StateProvider<ReportRequest>((ref) => const ReportRequest());

final adminReportProvider = FutureProvider.autoDispose<AdminReport>((ref) {
  final request = ref.watch(reportRequestProvider);
  return ref.read(adminRepositoryProvider).fetchReport(
        request.kind,
        start: request.start,
        end: request.end,
      );
});

/// Refreshes everything a complaint action could have changed.
///
/// The dashboard is invalidated alongside the queue because closing a complaint
/// moves the SLA figures immediately, and a stale "3 overdue" badge next to an
/// empty queue is the kind of small wrongness that stops people trusting the
/// number.
void invalidateComplaints(WidgetRef ref) {
  ref.invalidate(complaintsProvider);
  ref.invalidate(adminDashboardProvider);
}
