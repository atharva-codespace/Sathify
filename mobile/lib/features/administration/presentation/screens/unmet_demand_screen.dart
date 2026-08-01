import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:intl/intl.dart';

import '../../../../core/errors/api_exception.dart';
import '../../../../shared/design_system.dart';
import '../providers/admin_provider.dart';

/// Module 11.4 — requests nobody could fill.
///
/// The recruiting brief, as a list. Each row is a moment somebody wanted help
/// and the society had none to offer: a booking search that matched nobody, or
/// a hire request that expired unanswered.
///
/// Kept as a log rather than a queue on purpose. Nobody works these — they are
/// evidence for a committee deciding who to bring on, which is the one action
/// this panel can actually lead to.
class UnmetDemandScreen extends ConsumerWidget {
  const UnmetDemandScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final entries = ref.watch(unmetDemandProvider);

    return Scaffold(
      appBar: AppBar(title: const Text('Demand nobody could fill')),
      body: entries.when(
        loading: () => const AppSkeletonList(),
        error: (error, _) => AppErrorState(
          message:
              error is ApiException ? error.message : 'Could not load the log.',
          onRetry: () => ref.invalidate(unmetDemandProvider),
        ),
        data: (rows) {
          if (rows.isEmpty) {
            return const Center(
              child: Padding(
                padding: EdgeInsets.all(32),
                child: Column(
                  mainAxisAlignment: MainAxisAlignment.center,
                  children: [
                    Icon(
                      Icons.check_circle_outline,
                      size: 72,
                      color: AppColors.textTertiary,
                    ),
                    SizedBox(height: 16),
                    Text('Every request found somebody'),
                    SizedBox(height: 6),
                    Text(
                      'When a search finds no available worker, or a hire '
                      'request expires unanswered, it is logged here.',
                      textAlign: TextAlign.center,
                    ),
                  ],
                ),
              ),
            );
          }

          return RefreshIndicator(
            onRefresh: () async => ref.invalidate(unmetDemandProvider),
            child: ListView.separated(
              itemCount: rows.length,
              separatorBuilder: (_, __) => const Divider(height: 1),
              itemBuilder: (context, index) {
                final entry = rows[index];
                return ListTile(
                  leading: const Icon(Icons.person_search_outlined),
                  title: Text(
                    entry.serviceLabel.isEmpty
                        ? 'Unspecified service'
                        : entry.serviceLabel,
                  ),
                  subtitle: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(entry.kindLabel),
                      if (entry.detail.isNotEmpty)
                        Text(
                          entry.detail,
                          style: const TextStyle(
                            fontSize: 12,
                            color: AppColors.textSecondary,
                          ),
                        ),
                    ],
                  ),
                  isThreeLine: entry.detail.isNotEmpty,
                  trailing: Text(
                    _date(entry.requestedDate ?? entry.createdAt),
                    style: const TextStyle(
                      fontSize: 12,
                      color: AppColors.textSecondary,
                    ),
                  ),
                );
              },
            ),
          );
        },
      ),
    );
  }
}

String _date(DateTime? value) =>
    value == null ? '' : DateFormat('d MMM').format(value.toLocal());
