import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../../core/errors/api_exception.dart';
import '../../../../shared/design_system.dart';
import '../../data/models/attendance_models.dart';
import '../providers/attendance_provider.dart';

/// Modules 7.5 and 7.6 — today's gate log, and the manual fallback.
///
/// Three things live together here because a guard uses them together: what has
/// happened today, what still needs their decision (a face check that did not
/// clear), and the ability to log someone by hand when scanning simply will not
/// work. Splitting them across screens would mean hunting during the morning
/// rush.
class GateLogScreen extends ConsumerStatefulWidget {
  const GateLogScreen({super.key});

  @override
  ConsumerState<GateLogScreen> createState() => _GateLogScreenState();
}

class _GateLogScreenState extends ConsumerState<GateLogScreen>
    with SingleTickerProviderStateMixin {
  late final TabController _tabs = TabController(length: 3, vsync: this);

  @override
  void dispose() {
    _tabs.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final pendingReviews = ref.watch(pendingReviewsProvider);
    final reviewCount = pendingReviews.valueOrNull?.length ?? 0;

    return Scaffold(
      appBar: AppBar(
        title: const Text('Gate'),
        bottom: TabBar(
          controller: _tabs,
          tabs: [
            const Tab(text: 'Today'),
            Tab(
              text: reviewCount > 0 ? 'To decide ($reviewCount)' : 'To decide',
            ),
            const Tab(text: 'Log by hand'),
          ],
        ),
      ),
      body: TabBarView(
        controller: _tabs,
        children: const [_TodayTab(), _ReviewTab(), _ManualTab()],
      ),
    );
  }
}

class _TodayTab extends ConsumerWidget {
  const _TodayTab();

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final log = ref.watch(gateLogProvider);

    return log.when(
      loading: () => const AppSkeletonList(),
      error: (error, _) => _Error(
        message:
            error is ApiException ? error.message : 'Could not load the log.',
        onRetry: () => ref.invalidate(gateLogProvider),
      ),
      data: (events) {
        if (events.isEmpty) {
          return const _Empty(
            icon: Icons.meeting_room_outlined,
            title: 'Nothing yet today',
            subtitle: 'Entries appear here as you scan them.',
          );
        }
        return RefreshIndicator(
          onRefresh: () async => ref.invalidate(gateLogProvider),
          child: ListView.builder(
            padding: const EdgeInsets.symmetric(vertical: 8),
            itemCount: events.length,
            itemBuilder: (context, index) => _EventTile(event: events[index]),
          ),
        );
      },
    );
  }
}

/// Module 7.3 — face checks that did not clear, waiting on the guard.
class _ReviewTab extends ConsumerWidget {
  const _ReviewTab();

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final reviews = ref.watch(pendingReviewsProvider);

    return reviews.when(
      loading: () => const AppSkeletonList(),
      error: (error, _) => _Error(
        message:
            error is ApiException ? error.message : 'Could not load reviews.',
        onRetry: () => ref.invalidate(pendingReviewsProvider),
      ),
      data: (events) {
        if (events.isEmpty) {
          return const _Empty(
            icon: Icons.done_all,
            title: 'Nothing to decide',
            subtitle: 'Entries needing your judgement appear here.',
          );
        }
        return ListView.builder(
          padding: const EdgeInsets.symmetric(vertical: 8),
          itemCount: events.length,
          itemBuilder: (context, index) => _ReviewCard(event: events[index]),
        );
      },
    );
  }
}

class _ReviewCard extends ConsumerStatefulWidget {
  const _ReviewCard({required this.event});

  final AttendanceEvent event;

  @override
  ConsumerState<_ReviewCard> createState() => _ReviewCardState();
}

class _ReviewCardState extends ConsumerState<_ReviewCard> {
  bool _isBusy = false;

