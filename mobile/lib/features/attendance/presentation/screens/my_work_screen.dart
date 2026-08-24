import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../../core/errors/api_exception.dart';
import '../../../../shared/design_system.dart';
import '../../data/models/work_session_models.dart';
import '../providers/work_session_provider.dart';

/// Module 7.7 — the worker's month.
///
/// -------------------------------------------------------------------------
/// BROKEN DOWN BY FLAT, NOT JUST TOTALLED
/// -------------------------------------------------------------------------
/// A single monthly figure is a number she has to take on trust. The same
/// month split by home is a number she can *check* — against the four
/// households she actually visited, one at a time, in an order she remembers.
/// That is the difference between a payslip and a receipt, and it is the whole
/// reason this screen is not just a total.
///
/// The visit fee is shown as its own column for the same reason it is a
/// separate line on the resident's bill: it is a real part of what she earns,
/// and folding it into an hourly total would make it impossible to verify.
class MyWorkScreen extends ConsumerWidget {
  const MyWorkScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final month = ref.watch(sessionDayProvider);
    final range = SessionRange.month(month);
    final sessions = ref.watch(sessionHistoryProvider(range));

    return Scaffold(
      appBar: AppBar(
        title: const Text('My work'),
        actions: [
          IconButton(
            icon: const Icon(Icons.chevron_left),
            tooltip: 'Previous month',
            onPressed: () => _shift(ref, -1),
          ),
          Center(
            child: Text(
              '${_monthName(month.month)} ${month.year}',
              style: Theme.of(context).textTheme.bodyMedium,
            ),
          ),
          IconButton(
            icon: const Icon(Icons.chevron_right),
            tooltip: 'Next month',
            onPressed: _isCurrentMonth(month) ? null : () => _shift(ref, 1),
          ),
        ],
      ),
      body: sessions.when(
        loading: () => const AppSkeletonList(),
        error: (error, _) => AppErrorState(
          message: error is ApiException
              ? error.message
              : 'Could not load your month.',
          onRetry: () => ref.invalidate(sessionHistoryProvider(range)),
        ),
        data: (data) {
          if (data.isEmpty) {
            return const AppEmptyState(
              icon: Icons.calendar_month_outlined,
              title: 'Nothing recorded this month',
              message: 'Your visits will appear here as you work them.',
            );
          }
          return RefreshIndicator(
            onRefresh: () async =>
                ref.invalidate(sessionHistoryProvider(range)),
            child: _Body(sessions: data),
          );
        },
      ),
    );
  }

  static void _shift(WidgetRef ref, int delta) {
    final current = ref.read(sessionDayProvider);
    final shifted = DateTime(current.year, current.month + delta);
    final now = DateTime.now();
    // No future months: there is nothing there, and offering them looks like
    // the app has lost her work.
    if (shifted.isAfter(DateTime(now.year, now.month))) return;
    ref.read(sessionDayProvider.notifier).state = shifted;
  }

  static bool _isCurrentMonth(DateTime month) {
    final now = DateTime.now();
    return month.year == now.year && month.month == now.month;
  }

  static String _monthName(int month) => const [
        'January', 'February', 'March', 'April', 'May', 'June',
        'July', 'August', 'September', 'October', 'November', 'December',
      ][month - 1];
}

class _FlatTotal {
  _FlatTotal(this.flat);

  final String flat;
  int minutes = 0;
  int visits = 0;
  int paise = 0;
}

class _Body extends StatelessWidget {
  const _Body({required this.sessions});

  final List<WorkSession> sessions;

  @override
  Widget build(BuildContext context) {
    final text = Theme.of(context).textTheme;

    final billable =
        sessions.where((s) => s.status != SessionStatus.noShow).toList();
    final minutes = billable.fold<int>(0, (sum, s) => sum + s.billedMinutes);
    final feePaise = billable.fold<int>(0, (sum, s) => sum + s.visitFeePaise);
    final workPaise =
        billable.fold<int>(0, (sum, s) => sum + s.timePaise + s.overtimePaise);

    final byFlat = <String, _FlatTotal>{};
    for (final session in billable) {
      final key = session.flat.isEmpty ? session.residentName : session.flat;
      final row = byFlat.putIfAbsent(key, () => _FlatTotal(key));
      row.minutes += session.billedMinutes;
      row.visits += 1;
      row.paise += session.totalPaise;
    }
    final flats = byFlat.values.toList()
      ..sort((a, b) => b.paise.compareTo(a.paise));

    final needsCheck = sessions.where((s) => s.needsReview).toList();

    return ListView(
      padding: const EdgeInsets.fromLTRB(
        AppSpacing.gutter,
        AppSpacing.md,
        AppSpacing.gutter,
        AppSpacing.bottomNavClearance,
      ),
      children: [
        AppCard(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(
                'THIS MONTH',
                style: text.labelSmall?.copyWith(
                  color: AppColors.textTertiary,
                  letterSpacing: 1.2,
                ),
              ),
              const SizedBox(height: AppSpacing.xs),
              _Line(
                label: '${_duration(minutes)} worked',
                value: formatPaise(workPaise),
              ),
              _Line(
                label: '${billable.length} visits × visit fee',
                value: formatPaise(feePaise),
              ),
              const Divider(height: AppSpacing.lg),
              _Line(
                label: 'Expected',
                value: formatPaise(workPaise + feePaise),
                emphasise: true,
              ),
            ],
          ),
        ),
        if (needsCheck.isNotEmpty) ...[
          const SizedBox(height: AppSpacing.sm),
          AppCard(
            color: AppColors.warningSoft,
            borderColor: AppColors.warning.withValues(alpha: 0.3),
            child: Text(
              '${needsCheck.length} '
              '${needsCheck.length == 1 ? 'day needs' : 'days need'} checking. '
              'Open Today to confirm them — your pay is not affected while they '
              'are checked.',
              style: text.bodyMedium,
            ),
          ),
        ],
        const SizedBox(height: AppSpacing.lg),
        const AppSectionHeader(title: 'By home'),
        for (final flat in flats)
          Padding(
            padding: const EdgeInsets.only(bottom: AppSpacing.xs),
            child: AppCard(
              padding: const EdgeInsets.symmetric(
                horizontal: AppSpacing.md,
                vertical: AppSpacing.sm,
              ),
              child: Row(
                children: [
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text(
                          flat.flat,
                          style: text.bodyMedium
                              ?.copyWith(fontWeight: FontWeight.w600),
                        ),
                        Text(
                          '${_duration(flat.minutes)} · '
                          '${flat.visits} ${flat.visits == 1 ? 'visit' : 'visits'}',
                          style: text.bodySmall
                              ?.copyWith(color: AppColors.textSecondary),
                        ),
                      ],
                    ),
                  ),
                  Text(
                    formatPaise(flat.paise),
                    style: text.bodyMedium
                        ?.copyWith(fontWeight: FontWeight.w700),
                  ),
                ],
              ),
            ),
          ),
      ],
    );
  }

  static String _duration(int minutes) =>
      '${minutes ~/ 60}h ${(minutes % 60).toString().padLeft(2, '0')}m';
}

class _Line extends StatelessWidget {
  const _Line({
    required this.label,
    required this.value,
    this.emphasise = false,
  });

  final String label;
  final String value;
  final bool emphasise;

  @override
  Widget build(BuildContext context) {
    final text = Theme.of(context).textTheme;
    final style = emphasise
        ? text.titleMedium?.copyWith(fontWeight: FontWeight.w700)
        : text.bodyMedium;

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
