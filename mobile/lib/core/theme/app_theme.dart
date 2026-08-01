import 'package:flutter/material.dart';

import 'app_tokens.dart';

// Re-exported so the ~40 screens that already import this file for `AppColors`
// keep compiling unchanged, and pick up `AppSpacing`, `AppRadius`, `AppShadow`,
// `AppMotion` and `AppIconSize` for free.
export 'app_tokens.dart';

/// Shared visual language, assembled from the tokens in `app_tokens.dart`.
///
/// Two constraints from the SRS shape this theme more than aesthetics do, and
/// both survive the redesign intact:
///
/// * SRS 5.4 — users span a wide range of digital literacy. Touch targets stay
///   larger than Material's 48dp minimum, and body text stays a step above
///   Material's defaults rather than shrinking to look fashionable.
/// * Many workers do not read English comfortably, so the UI leans on icons
///   and colour alongside text everywhere it can.
///
/// -----------------------------------------------------------------------
/// TYPEFACE
/// -----------------------------------------------------------------------
/// Deliberately the platform face (Roboto on Android) rather than a bundled or
/// network-fetched one. `google_fonts` resolves over the network on first use,
/// which is the wrong trade for an offline-first app (Module 13) running on
/// patchy mobile data — a missing font would mean a visible reflow on exactly
/// the connections we designed around. The premium feel here comes from the
/// scale, the weights and the letter-spacing below, not from the family. If a
/// bundled face is wanted later it drops in at `fontFamily` with no other change.
class AppTheme {
  const AppTheme._();

  /// Comfortably above Material's 48dp minimum — these screens get used
  /// one-handed, outdoors, at a gate.
  static const double minTouchTarget = 56;

  /// The floor for anything tappable that is not a full-width button: icon
  /// buttons, chips, list actions. Matches the 44x44 accessibility minimum.
  static const double minTapArea = 44;