  Future<void> _resolve(bool allow) async {
    final reason = await showDialog<String>(
      context: context,
      builder: (_) => _ReasonDialog(
        title: allow ? 'Allow this entry?' : 'Refuse this entry?',
        hint: allow
            ? 'e.g. I know her, the light here is poor'
            : 'e.g. not the person on the card',
      ),
    );
    if (reason == null || reason.trim().isEmpty) return;

    setState(() => _isBusy = true);
    try {
      await ref.read(attendanceRepositoryProvider).resolveEvent(
            widget.event.id,
            allow: allow,
            reason: reason.trim(),
          );
      if (!mounted) return;
      invalidateAttendance(ref);
    } on ApiException catch (error) {
      if (!mounted) return;
      setState(() => _isBusy = false);
      ScaffoldMessenger.of(context)
          .showSnackBar(SnackBar(content: Text(error.message)));
    }
  }

  @override
  Widget build(BuildContext context) {
    final event = widget.event;

    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              event.workerName,
              style: Theme.of(context)
                  .textTheme
                  .titleMedium
                  ?.copyWith(fontWeight: FontWeight.w600),
            ),
            const SizedBox(height: 8),
            // The distinction matters: "we could not check" is not "we checked
            // and it failed", and the guard should not be told otherwise.
            Text(
              event.faceChecked && event.faceMatchScore != null
                  ? 'Face check scored '
                      '${(event.faceMatchScore! * 100).round()}% — below the level '
                      'we accept automatically.'
                  : 'The face check could not run. Please verify visually.',
              style: const TextStyle(color: AppColors.textSecondary),
            ),
            if (event.decisionReason.isNotEmpty) ...[
              const SizedBox(height: 6),
              Text(event.decisionReason, style: const TextStyle(fontSize: 13)),
            ],
            const SizedBox(height: 14),
            if (_isBusy)
              const Center(child: CircularProgressIndicator())
            else
              Row(
                children: [
                  Expanded(
                    child: OutlinedButton.icon(
                      onPressed: () => _resolve(false),
                      icon: const Icon(Icons.close),
                      label: const Text('Refuse'),
                      style: OutlinedButton.styleFrom(
                        foregroundColor: AppColors.danger,
                        minimumSize: const Size.fromHeight(48),
                      ),
                    ),
                  ),
                  const SizedBox(width: 12),
                  Expanded(
                    child: ElevatedButton.icon(
                      onPressed: () => _resolve(true),
                      icon: const Icon(Icons.check),
                      label: const Text('Allow'),
                      style: ElevatedButton.styleFrom(
                        minimumSize: const Size.fromHeight(48),
                      ),
                    ),
                  ),
                ],
              ),
          ],
        ),
      ),
    );
  }
}

/// Module 7.5 — log an entry against the day's list when scanning fails.
class _ManualTab extends ConsumerWidget {
  const _ManualTab();

