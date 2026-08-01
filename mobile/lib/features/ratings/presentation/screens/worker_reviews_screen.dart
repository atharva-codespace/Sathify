import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../../../core/errors/api_exception.dart';
import '../../../../core/routing/app_router.dart';
import '../../../../shared/design_system.dart';
import '../../data/models/rating_models.dart';
import '../providers/rating_provider.dart';
import '../widgets/star_rating.dart';

/// Modules 9.1 and 9.2 — what residents have said about a worker.
///
/// Withheld ratings never arrive here; the server excludes them. A rating under
/// review has not been judged genuine, and showing it would let the flagging
/// system be bypassed simply by the rating being visible.
class WorkerReviewsScreen extends ConsumerWidget {
  const WorkerReviewsScreen({required this.workerId, super.key});

  final int workerId;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final reviews = ref.watch(workerRatingsProvider(workerId));

    return Scaffold(
      appBar: AppBar(
        title: const Text('Reviews'),
        actions: [
          IconButton(
            tooltip: 'Trust score',
            icon: const Icon(Icons.verified_outlined),
            onPressed: () => context.push(Routes.workerTrustPath(workerId)),
          ),
        ],
      ),
      body: reviews.when(
        loading: () => const AppSkeletonList(),
        error: (error, _) => AppErrorState(
          message:
              error is ApiException ? error.message : 'Could not load reviews.',
          onRetry: () => ref.invalidate(workerRatingsProvider(workerId)),
        ),
        data: (items) {
          if (items.isEmpty) {
            return const Center(
              child: Padding(
                padding: EdgeInsets.all(32),
                child: Text(
                  'No reviews yet.\n\nA new worker has no history — that is not '
                  'the same as a poor one.',
                  textAlign: TextAlign.center,
                ),
              ),
            );
          }
          return RefreshIndicator(
            onRefresh: () async =>
                ref.invalidate(workerRatingsProvider(workerId)),
            child: ListView.builder(
              padding: const EdgeInsets.symmetric(vertical: 8),
              itemCount: items.length,
              itemBuilder: (context, index) =>
                  _ReviewCard(rating: items[index]),
            ),
          );
        },
      ),
    );
  }
}

class _ReviewCard extends StatelessWidget {
  const _ReviewCard({required this.rating});

  final Rating rating;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final sentiment = rating.sentiment;
    final when = rating.createdAt;

    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                StarDisplay(stars: rating.stars),
                const Spacer(),
                if (when != null)
                  Text(
                    '${when.day}/${when.month}/${when.year}',
                    style: const TextStyle(
                      fontSize: 12,
                      color: AppColors.textSecondary,
                    ),
                  ),
              ],
            ),
            if (rating.hasReview) ...[
              const SizedBox(height: 10),
              Text(rating.review, style: theme.textTheme.bodyMedium),
            ],
            if (rating.raterName.isNotEmpty) ...[
              const SizedBox(height: 8),
              Text(
                '— ${rating.raterName}',
                style: const TextStyle(
                  fontSize: 12.5,
                  color: AppColors.textSecondary,
                ),
              ),
            ],
            // Module 9.2's themes are shown; the overall label is not, unless
            // the engine was confident. A keyword guess presented as a verdict
            // on somebody's work would be worse than showing nothing.
            if (sentiment != null &&
                sentiment.isReliable &&
                sentiment.themes.isNotEmpty) ...[
              const SizedBox(height: 12),
              Wrap(
                spacing: 6,
                runSpacing: 4,
                children: sentiment.themes.entries
                    .map(
                      (entry) =>
                          _ThemeChip(theme: entry.key, verdict: entry.value),
                    )
                    .toList(),
              ),
            ],
          ],
        ),
      ),
    );
  }
}

class _ThemeChip extends StatelessWidget {
  const _ThemeChip({required this.theme, required this.verdict});

  final String theme;
  final String verdict;

  static const _labels = {
    'punctuality': 'Punctuality',
    'hygiene': 'Cleanliness',
    'behaviour': 'Behaviour',
    'quality': 'Quality of work',
  };

  @override
  Widget build(BuildContext context) {
    final colour = verdict == 'positive'
        ? AppColors.success
        : verdict == 'negative'
            ? AppColors.danger
            : AppColors.textSecondary;

    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
      decoration: BoxDecoration(
        color: colour.withValues(alpha: 0.12),
        borderRadius: BorderRadius.circular(20),
      ),
      child: Text(
        _labels[theme] ?? theme,
        style: TextStyle(
          color: colour,
          fontSize: 12,
          fontWeight: FontWeight.w600,
        ),
      ),
    );
  }
}
