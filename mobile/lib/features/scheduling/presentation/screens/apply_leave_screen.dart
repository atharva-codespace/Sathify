import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../../../core/errors/api_exception.dart';
import '../../../../shared/design_system.dart';
import '../../../hiring/data/models/hiring_models.dart';
import '../../../hiring/presentation/providers/hiring_provider.dart';
import '../../data/models/schedule_models.dart';
import '../providers/schedule_provider.dart';

/// Module 6.5 — a worker takes an urgent day off ("chutti").
///
/// -----------------------------------------------------------------------
/// THIS SCREEN NEVER SAYS "REQUEST" OR "APPLY FOR APPROVAL"
/// -----------------------------------------------------------------------
/// Leave is approved the moment it is asked for, and the wording here has to
/// match, because a worker who believes they might be refused does not ask —
/// they stop turning up, and the household finds out at seven in the morning.
/// The screen tells them the answer before they tap: the day is theirs, and
/// what happens next is the household arranging cover, not a decision about
/// them.
///
/// The reason field is optional and stays optional. A worker should not have to
/// describe a private emergency to a form in order to be believed.
class ApplyLeaveScreen extends ConsumerStatefulWidget {
  const ApplyLeaveScreen({super.key});

  @override
  ConsumerState<ApplyLeaveScreen> createState() => _ApplyLeaveScreenState();
}

class _ApplyLeaveScreenState extends ConsumerState<ApplyLeaveScreen> {
  final _reasonController = TextEditingController();

  Engagement? _engagement;
  DateTime? _leaveDate;
  bool _submitting = false;
  String? _error;

  @override
  void dispose() {
    _reasonController.dispose();
    super.dispose();
  }

  /// The engagement runs on particular weekdays, so only those are selectable —
  /// there is nothing to take leave from on a day nobody expects you.
  bool _isWorkingDay(DateTime day) {
    final engagement = _engagement;
    if (engagement == null) return false;
    // Dart's weekday is 1..7 from Monday; the server's is 0..6 from Monday.
    return engagement.terms.daysOfWeek.contains(day.weekday - 1);
  }

  Future<void> _pickDate() async {
    final engagement = _engagement;
    if (engagement == null) return;

    final today = DateTime.now();
    final first = DateTime(today.year, today.month, today.day);

    final picked = await showDatePicker(
      context: context,
      initialDate: _firstWorkingDayFrom(first),
      firstDate: first,
      // Mirrors MAX_LEAVE_LEAD_DAYS on the server. This flow is for an
      // emergency; a month ahead is a conversation about the engagement.
      lastDate: first.add(const Duration(days: 14)),
      selectableDayPredicate: _isWorkingDay,
      helpText: 'Which day cannot you come?',
    );

    if (picked != null && mounted) {
      setState(() => _leaveDate = picked);
    }
  }

  DateTime _firstWorkingDayFrom(DateTime from) {
    var day = from;
    for (var i = 0; i < 15; i++) {
      if (_isWorkingDay(day)) return day;
      day = day.add(const Duration(days: 1));
    }
    return from;
  }

