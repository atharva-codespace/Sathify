import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import 'package:intl/intl.dart';

import '../../../../core/errors/api_exception.dart';
import '../../../../core/routing/app_router.dart';
import '../../../../shared/design_system.dart';
import '../../data/models/admin_models.dart';
import '../providers/admin_provider.dart';

/// Module 11.4 — the analytics dashboard.
///
/// -----------------------------------------------------------------------
/// A PANEL WITH NO DATA SAYS SO
/// -----------------------------------------------------------------------
/// Every panel carries its own `has_data`, and this screen renders that as a
/// sentence rather than as a chart of zeros. A brand-new society genuinely has
/// no sentiment and no trust distribution; drawing empty bars invites people to
/// read a shape into noise, which is the same reason Modules 4.3 and 9.3 shrink
/// sparse evidence toward a prior instead of scoring it at zero.
///
/// The bars are drawn with plain containers rather than a charting package.
/// Five bars and a legend do not justify a dependency, and this has to render
/// on a cheap device over a free-tier connection.
class AdminDashboardScreen extends ConsumerWidget {
  const AdminDashboardScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final dashboard = ref.watch(adminDashboardProvider);

    return Scaffold(
      appBar: AppBar(
        title: const Text('Society insights'),
        actions: [
          IconButton(
            tooltip: 'Reports',
            icon: const Icon(Icons.description_outlined),
            onPressed: () => context.push(Routes.adminReports),
          ),
        ],
      ),
      body: dashboard.when(
        loading: () => const AppSkeletonList(),
        error: (error, _) => Center(
          child: Padding(
            padding: const EdgeInsets.all(24),
            child: Text(
              error is ApiException
                  ? error.message
                  : 'Could not load the dashboard.',
              textAlign: TextAlign.center,
            ),
          ),
        ),
        data: (data) => RefreshIndicator(
          onRefresh: () async => ref.invalidate(adminDashboardProvider),
          child: ListView(
            padding: const EdgeInsets.all(16),
            children: [
              if (data.periodStart != null && data.periodEnd != null)
                Padding(
                  padding: const EdgeInsets.only(bottom: 12),
                  child: Text(
                    '${_date(data.periodStart)} – ${_date(data.periodEnd)}',
                    style: const TextStyle(color: AppColors.textSecondary),
                  ),
                ),
              _ComplaintHealth(panel: data.complaints),
              const SizedBox(height: 16),
              _TrustDistribution(panel: data.trust),
              const SizedBox(height: 16),
              _UnmetDemand(panel: data.unmetDemand),
              const SizedBox(height: 16),
              _Availability(panel: data.availability),
              const SizedBox(height: 16),
              _Sentiment(panel: data.sentiment),
              const SizedBox(height: 32),
            ],
          ),
        ),
      ),
    );
  }
}

class _Panel extends StatelessWidget {
  const _Panel({
    required this.title,
    required this.child,
    this.subtitle = '',
    this.onTap,
  });

  final String title;
  final String subtitle;
  final Widget child;
  final VoidCallback? onTap;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);

    return Card(
      margin: EdgeInsets.zero,
      child: InkWell(
        borderRadius: BorderRadius.circular(14),
        onTap: onTap,
        child: Padding(
          padding: const EdgeInsets.all(16),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Row(
                children: [
                  Expanded(
                    child: Text(
                      title,
                      style: theme.textTheme.titleMedium
                          ?.copyWith(fontWeight: FontWeight.w700),
                    ),
                  ),
                  if (onTap != null)
                    const Icon(
                      Icons.chevron_right,
                      color: AppColors.textSecondary,
                    ),
                ],
              ),
              if (subtitle.isNotEmpty) ...[
                const SizedBox(height: 2),
                Text(
                  subtitle,
                  style: const TextStyle(
                    fontSize: 12,
                    color: AppColors.textSecondary,
                  ),
                ),
              ],
              const SizedBox(height: 14),
              child,
            ],
          ),
        ),
      ),
    );
  }
}

class _NoData extends StatelessWidget {
  const _NoData({required this.message});

  final String message;

  @override
  Widget build(BuildContext context) {
    return Text(
      message,
      style: const TextStyle(color: AppColors.textSecondary),
    );
  }
}

class _ComplaintHealth extends StatelessWidget {
  const _ComplaintHealth({required this.panel});

