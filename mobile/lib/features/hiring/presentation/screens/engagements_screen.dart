import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../../core/errors/api_exception.dart';
import '../../../../shared/design_system.dart';
import '../../../auth/data/models/user_model.dart';
import '../../../auth/presentation/providers/auth_provider.dart';
import '../../../payments/presentation/providers/payment_provider.dart';
import '../../../payments/presentation/widgets/pay_sheet.dart';
import '../../../ratings/data/models/rating_models.dart';
import '../../../ratings/presentation/widgets/rate_sheet.dart';
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

  Future<void> _run(
    Future<void> Function() action,
    String message, {
    String? actionLabel,
    VoidCallback? onAction,
  }) async {
    final messenger = ScaffoldMessenger.of(context);
    setState(() => _isBusy = true);
    try {
      await action();
      if (!mounted) return;
      invalidateHiring(ref);
      showAppSnackBarOn(
        messenger,
        message,
        tone: AppTone.success,
        actionLabel: actionLabel,
        onAction: onAction,
      );
    } on ApiException catch (error) {
      if (!mounted) return;
      setState(() => _isBusy = false);
      showAppSnackBarOn(messenger, error.message, tone: AppTone.danger);
    }
  }

  /// Module 9.1 — offered the moment the arrangement ends.
  ///
  /// Only from [_terminate], not from notice: notice does not end anything for
  /// ten more days, and the server refuses to rate an engagement that is still
  /// running. Prompting there would offer a button that could only fail.
  void _rate() {
    // The snackbar outlives the card when the list refetches around it.
    if (!mounted) return;
    final engagement = widget.engagement;

    unawaited(
      showRateJobSheet(
        context,
        ref,
        RateableJob(
          kind: 'engagement',
          id: engagement.id,
          title: engagement.serviceType?.name ?? 'Regular work',
          counterpartyName: widget.isWorker
              ? engagement.residentName
              : engagement.workerName,
          flatLabel: engagement.residentFlat,
          finishedOn: DateTime.now(),
        ),
      ),
    );
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
  /// -----------------------------------------------------------------------
  /// A HOUSEHOLD SETTLES THIS MONTH FIRST
  /// -----------------------------------------------------------------------
  /// Module 4.6. Before notice takes effect the resident pays for the days
  /// already worked this month, and the breakdown is shown before they commit —
  /// days worked, days in the month, the rate, the amount — because this is
  /// the last money to move in a relationship that is ending and an unexplained
  /// figure at that moment turns into a dispute.
  ///
  /// The order is: show, pay, *then* notice. The server enforces the same
  /// order and refuses with `dues_outstanding`, so a stale build cannot end an
  /// arrangement with wages unpaid.
  ///
  /// A **worker** giving notice skips all of it. She is owed the money either
  /// way, but making it harder for her to resign is how you get somebody who
  /// stops turning up instead of giving notice — see the note in
  /// `hiring/services.give_notice`.
  Future<void> _giveNotice() async {
    final reason = await showDialog<EngagementEndReason>(
      context: context,
      builder: (_) => _EndReasonDialog(isWorker: widget.isWorker),
    );
    if (reason == null) return;
    if (!mounted) return;

    if (!widget.isWorker && !await _settleDues()) return;
    if (!mounted) return;

    final lastDay = NoticePeriod.earliestLastDay(DateTime.now());
    await _run(
      () => ref
          .read(hiringRepositoryProvider)
          .giveNotice(widget.engagement.id, reason: reason),
      'Notice given. The last working day is ${_formatDate(lastDay)}.',
    );
  }

  /// Shows what is owed and collects it. Returns whether notice may proceed.
  ///
  /// True when there was nothing to pay as well as when payment succeeded —
  /// a household that already paid this month's salary is square, and asking
  /// again would charge twice for the same work.
  Future<bool> _settleDues() async {
    final messenger = ScaffoldMessenger.of(context);
    final repository = ref.read(hiringRepositoryProvider);
    setState(() => _isBusy = true);

    final NoticeSettlement settlement;
    try {
      settlement = await repository.fetchNoticeSettlement(widget.engagement.id);
    } on ApiException catch (error) {
      if (!mounted) return false;
      setState(() => _isBusy = false);
      showAppSnackBarOn(messenger, error.message, tone: AppTone.danger);
      return false;
    }

    if (!mounted) return false;
    setState(() => _isBusy = false);

    if (!settlement.isOutstanding) {
      // Nothing owed, or already covered by a salary paid this month. Say so
      // rather than passing silently — "you owe nothing" is information the
      // resident wants at exactly this moment.
      showAppSnackBarOn(
        messenger,
        settlement.amountPaise > 0
            ? 'This month is already paid up.'
            : 'Nothing is owed for this month.',
        tone: AppTone.info,
      );
      return true;
    }

    final confirmed = await showDialog<bool>(
      context: context,
      builder: (_) => _SettlementDialog(
        settlement: settlement,
        workerName: widget.engagement.workerName,
      ),
    );
    if (confirmed != true || !mounted) return false;

    setState(() => _isBusy = true);
    try {
      final paymentId =
          await repository.openNoticeSettlement(widget.engagement.id);
      final payment =
          await ref.read(paymentRepositoryProvider).fetchPayment(paymentId);
      if (!mounted) return false;
      setState(() => _isBusy = false);

      final outcome = await showPaySheet(context, payment);
      if (!mounted) return false;
      invalidatePayments(ref);

      if (outcome == PayOutcome.paid) return true;

      // A UPI transfer has not settled when the sheet closes, and the server
      // gates notice on a *settled* payment. Telling the resident notice is
      // given here would be a promise the next screen would contradict.
      showAppSnackBarOn(
        messenger,
        outcome == PayOutcome.pendingUpi
            ? 'Finish in your UPI app, then give notice again once it clears.'
            : 'Notice not given — the settlement is still unpaid.',
        tone: AppTone.warning,
      );
      return false;
    } on ApiException catch (error) {
      if (!mounted) return false;
      setState(() => _isBusy = false);
      showAppSnackBarOn(messenger, error.message, tone: AppTone.danger);
      return false;
    }
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
      actionLabel: 'Rate',
      onAction: _rate,
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

/// Module 4.6 — the pro-rata, shown as a division rather than an answer.
///
/// A resident ending an arrangement is about to be asked for money at the least
/// convenient possible moment. The one thing that makes that land as fair
/// rather than as a parting charge is being able to see where the number came
/// from — so the days, the denominator and the rate are all on screen, above
/// the total, in that order.
class _SettlementDialog extends StatelessWidget {
  const _SettlementDialog({
    required this.settlement,
    required this.workerName,
  });

  final NoticeSettlement settlement;
  final String workerName;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final who = workerName.isEmpty ? 'your helper' : workerName;

    return AlertDialog(
      title: const Text('Settle this month first'),
      content: Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            '$who has already worked part of this month. That is paid before '
            'notice starts.',
            style: theme.textTheme.bodyMedium,
          ),
          const SizedBox(height: AppSpacing.md),
          // In the order the sum is done, so the total below can be checked
          // by eye: worked ÷ days in month × rate.
          _Line(
            label: 'Days worked',
            value: '${settlement.daysWorked}',
          ),
          _Line(
            label: 'Days in ${_monthName(settlement)}',
            value: '${settlement.daysInMonth}',
          ),
          _Line(
            label: 'Monthly rate',
            value: settlement.monthlyRateDisplay,
          ),
          if (settlement.scheduledDays > 0)
            _Line(
              label: 'Visits scheduled this month',
              value: '${settlement.scheduledDays}',
            ),
          const Divider(height: AppSpacing.lg),
          Row(
            mainAxisAlignment: MainAxisAlignment.spaceBetween,
            children: [
              Text('Amount due', style: theme.textTheme.titleSmall),
              Text(
                settlement.amountDisplay,
                style: const TextStyle(
                  fontSize: 18,
                  fontWeight: FontWeight.w700,
                  color: AppColors.textPrimary,
                ),
              ),
            ],
          ),
          if (settlement.explanation.isNotEmpty) ...[
            const SizedBox(height: AppSpacing.xs),
            Text(
              // The server's own wording, so the app and the server can never
              // disagree about how the figure was reached.
              settlement.explanation,
              style: theme.textTheme.bodySmall,
            ),
          ],
        ],
      ),
      actions: [
        TextButton(
          onPressed: () => Navigator.of(context).pop(false),
          child: const Text('Not now'),
        ),
        FilledButton(
          onPressed: () => Navigator.of(context).pop(true),
          child: Text('Pay ${settlement.amountDisplay}'),
        ),
      ],
    );
  }
}

/// The month the settlement covers, e.g. "August".
String _monthName(NoticeSettlement settlement) {
  const months = [
    'January', 'February', 'March', 'April', 'May', 'June',
    'July', 'August', 'September', 'October', 'November', 'December',
  ];
  final now = DateTime.now();
  return months[now.month - 1];
}

class _Line extends StatelessWidget {
  const _Line({required this.label, required this.value});

  final String label;
  final String value;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 3),
      child: Row(
        mainAxisAlignment: MainAxisAlignment.spaceBetween,
        children: [
          Text(label, style: Theme.of(context).textTheme.bodySmall),
          Text(
            value,
            style: const TextStyle(
              fontWeight: FontWeight.w600,
              color: AppColors.textPrimary,
            ),
          ),
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
