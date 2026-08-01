import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../../core/errors/api_exception.dart';
import '../../../../shared/design_system.dart';
import '../../data/models/rating_models.dart';
import '../providers/rating_provider.dart';
import '../widgets/star_rating.dart';

/// Module 9.3 — a trust score, always shown with the reasons behind it.
///
/// The modspec makes explainability the key requirement, because a score
/// nobody can justify gets disputed — and this one decides whether somebody
/// gets hired. So the breakdown is not a detail behind a tap: the components,
/// their weights and the server's plain-language reason for each are the body
/// of the screen, and the number is just the headline.
///
/// [workerId] null shows the caller's own score.
class TrustScoreScreen extends ConsumerWidget {
  const TrustScoreScreen({this.workerId, super.key});

  final int? workerId;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final score = workerId == null
        ? ref.watch(myTrustScoreProvider)
        : ref.watch(workerTrustScoreProvider(workerId!));

    return Scaffold(
      appBar: AppBar(
        title: Text(workerId == null ? 'My trust score' : 'Trust score'),
      ),
      body: score.when(
        loading: () => const AppSkeletonList(),
        error: (error, _) => AppErrorState(
          message: error is ApiException
              ? error.message
              : 'Could not load the score.',
          // Retries whichever of the two providers this screen is showing —
          // the caller's own score, or a specific worker's.
          onRetry: () => workerId == null
              ? ref.invalidate(myTrustScoreProvider)
              : ref.invalidate(workerTrustScoreProvider(workerId!)),
        ),
        data: (trust) => _TrustBody(trust: trust, isOwn: workerId == null),
      ),
    );
  }
}

class _TrustBody extends ConsumerWidget {
  const _TrustBody({required this.trust, required this.isOwn});

  final TrustScore trust;
  final bool isOwn;

  Color get _bandColour {
    switch (trust.band) {
      case 'strong':
        return AppColors.success;
      case 'good':
        return AppColors.primary;
      case 'building':
        return AppColors.accent;
      default:
        return AppColors.info;
    }
  }

  String get _bandWording {
    switch (trust.band) {
      case 'strong':
        return 'Strong track record';
      case 'good':
        return 'Good track record';
      case 'building':
        return 'Still building a track record';
      default:
        // Deliberately not "poor". A new worker sits here through having no
        // history, not through having a bad one, and the wording should not
        // imply otherwise to a resident reading it.
        return 'New — not enough history yet';
    }
  }

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final theme = Theme.of(context);
    final history = isOwn ? ref.watch(trustHistoryProvider) : null;

