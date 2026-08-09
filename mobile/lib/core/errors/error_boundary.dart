import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';

import '../../shared/design_system.dart';

/// Turns a widget-build crash into something a person can read and report.
///
/// -----------------------------------------------------------------------
/// WHY THIS EXISTS: THE BLANK WHITE SCREEN
/// -----------------------------------------------------------------------
/// Flutter's famous red error screen is **debug-only**. In a profile or release
/// build the default [ErrorWidget] is an unstyled grey box with no text, so any
/// exception thrown inside `build()` renders as a blank area where the screen
/// should be — no message, no stack, nothing in the UI to say anything went
/// wrong. That is indistinguishable from "the screen is broken", and it is
/// exactly how a silently crashing component was reported here.
///
/// It also loses the diagnosis: without [FlutterError.onError] the details are
/// printed to a console nobody is attached to on a tester's phone.
///
/// So this does two things, and neither of them hides the fault:
///
/// * every build failure renders a plain, honest panel saying this part of the
///   screen could not be shown — the surrounding navigation stays usable, so
///   one broken card no longer costs the whole screen; and
/// * the error is always logged, so it is recoverable from a device log rather
///   than only reproducible by luck.
///
/// In debug the panel additionally prints the exception, because the person
/// looking at it then is the person who can fix it.
void installErrorBoundary() {
  // Keep the framework's own handler: it is what prints the full diagnostic
  // to the console and feeds the test harness. This wraps it, never replaces
  // it — swallowing the original would trade one silent failure for another.
  final previous = FlutterError.onError;
  FlutterError.onError = (details) {
    previous?.call(details);
    debugPrint('Sathify caught a widget error: ${details.exceptionAsString()}');
  };

  ErrorWidget.builder = (details) => _BuildFailurePanel(details: details);
}

class _BuildFailurePanel extends StatelessWidget {
  const _BuildFailurePanel({required this.details});

  final FlutterErrorDetails details;

  @override
  Widget build(BuildContext context) {
    // Deliberately does not depend on Theme, Directionality or MediaQuery
    // being present. This widget is substituted at the exact point a build
    // failed, which can be above the point where any of those are provided —
    // an ErrorWidget that throws while reporting an error takes the whole app
    // down and reports nothing.
    return Directionality(
      textDirection: TextDirection.ltr,
      child: Container(
        color: AppColors.surface,
        padding: const EdgeInsets.all(AppSpacing.lg),
        alignment: Alignment.center,
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            const Icon(
              Icons.broken_image_outlined,
              size: 40,
              color: AppColors.textTertiary,
            ),
            const SizedBox(height: AppSpacing.sm),
            const Text(
              'This part could not be shown',
              textAlign: TextAlign.center,
              style: TextStyle(
                fontSize: 15,
                fontWeight: FontWeight.w700,
                color: AppColors.textPrimary,
              ),
            ),
            const SizedBox(height: AppSpacing.xxs),
            const Text(
              'Please go back and try again. If it keeps happening, tell your '
              'society administrator.',
              textAlign: TextAlign.center,
              style: TextStyle(
                fontSize: 13,
                height: 1.4,
                color: AppColors.textSecondary,
              ),
            ),
            if (kDebugMode) ...[
              const SizedBox(height: AppSpacing.sm),
              Text(
                details.exceptionAsString(),
                textAlign: TextAlign.center,
                style: const TextStyle(
                  fontSize: 11.5,
                  color: AppColors.danger,
                ),
              ),
            ],
          ],
        ),
      ),
    );
  }
}