  final ComplaintPanel panel;

  @override
  Widget build(BuildContext context) {
    return _Panel(
      title: 'Complaints',
      onTap: () => context.push(Routes.complaints),
      child: panel.hasData
          ? Column(
              children: [
                Row(
                  children: [
                    _Stat(label: 'Raised', value: '${panel.raised}'),
                    _Stat(label: 'Open now', value: '${panel.openNow}'),
                    _Stat(
                      label: 'Overdue',
                      value: '${panel.overdueNow}',
                      colour: panel.overdueNow > 0 ? AppColors.danger : null,
                    ),
                  ],
                ),
                const SizedBox(height: 14),
                Row(
                  children: [
                    const Icon(
                      Icons.schedule,
                      size: 16,
                      color: AppColors.textSecondary,
                    ),
                    const SizedBox(width: 6),
                    Text(
                      // Null rather than 100% when nothing is resolved yet — a
                      // perfect score over an empty set is the most misleading
                      // figure a dashboard can print.
                      panel.slaComplianceRate == null
                          ? 'Nothing closed yet in this period'
                          : '${(panel.slaComplianceRate! * 100).round()}% '
                              'answered within the response window',
                      style: const TextStyle(fontSize: 12),
                    ),
                  ],
                ),
              ],
            )
          : const _NoData(message: 'No complaints have been raised yet.'),
    );
  }
}

class _TrustDistribution extends StatelessWidget {
  const _TrustDistribution({required this.panel});

  final TrustPanel panel;

  @override
  Widget build(BuildContext context) {
    return _Panel(
      title: 'Trust scores',
      subtitle: 'Workers with no ratings yet are counted separately',
      onTap: () => context.push(Routes.directory),
      child: panel.hasData
          ? Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                _TrustGroupView(label: 'Workers', group: panel.workers),
                const SizedBox(height: 16),
                _TrustGroupView(label: 'Residents', group: panel.residents),
              ],
            )
          : const _NoData(
              message: 'Nobody has been rated yet, so there is no '
                  'distribution to show.',
            ),
    );
  }
}

class _TrustGroupView extends StatelessWidget {
  const _TrustGroupView({required this.label, required this.group});

  final String label;
  final TrustGroup group;

  @override
  Widget build(BuildContext context) {
    final peak = group.buckets.fold<int>(
      1,
      (highest, bucket) => bucket.count > highest ? bucket.count : highest,
    );

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Row(
          mainAxisAlignment: MainAxisAlignment.spaceBetween,
          children: [
            Text(label, style: const TextStyle(fontWeight: FontWeight.w600)),
            Text(
              group.rated == 0
                  ? '${group.unrated} not rated yet'
                  : 'average ${group.average.toStringAsFixed(0)} · '
                      '${group.unrated} not rated yet',
              style: const TextStyle(
                fontSize: 12,
                color: AppColors.textSecondary,
              ),
            ),
          ],
        ),
        const SizedBox(height: 8),
        for (final bucket in group.buckets)
          Padding(
            padding: const EdgeInsets.only(bottom: 4),
            child: Row(
              children: [
                SizedBox(
                  width: 58,
                  child: Text(
                    bucket.label,
                    style: const TextStyle(fontSize: 11),
                  ),
                ),
                Expanded(
                  child: LayoutBuilder(
                    builder: (context, constraints) => Align(
                      alignment: Alignment.centerLeft,
                      child: Container(
                        height: 14,
                        width: constraints.maxWidth * (bucket.count / peak),
                        decoration: BoxDecoration(
                          color: AppColors.primary.withValues(alpha: 0.75),
                          borderRadius: BorderRadius.circular(4),
                        ),
                      ),
                    ),
                  ),
                ),
                SizedBox(
                  width: 28,
                  child: Text(
                    '${bucket.count}',
                    textAlign: TextAlign.right,
                    style: const TextStyle(fontSize: 11),
                  ),
                ),
              ],
            ),
          ),
      ],
    );
  }
}

class _UnmetDemand extends StatelessWidget {
  const _UnmetDemand({required this.panel});

  final UnmetDemandPanel panel;

