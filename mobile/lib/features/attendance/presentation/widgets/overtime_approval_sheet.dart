import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../../core/errors/api_exception.dart';
import '../../../../shared/design_system.dart';
import '../../data/models/work_session_models.dart';
import '../providers/work_session_provider.dart';

/// Module 7.7 rule 5 — the resident's half of the extra-time exchange.
///
/// -------------------------------------------------------------------------
/// WITHOUT THIS SCREEN THE RULE IS A TRAP
/// -------------------------------------------------------------------------
/// Approved overtime is the only overtime that gets paid. The worker's app can
/// *ask*, and it is careful to tell her that asking is not the same as being
/// paid — but if nothing on the resident's side can answer, the honest warning
/// becomes a permanent one and every extra minute she works is unpaid by
/// construction.
///
/// So the approval is a first-class screen rather than a notification action:
/// the push may never arrive (no `google-services.json` on most builds), and a
/// rule that only works when Firebase does is not a rule.
///
/// -------------------------------------------------------------------------
/// THE TOTAL IS SHOWN BEFORE THE TAP
/// -------------------------------------------------------------------------
/// A resident approving "30 more minutes" is agreeing to a number, and the
/// number is the thing they will be asked about later. It appears above the
/// button, including the fact that no second visit fee applies — she is
/// already there, and charging the journey twice would be indefensible.
class OvertimeApprovalSheet extends ConsumerStatefulWidget {
  const OvertimeApprovalSheet({
    super.key,
    required this.session,
    this.hourlyRate = 0,
  });

  final WorkSession session;

  /// Whole rupees. Zero when the caller could not supply it, in which case the
  /// money preview is hidden rather than guessed at.
  final int hourlyRate;

  @override
  ConsumerState<OvertimeApprovalSheet> createState() =>
      _OvertimeApprovalSheetState();
}

class _OvertimeApprovalSheetState extends ConsumerState<OvertimeApprovalSheet> {
  static const _choices = [15, 30, 60];

  late int _minutes = widget.session.approvedOvertimeMinutes > 0
      ? widget.session.approvedOvertimeMinutes
      : 30;
  bool _saving = false;

  int get _extraPaise => (widget.hourlyRate * 100 * _minutes / 60).round();

  Future<void> _submit(int minutes) async {
    setState(() => _saving = true);
    final messenger = ScaffoldMessenger.of(context);
    final navigator = Navigator.of(context);
    try {
      await ref
          .read(workSessionRepositoryProvider)
          .approveOvertime(widget.session.id, minutes: minutes);
      invalidateSessions(ref);
      navigator.pop();
      showAppSnackBarOn(
        messenger,
        minutes == 0
            ? 'Declined. She will not be charged for extra time.'
            : 'Approved $minutes more minutes.',
        tone: AppTone.success,
      );
    } on ApiException catch (error) {
      showAppSnackBarOn(messenger, error.message, tone: AppTone.danger);
    } finally {
      if (mounted) setState(() => _saving = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final text = Theme.of(context).textTheme;
    final session = widget.session;
    final firstName = session.workerName.split(' ').first;

    // Scrollable and height-capped. On a 360x640 phone the full sheet is 70
    // pixels taller than the screen, and an un-scrollable Column there does not
    // degrade — it throws a RenderFlex overflow and the decline button is the
    // part that disappears. Losing "No, thank you" is the worst possible half
    // to lose: it turns a refusal into silence.
    return Material(
      color: AppColors.surface,
      borderRadius: AppRadius.sheet,
      clipBehavior: Clip.antiAlias,
      child: Container(
        constraints: BoxConstraints(
          maxHeight: MediaQuery.of(context).size.height * 0.88,
        ),
        padding: EdgeInsets.fromLTRB(
          AppSpacing.lg,
          AppSpacing.md,
          AppSpacing.lg,
          AppSpacing.lg + MediaQuery.of(context).viewInsets.bottom,
        ),
        child: SingleChildScrollView(
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
                '$firstName wants to stay longer',
                style: text.titleLarge?.copyWith(fontWeight: FontWeight.w700),
              ),
              const SizedBox(height: AppSpacing.xxs),
              Text(
                'She was due to finish at '
                '${WorkSession.prettyTime(session.scheduledEnd)}.',
                style:
                    text.bodyMedium?.copyWith(color: AppColors.textSecondary),
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
              if (widget.hourlyRate > 0)
                AppCard(
                  color: AppColors.primarySoft,
                  borderColor: AppColors.primary.withValues(alpha: 0.2),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Row(
                        children: [
                          Expanded(
                            child: Text(
                              '$_minutes min extra',
                              style: text.bodyMedium,
                            ),
                          ),
                          Text(
                            '+ ${formatPaise(_extraPaise)}',
                            style: text.titleMedium
                                ?.copyWith(fontWeight: FontWeight.w700),
                          ),
                        ],
                      ),
                      const SizedBox(height: AppSpacing.xxs),
                      // She is already in the building; the journey is not repeated,
                      // so neither is the fee.
                      Text(
                        'No second visit fee — she is already here.',
                        style: text.bodySmall
                            ?.copyWith(color: AppColors.textSecondary),
                      ),
                    ],
                  ),
                ),
              const SizedBox(height: AppSpacing.md),
              AppButton(
                label: 'Approve $_minutes minutes',
                onPressed: _saving ? null : () => _submit(_minutes),
                isLoading: _saving,
              ),
              const SizedBox(height: AppSpacing.xs),
              AppButton.secondary(
                label: 'No, thank you',
                onPressed: _saving ? null : () => _submit(0),
              ),
              const SizedBox(height: AppSpacing.sm),
              Text(
                'If you do nothing, she is not charged for the extra time — and she '
                'is told that before she works it, not after.',
                style: text.bodySmall?.copyWith(color: AppColors.textSecondary),
              ),
            ],
          ),
        ),
      ),
    );
  }
}
