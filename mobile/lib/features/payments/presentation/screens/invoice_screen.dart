import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../../core/errors/api_exception.dart';
import '../../../../shared/design_system.dart';
import '../../../attendance/data/models/work_session_models.dart';
import '../../../attendance/presentation/providers/work_session_provider.dart';
import '../../../attendance/presentation/widgets/raise_query_sheet.dart';

/// Module 8.10 — the resident's monthly bill for an hourly engagement.
///
/// -------------------------------------------------------------------------
/// THE NUMBER HAS TO BE AUDITABLE
/// -------------------------------------------------------------------------
/// A monthly rate needed no explanation: it was the figure two people agreed.
/// An hourly bill is arithmetic, and arithmetic nobody can check is arithmetic
/// people argue about — over WhatsApp, with the worker, who has no record of her
/// own to answer with. So every line is here, the visit fee is its own item, and
/// any single visit can be queried in two taps.
///
/// -------------------------------------------------------------------------
/// A QUERY DOES NOT FREEZE THE MONTH
/// -------------------------------------------------------------------------
/// Querying one visit holds only that line. The rest issues and pays on time,
/// and the screen says so before the button is pressed — because a worker who
/// believes a question risks her whole month will make sure no question is ever
/// asked, and the record will look unchallenged for the wrong reason.
class InvoiceScreen extends ConsumerWidget {
  const InvoiceScreen({super.key, required this.invoiceId});

  final int invoiceId;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final invoice = ref.watch(invoiceProvider(invoiceId));

    return Scaffold(
      appBar: AppBar(title: const Text('Bill')),
      body: invoice.when(
        loading: () => const AppSkeletonList(count: 4),
        error: (error, _) => AppErrorState(
          message: error is ApiException
              ? error.message
              : 'Could not load this bill.',
          onRetry: () => ref.invalidate(invoiceProvider(invoiceId)),
        ),
        data: (data) => _Body(invoice: data),
      ),
    );
  }
}

class _Body extends ConsumerWidget {
  const _Body({required this.invoice});

  final Invoice invoice;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final text = Theme.of(context).textTheme;