  @override
  Widget build(BuildContext context) {
    return _Panel(
      title: 'Demand nobody could fill',
      subtitle: 'What to recruit for',
      onTap: () => context.push(Routes.unmetDemand),
      child: panel.hasData
          ? Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  '${panel.total} request${panel.total == 1 ? '' : 's'} went '
                  'unserved in this period.',
                ),
                const SizedBox(height: 10),
                for (final service in panel.byService.take(5))
                  Padding(
                    padding: const EdgeInsets.only(bottom: 4),
                    child: Row(
                      mainAxisAlignment: MainAxisAlignment.spaceBetween,
                      children: [
                        Text(service.service),
                        Text(
                          '${service.count}×',
                          style: const TextStyle(fontWeight: FontWeight.w700),
                        ),
                      ],
                    ),
                  ),
              ],
            )
          : const _NoData(
              message: 'Every request so far has found somebody. Nothing to '
                  'recruit for.',
            ),
    );
  }
}

class _Availability extends StatelessWidget {
  const _Availability({required this.panel});

  final AvailabilityPanel panel;

  @override
  Widget build(BuildContext context) {
    return _Panel(
      title: 'Who is available',
      subtitle: 'Next ${panel.horizonDays} days',
      child: panel.hasData
          ? Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(
                  children: [
                    _Stat(label: 'Workers', value: '${panel.workersTotal}'),
                    _Stat(
                      label: 'Available',
                      value: '${panel.workersAvailableNow}',
                      colour: AppColors.success,
                    ),
                    _Stat(
                      label: 'Days blocked',
                      value: '${panel.blockedWorkerDays}',
                      colour: panel.blockedWorkerDays > 0
                          ? AppColors.warning
                          : null,
                    ),
                  ],
                ),
                if (panel.blockedWorkerDays > 0) ...[
                  const SizedBox(height: 12),
                  const Text(
                    'A rise here usually means a festival week. It is worth '
                    'seeing before it happens, not after.',
                    style: TextStyle(
                      fontSize: 12,
                      color: AppColors.textSecondary,
                    ),
                  ),
                ],
              ],
            )
          : const _NoData(message: 'No workers are registered yet.'),
    );
  }
}

class _Sentiment extends StatelessWidget {
  const _Sentiment({required this.panel});

  final SentimentPanel panel;

  @override
  Widget build(BuildContext context) {
    return _Panel(
      title: 'What people are saying',
      child: panel.hasData
          ? Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(
                  children: [
                    _Stat(
                      label: 'Positive',
                      value: '${panel.positive}',
                      colour: AppColors.success,
                    ),
                    _Stat(label: 'Neutral', value: '${panel.neutral}'),
                    _Stat(
                      label: 'Negative',
                      value: '${panel.negative}',
                      colour: AppColors.danger,
                    ),
                  ],
                ),
                if (panel.themes.isNotEmpty) ...[
                  const SizedBox(height: 14),
                  Wrap(
                    spacing: 8,
                    runSpacing: 6,
                    children: [
                      for (final theme in panel.themes.take(6))
                        Chip(
                          label: Text(
                            '${theme.theme}  ${theme.positive}↑ ${theme.negative}↓',
                            style: const TextStyle(fontSize: 12),
                          ),
                        ),
                    ],
                  ),
                ],
                if (panel.notConfident > 0) ...[
                  const SizedBox(height: 12),
                  Text(
                    '${panel.notConfident} review'
                    '${panel.notConfident == 1 ? '' : 's'} could not be read '
                    'confidently and are excluded.',
                    style: const TextStyle(
                      fontSize: 12,
                      color: AppColors.textSecondary,
                    ),
                  ),
                ],
              ],
            )
          : const _NoData(
              message: 'No reviews have been analysed yet.',
            ),
    );
  }
}

class _Stat extends StatelessWidget {
  const _Stat({required this.label, required this.value, this.colour});

  final String label;
  final String value;
  final Color? colour;

  @override
  Widget build(BuildContext context) {
    return Expanded(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            value,
            style: TextStyle(
              fontSize: 24,
              fontWeight: FontWeight.w700,
              color: colour,
            ),
          ),
          Text(
            label,
            style: const TextStyle(
              fontSize: 12,
              color: AppColors.textSecondary,
            ),
          ),
        ],
      ),
    );
  }
}

String _date(DateTime? value) =>
    value == null ? '' : DateFormat('d MMM').format(value);
