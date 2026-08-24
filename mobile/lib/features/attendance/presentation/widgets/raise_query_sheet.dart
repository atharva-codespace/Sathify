import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../../core/errors/api_exception.dart';
import '../../../../shared/design_system.dart';
import '../../data/models/work_session_models.dart';
import '../providers/work_session_provider.dart';

/// Module 8.10 — asking about one visit, before any money moves.
///
/// -------------------------------------------------------------------------
/// THE ESCALATION LADDER IS PUBLISHED IN ADVANCE
/// -------------------------------------------------------------------------
/// A resident deciding whether to raise a query is really deciding whether it
/// is worth the trouble, and the honest answer depends on what happens next.
/// So the three stages are on the screen *before* the button: both parties see
/// the same record, either can accept the other's version in one tap, and only
/// then does a volunteer committee member get involved.
///
/// The sentence that keeps the platform out of the facts — "your society admin
/// decides. Sathify does not." — is here for the same reason. A resident who
/// believes the app arbitrates will argue with the app.
///
/// -------------------------------------------------------------------------
/// AND IT SAYS WHAT IT COSTS HER
/// -------------------------------------------------------------------------
/// Only the queried line is held; the rest of the bill pays on time. Saying so
/// out loud matters more on this screen than anywhere else, because a resident
/// who thinks a question freezes a month's wages will keep quiet about a real
/// error — and a record that looks unchallenged for that reason is worse than
/// one that was never checked.
class RaiseQuerySheet extends ConsumerStatefulWidget {
  const RaiseQuerySheet({
    super.key,
    required this.invoiceId,
    required this.sessionId,
    required this.workerName,
    this.amountPaise = 0,
    this.gateNote = '',
  });

  final int invoiceId;
  final String sessionId;
  final String workerName;
  final int amountPaise;

  /// What the gate log says about that day, when it is known. Shown as the
  /// evidence stage one would surface anyway.
  final String gateNote;

  @override
  ConsumerState<RaiseQuerySheet> createState() => _RaiseQuerySheetState();
}

class _RaiseQuerySheetState extends ConsumerState<RaiseQuerySheet> {
  /// Wire values from `payments.DisputeReason`, which the API validates
  /// against. Kept as the server's own vocabulary so a new reason is added in
  /// one place rather than translated in two.
  static const _reasons = [
    ('hours_disputed', 'The times are wrong'),
    ('not_provided', 'She did not come'),
    ('wrong_amount', 'The amount is wrong'),
    ('other', 'Something else'),
  ];

  String _reason = 'hours_disputed';
  final _description = TextEditingController();
  bool _sending = false;

  @override
  void dispose() {
    _description.dispose();
    super.dispose();
  }

  Future<void> _send() async {
    setState(() => _sending = true);
    final messenger = ScaffoldMessenger.of(context);
    final navigator = Navigator.of(context);
    try {
      await ref.read(workSessionRepositoryProvider).raiseQuery(
            widget.invoiceId,
            sessionId: widget.sessionId,
            reason: _reason,
            description: _description.text.trim(),
          );
      invalidateSessions(ref);
      navigator.pop();
      showAppSnackBarOn(
        messenger,
        'Sent. ${widget.workerName.split(' ').first} is paid the rest of the '
        'month as normal.',
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
    final firstName = widget.workerName.split(' ').first;

    // `Material` rather than a decorated Container: the radio tiles below are
    // ListTiles, and a ListTile paints its ink splash on the nearest Material
    // ancestor. Wrapped in a plain DecoratedBox instead, the splash lands
    // *behind* the background and the tiles look unresponsive on a device —
    // Flutter asserts about exactly this.
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
                'Ask about this visit',
                style: text.titleLarge?.copyWith(fontWeight: FontWeight.w700),
              ),
              if (widget.amountPaise > 0) ...[
                const SizedBox(height: AppSpacing.xxs),
                Text(
                  '${formatPaise(widget.amountPaise)} on this month’s bill',
                  style:
                      text.bodyMedium?.copyWith(color: AppColors.textSecondary),
                ),
              ],
              const SizedBox(height: AppSpacing.lg),

              Text('What looks wrong?', style: text.titleSmall),
              const SizedBox(height: AppSpacing.xs),
              // `RadioGroup` rather than per-tile `groupValue`/`onChanged`: those
              // were deprecated after Flutter 3.32 and the analyzer treats them as
              // such, so using them would ship a warning on every build.
              RadioGroup<String>(
                groupValue: _reason,
                onChanged: (chosen) => setState(() => _reason = chosen!),
                child: Column(
                  children: [
                    for (final (value, label) in _reasons)
                      RadioListTile<String>(
                        value: value,
                        title: Text(label, style: text.bodyMedium),
                        contentPadding: EdgeInsets.zero,
                        dense: true,
                      ),
                  ],
                ),
              ),

              const SizedBox(height: AppSpacing.sm),
              TextField(
                controller: _description,
                maxLines: 3,
                maxLength: 400,
                decoration: const InputDecoration(
                  labelText: 'What do you think happened? (optional)',
                  hintText: 'She reached about 11:40, not 11:52.',
                  border: OutlineInputBorder(),
                ),
              ),

              AppCard(
                color: AppColors.surfaceMuted,
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      'What happens next',
                      style: text.titleSmall
                          ?.copyWith(fontWeight: FontWeight.w700),
                    ),
                    const SizedBox(height: AppSpacing.xs),
                    _Step(
                      number: '1',
                      body: 'You both see the same record for that day.'
                          '${widget.gateNote.isEmpty ? '' : '\n${widget.gateNote}'}',
                    ),
                    _Step(
                      number: '2',
                      body:
                          '$firstName can agree in one tap, and it is corrected '
                          'on next month’s bill. Most questions end here.',
                    ),
                    const _Step(
                      number: '3',
                      body:
                          'If you still disagree after 48 hours, your society '
                          'admin decides. Sathify does not.',
                    ),
                    const SizedBox(height: AppSpacing.xs),
                    Text(
                      'She is paid the rest of the month now. Only this amount '
                      'waits.',
                      style: text.bodySmall
                          ?.copyWith(color: AppColors.textSecondary),
                    ),
                  ],
                ),
              ),
              const SizedBox(height: AppSpacing.md),

              AppButton(
                label: 'Send query',
                onPressed: _sending ? null : _send,
                isLoading: _sending,
              ),
              const SizedBox(height: AppSpacing.xs),
              AppButton.text(
                label: 'Cancel',
                onPressed: () => Navigator.of(context).pop(),
                expand: true,
              ),
            ],
          ),
        ),
      ),
    );
  }
}

class _Step extends StatelessWidget {
  const _Step({required this.number, required this.body});

  final String number;
  final String body;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(bottom: AppSpacing.xs),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          SizedBox(
            width: 20,
            child: Text(
              number,
              style: Theme.of(context)
                  .textTheme
                  .bodyMedium
                  ?.copyWith(fontWeight: FontWeight.w700),
            ),
          ),
          Expanded(
            child: Text(body, style: Theme.of(context).textTheme.bodyMedium),
          ),
        ],
      ),
    );
  }
}