  Future<void> _submit() async {
    final engagement = _engagement;
    final date = _leaveDate;
    if (engagement == null || date == null) return;

    setState(() {
      _submitting = true;
      _error = null;
    });

    try {
      final leave = await ref.read(scheduleRepositoryProvider).applyForLeave(
            engagementId: engagement.id,
            leaveDate: date,
            reason: _reasonController.text.trim(),
          );
      if (!mounted) return;

      invalidateSchedule(ref);
      showAppSnackBar(
        context,
        leave.status == LeaveStatus.approved
            ? 'Your leave is approved. The household has been told.'
            : leave.summary,
      );
      context.pop();
    } on ApiException catch (error) {
      if (!mounted) return;
      setState(() => _error = error.message);
    } finally {
      if (mounted) setState(() => _submitting = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final engagements = ref.watch(engagementsProvider);

    return Scaffold(
      appBar: AppBar(title: const Text('Take a day off')),
      body: SafeArea(
        child: engagements.when(
          loading: () => const Center(child: CircularProgressIndicator()),
          error: (error, _) => AppErrorState(
            message: error is ApiException ? error.message : '$error',
            onRetry: () => ref.invalidate(engagementsProvider),
          ),
          data: (list) {
            final active = list.where((e) => e.isActive).toList();
            if (active.isEmpty) {
              return const AppEmptyState(
                icon: Icons.event_busy_outlined,
                title: 'No regular work yet',
                message:
                    'Once a household hires you for regular visits, you can '
                    'tell them here when you cannot come.',
              );
            }

            // One engagement is the common case — choose it so the worker only
            // has to answer the question that actually varies.
            _engagement ??= active.length == 1 ? active.first : null;

            return ListView(
              padding: const EdgeInsets.all(AppSpacing.gutter),
              children: [
                _ReassuranceCard(),
                const SizedBox(height: AppSpacing.lg),

                if (active.length > 1) ...[
                  Text('Which household?',
                      style: Theme.of(context).textTheme.titleSmall,),
                  const SizedBox(height: AppSpacing.xs),
                  // RadioGroup owns the selection; the tiles carry only their
                  // value. Replaces the pair deprecated after Flutter 3.32.
                  RadioGroup<int>(
                    groupValue: _engagement?.id,
                    onChanged: (id) => setState(() {
                      _engagement = active.firstWhere((e) => e.id == id);
                      // The chosen day belongs to the previous household's
                      // working days, so it cannot carry over.
                      _leaveDate = null;
                    }),
                    child: Column(
                      mainAxisSize: MainAxisSize.min,
                      children: [
                        for (final engagement in active)
                          RadioListTile<int>(
                            value: engagement.id,
                            title: Text(engagement.residentName),
                            subtitle: Text(engagement.residentFlat),
                          ),
                      ],
                    ),
                  ),
                  const SizedBox(height: AppSpacing.md),
                ],

                Text('Which day?',
                    style: Theme.of(context).textTheme.titleSmall,),
                const SizedBox(height: AppSpacing.xs),
                AppCard(
                  onTap: _engagement == null ? null : _pickDate,
                  child: Row(
                    children: [
                      const Icon(Icons.event_outlined),
                      const SizedBox(width: AppSpacing.sm),
                      Expanded(
                        child: Text(
                          _leaveDate == null
                              ? 'Choose a day'
                              : _formatFullDate(_leaveDate!),
                          style: Theme.of(context).textTheme.bodyLarge,
                        ),
                      ),
                      const Icon(Icons.chevron_right),
                    ],
                  ),
                ),
                const SizedBox(height: AppSpacing.lg),

                Text('Reason (optional)',
                    style: Theme.of(context).textTheme.titleSmall,),
                const SizedBox(height: AppSpacing.xxs),
                Text(
                  'You do not have to give one.',
                  style: Theme.of(context).textTheme.bodySmall,
                ),
                const SizedBox(height: AppSpacing.xs),
                TextField(
                  controller: _reasonController,
                  maxLength: 200,
                  maxLines: 2,
                  textCapitalization: TextCapitalization.sentences,
                  decoration: const InputDecoration(
                    hintText: 'Anything you want them to know',
                  ),
                ),

                if (_error != null) ...[
                  const SizedBox(height: AppSpacing.sm),
                  AppErrorBanner(
                    message: _error!,
                    onDismiss: () => setState(() => _error = null),
                  ),
                ],

                const SizedBox(height: AppSpacing.lg),
                AppButton(
                  label: 'Confirm my day off',
                  icon: Icons.check_rounded,
                  isLoading: _submitting,
                  onPressed: (_engagement == null || _leaveDate == null)
                      ? null
                      : _submit,
                ),
              ],
            );
          },
        ),
      ),
    );
  }
}

/// Says the quiet part out loud, before the worker commits to anything.
class _ReassuranceCard extends StatelessWidget {
  @override
  Widget build(BuildContext context) {
    return AppCard(
      color: AppColors.successSoft,
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const Icon(Icons.verified_outlined, color: AppColors.success),
          const SizedBox(width: AppSpacing.sm),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  'You do not need permission',
                  style: Theme.of(context).textTheme.titleSmall,
                ),
                const SizedBox(height: AppSpacing.xxs),
                Text(
                  'Your day off is approved as soon as you confirm it. The '
                  'household is only told so they can arrange someone else if '
                  'they need to.',
                  style: Theme.of(context).textTheme.bodySmall,
                ),
              ],
            ),
          ),
        ],
      ),
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
