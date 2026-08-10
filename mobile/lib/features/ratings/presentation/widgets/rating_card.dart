import 'package:flutter/material.dart';

import '../../../../shared/design_system.dart';
import '../../data/models/rating_models.dart';
import 'star_rating.dart';

/// One rating, as read by anybody allowed to see it.
///
/// Shared between a worker's public reviews and the "your ratings" list, which
/// draw the same thing and differ only in the line of attribution: a profile
/// says who wrote it, your own list says which direction it ran. Keeping one
/// widget means the sentiment chips and the withheld notice cannot end up
/// saying different things in the two places somebody might read them.
class RatingCard extends StatelessWidget {
  const RatingCard({
    required this.rating,
    this.attribution,
    this.showStatus = false,
    super.key,
  });

  final Rating rating;

  /// Overrides the default `— rater` line. Pass the direction ("You rated
  /// Sunita P") where the reader already knows who wrote it.
  final String? attribution;

  /// Whether to say that a rating is being held out of scoring.
  ///
  /// On for the rater's own list and the administrator's queue; off on a public
  /// profile, which never receives withheld ratings in the first place.
  final bool showStatus;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final sentiment = rating.sentiment;
    final when = rating.createdAt;
    final byline = attribution ??
        (rating.raterName.isEmpty ? '' : '— ${rating.raterName}');

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
            if (byline.isNotEmpty) ...[
              const SizedBox(height: 8),
              Text(
                byline,
                style: const TextStyle(
                  fontSize: 12.5,
                  color: AppColors.textSecondary,
                ),
              ),
            ],
            // Keyed on `isWithheld`, not `isFlagged`: a flag that an
            // administrator dismissed leaves `isFlagged` set forever as a
            // historical marker, and telling somebody their rating is still
            // under review after it was cleared and counted would be a lie.
            if (showStatus && rating.isWithheld) ...[
              const SizedBox(height: 10),
              const AppStatusChip(
                label: 'Under review before it counts',
                tone: AppTone.warning,
                icon: Icons.flag_outlined,
                dense: true,
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
