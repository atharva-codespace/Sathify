import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../../core/errors/api_exception.dart';
import '../../../../shared/design_system.dart';
import '../../data/models/work_session_models.dart';
import '../providers/work_session_provider.dart';

/// Module 7.7 rule 5 — she asks before she works it.
///
/// The whole sheet exists to make one thing impossible: working extra time in
/// the belief that it will be paid. Unapproved overtime is recorded and shown to
/// both parties but never charged, so an app that let her simply "log" extra
/// time would be quietly arranging for her to work for free — and she would only
/// find out on the invoice, a month later, with no record of her own to argue
/// from.
///
/// So the framing is a *request*, the wording says what happens if nobody
/// answers, and the amount she would earn is shown before she asks rather than
/// after it is approved.
class OvertimeRequestSheet extends ConsumerStatefulWidget {
  const OvertimeRequestSheet({
    super.key,
    required this.card,
    required this.session,
  });

  final TodayCard card;
  final WorkSession session;

  @override
  ConsumerState<OvertimeRequestSheet> createState() =>
      _OvertimeRequestSheetState();
}

class _OvertimeRequestSheetState extends ConsumerState<OvertimeRequestSheet> {
  static const _choices = [15, 30, 60];

  int _minutes = 30;
  bool _sending = false;

  /// What the extra time is worth at her rate. Shown before she asks, not
  /// after: a number she can check is a number she can insist on.
  int get _extraPaise => (widget.card.hourlyRate * 100 * _minutes / 60).round();

  Future<void> _send() async {
    setState(() => _sending = true);
    final messenger = ScaffoldMessenger.of(context);
    final navigator = Navigator.of(context);
    try {
      await ref
          .read(workSessionRepositoryProvider)
          .requestOvertime(widget.session.id, minutes: _minutes);
      invalidateSessions(ref);
      navigator.pop();
      showAppSnackBarOn(
        messenger,
        'Asked ${widget.card.residentName.split(' ').first} for '
        '$_minutes more minutes.',
        tone: AppTone.success,
      );
    } on ApiException catch (error) {
      showAppSnackBarOn(messenger, error.message, tone: AppTone.danger);
    } finally {
      if (mounted) setState(() => _sending = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final text = Theme.of(context).textTheme;

    return Container(
      decoration: const BoxDecoration(
        color: AppColors.surface,
        borderRadius: AppRadius.sheet,
      ),
      padding: EdgeInsets.fromLTRB(
        AppSpacing.lg,
        AppSpacing.md,
        AppSpacing.lg,
        AppSpacing.lg + MediaQuery.of(context).viewInsets.bottom,
      ),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Center(
            child: Container(
              width: 40,
              height: 4,
              decoration: BoxDecoration(
                color: AppColors.borderStrong,
                borderRadius: BorderRadius.circular(AppRadius.pill),
              ),
            ),
          ),
          const SizedBox(height: AppSpacing.md),
          Text(
            'Ask to stay longer',
            style: text.titleLarge?.copyWith(fontWeight: FontWeight.w700),
          ),
          const SizedBox(height: AppSpacing.xxs),
          Text(
            'You were due to finish at '
            '${WorkSession.prettyTime(widget.card.scheduledEnd)}.',
            style: text.bodyMedium?.copyWith(color: AppColors.textSecondary),
          ),
          const SizedBox(height: AppSpacing.lg),

          Wrap(
            spacing: AppSpacing.xs,
            children: [
              for (final choice in _choices)
                AppFilterChip(
                  label: '$choice min',
                  selected: _minutes == choice,
                  onTap: () => setState(() => _minutes = choice),
                ),
            ],
          ),
          const SizedBox(height: AppSpacing.lg),

          AppCard(
            color: AppColors.primarySoft,
            borderColor: AppColors.primary.withValues(alpha: 0.2),
            child: Row(
              children: [
                Expanded(
                  child: Text('If they say yes', style: text.bodyMedium),
                ),
                Text(
                  '+ ${formatPaise(_extraPaise)}',
                  style:
                      text.titleMedium?.copyWith(fontWeight: FontWeight.w700),
                ),
              ],
            ),
          ),
          const SizedBox(height: AppSpacing.sm),

          // The sentence this sheet exists for.
          Text(
            'Extra time is only paid if they say yes. If nobody answers, do '
            'not stay — you will not be paid for it, and we will still show '
            'them that you worked it.',
            style: text.bodySmall?.copyWith(color: AppColors.textSecondary),
          ),
          const SizedBox(height: AppSpacing.lg),

          AppButton(
            label: 'Ask for $_minutes more minutes',
            onPressed: _sending ? null : _send,
            isLoading: _sending,
          ),
          const SizedBox(height: AppSpacing.xs),
          AppButton.text(
            label: 'Not now',
            onPressed: () => Navigator.of(context).pop(),
            expand: true,
          ),
        ],
      ),
    );
  }
}
