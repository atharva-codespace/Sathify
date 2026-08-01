import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../../../core/errors/api_exception.dart';
import '../../../../core/routing/app_router.dart';
import '../../../../shared/design_system.dart';
import '../providers/payment_provider.dart';

/// Module 8.3 — a worker's monthly statement.
///
/// The total is the largest thing on the screen because it is what a worker
/// opens this for: to know what they earned, and to show someone. Everything
/// else supports that number.
///
/// PDF and CSV export exist on the server (`/payments/summary/pdf/` and
/// `/csv/`) but are not wired up here — downloading an authenticated file and
/// opening it needs a file-handling package the project has not chosen yet. The
/// in-app statement below is the same data.
class EarningsScreen extends ConsumerWidget {
  const EarningsScreen({super.key});

  static const _monthNames = [
    'January',
    'February',
    'March',
    'April',
    'May',
    'June',
    'July',
    'August',
    'September',
    'October',
    'November',
    'December',
  ];

  void _shiftMonth(WidgetRef ref, int delta) {
    final current = ref.read(summaryMonthProvider);
    final shifted = DateTime(current.year, current.month + delta);
    final now = DateTime.now();
    // No future months: there is nothing to show, and offering them looks like
    // the app has lost data.
    if (shifted.isAfter(DateTime(now.year, now.month))) return;
    ref.read(summaryMonthProvider.notifier).state = shifted;
  }

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final summary = ref.watch(monthlySummaryProvider);
    final month = ref.watch(summaryMonthProvider);
    final now = DateTime.now();
    final atCurrentMonth = month.year == now.year && month.month == now.month;

    return Scaffold(
      appBar: AppBar(title: const Text('My earnings')),
      body: Column(
        children: [
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
            child: Row(
              mainAxisAlignment: MainAxisAlignment.spaceBetween,
              children: [
                IconButton(
                  icon: const Icon(Icons.chevron_left),
                  onPressed: () => _shiftMonth(ref, -1),
                ),
                Text(
                  '${_monthNames[month.month - 1]} ${month.year}',
                  style: Theme.of(context)
                      .textTheme
                      .titleMedium
                      ?.copyWith(fontWeight: FontWeight.w700),
                ),
                IconButton(
                  icon: const Icon(Icons.chevron_right),
                  onPressed: atCurrentMonth ? null : () => _shiftMonth(ref, 1),
                ),
              ],
            ),
          ),
          const Divider(height: 1),
          Expanded(
            child: summary.when(
              loading: () => const AppSkeletonList(),
              error: (error, _) => Center(
                child: Padding(
                  padding: const EdgeInsets.all(24),
                  child: Text(
                    error is ApiException
                        ? error.message
                        : 'Could not load your statement.',
                    textAlign: TextAlign.center,
                  ),
                ),
              ),
              data: (data) => RefreshIndicator(
                onRefresh: () async => ref.invalidate(monthlySummaryProvider),
                child: ListView(
                  padding: const EdgeInsets.all(16),
                  children: [
                    Card(
                      margin: EdgeInsets.zero,
                      child: Padding(
                        padding: const EdgeInsets.symmetric(
                          horizontal: 20,
                          vertical: 28,
                        ),
                        child: Column(
                          children: [
                            const Text(
                              'Received this month',
                              style: TextStyle(color: AppColors.textSecondary),
                            ),
                            const SizedBox(height: 8),
                            Text(
                              data.totalDisplay,
                              style: Theme.of(context)
                                  .textTheme
                                  .headlineLarge
                                  ?.copyWith(fontWeight: FontWeight.w800),
                            ),
                            if (data.hasTips) ...[
                              const SizedBox(height: 6),
                              Text(
                                'includes ${data.tipsDisplay} in tips',
                                style: const TextStyle(
                                  fontSize: 13,
                                  color: AppColors.success,
                                ),
                              ),
                            ],
                            const SizedBox(height: 6),
                            Text(
                              data.paymentCount == 1
                                  ? 'from 1 payment'
                                  : 'from ${data.paymentCount} payments',
                              style: const TextStyle(
                                fontSize: 13,
                                color: AppColors.textSecondary,
                              ),
                            ),
                          ],
                        ),
                      ),
                    ),
                    const SizedBox(height: 20),
                    if (data.isEmpty)
                      const Padding(
                        padding: EdgeInsets.symmetric(vertical: 32),
                        child: Text(
                          'No payments were settled this month.',
                          textAlign: TextAlign.center,
                          style: TextStyle(color: AppColors.textSecondary),
                        ),
                      )
                    else
                      ...data.lines.map(
                        (line) => Card(
                          child: ListTile(
                            onTap: line.paymentId.isEmpty
                                ? null
                                : () => context.push(
                                      Routes.receiptPath(line.paymentId),
                                    ),
                            title: Text(
                              line.description,
                              style:
                                  const TextStyle(fontWeight: FontWeight.w600),
                            ),
                            subtitle: Text(
                              '${line.date.day}/${line.date.month} · '
                              '${line.receiptNumber}',
                              style: const TextStyle(fontSize: 12.5),
                            ),
                            trailing: Text(
                              line.netDisplay,
                              style: const TextStyle(
                                fontWeight: FontWeight.w700,
                                fontSize: 15,
                              ),
                            ),
                          ),
                        ),
                      ),
                  ],
                ),
              ),
            ),
          ),
        ],
      ),
    );
  }
}
