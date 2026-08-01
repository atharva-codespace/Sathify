import 'package:flutter/material.dart';

import '../../../../shared/design_system.dart';
import '../../data/models/hiring_models.dart';

/// The Module 4.3 match percentage, as the SRS specifies it ("Priya — 98% Match").
class MatchBadge extends StatelessWidget {
  const MatchBadge({required this.percentage, this.compact = true, super.key});

  final int percentage;
  final bool compact;

  /// Green for a strong match, amber for a fair one, neutral below that.
  ///
  /// Deliberately no red: every worker shown here has already been verified and
  /// approved by an administrator, so a low match means "less suited to this
  /// request", not "bad worker". Colouring them red would read as a warning
  /// about the person.
  Color get _colour {
    if (percentage >= 80) return AppColors.success;
    if (percentage >= 60) return AppColors.accent;
    return AppColors.textSecondary;
  }

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: EdgeInsets.symmetric(horizontal: compact ? 8 : 12, vertical: 4),
      decoration: BoxDecoration(
        color: _colour.withValues(alpha: 0.12),
        borderRadius: BorderRadius.circular(20),
        border: Border.all(color: _colour.withValues(alpha: 0.4)),
      ),
      child: Text(
        compact ? '$percentage%' : '$percentage% match',
        style: TextStyle(
          color: _colour,
          fontWeight: FontWeight.w700,
          fontSize: compact ? 13 : 15,
        ),
      ),
    );
  }
}

/// The per-signal breakdown behind a match percentage (Module 4.2).
///
/// Shown in full rather than summarised: a resident choosing who enters their
/// home is entitled to see which signals produced the number, and the same
/// explainability requirement applies to Module 9's trust score.
class MatchBreakdown extends StatelessWidget {
  const MatchBreakdown({required this.components, super.key});

  final List<MatchComponent> components;

  @override
  Widget build(BuildContext context) {
    if (components.isEmpty) return const SizedBox.shrink();

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(
          'Why this match',
          style: Theme.of(context)
              .textTheme
              .titleSmall
              ?.copyWith(fontWeight: FontWeight.w700),
        ),
        const SizedBox(height: 10),
        ...components.map((component) => _ComponentRow(component: component)),
      ],
    );
  }
}

class _ComponentRow extends StatelessWidget {
  const _ComponentRow({required this.component});

  final MatchComponent component;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 12),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Expanded(
                child:
                    Text(component.label, style: const TextStyle(fontSize: 14)),
              ),
              Text(
                '${component.scorePercentage}%',
                style: const TextStyle(
                  fontSize: 14,
                  fontWeight: FontWeight.w600,
                  color: AppColors.textSecondary,
                ),
              ),
            ],
          ),
          const SizedBox(height: 4),
          ClipRRect(
            borderRadius: BorderRadius.circular(4),
            child: LinearProgressIndicator(
              value: component.score.clamp(0, 1),
              minHeight: 6,
              backgroundColor: AppColors.border,
            ),
          ),
        ],
      ),
    );
  }
}