  static ThemeData get light {
    final scheme = ColorScheme.fromSeed(
      seedColor: AppColors.primary,
      brightness: Brightness.light,
    ).copyWith(
      primary: AppColors.primary,
      onPrimary: AppColors.textOnPrimary,
      primaryContainer: AppColors.primarySoft,
      onPrimaryContainer: AppColors.primaryDark,
      secondary: AppColors.accent,
      onSecondary: AppColors.textPrimary,
      secondaryContainer: AppColors.accentSoft,
      surface: AppColors.surface,
      onSurface: AppColors.textPrimary,
      onSurfaceVariant: AppColors.textSecondary,
      surfaceContainerLowest: AppColors.surface,
      surfaceContainerLow: AppColors.background,
      surfaceContainer: AppColors.surfaceMuted,
      outline: AppColors.border,
      outlineVariant: AppColors.border,
      error: AppColors.danger,
      errorContainer: AppColors.dangerSoft,
      onErrorContainer: AppColors.danger,
    );

    return ThemeData(
      useMaterial3: true,
      colorScheme: scheme,
      scaffoldBackgroundColor: AppColors.background,
      canvasColor: AppColors.background,
      dividerColor: AppColors.border,
      splashFactory: InkSparkle.splashFactory,
      textTheme: _textTheme,

      // ---------------------------------------------------------------------
      // App bar — the single largest visual change in the redesign.
      //
      // Every one of the 39 screens previously wore a solid #1B6B50 bar, about
      // 15% of a 360dp viewport in saturated green before any content. Not one
      // reference app does this: Book My Bai is white with a blue back-chip,
      // Urban Company washes light lavender, Snabbit is plain white. Going
      // transparent-on-background spends the brand colour on the CTA instead,
      // which is what "minimal colour reserved for accents" means in practice.
      // ---------------------------------------------------------------------
      appBarTheme: const AppBarTheme(
        centerTitle: false,
        elevation: 0,
        scrolledUnderElevation: 0,
        backgroundColor: AppColors.background,
        foregroundColor: AppColors.textPrimary,
        surfaceTintColor: Colors.transparent,
        iconTheme: IconThemeData(
          color: AppColors.textPrimary,
          size: AppIconSize.md,
        ),
        actionsIconTheme: IconThemeData(
          color: AppColors.textPrimary,
          size: AppIconSize.md,
        ),
        titleTextStyle: TextStyle(
          fontSize: 21,
          fontWeight: FontWeight.w700,
          letterSpacing: -0.3,
          color: AppColors.textPrimary,
        ),
      ),

      iconTheme: const IconThemeData(
        color: AppColors.textSecondary,
        size: AppIconSize.md,
      ),

      // --- Buttons ---------------------------------------------------------
      elevatedButtonTheme: ElevatedButtonThemeData(
        style: ElevatedButton.styleFrom(
          backgroundColor: AppColors.primary,
          foregroundColor: AppColors.textOnPrimary,
          disabledBackgroundColor: AppColors.surfaceMuted,
          disabledForegroundColor: AppColors.textTertiary,
          minimumSize: const Size.fromHeight(minTouchTarget),
          elevation: 0,
          shadowColor: Colors.transparent,
          textStyle: const TextStyle(
            fontSize: 16,
            fontWeight: FontWeight.w600,
            letterSpacing: 0.1,
          ),
          shape: const RoundedRectangleBorder(borderRadius: AppRadius.button),
        ),
      ),
      filledButtonTheme: FilledButtonThemeData(
        style: FilledButton.styleFrom(
          backgroundColor: AppColors.primary,
          foregroundColor: AppColors.textOnPrimary,
          minimumSize: const Size.fromHeight(minTouchTarget),
          textStyle: const TextStyle(fontSize: 16, fontWeight: FontWeight.w600),
          shape: const RoundedRectangleBorder(borderRadius: AppRadius.button),
        ),
      ),
      outlinedButtonTheme: OutlinedButtonThemeData(
        style: OutlinedButton.styleFrom(
          foregroundColor: AppColors.textPrimary,
          backgroundColor: AppColors.surface,
          minimumSize: const Size.fromHeight(minTouchTarget),
          side: const BorderSide(color: AppColors.borderStrong),
          textStyle: const TextStyle(fontSize: 16, fontWeight: FontWeight.w600),
          shape: const RoundedRectangleBorder(borderRadius: AppRadius.button),
        ),
      ),
      textButtonTheme: TextButtonThemeData(
        style: TextButton.styleFrom(
          foregroundColor: AppColors.primary,
          minimumSize: const Size(minTapArea, minTapArea),
          textStyle: const TextStyle(fontSize: 15, fontWeight: FontWeight.w600),
          shape: const RoundedRectangleBorder(borderRadius: AppRadius.button),
        ),
      ),
      iconButtonTheme: IconButtonThemeData(
        style: IconButton.styleFrom(
          foregroundColor: AppColors.textPrimary,
          minimumSize: const Size(minTapArea, minTapArea),
        ),
      ),

      // --- Inputs ----------------------------------------------------------
      // Filled and borderless at rest, with the border appearing only on focus.
      // All three references use this: it keeps a form full of fields from
      // reading as a grid of boxes.
      inputDecorationTheme: const InputDecorationTheme(
        filled: true,
        fillColor: AppColors.surface,
        contentPadding: EdgeInsets.symmetric(
          horizontal: AppSpacing.md,
          vertical: AppSpacing.md,
        ),
        hintStyle: TextStyle(
          color: AppColors.textTertiary,
          fontSize: 16,
          fontWeight: FontWeight.w400,
        ),
        labelStyle: TextStyle(
          color: AppColors.textSecondary,
          fontSize: 15,
        ),
        floatingLabelStyle: TextStyle(
          color: AppColors.primary,
          fontSize: 15,
          fontWeight: FontWeight.w600,
        ),
        prefixIconColor: AppColors.textTertiary,
        suffixIconColor: AppColors.textTertiary,
        border: OutlineInputBorder(
          borderRadius: AppRadius.button,
          borderSide: BorderSide(color: AppColors.border),
        ),
        enabledBorder: OutlineInputBorder(
          borderRadius: AppRadius.button,
          borderSide: BorderSide(color: AppColors.border),
        ),
        focusedBorder: OutlineInputBorder(
          borderRadius: AppRadius.button,
          borderSide: BorderSide(color: AppColors.primary, width: 1.6),
        ),
        errorBorder: OutlineInputBorder(
          borderRadius: AppRadius.button,
          borderSide: BorderSide(color: AppColors.danger),
        ),
        focusedErrorBorder: OutlineInputBorder(
          borderRadius: AppRadius.button,
          borderSide: BorderSide(color: AppColors.danger, width: 1.6),
        ),
        errorStyle: TextStyle(
          color: AppColors.danger,
          fontSize: 13,
          fontWeight: FontWeight.w500,
        ),
      ),

      // --- Surfaces --------------------------------------------------------
      cardTheme: const CardThemeData(
        elevation: 0,
        color: AppColors.surface,
        surfaceTintColor: Colors.transparent,
        shape: RoundedRectangleBorder(
          borderRadius: AppRadius.card,
          side: BorderSide(color: AppColors.border),
        ),
        margin: EdgeInsets.symmetric(
          horizontal: AppSpacing.gutter,
          vertical: AppSpacing.xs,
        ),
      ),
      dividerTheme: const DividerThemeData(
        color: AppColors.border,
        thickness: 1,
        space: 1,
      ),
      bottomSheetTheme: const BottomSheetThemeData(
        backgroundColor: AppColors.surface,
        surfaceTintColor: Colors.transparent,
        elevation: 0,
        shape: RoundedRectangleBorder(borderRadius: AppRadius.sheet),
        showDragHandle: true,
        dragHandleColor: AppColors.borderStrong,
      ),
      dialogTheme: const DialogThemeData(
        backgroundColor: AppColors.surface,
        surfaceTintColor: Colors.transparent,
        elevation: 0,
        shape: RoundedRectangleBorder(borderRadius: AppRadius.hero),
        titleTextStyle: TextStyle(
          fontSize: 20,
          fontWeight: FontWeight.w700,
          letterSpacing: -0.2,
          color: AppColors.textPrimary,
        ),
        contentTextStyle: TextStyle(
          fontSize: 15.5,
          height: 1.45,
          color: AppColors.textSecondary,
        ),
      ),
      listTileTheme: const ListTileThemeData(
        iconColor: AppColors.textSecondary,
        contentPadding: EdgeInsets.symmetric(horizontal: AppSpacing.md),
        minVerticalPadding: AppSpacing.sm,
      ),

      // --- Chips -----------------------------------------------------------
      chipTheme: const ChipThemeData(
        backgroundColor: AppColors.surface,
        selectedColor: AppColors.primarySoft,
        disabledColor: AppColors.surfaceMuted,
        checkmarkColor: AppColors.primary,
        side: BorderSide(color: AppColors.border),
        shape: RoundedRectangleBorder(borderRadius: AppRadius.chip),
        labelStyle: TextStyle(
          fontSize: 14,
          fontWeight: FontWeight.w600,
          color: AppColors.textSecondary,
        ),
        secondaryLabelStyle: TextStyle(
          fontSize: 14,
          fontWeight: FontWeight.w600,
          color: AppColors.primaryDark,
        ),
        padding: EdgeInsets.symmetric(
          horizontal: AppSpacing.sm,
          vertical: AppSpacing.xs,
        ),
      ),

      // --- Tabs ------------------------------------------------------------
      // Used by the schedule, gate log and directory screens, which previously
      // sat under the green bar with white-on-green tabs.
      tabBarTheme: const TabBarThemeData(
        labelColor: AppColors.primary,
        unselectedLabelColor: AppColors.textTertiary,
        indicatorColor: AppColors.primary,
        indicatorSize: TabBarIndicatorSize.label,
        dividerColor: AppColors.border,
        labelStyle: TextStyle(fontSize: 15, fontWeight: FontWeight.w700),
        unselectedLabelStyle:
            TextStyle(fontSize: 15, fontWeight: FontWeight.w500),
      ),

      // --- Feedback --------------------------------------------------------
      snackBarTheme: const SnackBarThemeData(
        behavior: SnackBarBehavior.floating,
        backgroundColor: AppColors.textPrimary,
        contentTextStyle: TextStyle(
          color: AppColors.surface,
          fontSize: 15,
          fontWeight: FontWeight.w500,
        ),
        actionTextColor: AppColors.accent,
        elevation: 0,
        insetPadding: EdgeInsets.all(AppSpacing.md),
        shape: RoundedRectangleBorder(borderRadius: AppRadius.button),
      ),
      progressIndicatorTheme: const ProgressIndicatorThemeData(
        color: AppColors.primary,
        linearTrackColor: AppColors.surfaceMuted,
        circularTrackColor: AppColors.surfaceMuted,
      ),
      switchTheme: SwitchThemeData(
        thumbColor: WidgetStateProperty.resolveWith(
          (states) => states.contains(WidgetState.selected)
              ? AppColors.surface
              : AppColors.surface,
        ),
        trackColor: WidgetStateProperty.resolveWith(
          (states) => states.contains(WidgetState.selected)
              ? AppColors.primary
              : AppColors.borderStrong,
        ),
        trackOutlineColor:
            const WidgetStatePropertyAll<Color>(Colors.transparent),
      ),
      navigationBarTheme: const NavigationBarThemeData(
        backgroundColor: AppColors.surface,
        surfaceTintColor: Colors.transparent,
        indicatorColor: AppColors.primarySoft,
        elevation: 0,
      ),

      // Replaces the platform default (a vertical slide on Android) with a
      // shared axis feel across every push. Part of closing out the "zero
      // animations anywhere" finding.
      pageTransitionsTheme: const PageTransitionsTheme(
        builders: {
          TargetPlatform.android: _FadeThroughTransitionBuilder(),
          TargetPlatform.iOS: _FadeThroughTransitionBuilder(),
        },
      ),
    );
  }

