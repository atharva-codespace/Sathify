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

  /// Terminating is final, so it is confirmed and always carries a reason —
  /// the server requires one, and it is what the other party will be told.
  Future<void> _terminate() async {
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
            if (engagement.isLive) ...[
              const SizedBox(height: 14),
              if (_isBusy)
                const Center(child: CircularProgressIndicator())
              else
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
                        onPressed: _terminate,
                        icon: const Icon(Icons.stop_circle_outlined),
                        label: const Text('End'),
                        style: OutlinedButton.styleFrom(
                          foregroundColor: AppColors.danger,
                          minimumSize: const Size.fromHeight(48),
                        ),
                      ),
                    ),
                  ],
                ),
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
          ..._reasons.map(
            (reason) => ListTile(
              contentPadding: EdgeInsets.zero,
              title: Text(reason.label),
              onTap: () => Navigator.of(context).pop(reason),
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
