import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../data/models/schedule_models.dart';
import '../../data/repositories/schedule_repository.dart';

final scheduleRepositoryProvider =
    Provider<ScheduleRepository>((ref) => ScheduleRepository());

/// Module 6.1 — the caller's day. The home screen for both workers and
/// residents once they have anything scheduled.
final todayScheduleProvider = FutureProvider.autoDispose<List<ScheduleItem>>(
  (ref) => ref.read(scheduleRepositoryProvider).fetchToday(),
);

/// The same over a range. Keyed on the range so switching weeks refetches
/// without the screen orchestrating it.
final agendaProvider =
    FutureProvider.autoDispose.family<List<ScheduleItem>, AgendaRange>(
  (ref, range) => ref.read(scheduleRepositoryProvider).fetchAgenda(range),
);

/// Module 6.2 — expectations for one engagement.
final taskTimingProvider = FutureProvider.autoDispose.family<TaskTiming, int>(
  (ref, engagementId) =>
      ref.read(scheduleRepositoryProvider).fetchTaskTiming(engagementId),
);

/// Module 6.4 — reminders ready to deliver.
final dueRemindersProvider = FutureProvider.autoDispose<List<Reminder>>(
  (ref) => ref.read(scheduleRepositoryProvider).fetchDueReminders(),
);

/// Module 6.5 — leave the caller can see, soonest first.
final leaveRequestsProvider = FutureProvider.autoDispose<List<LeaveRequest>>(
  (ref) => ref.read(scheduleRepositoryProvider).fetchLeaveRequests(),
);

/// The days still waiting on the household's answer. Drives the prompt on the
/// resident's home screen — the whole workflow depends on this being answered
/// quickly, so it is surfaced rather than buried in a list.
final leaveAwaitingResponseProvider =
    FutureProvider.autoDispose<List<LeaveRequest>>((ref) async {
  final all = await ref.watch(leaveRequestsProvider.future);
  return all.where((leave) => leave.needsResidentResponse).toList();
});

/// Workers free to cover one particular day.
final replacementCandidatesProvider =
    FutureProvider.autoDispose.family<List<ReplacementCandidate>, int>(
  (ref, leaveId) =>
      ref.read(scheduleRepositoryProvider).fetchReplacementCandidates(leaveId),
);

/// Refreshes every schedule view.
///
/// Called after anything that changes what is expected — confirming a booking,
/// pausing an engagement, editing timing, taking or covering a day of leave.
/// The schedule is derived on the server, so a stale client view is the only
/// way it can be wrong.
void invalidateSchedule(WidgetRef ref) {
  ref.invalidate(todayScheduleProvider);
  ref.invalidate(agendaProvider);
  ref.invalidate(leaveRequestsProvider);
  ref.invalidate(leaveAwaitingResponseProvider);
}
