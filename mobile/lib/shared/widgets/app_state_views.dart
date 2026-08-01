import 'package:flutter/material.dart';

import '../../core/theme/app_theme.dart';
import 'app_button.dart';

/// Nothing to show.
///
/// The audit found every screen hand-rolling its own version of this: worker
/// search defined private `_NoResults`, while the service catalogue inlined a
/// bare `Text` with no icon and no retry at all. Same situation, different
/// screen, different quality. One component fixes that.
///
/// [message] should say *why* it is empty and what to do next — "Try clearing
/// your filters" beats "No results".
class AppEmptyState extends StatelessWidget {
  const AppEmptyState({
    super.key,
    required this.icon,
    required this.title,
    this.message,
    this.actionLabel,
    this.onAction,
  });

  final IconData icon;
  final String title;
  final String? message;
  final String? actionLabel;
  final VoidCallback? onAction;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);

    return Center(
      child: SingleChildScrollView(
        padding: const EdgeInsets.all(AppSpacing.xl),
        child: Column(
          mainAxisAlignment: MainAxisAlignment.center,
          mainAxisSize: MainAxisSize.min,
          children: [
            // A tinted disc rather than a bare grey glyph. The old screens used
            // `Colors.black26`, which reads as a rendering failure rather than
            // a designed state.
            Container(
              width: 76,
              height: 76,
              decoration: const BoxDecoration(
                color: AppColors.surfaceMuted,
                shape: BoxShape.circle,
              ),
              child: Icon(icon,
                  size: AppIconSize.xl, color: AppColors.textTertiary,),
            ),
            const SizedBox(height: AppSpacing.lg),
            Text(
              title,
              textAlign: TextAlign.center,
              style: theme.textTheme.titleMedium,
            ),
            if (message != null) ...[
              const SizedBox(height: AppSpacing.xs),
              ConstrainedBox(
                constraints: const BoxConstraints(maxWidth: 320),
                child: Text(
                  message!,
                  textAlign: TextAlign.center,
                  style: theme.textTheme.bodyMedium,
                ),
              ),
            ],
            if (actionLabel != null && onAction != null) ...[
              const SizedBox(height: AppSpacing.lg),
              AppButton.secondary(
                label: actionLabel!,
                onPressed: onAction,
                expand: false,
              ),
            ],
          ],
        ),
      ),
    );
  }
}

/// Something went wrong, with a way back.
///
/// Always offers retry: on this stack the overwhelmingly common cause is a
/// sleeping free-tier backend or patchy mobile data, and both are fixed by
/// trying again rather than by the user changing anything.
class AppErrorState extends StatelessWidget {
  const AppErrorState({
    super.key,
    required this.message,
    this.onRetry,
    this.icon = Icons.cloud_off_rounded,
    this.title = 'Something went wrong',
  });

  final String message;
  final VoidCallback? onRetry;
  final IconData icon;
  final String title;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);

    return Center(
      child: SingleChildScrollView(
        padding: const EdgeInsets.all(AppSpacing.xl),
        child: Column(
          mainAxisAlignment: MainAxisAlignment.center,
          mainAxisSize: MainAxisSize.min,
          children: [
            Container(
              width: 76,
              height: 76,
              decoration: const BoxDecoration(
                color: AppColors.dangerSoft,
                shape: BoxShape.circle,
              ),
              child: Icon(icon, size: AppIconSize.xl, color: AppColors.danger),
            ),
            const SizedBox(height: AppSpacing.lg),
            Text(title,
                textAlign: TextAlign.center,
                style: theme.textTheme.titleMedium,),
            const SizedBox(height: AppSpacing.xs),
            ConstrainedBox(
              constraints: const BoxConstraints(maxWidth: 320),
              child: Text(
                message,
                textAlign: TextAlign.center,
                style: theme.textTheme.bodyMedium,
              ),
            ),
            if (onRetry != null) ...[
              const SizedBox(height: AppSpacing.lg),
              AppButton.secondary(
                label: 'Try again',
                icon: Icons.refresh_rounded,
                onPressed: onRetry,
                expand: false,
              ),
            ],
          ],
        ),
      ),
    );
  }
}

/// An inline error strip, for form-level failures that should not replace the
/// whole screen. Extracted from the login screen's private `_ErrorBanner`, which
/// was the only decent version of this pattern in the app.
class AppErrorBanner extends StatelessWidget {
  const AppErrorBanner({super.key, required this.message, this.onDismiss});

  final String message;
  final VoidCallback? onDismiss;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.all(AppSpacing.sm),
      decoration: BoxDecoration(
        color: AppColors.dangerSoft,
        borderRadius: AppRadius.button,
        border: Border.all(color: AppColors.danger.withValues(alpha: 0.28)),
      ),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const Icon(
            Icons.error_outline_rounded,
            color: AppColors.danger,
            size: AppIconSize.md,
          ),
          const SizedBox(width: AppSpacing.xs + 2),
          Expanded(
            child: Text(
              message,
              style: const TextStyle(
                color: AppColors.danger,
                fontSize: 14.5,
                height: 1.4,
                fontWeight: FontWeight.w500,
              ),
            ),
          ),
          if (onDismiss != null)
            GestureDetector(
              onTap: onDismiss,
              child: const Padding(
                padding: EdgeInsets.only(left: AppSpacing.xs),
                child: Icon(
                  Icons.close_rounded,
                  color: AppColors.danger,
                  size: AppIconSize.sm,
                ),
              ),
            ),
        ],
      ),
    );
  }
}
