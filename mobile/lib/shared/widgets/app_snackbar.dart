import 'package:flutter/material.dart';

import '../../core/theme/app_theme.dart';
import 'app_chip.dart';

/// Transient feedback.
///
/// The app currently raises bare `SnackBar(content: Text(...))` in a dozen
/// places, so a success and a failure look identical — the user has to read the
/// sentence to find out which happened. These carry an icon and a tone, so the
/// outcome reads before the words do.
///
/// Takes a [ScaffoldMessengerState] rather than a [BuildContext] at the call
/// site where the caller has already captured one before an `await`; the
/// context-taking form is the convenience wrapper.
void showAppSnackBar(
  BuildContext context,
  String message, {
  AppTone tone = AppTone.neutral,
  String? actionLabel,
  VoidCallback? onAction,
}) {
  showAppSnackBarOn(
    ScaffoldMessenger.of(context),
    message,
    tone: tone,
    actionLabel: actionLabel,
    onAction: onAction,
  );
}

/// The messenger-first form.
///
/// Safe to call after an `await`: capture the messenger before the gap and this
/// cannot throw the "don't use BuildContext across async gaps" failure that the
/// existing admin screens work around by hand.
void showAppSnackBarOn(
  ScaffoldMessengerState messenger,
  String message, {
  AppTone tone = AppTone.neutral,
  String? actionLabel,
  VoidCallback? onAction,
}) {
  final icon = switch (tone) {
    AppTone.success => Icons.check_circle_rounded,
    AppTone.danger => Icons.error_rounded,
    AppTone.warning => Icons.warning_rounded,
    AppTone.info => Icons.info_rounded,
    _ => null,
  };

  // Replace rather than queue. Two stacked snackbars mean the second is read
  // several seconds after the action that caused it, by which point it is noise.
  messenger.hideCurrentSnackBar();

  messenger.showSnackBar(
    SnackBar(
      content: Row(
        children: [
          if (icon != null) ...[
            Icon(icon, size: AppIconSize.md, color: _accentFor(tone)),
            const SizedBox(width: AppSpacing.sm),
          ],
          Expanded(child: Text(message)),
        ],
      ),
      duration: Duration(seconds: tone == AppTone.danger ? 5 : 3),
      action: actionLabel != null && onAction != null
          ? SnackBarAction(
              label: actionLabel,
              textColor: AppColors.accent,
              onPressed: onAction,
            )
          : null,
    ),
  );
}

/// Snackbars sit on the near-black surface from the theme, so the semantic
/// colours need their light-on-dark counterparts rather than the ink values,
/// which would be unreadable there.
Color _accentFor(AppTone tone) => switch (tone) {
      AppTone.success => const Color(0xFF7FD48A),
      AppTone.danger => const Color(0xFFFF9A8F),
      AppTone.warning => const Color(0xFFF5C46B),
      AppTone.info => const Color(0xFF7FC4EC),
      AppTone.brand => const Color(0xFF6FCFA8),
      AppTone.neutral => AppColors.surface,
    };
