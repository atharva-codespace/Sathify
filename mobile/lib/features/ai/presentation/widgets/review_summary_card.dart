import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../../shared/design_system.dart';
import '../providers/ai_provider.dart';

/// Module 12.5 — a worker's reviews, condensed, on their profile.
///
/// -----------------------------------------------------------------------
/// IT SAYS WHERE IT CAME FROM
/// -----------------------------------------------------------------------
/// The server returns `engine` and `is_ai`, and this renders the difference.
/// A summary written by a model and one assembled from keyword counts read
/// very differently, and a resident deciding whether to hire someone is
/// entitled to know which they are looking at.
///
/// -----------------------------------------------------------------------
/// IT DISAPPEARS RATHER THAN APOLOGISING
/// -----------------------------------------------------------------------
/// A worker with no written reviews, or a summariser that could not run, gets
/// nothing — not an empty card and not an error. This sits above the reviews
/// themselves, which are always there; a broken summary of a list the reader
/// can see anyway is pure noise on a screen that already has the real thing.
class ReviewSummaryCard extends ConsumerWidget {
  const ReviewSummaryCard({super.key, required this.workerId});

  final int workerId;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final summary = ref.watch(reviewSummaryProvider(workerId));

    return summary.when(
      loading: () => const SizedBox.shrink(),
      error: (_, __) => const SizedBox.shrink(),
      data: (data) {
        if (data.isEmpty || data.reviewCount == 0) {
          return const SizedBox.shrink();
        }

        final theme = Theme.of(context);

        return Card(
          margin: const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
          child: Padding(
            padding: const EdgeInsets.all(16),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(
                  children: [
                    const Icon(
                      Icons.auto_awesome_outlined,
                      size: 18,
                      color: AppColors.info,
                    ),
                    const SizedBox(width: 8),
                    Text(
                      'What ${data.reviewCount} review'
                      '${data.reviewCount == 1 ? '' : 's'} say',
                      style: theme.textTheme.titleSmall
                          ?.copyWith(fontWeight: FontWeight.w700),
                    ),
                  ],
                ),
                const SizedBox(height: 10),
                Text(data.headline),
                if (data.strengths.isNotEmpty) ...[
                  const SizedBox(height: 12),
                  for (final point in data.strengths)
                    _Point(
                      text: point,
                      colour: AppColors.success,
                      icon: Icons.add,
                    ),
                ],
                if (data.concerns.isNotEmpty) ...[
                  const SizedBox(height: 8),
                  for (final point in data.concerns)
                    _Point(
                      text: point,
                      colour: AppColors.warning,
                      icon: Icons.remove,
                    ),
                ],
                const SizedBox(height: 12),
                Text(
                  data.isAi
                      ? 'Summarised automatically from the reviews below.'
                      : 'Counted from the reviews below — no summariser is '
                          'available on this server.',
                  style: const TextStyle(
                    fontSize: 11,
                    color: AppColors.textSecondary,
                  ),
                ),
              ],
            ),
          ),
        );
      },
    );
  }
}

class _Point extends StatelessWidget {
  const _Point({required this.text, required this.colour, required this.icon});

  final String text;
  final Color colour;
  final IconData icon;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 4),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Padding(
            padding: const EdgeInsets.only(top: 3),
            child: Icon(icon, size: 14, color: colour),
          ),
          const SizedBox(width: 8),
          Expanded(child: Text(text, style: const TextStyle(fontSize: 14))),
        ],
      ),
    );
  }
}
