import 'package:flutter/material.dart';

import '../../core/theme/app_theme.dart';

/// Semantic meaning for a status chip.
///
/// Kept separate from the brand accent on purpose: a chip saying "Verified"
/// must not be the same green as the "Book now" button, or neither reads as
/// meaningful. This is the vocabulary every module shares — verification
/// badges, attendance rows, payment states, complaint SLA.
enum AppTone { neutral, brand, success, warning, danger, info }

extension AppToneColours on AppTone {
  Color get ink => switch (this) {
        AppTone.neutral => AppColors.textSecondary,
        AppTone.brand => AppColors.primary,
        AppTone.success => AppColors.success,
        AppTone.warning => AppColors.warning,
        AppTone.danger => AppColors.danger,
        AppTone.info => AppColors.info,
      };

  Color get fill => switch (this) {
        AppTone.neutral => AppColors.surfaceMuted,
        AppTone.brand => AppColors.primarySoft,
        AppTone.success => AppColors.successSoft,
        AppTone.warning => AppColors.warningSoft,
        AppTone.danger => AppColors.dangerSoft,
        AppTone.info => AppColors.infoSoft,
      };
}

/// A small, non-interactive status marker.
///
/// Encodes state in *form* as well as text — colour plus an optional icon —
/// which matters here because many workers do not read English comfortably
/// (SRS 5.4) and a colour they recognise carries meaning a word may not.
class AppStatusChip extends StatelessWidget {
  const AppStatusChip({
    super.key,
    required this.label,
    this.tone = AppTone.neutral,
    this.icon,
    this.dense = false,
  });

  final String label;
  final AppTone tone;
  final IconData? icon;

  /// Tighter padding, for chips sitting inside a dense list row.
  final bool dense;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: EdgeInsets.symmetric(
        horizontal: dense ? AppSpacing.xs : AppSpacing.sm,
        vertical: dense ? 3 : AppSpacing.xxs,
      ),
      decoration: BoxDecoration(
        color: tone.fill,
        borderRadius: AppRadius.chip,
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          if (icon != null) ...[
            Icon(icon,
                size: dense ? AppIconSize.xs : AppIconSize.sm, color: tone.ink,),
            const SizedBox(width: AppSpacing.xxs),
          ],
          Text(
            label,
            style: TextStyle(
              fontSize: dense ? 11.5 : 12.5,
              fontWeight: FontWeight.w700,
              letterSpacing: 0.1,
              color: tone.ink,
            ),
          ),
        ],
      ),
    );
  }
}

/// A selectable filter chip, as used by the sort bar on worker search.
///
/// Written rather than themed because Material's `ChoiceChip` cannot express
/// the selected state the references use — filled brand tint with a matching
/// border and a weight change — and because its shrink-wrapped tap target fell
/// under the 44px floor the audit flagged.
class AppFilterChip extends StatelessWidget {
  const AppFilterChip({
    super.key,
    required this.label,
    required this.selected,
    required this.onTap,
    this.icon,
  });

  final String label;
  final bool selected;
  final VoidCallback onTap;
  final IconData? icon;

  @override
  Widget build(BuildContext context) {
    return Semantics(
      selected: selected,
      button: true,
      child: GestureDetector(
        onTap: onTap,
        behavior: HitTestBehavior.opaque,
        child: AnimatedContainer(
          duration: AppMotion.fast,
          curve: AppMotion.standard,
          constraints: const BoxConstraints(minHeight: 40),
          padding: const EdgeInsets.symmetric(
            horizontal: AppSpacing.md,
            vertical: AppSpacing.xs,
          ),
          decoration: BoxDecoration(
            color: selected ? AppColors.primarySoft : AppColors.surface,
            borderRadius: AppRadius.chip,
            border: Border.all(
              color: selected ? AppColors.primary : AppColors.border,
              width: selected ? 1.4 : 1,
            ),
          ),
          alignment: Alignment.center,
          child: Row(
            mainAxisSize: MainAxisSize.min,
            children: [
              if (icon != null) ...[
                Icon(
                  icon,
                  size: AppIconSize.sm,
                  color: selected ? AppColors.primary : AppColors.textSecondary,
                ),
                const SizedBox(width: AppSpacing.xxs + 2),
              ],
              Text(
                label,
                style: TextStyle(
                  fontSize: 14,
                  fontWeight: selected ? FontWeight.w700 : FontWeight.w500,
                  color: selected
                      ? AppColors.primaryDark
                      : AppColors.textSecondary,
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}