    return ListView(
      padding: const EdgeInsets.fromLTRB(
        AppSpacing.gutter,
        AppSpacing.md,
        AppSpacing.gutter,
        AppSpacing.xxl,
      ),
      children: [
        AppCard(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Row(
                children: [
                  Expanded(
                    child: Text(
                      invoice.number,
                      style: text.bodySmall
                          ?.copyWith(color: AppColors.textTertiary),
                    ),
                  ),
                  if (invoice.inReview)
                    const AppStatusChip(
                      label: 'Open for questions',
                      tone: AppTone.info,
                      dense: true,
                    ),
                ],
              ),
              const SizedBox(height: AppSpacing.xs),
              Text(
                invoice.totalDisplay,
                style:
                    text.headlineMedium?.copyWith(fontWeight: FontWeight.w700),
              ),
              const SizedBox(height: AppSpacing.xxs),
              Text(
                '${invoice.workerName} · '
                '${_date(invoice.periodStart)} – ${_date(invoice.periodEnd)}',
                style:
                    text.bodyMedium?.copyWith(color: AppColors.textSecondary),
              ),
            ],
          ),
        ),
        if (invoice.inReview) ...[
          const SizedBox(height: AppSpacing.sm),
          AppCard(
            color: AppColors.infoSoft,
            borderColor: AppColors.info.withValues(alpha: 0.25),
            child: Text(
              invoice.reviewClosesAt == null
                  ? 'Check this before you pay. Ask about anything that looks '
                      'wrong.'
                  : 'You have until ${_dateTime(invoice.reviewClosesAt!)} to '
                      'check this. Ask about anything that looks wrong.',
              style: text.bodyMedium,
            ),
          ),
        ],
        for (final query in invoice.openQueries.where((q) => q.canAccept)) ...[
          const SizedBox(height: AppSpacing.sm),
          _AcceptQueryCard(query: query),
        ],
        if (invoice.hasHeldAmount) ...[
          const SizedBox(height: AppSpacing.sm),
          AppCard(
            color: AppColors.warningSoft,
            borderColor: AppColors.warning.withValues(alpha: 0.3),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  '${formatPaise(invoice.heldPaise)} is being checked',
                  style: text.titleSmall?.copyWith(fontWeight: FontWeight.w700),
                ),
                const SizedBox(height: AppSpacing.xxs),
                Text(
                  'The rest of the bill — ${invoice.payableDisplay} — is '
                  'unaffected and ${invoice.workerName.split(' ').first} is '
                  'paid it on time.',
                  style: text.bodyMedium,
                ),
              ],
            ),
          ),
        ],
        const SizedBox(height: AppSpacing.lg),
        const AppSectionHeader(title: 'Time'),
        _Group(
          lines: [
            ...invoice.linesOf(InvoiceLineKind.time),
            ...invoice.linesOf(InvoiceLineKind.overtime),
          ],
          // Only while the window is open. After issue a line is corrected by
          // an adjustment on the next bill rather than by editing this one, so
          // the control would promise something the API will refuse.
          onQuery:
              invoice.inReview ? (line) => _ask(context, invoice, line) : null,
        ),
        const SizedBox(height: AppSpacing.md),
        const AppSectionHeader(title: 'Visits'),
        _Group(
          lines: invoice.linesOf(InvoiceLineKind.visitFee),
          onQuery:
              invoice.inReview ? (line) => _ask(context, invoice, line) : null,
        ),
        Padding(
          padding: const EdgeInsets.symmetric(horizontal: AppSpacing.xxs),
          child: Text(
            'The visit fee covers her travel and the time your slot commits. '
            'It is the same for every visit, long or short.',
            style: text.bodySmall?.copyWith(color: AppColors.textSecondary),
          ),
        ),
        if (invoice.linesOf(InvoiceLineKind.adjustment).isNotEmpty) ...[
          const SizedBox(height: AppSpacing.md),
          const AppSectionHeader(title: 'Adjustments'),
          _Group(lines: invoice.linesOf(InvoiceLineKind.adjustment)),
        ],
        const SizedBox(height: AppSpacing.md),
        AppCard(
          child: Column(
            children: [
              _Total(label: 'Total', value: invoice.totalDisplay),
              if (invoice.hasHeldAmount)
                _Total(
                  label: 'Being checked',
                  value: '− ${formatPaise(invoice.heldPaise)}',
                  muted: true,
                ),
              const Divider(height: AppSpacing.lg),
              _Total(
                label: 'To pay now',
                value: invoice.payableDisplay,
                emphasise: true,
              ),
              const SizedBox(height: AppSpacing.xxs),
              // The platform takes nothing from wages, and the bill says so
              // rather than leaving the resident to wonder.
              Text(
                '${invoice.workerName.split(' ').first} receives all of it. '
                'Sathify takes no fee from wages.',
                style: text.bodySmall?.copyWith(color: AppColors.textSecondary),
              ),
            ],
          ),
        ),
        if (invoice.unbilledExtraMinutes > 0) ...[
          const SizedBox(height: AppSpacing.sm),
          Text(
            'She also worked ${invoice.unbilledExtraMinutes} minutes of extra '
            'time that was not approved. You have not been charged for it.',
            style: text.bodySmall?.copyWith(color: AppColors.textSecondary),
          ),
        ],
      ],
    );
  }

  static String _date(DateTime d) => '${d.day} ${_shortMonth(d.month)}';

  static String _dateTime(DateTime d) => '${d.day} ${_shortMonth(d.month)}, '
      '${d.hour.toString().padLeft(2, '0')}:'
      '${d.minute.toString().padLeft(2, '0')}';

  static String _shortMonth(int month) => const [
        'Jan',
        'Feb',
        'Mar',
        'Apr',
        'May',
        'Jun',
        'Jul',
        'Aug',
        'Sep',
        'Oct',
        'Nov',
        'Dec',
      ][month - 1];
}

void _ask(BuildContext context, Invoice invoice, InvoiceLine line) {
  final sessionId = line.sessionId;
  if (sessionId == null) return;
  showModalBottomSheet<void>(
    context: context,
    isScrollControlled: true,
    backgroundColor: Colors.transparent,
    builder: (_) => RaiseQuerySheet(
      invoiceId: invoice.id,
      sessionId: sessionId,
      workerName: invoice.workerName,
      amountPaise: line.amountPaise,
    ),
  );
}

/// Stage two of the escalation ladder, in one tap.
///
/// This is where most questions are meant to end. Without it a query simply
/// waits out its 48 hours and lands on a volunteer committee member — the
/// outcome the three-stage design exists to avoid, arrived at by omission.
class _AcceptQueryCard extends ConsumerStatefulWidget {
  const _AcceptQueryCard({required this.query});

  final OpenQuery query;

  @override
  ConsumerState<_AcceptQueryCard> createState() => _AcceptQueryCardState();
}

