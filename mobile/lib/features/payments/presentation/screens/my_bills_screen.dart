import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../../core/errors/api_exception.dart';
import '../../../../shared/design_system.dart';
import '../../../attendance/data/models/work_session_models.dart';
import '../../../attendance/presentation/providers/work_session_provider.dart';
import 'invoice_screen.dart';

/// Module 8.10 — the bills an hourly engagement has produced.
///
/// The list exists so [InvoiceScreen] is reachable at all, but it does one
/// thing of its own: it says which bills are *open for questions* and which are
/// holding money, before either becomes urgent. A review window a resident only
/// discovers on the day it closes is a review window in name.
class MyBillsScreen extends ConsumerWidget {
  const MyBillsScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final invoices = ref.watch(invoicesProvider);

    return Scaffold(
      appBar: AppBar(title: const Text('Bills')),
      body: invoices.when(
        loading: () => const AppSkeletonList(count: 4),
        error: (error, _) => AppErrorState(
          message: error is ApiException
              ? error.message
              : 'Could not load your bills.',
          onRetry: () => ref.invalidate(invoicesProvider),
        ),
        data: (data) {
          if (data.isEmpty) {
            return const AppEmptyState(
              icon: Icons.receipt_long_outlined,
              title: 'No bills yet',
              message:
                  'Bills appear here once an hourly engagement has finished a '
                  'billing period.',
            );
          }

          return RefreshIndicator(
            onRefresh: () async => ref.invalidate(invoicesProvider),
            child: ListView.builder(
              padding: const EdgeInsets.fromLTRB(
                AppSpacing.gutter,
                AppSpacing.md,
                AppSpacing.gutter,
                AppSpacing.bottomNavClearance,
              ),
              itemCount: data.length,
              itemBuilder: (context, index) => _BillRow(
                invoice: data[index],
                onTap: () => Navigator.of(context).push(
                  MaterialPageRoute<void>(
                    builder: (_) => InvoiceScreen(invoiceId: data[index].id),
                  ),
                ),
              ),
            ),
          );
        },
      ),
    );
  }
}

class _BillRow extends StatelessWidget {
  const _BillRow({required this.invoice, required this.onTap});

  final Invoice invoice;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final text = Theme.of(context).textTheme;

    return Padding(
      padding: const EdgeInsets.only(bottom: AppSpacing.xs),
      child: AppCard(
        onTap: onTap,
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Expanded(
                  child: Text(
                    invoice.workerName,
                    style:
                        text.titleMedium?.copyWith(fontWeight: FontWeight.w700),
                  ),
                ),
                Text(
                  invoice.totalDisplay.isEmpty
                      ? formatPaise(invoice.totalPaise)
                      : invoice.totalDisplay,
                  style:
                      text.titleMedium?.copyWith(fontWeight: FontWeight.w700),
                ),
              ],
            ),
            const SizedBox(height: AppSpacing.xxs),
            Text(
              '${_date(invoice.periodStart)} – ${_date(invoice.periodEnd)}'
              '  ·  ${invoice.daysBilled} days',
              style: text.bodySmall?.copyWith(color: AppColors.textSecondary),
            ),
            const SizedBox(height: AppSpacing.xs),
            Wrap(
              spacing: AppSpacing.xs,
              runSpacing: AppSpacing.xxs,
              children: [
                if (invoice.inReview)
                  const AppStatusChip(
                    label: 'Open for questions',
                    tone: AppTone.info,
                    dense: true,
                  ),
                if (invoice.hasHeldAmount)
                  AppStatusChip(
                    label: '${formatPaise(invoice.heldPaise)} being checked',
                    tone: AppTone.warning,
                    dense: true,
                  ),
                if (!invoice.inReview && !invoice.hasHeldAmount)
                  AppStatusChip(
                    label: invoice.status,
                    tone: invoice.status == 'settled'
                        ? AppTone.success
                        : AppTone.neutral,
                    dense: true,
                  ),
              ],
            ),
          ],
        ),
      ),
    );
  }

  static String _date(DateTime d) => '${d.day} ${const [
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
      ][d.month - 1]}';
}
