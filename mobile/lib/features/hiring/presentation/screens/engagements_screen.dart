import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../../core/errors/api_exception.dart';
import '../../../../shared/design_system.dart';
import '../../../auth/data/models/user_model.dart';
import '../../../auth/presentation/providers/auth_provider.dart';
import '../../data/models/hiring_models.dart';
import '../providers/hiring_provider.dart';

/// Module 4.5 — standing engagements and their lifecycle.
///
/// Serves both sides: the viewer's role switches which name is shown, but the
/// available actions are the same, because either party may pause or end an
/// arrangement they are part of.
class EngagementsScreen extends ConsumerWidget {
  const EngagementsScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final engagements = ref.watch(engagementsProvider);
    final isWorker = ref.watch(authProvider).user?.role == UserRole.worker;

    return Scaffold(
      appBar: AppBar(title: Text(isWorker ? 'My work' : 'My hires')),
      body: engagements.when(
        loading: () => const AppSkeletonList(count: 3),
        error: (error, _) => AppErrorState(
          message: error is ApiException
              ? error.message
              : 'Could not load engagements.',
          onRetry: () => ref.invalidate(engagementsProvider),
        ),
        data: (items) {
          if (items.isEmpty) {
            return AppEmptyState(
              icon: Icons.handshake_outlined,
              title: 'Nothing running yet',
              message: isWorker
                  ? 'Accepted hire requests will appear here.'
                  : 'Once a worker accepts your request, it appears here.',
            );
          }

          return RefreshIndicator(
            onRefresh: () async => ref.invalidate(engagementsProvider),
            child: ListView.builder(
              physics: const AlwaysScrollableScrollPhysics(),
              padding: const EdgeInsets.fromLTRB(
                AppSpacing.gutter,
                AppSpacing.sm,
                AppSpacing.gutter,
                AppSpacing.xxl,
              ),
              itemCount: items.length,
              itemBuilder: (context, index) => AppFadeIn(
                index: index,
                child: _EngagementCard(
                  engagement: items[index],
                  isWorker: isWorker,
                ),
              ),
            ),
          );
        },
      ),
    );
  }
}

class _EngagementCard extends ConsumerStatefulWidget {
  const _EngagementCard({required this.engagement, required this.isWorker});

  final Engagement engagement;
  final bool isWorker;

  @override
  ConsumerState<_EngagementCard> createState() => _EngagementCardState();
}

class _EngagementCardState extends ConsumerState<_EngagementCard> {
  bool _isBusy = false;

  Future<void> _run(Future<void> Function() action, String message) async {
    setState(() => _isBusy = true);
    try {
      await action();
      if (!mounted) return;
      invalidateHiring(ref);
      ScaffoldMessenger.of(context)
          .showSnackBar(SnackBar(content: Text(message)));
    } on ApiException catch (error) {
      if (!mounted) return;
      setState(() => _isBusy = false);
      ScaffoldMessenger.of(context)
          .showSnackBar(SnackBar(content: Text(error.message)));
    }
  }

  Future<void> _pause() => _run(
        () => ref
            .read(hiringRepositoryProvider)
            .pauseEngagement(widget.engagement.id),
        'Paused. Resume it whenever you need to.',
      );

  Future<void> _resume() => _run(
        () => ref
            .read(hiringRepositoryProvider)
            .resumeEngagement(widget.engagement.id),
        'Resumed.',
      );

  /// Module 4.6 — the ordinary way to end an arrangement.
  ///
  /// Ten days' notice, during which nothing changes: the visits still happen,
  /// the gate still admits the worker, and every one of those days is paid.
  /// This is the button people should reach for, which is why it is the plain
  /// one and `_terminate` below is not.
  Future<void> _giveNotice() async {
    final reason = await showDialog<EngagementEndReason>(
      context: context,
      builder: (_) => _EndReasonDialog(isWorker: widget.isWorker),
    );
    if (reason == null) return;

    final lastDay = NoticePeriod.earliestLastDay(DateTime.now());
    await _run(
      () => ref
          .read(hiringRepositoryProvider)
          .giveNotice(widget.engagement.id, reason: reason),
      'Notice given. The last working day is ${_formatDate(lastDay)}.',
    );
  }

  Future<void> _withdrawNotice() => _run(
        () => ref
            .read(hiringRepositoryProvider)
            .withdrawNotice(widget.engagement.id),
        'Notice withdrawn. This arrangement continues.',
      );

