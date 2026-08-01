import 'package:flutter/material.dart';

import '../../../../shared/design_system.dart';

/// A read-only star display.
class StarDisplay extends StatelessWidget {
  const StarDisplay({required this.stars, this.size = 18, super.key});

  final int stars;
  final double size;

  @override
  Widget build(BuildContext context) {
    return Row(
      mainAxisSize: MainAxisSize.min,
      children: List.generate(
        5,
        (index) => Icon(
          index < stars ? Icons.star_rounded : Icons.star_outline_rounded,
          size: size,
          color: index < stars ? AppColors.accent : AppColors.textTertiary,
        ),
      ),
    );
  }
}

/// A tappable star picker.
///
/// Deliberately large. This is the main input on the screen and gets used
/// one-handed by people who may not be confident with a phone — SRS 5.4 puts
/// digital literacy front and centre, and a cramped star row is the easiest way
/// to record the wrong number.
class StarPicker extends StatelessWidget {
  const StarPicker({
    required this.value,
    required this.onChanged,
    this.size = 44,
    super.key,
  });

  final int value;
  final ValueChanged<int> onChanged;
  final double size;

  static const _labels = [
    'Very poor',
    'Poor',
    'All right',
    'Good',
    'Very good',
  ];

  @override
  Widget build(BuildContext context) {
    return Column(
      children: [
        Row(
          mainAxisAlignment: MainAxisAlignment.center,
          children: List.generate(5, (index) {
            final star = index + 1;
            return IconButton(
              onPressed: () => onChanged(star),
              iconSize: size,
              // Larger than Material's default, per AppTheme.minTouchTarget.
              constraints: const BoxConstraints(minWidth: 56, minHeight: 56),
              icon: Icon(
                star <= value ? Icons.star_rounded : Icons.star_outline_rounded,
                color:
                    star <= value ? AppColors.accent : AppColors.textTertiary,
              ),
            );
          }),
        ),
        const SizedBox(height: 4),
        // The word matters as much as the count: "3 stars" means different
        // things to different people, and many users read the label first.
        Text(
          value >= 1 && value <= 5 ? _labels[value - 1] : 'Tap to rate',
          style: const TextStyle(
            fontWeight: FontWeight.w600,
            color: AppColors.textSecondary,
          ),
        ),
      ],
    );
  }
}
