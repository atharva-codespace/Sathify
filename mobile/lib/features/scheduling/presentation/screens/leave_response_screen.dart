import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../../core/errors/api_exception.dart';
import '../../../../shared/design_system.dart';
import '../../data/models/schedule_models.dart';
import '../providers/schedule_provider.dart';

/// Module 6.5 — the household answers, and arranges cover if they want it.
///
/// -----------------------------------------------------------------------
/// ONE QUESTION, AND IT IS NOT "MAY SHE?"
/// -----------------------------------------------------------------------
/// The worker is not asking permission — the leave is already approved. So this
/// screen never offers an approve/reject pair. It asks the only thing the
/// household can actually answer: **do you need somebody else that day?**
///
/// Both answers are given equal visual weight. Making "send a replacement" the
/// prominent one would push households into booking cover they do not need,
/// which costs them money and costs the absent worker a day's pay they would
/// otherwise have kept.
class LeaveResponseScreen extends ConsumerStatefulWidget {
  const LeaveResponseScreen({super.key, required this.leaveId});

  /// Resolved from the caller's own leave list rather than passed as an object,
  /// so the screen works identically when it is opened from a notification —
  /// which arrives with an id and nothing else.
  final int leaveId;

  @override
  ConsumerState<LeaveResponseScreen> createState() =>
      _LeaveResponseScreenState();
}

class _LeaveResponseScreenState extends ConsumerState<LeaveResponseScreen> {
  /// Set once an action returns a newer copy than the list has. The list is
  /// invalidated at the same time, so this only bridges the refetch.
  LeaveRequest? _updated;
  bool _busy = false;
  String? _error;

  Future<void> _run(Future<LeaveRequest> Function() action) async {
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      final updated = await action();
      if (!mounted) return;
      setState(() => _updated = updated);
      invalidateSchedule(ref);
    } on ApiException catch (error) {
      if (!mounted) return;
      setState(() => _error = error.message);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  void _respond({required bool needsReplacement}) {
    _run(
      () => ref
          .read(scheduleRepositoryProvider)
          .respondToLeave(widget.leaveId, needsReplacement: needsReplacement),
    );
  }

  void _assign(ReplacementCandidate candidate) {
    _run(
      () => ref
          .read(scheduleRepositoryProvider)
          .assignReplacement(widget.leaveId, candidate.workerId),
    );
  }

  @override
  Widget build(BuildContext context) {
    final leaveList = ref.watch(leaveRequestsProvider);

    return Scaffold(
      appBar: AppBar(title: const Text('Leave')),
      body: SafeArea(
        child: leaveList.when(
          loading: () => const Center(child: CircularProgressIndicator()),
          error: (error, _) => AppErrorState(
            message: error is ApiException ? error.message : '$error',
            onRetry: () => ref.invalidate(leaveRequestsProvider),
          ),
          data: (list) {
            final leave = _updated ??
                list.where((l) => l.id == widget.leaveId).firstOrNull;

            if (leave == null) {
              return const AppEmptyState(
                icon: Icons.event_busy_outlined,
                title: 'This leave is no longer listed',
                message: 'It may have been withdrawn.',
              );
            }

            return ListView(
              padding: const EdgeInsets.all(AppSpacing.gutter),
              children: [
                _LeaveHeader(leave: leave),
                const SizedBox(height: AppSpacing.lg),

                if (_error != null) ...[
                  AppErrorBanner(
                    message: _error!,
                    onDismiss: () => setState(() => _error = null),
                  ),
                  const SizedBox(height: AppSpacing.md),
                ],

                if (leave.needsResidentResponse)
                  _TheQuestion(busy: _busy, onAnswer: _respond)
                else
                  _Outcome(leave: leave),

                if (leave.status == LeaveStatus.replacementRequested) ...[
                  const SizedBox(height: AppSpacing.lg),
                  const AppSectionHeader(title: 'Who can come instead'),
                  const SizedBox(height: AppSpacing.xs),
                  _Candidates(
                    leaveId: leave.id,
                    busy: _busy,
                    onPick: _assign,
                  ),
                ],
              ],
            );
          },
        ),
      ),
    );
  }
}

class _LeaveHeader extends StatelessWidget {
  const _LeaveHeader({required this.leave});

  final LeaveRequest leave;