  /// The exceptional path — abuse, safety, a mutual decision to stop today.
  ///
  /// Kept behind a confirmation *and* visually quieter than notice, because a
  /// worker who is ended without notice loses ten days of income they were
  /// entitled to work. That should be a deliberate act, not the nearest button.
  Future<void> _terminate() async {
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (dialogContext) => AlertDialog(
        title: const Text('End today, without notice?'),
        content: Text(
          widget.isWorker
              ? 'This ends the arrangement immediately. Giving 10 days\' notice '
                  'instead means you keep working — and being paid — until then.'
              : 'This ends the arrangement immediately, and '
                  '${widget.engagement.workerName} loses the 10 days of work '
                  'they would otherwise have been paid for. Use notice unless '
                  'there is a reason not to.',
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(dialogContext).pop(false),
            child: const Text('Cancel'),
          ),
          TextButton(
            onPressed: () => Navigator.of(dialogContext).pop(true),
            style: TextButton.styleFrom(foregroundColor: AppColors.danger),
            child: const Text('End today'),
          ),
        ],
      ),
    );
    if (confirmed != true || !mounted) return;

    final reason = await showDialog<EngagementEndReason>(
      context: context,
      builder: (_) => _EndReasonDialog(isWorker: widget.isWorker),
    );
    if (reason == null) return;

    await _run(
      () => ref
          .read(hiringRepositoryProvider)
          .terminateEngagement(widget.engagement.id, reason: reason),
      'Engagement ended.',
    );
  }

  Color get _statusColour {
    switch (widget.engagement.status) {
      case EngagementStatus.active:
        return AppColors.success;
      case EngagementStatus.paused:
        return AppColors.warning;
      case EngagementStatus.terminated:
        return AppColors.textSecondary;
    }
  }

  @override
  Widget build(BuildContext context) {
    final engagement = widget.engagement;
    final theme = Theme.of(context);
    final counterparty =
        widget.isWorker ? engagement.residentName : engagement.workerName;

    return AppCard(
      margin: const EdgeInsets.only(bottom: AppSpacing.sm),
      child: Padding(
        padding: EdgeInsets.zero,
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                AppAvatar(
                  name: counterparty,
                  // A worker looking at this sees the resident, whose photo the
                  // engagement does not carry — only the worker's does.
                  imageUrl: widget.isWorker ? null : engagement.workerPhotoUrl,
                  seed: engagement.id,
                  size: 44,
                ),
                const SizedBox(width: 12),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                        counterparty.isEmpty ? 'Unknown' : counterparty,
                        style: theme.textTheme.titleMedium
                            ?.copyWith(fontWeight: FontWeight.w600),
                      ),
                      Text(
                        widget.isWorker
                            ? engagement.residentFlat
                            : (engagement.serviceType?.name ?? ''),
                        style: theme.textTheme.bodySmall,
                      ),
                    ],
                  ),
                ),
                Container(
                  padding:
                      const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
                  decoration: BoxDecoration(
                    color: _statusColour.withValues(alpha: 0.12),
                    borderRadius: BorderRadius.circular(20),
                  ),
                  child: Text(
                    engagement.status.label,
                    style: TextStyle(
                      color: _statusColour,
                      fontSize: 12,
                      fontWeight: FontWeight.w700,
                    ),
                  ),
                ),
              ],
            ),
            const SizedBox(height: 12),
            _DetailRow(
              icon: Icons.event_repeat,
              text: engagement.terms.scheduleLabel,
            ),
            _DetailRow(
              icon: Icons.currency_rupee,
              text: '₹${engagement.terms.monthlyRate} per month',
            ),
            if (!widget.isWorker && engagement.workerPhone.isNotEmpty)
              _DetailRow(
                icon: Icons.phone_outlined,
                text: engagement.workerPhone,
              ),
            if (engagement.isPaused && engagement.pauseReason.isNotEmpty)
              _DetailRow(
                icon: Icons.pause_circle_outline,
                text: engagement.pauseReason,
              ),
            if (engagement.status == EngagementStatus.terminated &&
                engagement.endNote.isNotEmpty)
              _DetailRow(icon: Icons.info_outline, text: engagement.endNote),
            // Module 4.6 — while notice runs, the countdown is the headline.
            // Both sides are planning around it: the household is looking for
            // somebody, the worker is counting the days they will be paid for.
            if (engagement.isServingNotice &&
                engagement.lastWorkingDay != null) ...[
              const SizedBox(height: 12),
              Container(
                width: double.infinity,
                padding: const EdgeInsets.all(12),
                decoration: const BoxDecoration(
                  color: AppColors.warningSoft,
                  borderRadius: AppRadius.chip,
                ),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      'Finishing on '
                      '${_formatDate(engagement.lastWorkingDay!)}',
                      style: theme.textTheme.titleSmall,
                    ),
                    const SizedBox(height: 2),
                    Text(
                      NoticePeriod.summary(
                        visitsRemaining: engagement.visitsRemaining,
                      ),
                      style: theme.textTheme.bodySmall,
                    ),
                  ],
                ),
              ),
            ],
            if (engagement.isLive) ...[
              const SizedBox(height: 14),
              if (_isBusy)
                const Center(child: CircularProgressIndicator())
              else if (engagement.isServingNotice)
                OutlinedButton.icon(
                  onPressed: _withdrawNotice,
                  icon: const Icon(Icons.undo_rounded),
                  label: const Text('Withdraw notice'),
                  style: OutlinedButton.styleFrom(
                    minimumSize: const Size.fromHeight(48),
                  ),
                )
              else ...[
                Row(
                  children: [
                    Expanded(
                      child: OutlinedButton.icon(
                        onPressed: engagement.isPaused ? _resume : _pause,
                        icon: Icon(
                          engagement.isPaused ? Icons.play_arrow : Icons.pause,
                        ),
                        label: Text(engagement.isPaused ? 'Resume' : 'Pause'),
                        style: OutlinedButton.styleFrom(
                          minimumSize: const Size.fromHeight(48),
                        ),
                      ),
                    ),
                    const SizedBox(width: 12),
                    Expanded(
                      child: OutlinedButton.icon(
                        onPressed: _giveNotice,
                        icon: const Icon(Icons.event_available_outlined),
                        label: const Text('Give notice'),
                        style: OutlinedButton.styleFrom(
                          minimumSize: const Size.fromHeight(48),
                        ),
                      ),
                    ),
                  ],
                ),
                // Quieter than notice on purpose: ending without notice costs
                // the worker ten days of income they were entitled to work.
                TextButton(
                  onPressed: _terminate,
                  style: TextButton.styleFrom(
                    foregroundColor: AppColors.textTertiary,
                  ),
                  child: const Text('End today, without notice'),
                ),
              ],
            ],
          ],
        ),
      ),
    );
  }
}