    return ListView(
      padding: const EdgeInsets.all(16),
      children: [
        Card(
          margin: EdgeInsets.zero,
          child: Padding(
            padding: const EdgeInsets.symmetric(horizontal: 20, vertical: 28),
            child: Column(
              children: [
                Text(
                  trust.score.toStringAsFixed(0),
                  style: theme.textTheme.displayMedium?.copyWith(
                    fontWeight: FontWeight.w800,
                    color: _bandColour,
                  ),
                ),
                const Text(
                  'out of 100',
                  style: TextStyle(color: AppColors.textSecondary),
                ),
                const SizedBox(height: 10),
                Text(
                  _bandWording,
                  style: TextStyle(
                    fontWeight: FontWeight.w600,
                    color: _bandColour,
                  ),
                ),
                if (trust.hasRatings) ...[
                  const SizedBox(height: 14),
                  StarDisplay(stars: trust.averageRating.round()),
                  const SizedBox(height: 4),
                  Text(
                    '${trust.averageRating.toStringAsFixed(1)} from '
                    '${trust.ratingCount} rating(s)',
                    style: const TextStyle(
                      fontSize: 13,
                      color: AppColors.textSecondary,
                    ),
                  ),
                ],
              ],
            ),
          ),
        ),
        const SizedBox(height: 24),
        Text(
          'What makes up this score',
          style: theme.textTheme.titleMedium
              ?.copyWith(fontWeight: FontWeight.w700),
        ),
        const SizedBox(height: 4),
        const Text(
          'Every part is shown, with how much it counts.',
          style: TextStyle(fontSize: 13, color: AppColors.textSecondary),
        ),
        const SizedBox(height: 14),
        ...trust.components
            .map((component) => _ComponentTile(component: component)),
        if (isOwn && trust.weakest != null) ...[
          const SizedBox(height: 12),
          Container(
            padding: const EdgeInsets.all(14),
            decoration: BoxDecoration(
              color: AppColors.info.withValues(alpha: 0.08),
              borderRadius: BorderRadius.circular(12),
            ),
            child: Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                const Icon(Icons.lightbulb_outline, color: AppColors.info),
                const SizedBox(width: 12),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      const Text(
                        'Where you can gain the most',
                        style: TextStyle(fontWeight: FontWeight.w700),
                      ),
                      const SizedBox(height: 2),
                      Text(
                        '${trust.weakest!.label} — ${trust.weakest!.detail}',
                        style: const TextStyle(fontSize: 13.5),
                      ),
                    ],
                  ),
                ),
              ],
            ),
          ),
        ],
        if (history != null) ...[
          const SizedBox(height: 28),
          Text(
            'How it has changed',
            style: theme.textTheme.titleMedium
                ?.copyWith(fontWeight: FontWeight.w700),
          ),
          const SizedBox(height: 10),
          history.when(
            loading: () => const AppSkeletonList(),
            error: (_, __) => const Text('Could not load the history.'),
            data: (entries) => entries.isEmpty
                ? const Text(
                    'No changes recorded yet.',
                    style: TextStyle(color: AppColors.textSecondary),
                  )
                : Column(
                    children: entries
                        .map((entry) => _HistoryTile(entry: entry))
                        .toList(),
                  ),
          ),
        ],
        const SizedBox(height: 24),
      ],
    );
  }
}

class _ComponentTile extends StatelessWidget {
  const _ComponentTile({required this.component});

  final TrustComponent component;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 16),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Expanded(
                child: Text(
                  component.label,
                  style: const TextStyle(fontWeight: FontWeight.w600),
                ),
              ),
              Text(
                'counts ${(component.weight * 100).round()}%',
                style: const TextStyle(
                  fontSize: 12,
                  color: AppColors.textSecondary,
                ),
              ),
            ],
          ),
          const SizedBox(height: 6),
          ClipRRect(
            borderRadius: BorderRadius.circular(4),
            child: LinearProgressIndicator(
              value: component.score.clamp(0, 1),
              minHeight: 8,
              backgroundColor: AppColors.border,
            ),
          ),
          const SizedBox(height: 6),
          // The server's own wording. "attendance: 0.72" explains nothing to a
          // worker asking why their score fell.
          Text(
            component.detail,
            style:
                const TextStyle(fontSize: 13, color: AppColors.textSecondary),
          ),
        ],
      ),
    );
  }
}

class _HistoryTile extends StatelessWidget {
  const _HistoryTile({required this.entry});

  final TrustScoreLog entry;

  @override
  Widget build(BuildContext context) {
    final colour = entry.improved ? AppColors.success : AppColors.danger;
    final when = entry.createdAt;

    return Card(
      child: ListTile(
        leading: Icon(
          entry.improved ? Icons.trending_up : Icons.trending_down,
          color: colour,
        ),
        title: Text(
          '${entry.previousScore.toStringAsFixed(0)} → '
          '${entry.newScore.toStringAsFixed(0)}',
          style: const TextStyle(fontWeight: FontWeight.w700),
        ),
        subtitle: Text(
          [
            entry.trigger,
            if (when != null) '${when.day}/${when.month}/${when.year}',
          ].where((part) => part.isNotEmpty).join(' · '),
        ),
        trailing: Text(
          '${entry.improved ? '+' : ''}${entry.delta.toStringAsFixed(1)}',
          style: TextStyle(color: colour, fontWeight: FontWeight.w700),
        ),
      ),
    );
  }
}
