import 'package:flutter/material.dart';

import '../../core/theme/app_theme.dart';

/// A heading above a group of content, with an optional trailing action.
///
/// Replaces the `titleMedium?.copyWith(fontWeight: FontWeight.w600)` incantation
/// that the audit found repeated in nearly every card and section in the app.
///
/// The trailing action is styled as "See all ›" rather than as a button, which
/// is the pattern Urban Company uses on every catalogue section — it reads as
/// navigation, not as a competing call to action.
class AppSectionHeader extends StatelessWidget {
  const AppSectionHeader({
    super.key,
    required this.title,
    this.subtitle,
    this.actionLabel,
    this.onAction,
    this.padding = const EdgeInsets.fromLTRB(
      AppSpacing.gutter,
      AppSpacing.lg,
      AppSpacing.gutter,
      AppSpacing.sm,
    ),
  });

  final String title;
  final String? subtitle;
  final String? actionLabel;
  final VoidCallback? onAction;
  final EdgeInsetsGeometry padding;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);

    return Padding(
      padding: padding,
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(title, style: theme.textTheme.titleLarge),
                if (subtitle != null) ...[
                  const SizedBox(height: 2),
                  Text(subtitle!, style: theme.textTheme.bodySmall),
                ],
              ],
            ),
          ),
          if (actionLabel != null && onAction != null)
            GestureDetector(
              onTap: onAction,
              behavior: HitTestBehavior.opaque,
              child: Padding(
                // Padding rather than a SizedBox: it grows the tap target to
                // the 44px floor without pushing the label off the baseline.
                padding: const EdgeInsets.symmetric(
                  horizontal: AppSpacing.xs,
                  vertical: AppSpacing.sm,
                ),
                child: Row(
                  children: [
                    Text(
                      actionLabel!,
                      style: const TextStyle(
                        fontSize: 14.5,
                        fontWeight: FontWeight.w600,
                        color: AppColors.primary,
                      ),
                    ),
                    const Icon(
                      Icons.chevron_right_rounded,
                      size: AppIconSize.sm,
                      color: AppColors.primary,
                    ),
                  ],
                ),
              ),
            ),
        ],
      ),
    );
  }
}

/// A small uppercase label for minor groupings inside a screen — the
/// "Housekeeping" style headers on the admin hub, or field groups in a form.
class AppEyebrow extends StatelessWidget {
  const AppEyebrow({super.key, required this.text, this.padding});

  final String text;
  final EdgeInsetsGeometry? padding;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: padding ??
          const EdgeInsets.fromLTRB(
            AppSpacing.gutter,
            AppSpacing.lg,
            AppSpacing.gutter,
            AppSpacing.xs,
          ),
      child: Text(text.toUpperCase(),
          style: Theme.of(context).textTheme.labelSmall,),
    );
  }
}