  /// The brief specifies light theme only, so this intentionally returns the
  /// light scheme. Keeping the getter means `app.dart` needs no special case
  /// and nothing can accidentally render half-dark.
  static ThemeData get dark => light;

  // ---------------------------------------------------------------------------
  // Type scale
  // ---------------------------------------------------------------------------
  //
  // The old theme defined three styles, so the largest and smallest text on a
  // screen differed by 4px and every card reached for
  // `titleMedium?.copyWith(fontWeight: w600)` to fake a heading. Snabbit's home
  // sets its section heading at roughly 24/700 against 14/500 labels — a 1.7x
  // jump that is most of why it scans instantly. This scale restores that range
  // while keeping body copy at or above the old sizes for SRS 5.4.
  //
  // Negative letter-spacing on the display sizes is what stops large Roboto
  // from looking loose; positive spacing on the small uppercase label is what
  // keeps it legible.
  static const TextTheme _textTheme = TextTheme(
    displaySmall: TextStyle(
      fontSize: 34,
      height: 1.14,
      fontWeight: FontWeight.w700,
      letterSpacing: -0.8,
      color: AppColors.textPrimary,
    ),
    headlineMedium: TextStyle(
      fontSize: 28,
      height: 1.2,
      fontWeight: FontWeight.w700,
      letterSpacing: -0.6,
      color: AppColors.textPrimary,
    ),
    headlineSmall: TextStyle(
      fontSize: 24,
      height: 1.25,
      fontWeight: FontWeight.w700,
      letterSpacing: -0.45,
      color: AppColors.textPrimary,
    ),
    titleLarge: TextStyle(
      fontSize: 20,
      height: 1.3,
      fontWeight: FontWeight.w700,
      letterSpacing: -0.3,
      color: AppColors.textPrimary,
    ),
    titleMedium: TextStyle(
      fontSize: 17,
      height: 1.35,
      fontWeight: FontWeight.w600,
      letterSpacing: -0.15,
      color: AppColors.textPrimary,
    ),
    titleSmall: TextStyle(
      fontSize: 15,
      height: 1.4,
      fontWeight: FontWeight.w600,
      color: AppColors.textPrimary,
    ),

    // Body sizes stay a step above Material's defaults (16/14/12), preserving
    // the original theme's readability intent.
    bodyLarge: TextStyle(
      fontSize: 16.5,
      height: 1.5,
      color: AppColors.textPrimary,
    ),
    bodyMedium: TextStyle(
      fontSize: 15,
      height: 1.5,
      color: AppColors.textSecondary,
    ),
    bodySmall: TextStyle(
      fontSize: 13.5,
      height: 1.45,
      color: AppColors.textTertiary,
    ),

    labelLarge: TextStyle(
      fontSize: 16,
      fontWeight: FontWeight.w600,
      color: AppColors.textPrimary,
    ),
    labelMedium: TextStyle(
      fontSize: 13,
      fontWeight: FontWeight.w600,
      color: AppColors.textSecondary,
    ),

    /// Eyebrows and badge text.
    labelSmall: TextStyle(
      fontSize: 11.5,
      fontWeight: FontWeight.w700,
      letterSpacing: 0.6,
      color: AppColors.textTertiary,
    ),
  );
}

/// A fade-through page transition: the outgoing page fades out and the incoming
/// one fades in while rising slightly.
///
/// Chosen over a slide because most navigation here is lateral (nav bar tabs,
/// menu destinations) rather than hierarchical, and a horizontal slide would
/// imply a depth relationship that does not exist.
class _FadeThroughTransitionBuilder extends PageTransitionsBuilder {
  const _FadeThroughTransitionBuilder();

  @override
  Widget buildTransitions<T>(
    PageRoute<T> route,
    BuildContext context,
    Animation<double> animation,
    Animation<double> secondaryAnimation,
    Widget child,
  ) {
    // Honour the platform "remove animations" accessibility setting.
    if (MediaQuery.maybeDisableAnimationsOf(context) ?? false) return child;

    final curved = CurvedAnimation(parent: animation, curve: AppMotion.enter);
    return FadeTransition(
      opacity: curved,
      child: SlideTransition(
        position: Tween<Offset>(
          begin: const Offset(0, 0.022),
          end: Offset.zero,
        ).animate(curved),
        child: child,
      ),
    );
  }
}
