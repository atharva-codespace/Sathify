import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:intl/intl.dart';

import '../../../../core/errors/api_exception.dart';
import '../../../../shared/design_system.dart';
import '../../data/models/admin_models.dart';
import '../providers/admin_provider.dart';

/// Module 11.2 — attendance, payment and complaint reports.
///
/// -----------------------------------------------------------------------
/// THE JSON REPORT IS THE SAME OBJECT THE FILES RENDER FROM
/// -----------------------------------------------------------------------
/// The server assembles one report and renders it three ways. This screen shows
/// the JSON form, which means a figure read here and a figure in a downloaded
/// CSV cannot disagree — and it is why the table is generic rather than three
/// hand-built layouts.
///
/// -----------------------------------------------------------------------
/// THE FILE EXPORTS ARE NOT WIRED UP HERE
/// -----------------------------------------------------------------------
/// `/csv/` and `/pdf/` exist, are tested, and return an authenticated file
/// download. Saving one to a phone needs a file-handling and share package the
/// project has not chosen yet — the same gap Module 8.3's statement export has.
/// Rather than ship a button that does nothing, this screen shows the full
/// report on screen and says where the exports live, so an administrator can
/// fetch one from a desktop against the same API.
class ReportsScreen extends ConsumerWidget {
  const ReportsScreen({super.key});

  static const _kinds = {
    'complaints': 'Complaints',
    'attendance': 'Attendance',
    'payments': 'Payments',
  };

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final request = ref.watch(reportRequestProvider);
    final report = ref.watch(adminReportProvider);

    return Scaffold(
      appBar: AppBar(title: const Text('Reports')),
      body: Column(
        children: [
          Padding(
            padding: const EdgeInsets.fromLTRB(12, 12, 12, 4),
            child: Row(
              children: [
                for (final entry in _kinds.entries) ...[
                  ChoiceChip(
                    label: Text(entry.value),
                    selected: request.kind == entry.key,
                    onSelected: (_) => ref
                        .read(reportRequestProvider.notifier)
                        .state = request.copyWith(kind: entry.key),
                  ),
                  const SizedBox(width: 8),
                ],
              ],
            ),
          ),
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: 12),
            child: Row(
              children: [
                Expanded(
                  child: OutlinedButton.icon(
                    onPressed: () => _pickPeriod(context, ref, request),
                    icon: const Icon(Icons.date_range),
                    label: Text(
                      request.start == null || request.end == null
                          ? 'Last 30 days'
                          : '${_date(request.start!)} – ${_date(request.end!)}',
                    ),
                  ),
                ),
              ],
            ),
          ),
          Expanded(
            child: report.when(
              loading: () => const AppSkeletonList(),
              error: (error, _) => Center(
                child: Padding(
                  padding: const EdgeInsets.all(24),
                  child: Text(
                    error is ApiException
                        ? error.message
                        : 'Could not build that report.',
                    textAlign: TextAlign.center,
                  ),
                ),
              ),
              data: (data) => _ReportView(report: data),
            ),
          ),
        ],
      ),
    );
  }

  Future<void> _pickPeriod(
    BuildContext context,
    WidgetRef ref,
    ReportRequest request,
  ) async {
    final now = DateTime.now();
    final picked = await showDateRangePicker(
      context: context,
      firstDate: DateTime(now.year - 3),
      lastDate: now,
      initialDateRange: request.start != null && request.end != null
          ? DateTimeRange(start: request.start!, end: request.end!)
          : null,
    );

    if (picked != null) {
      ref.read(reportRequestProvider.notifier).state =
          request.copyWith(start: picked.start, end: picked.end);
    }
  }
}

class _ReportView extends StatelessWidget {
  const _ReportView({required this.report});

  final AdminReport report;

  @override
  Widget build(BuildContext context) {
    return ListView(
      padding: const EdgeInsets.all(16),
      children: [
        Text(
          report.title,
          style: Theme.of(context)
              .textTheme
              .titleLarge
              ?.copyWith(fontWeight: FontWeight.w700),
        ),
        Text(
          '${report.societyName} · ${report.periodLabel}',
          style: const TextStyle(color: AppColors.textSecondary),
        ),
        const SizedBox(height: 16),
        Card(
          margin: EdgeInsets.zero,
          child: Padding(
            padding: const EdgeInsets.all(16),
            child: Column(
              children: [
                for (final line in report.summary)
                  Padding(
                    padding: const EdgeInsets.only(bottom: 8),
                    child: Row(
                      mainAxisAlignment: MainAxisAlignment.spaceBetween,
                      children: [
                        Text(line.label),
                        Text(
                          line.value,
                          style: const TextStyle(fontWeight: FontWeight.w700),
                        ),
                      ],
                    ),
                  ),
              ],
            ),
          ),
        ),
        const SizedBox(height: 16),
        if (report.isEmpty)
          const Padding(
            padding: EdgeInsets.symmetric(vertical: 24),
            child: Text(
              'No records in this period.',
              textAlign: TextAlign.center,
              style: TextStyle(color: AppColors.textSecondary),
            ),
          )
        else
          // Horizontally scrollable: these tables are up to eight columns wide,
          // which is why the PDF export is landscape.
          SingleChildScrollView(
            scrollDirection: Axis.horizontal,
            child: DataTable(
              columnSpacing: 22,
              headingRowHeight: 40,
              dataRowMinHeight: 38,
              dataRowMaxHeight: 56,
              columns: [
                for (final column in report.columns)
                  DataColumn(
                    label: Text(
                      column,
                      style: const TextStyle(
                        fontWeight: FontWeight.w700,
                        fontSize: 13,
                      ),
                    ),
                  ),
              ],
              rows: [
                for (final row in report.rows)
                  DataRow(
                    cells: [
                      // Padded to the column count: a short row would otherwise
                      // throw inside DataTable rather than render imperfectly.
                      for (var index = 0;
                          index < report.columns.length;
                          index++)
                        DataCell(
                          Text(
                            index < row.length ? row[index] : '',
                            style: const TextStyle(fontSize: 13),
                          ),
                        ),
                    ],
                  ),
              ],
            ),
          ),
        const SizedBox(height: 24),
        const Card(
          margin: EdgeInsets.zero,
          child: ListTile(
            leading: Icon(Icons.download_outlined),
            title: Text('CSV and PDF export'),
            subtitle: Text(
              'Available from the API at /reports/<kind>/csv/ and /pdf/. '
              'Downloading to a phone needs a file-handling package this app '
              'has not adopted yet.',
            ),
          ),
        ),
      ],
    );
  }
}

String _date(DateTime value) => DateFormat('d MMM').format(value);