  Future<void> _log(
    BuildContext context,
    WidgetRef ref,
    RosterEntry entry,
    GateDirection direction,
  ) async {
    final repository = ref.read(attendanceRepositoryProvider);
    final sent = await repository.recordDecision(
      AttendanceEventDraft(
        id: repository.newEventId(),
        workerId: entry.workerId,
        occurredAt: DateTime.now(),
        direction: direction,
        // Provenance is recorded honestly: this was not a scan.
        method: VerificationMethod.manual,
        decision: GateDecision.allowed,
        decisionReason: 'Logged by hand at the gate',
      ),
    );

    if (!context.mounted) return;
    invalidateAttendance(ref);
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(
        content: Text(
          sent
              ? '${entry.workerName} logged.'
              : '${entry.workerName} saved on this device.',
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final roster = ref.watch(gateRosterProvider);

    return roster.when(
      loading: () => const AppSkeletonList(),
      error: (error, _) => _Error(
        message:
            error is ApiException ? error.message : 'Could not load the list.',
        onRetry: () => ref.invalidate(gateRosterProvider),
      ),
      data: (entries) {
        if (entries.isEmpty) {
          return const _Empty(
            icon: Icons.people_outline,
            title: 'Nobody expected today',
            subtitle:
                'The day’s list is empty, or has not been downloaded yet.',
          );
        }
        return ListView.builder(
          padding: const EdgeInsets.symmetric(vertical: 8),
          itemCount: entries.length,
          itemBuilder: (context, index) {
            final entry = entries[index];
            return Card(
              child: ListTile(
                contentPadding:
                    const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
                title: Text(
                  entry.workerName,
                  style: const TextStyle(fontWeight: FontWeight.w600),
                ),
                subtitle: Text(
                  entry.visits.isEmpty
                      ? 'No visits listed'
                      : entry.visits
                          .map((v) => '${v.timeLabel} · ${v.flatLabel}')
                          .join('\n'),
                ),
                isThreeLine: entry.visits.length > 1,
                trailing: Wrap(
                  spacing: 4,
                  children: [
                    IconButton(
                      tooltip: 'Coming in',
                      icon: const Icon(Icons.login, color: AppColors.success),
                      onPressed: () =>
                          _log(context, ref, entry, GateDirection.entry),
                    ),
                    IconButton(
                      tooltip: 'Going out',
                      icon: const Icon(Icons.logout, color: AppColors.info),
                      onPressed: () =>
                          _log(context, ref, entry, GateDirection.exit),
                    ),
                  ],
                ),
              ),
            );
          },
        );
      },
    );
  }
}

class _EventTile extends StatelessWidget {
  const _EventTile({required this.event});

  final AttendanceEvent event;

  Color get _colour {
    switch (event.decision) {
      case GateDecision.allowed:
        return AppColors.success;
      case GateDecision.denied:
        return AppColors.danger;
      case GateDecision.pendingReview:
        return AppColors.warning;
    }
  }

  @override
  Widget build(BuildContext context) {
    final local = event.occurredAt.toLocal();
    final time = '${local.hour.toString().padLeft(2, '0')}:'
        '${local.minute.toString().padLeft(2, '0')}';

    return Card(
      child: ListTile(
        leading: CircleAvatar(
          backgroundColor: _colour.withValues(alpha: 0.14),
          child: Icon(
            event.direction == GateDirection.entry ? Icons.login : Icons.logout,
            color: _colour,
          ),
        ),
        title: Text(
          event.workerName,
          style: const TextStyle(fontWeight: FontWeight.w600),
        ),
        subtitle: Text(
          [
            '$time · ${event.decision.label}',
            event.method.label,
            if (!event.wasExpected) 'not on today’s list',
            if (event.wasOffline) 'synced later',
          ].join(' · '),
        ),
        trailing: event.wasOverridden
            ? const Tooltip(
                message: 'Decided by a guard after a face check',
                child: Icon(Icons.how_to_reg, size: 20),
              )
            : null,
      ),
    );
  }
}

class _ReasonDialog extends StatefulWidget {
  const _ReasonDialog({required this.title, required this.hint});

  final String title;
  final String hint;

  @override
  State<_ReasonDialog> createState() => _ReasonDialogState();
}

class _ReasonDialogState extends State<_ReasonDialog> {
  final _controller = TextEditingController();

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return AlertDialog(
      title: Text(widget.title),
      content: Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const Text(
            'Your reason is recorded against this entry.',
            style: TextStyle(fontSize: 13, color: AppColors.textSecondary),
          ),
          const SizedBox(height: 12),
          TextField(
            controller: _controller,
            autofocus: true,
            maxLines: 2,
            decoration: InputDecoration(hintText: widget.hint),
          ),
        ],
      ),
      actions: [
        TextButton(
          onPressed: () => Navigator.of(context).pop(),
          child: const Text('Cancel'),
        ),
        FilledButton(
          onPressed: () => Navigator.of(context).pop(_controller.text),
          child: const Text('Confirm'),
        ),
      ],
    );
  }
}

class _Empty extends StatelessWidget {
  const _Empty({
    required this.icon,
    required this.title,
    required this.subtitle,
  });

  final IconData icon;
  final String title;
  final String subtitle;

  @override
  Widget build(BuildContext context) {
    // Kept as a local wrapper rather than replaced at every call site: this
    // screen raises it from three different tabs, and delegating the body is a
    // one-line change that gets all three onto the shared component.
    return AppEmptyState(icon: icon, title: title, message: subtitle);
  }
}

class _Error extends StatelessWidget {
  const _Error({required this.message, required this.onRetry});

  final String message;
  final VoidCallback onRetry;

  @override
  Widget build(BuildContext context) {
    return AppErrorState(message: message, onRetry: onRetry);
  }
}