  @override
  Widget build(BuildContext context) {
    return AppCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            '${leave.workerName} cannot come on '
            '${_formatFullDate(leave.leaveDate)}',
            style: Theme.of(context).textTheme.titleMedium,
          ),
          if (leave.startTimeLabel.isNotEmpty) ...[
            const SizedBox(height: AppSpacing.xxs),
            Text(
              'Usually ${leave.startTimeLabel}',
              style: Theme.of(context).textTheme.bodySmall,
            ),
          ],
          if (leave.reason.isNotEmpty) ...[
            const SizedBox(height: AppSpacing.sm),
            Text(leave.reason, style: Theme.of(context).textTheme.bodyMedium),
          ],
        ],
      ),
    );
  }
}

/// The two answers, given equal weight on purpose.
class _TheQuestion extends StatelessWidget {
  const _TheQuestion({required this.busy, required this.onAnswer});

  final bool busy;
  final void Function({required bool needsReplacement}) onAnswer;

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Text(
          'Do you need someone else that day?',
          style: Theme.of(context).textTheme.titleMedium,
        ),
        const SizedBox(height: AppSpacing.md),
        AppButton(
          label: 'Yes, send a replacement',
          icon: Icons.person_add_alt_1_outlined,
          onPressed: busy ? null : () => onAnswer(needsReplacement: true),
        ),
        const SizedBox(height: AppSpacing.sm),
        AppButton.secondary(
          label: "No, I'll manage",
          icon: Icons.check_rounded,
          onPressed: busy ? null : () => onAnswer(needsReplacement: false),
        ),
      ],
    );
  }
}

class _Outcome extends StatelessWidget {
  const _Outcome({required this.leave});

  final LeaveRequest leave;

  @override
  Widget build(BuildContext context) {
    final covered = leave.isCovered;

    return AppCard(
      color: covered ? AppColors.successSoft : AppColors.surfaceMuted,
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Icon(
            covered ? Icons.how_to_reg_outlined : Icons.info_outline,
            color: covered ? AppColors.success : null,
          ),
          const SizedBox(width: AppSpacing.sm),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  leave.summary,
                  style: Theme.of(context).textTheme.bodyLarge,
                ),
                if (covered && leave.replacementDisplay.isNotEmpty) ...[
                  const SizedBox(height: AppSpacing.xxs),
                  Text(
                    'They will be paid ${leave.replacementDisplay} for the day.',
                    style: Theme.of(context).textTheme.bodySmall,
                  ),
                ],
              ],
            ),
          ),
        ],
      ),
    );
  }
}

class _Candidates extends ConsumerWidget {
  const _Candidates({
    required this.leaveId,
    required this.busy,
    required this.onPick,
  });

  final int leaveId;
  final bool busy;
  final void Function(ReplacementCandidate) onPick;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final candidates = ref.watch(replacementCandidatesProvider(leaveId));

    return candidates.when(
      loading: () => const Padding(
        padding: EdgeInsets.all(AppSpacing.lg),
        child: Center(child: CircularProgressIndicator()),
      ),
      error: (error, _) => AppErrorState(
        message: error is ApiException ? error.message : '$error',
        onRetry: () => ref.invalidate(replacementCandidatesProvider(leaveId)),
      ),
      data: (list) {
        if (list.isEmpty) {
          return const AppEmptyState(
            icon: Icons.person_search_outlined,
            title: 'Nobody is free at that time',
            message:
                'No one in your society is available for that slot. You will '
                'be told if that changes.',
          );
        }

        return Column(
          children: [
            for (final candidate in list)
              Padding(
                padding: const EdgeInsets.only(bottom: AppSpacing.sm),
                child: AppCard(
                  onTap: busy ? null : () => onPick(candidate),
                  child: Row(
                    children: [
                      AppAvatar(
                        name: candidate.name,
                        imageUrl: candidate.photoUrl.isEmpty
                            ? null
                            : candidate.photoUrl,
                      ),
                      const SizedBox(width: AppSpacing.sm),
                      Expanded(
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            Text(
                              candidate.name,
                              style: Theme.of(context).textTheme.titleSmall,
                            ),
                            const SizedBox(height: AppSpacing.xxs),
                            Text(
                              candidate.ratingCount > 0
                                  ? '${candidate.averageRating.toStringAsFixed(1)} ★ '
                                      '(${candidate.ratingCount}) · '
                                      '${candidate.matchPercentage}% match'
                                  : '${candidate.matchPercentage}% match · no ratings yet',
                              style: Theme.of(context).textTheme.bodySmall,
                            ),
                          ],
                        ),
                      ),
                      const Icon(Icons.chevron_right),
                    ],
                  ),
                ),
              ),
          ],
        );
      },
    );
  }
}

String _formatFullDate(DateTime date) {
  const months = [
    'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
    'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec',
  ];
  const days = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
  return '${days[date.weekday - 1]} ${date.day} ${months[date.month - 1]}';
}