class _AcceptQueryCardState extends ConsumerState<_AcceptQueryCard> {
  bool _busy = false;

  Future<void> _accept() async {
    setState(() => _busy = true);
    final messenger = ScaffoldMessenger.of(context);
    try {
      await ref
          .read(workSessionRepositoryProvider)
          .acceptQuery(widget.query.id);
      invalidateSessions(ref);
      showAppSnackBarOn(
        messenger,
        'Accepted. It is corrected on the next bill.',
        tone: AppTone.success,
      );
    } on ApiException catch (error) {
      showAppSnackBarOn(messenger, error.message, tone: AppTone.danger);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final text = Theme.of(context).textTheme;
    final query = widget.query;

    return AppCard(
      color: AppColors.infoSoft,
      borderColor: AppColors.info.withValues(alpha: 0.25),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            '${query.raisedByName.split(' ').first} asked about one visit',
            style: text.titleSmall?.copyWith(fontWeight: FontWeight.w700),
          ),
          if (query.description.isNotEmpty) ...[
            const SizedBox(height: AppSpacing.xxs),
            Text(query.description, style: text.bodyMedium),
          ],
          const SizedBox(height: AppSpacing.xs),
          Text(
            'If they are right, accepting settles it now — no admin, no wait.',
            style: text.bodySmall?.copyWith(color: AppColors.textSecondary),
          ),
          const SizedBox(height: AppSpacing.sm),
          AppButton.secondary(
            label: 'Yes, they are right',
            onPressed: _busy ? null : _accept,
            isLoading: _busy,
          ),
        ],
      ),
    );
  }
}

class _Group extends StatelessWidget {
  const _Group({required this.lines, this.onQuery});

  final List<InvoiceLine> lines;

  /// Null when the bill is past its review window.
  final void Function(InvoiceLine line)? onQuery;

  @override
  Widget build(BuildContext context) {
    if (lines.isEmpty) {
      return AppCard(
        child: Text(
          'Nothing here this month',
          style: Theme.of(context)
              .textTheme
              .bodyMedium
              ?.copyWith(color: AppColors.textTertiary),
        ),
      );
    }

    return AppCard(
      padding: const EdgeInsets.symmetric(vertical: AppSpacing.xs),
      child: Column(
        children: [
          for (final line in lines)
            Padding(
              padding: const EdgeInsets.symmetric(
                horizontal: AppSpacing.md,
                vertical: AppSpacing.xxs + 2,
              ),
              child: Row(
                children: [
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text(
                          line.description,
                          style: Theme.of(context).textTheme.bodyMedium,
                        ),
                        if (line.isHeld)
                          Text(
                            'Being checked',
                            style: Theme.of(context)
                                .textTheme
                                .bodySmall
                                ?.copyWith(color: AppColors.warning),
                          ),
                      ],
                    ),
                  ),
                  Text(
                    line.amountDisplay,
                    style: Theme.of(context).textTheme.bodyMedium?.copyWith(
                          fontWeight: FontWeight.w600,
                          decoration:
                              line.isHeld ? TextDecoration.lineThrough : null,
                          color: line.isHeld
                              ? AppColors.textTertiary
                              : AppColors.textPrimary,
                        ),
                  ),
                  if (onQuery != null && line.sessionId != null && !line.isHeld)
                    IconButton(
                      icon:
                          const Icon(Icons.help_outline, size: AppIconSize.sm),
                      tooltip: 'Ask about this visit',
                      onPressed: () => onQuery!(line),
                    ),
                ],
              ),
            ),
        ],
      ),
    );
  }
}

class _Total extends StatelessWidget {
  const _Total({
    required this.label,
    required this.value,
    this.emphasise = false,
    this.muted = false,
  });

  final String label;
  final String value;
  final bool emphasise;
  final bool muted;

  @override
  Widget build(BuildContext context) {
    final text = Theme.of(context).textTheme;
    final style = emphasise
        ? text.titleMedium?.copyWith(fontWeight: FontWeight.w700)
        : text.bodyMedium?.copyWith(
            color: muted ? AppColors.textTertiary : AppColors.textPrimary,
          );

    return Padding(
      padding: const EdgeInsets.symmetric(vertical: AppSpacing.xxs),
      child: Row(
        children: [
          Expanded(
            child: Text(
              label,
              style: emphasise
                  ? text.titleMedium
                  : text.bodyMedium?.copyWith(color: AppColors.textSecondary),
            ),
          ),
          Text(value, style: style),
        ],
      ),
    );
  }
}