class _DetailRow extends StatelessWidget {
  const _DetailRow({required this.icon, required this.text});

  final IconData icon;
  final String text;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 3),
      child: Row(
        children: [
          Icon(icon, size: 18, color: AppColors.textSecondary),
          const SizedBox(width: 10),
          Expanded(child: Text(text, style: const TextStyle(fontSize: 14))),
        ],
      ),
    );
  }
}

class _EndReasonDialog extends StatelessWidget {
  const _EndReasonDialog({required this.isWorker});

  final bool isWorker;

  /// Each side is offered the reasons that make sense from where they stand.
  /// `adminEnded` is never offered here — it belongs to Module 11.
  List<EngagementEndReason> get _reasons => isWorker
      ? const [
          EngagementEndReason.workerEnded,
          EngagementEndReason.workerLeftSociety,
        ]
      : const [
          EngagementEndReason.residentEnded,
          EngagementEndReason.residentMovedOut,
        ];

  @override
  Widget build(BuildContext context) {
    return AlertDialog(
      title: const Text('End this engagement?'),
      content: Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const Text(
            'This cannot be undone. You would need to send a new hire '
            'request to start again.',
          ),
          const SizedBox(height: 16),
          const Text(
            'Why are you ending it?',
            style: TextStyle(fontWeight: FontWeight.w600, fontSize: 13),
          ),
          const SizedBox(height: 8),
          // Bordered and iconed rather than bare ListTiles: sitting next to a
          // properly-styled "Cancel" button, plain text rows read as inert
          // description, not as the two actions that actually end this — which
          // is exactly what made "Cancel" look like the only option here.
          ..._reasons.map(
            (reason) => Padding(
              padding: const EdgeInsets.only(bottom: 8),
              child: Material(
                color: AppColors.danger.withValues(alpha: 0.06),
                borderRadius: BorderRadius.circular(10),
                child: InkWell(
                  borderRadius: BorderRadius.circular(10),
                  onTap: () => Navigator.of(context).pop(reason),
                  child: Container(
                    decoration: BoxDecoration(
                      borderRadius: BorderRadius.circular(10),
                      border: Border.all(
                        color: AppColors.danger.withValues(alpha: 0.3),
                      ),
                    ),
                    padding: const EdgeInsets.symmetric(
                      horizontal: 12,
                      vertical: 10,
                    ),
                    child: Row(
                      children: [
                        const Icon(
                          Icons.stop_circle_outlined,
                          size: 18,
                          color: AppColors.danger,
                        ),
                        const SizedBox(width: 10),
                        Expanded(
                          child: Text(
                            reason.label,
                            style: const TextStyle(
                              fontWeight: FontWeight.w600,
                              color: AppColors.danger,
                            ),
                          ),
                        ),
                        const Icon(
                          Icons.chevron_right,
                          size: 18,
                          color: AppColors.danger,
                        ),
                      ],
                    ),
                  ),
                ),
              ),
            ),
          ),
        ],
      ),
      actions: [
        TextButton(
          onPressed: () => Navigator.of(context).pop(),
          child: const Text('Cancel'),
        ),
      ],
    );
  }
}

/// `Tue 18 Aug` — short enough for a chip, unambiguous enough for a date
/// somebody is planning around.
String _formatDate(DateTime date) {
  const months = [
    'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
    'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec',
  ];
  const days = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
  return '${days[date.weekday - 1]} ${date.day} ${months[date.month - 1]}';
}
