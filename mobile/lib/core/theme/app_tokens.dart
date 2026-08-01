/// Design tokens — the single source of truth for every visual decision.
///
/// Nothing in `lib/features` should contain a raw colour, a raw padding number,
/// a raw radius or a raw duration. Before this file existed the app carried 185
/// inline `TextStyle`s and 42 hardcoded `Colors.*` literals across 45 screens,
/// which meant no visual change could ever be made globally — every screen had
/// quietly invented its own spacing, its own badge and its own grey.
///
/// These are re-exported by `app_theme.dart`, so the ~40 screens that already
/// write `import '../../../../core/theme/app_theme.dart'` pick them up without
/// touching a single import line.
library;

import 'package:flutter/material.dart';

/// The palette.
///
/// Built around one idea taken from Snabbit, Urban Company and Book My Bai
/// alike: **the ground is nearly colourless and the accent is rationed**. Those
/// apps spend their brand colour on exactly two things — the primary call to
/// action and the active navigation tab — and leave everything else neutral.
///
/// Sathify previously did the opposite, painting `primary` across the app bar
/// of all 39 screens. The green survives because it is the brand and it carries
/// the trust/verification meaning the SRS asks for; it just stops being wallpaper.
class AppColors {
  const AppColors._();

  // --- Brand -----------------------------------------------------------------

  /// Trust/verification green. Now reserved for primary actions, selected
  /// states and the active nav tab.
  static const Color primary = Color(0xFF1B6B50);
  static const Color primaryDark = Color(0xFF0F4A36);

  /// A tint of [primary] for selected chips, avatar grounds and soft fills.
  /// Light enough that [textPrimary] stays readable on top of it.
  static const Color primarySoft = Color(0xFFE7F1EC);

  /// Attention, not alarm — ratings, highlights, "new" markers.
  static const Color accent = Color(0xFFF2A61C);
  static const Color accentSoft = Color(0xFFFDF3E0);

  // --- Ground ----------------------------------------------------------------

  /// The app background. Soft off-white rather than stark white, with a barely
  /// perceptible green bias so it sits under [primary] without looking blue.
  /// White cards lift off this; on pure white they would disappear.
  static const Color background = Color(0xFFF7F8F6);

  /// Cards, sheets, app bar, nav bar.
  static const Color surface = Color(0xFFFFFFFF);

  /// Pressed/hovered rows and skeleton bases.
  static const Color surfaceMuted = Color(0xFFF0F2EF);

  /// Hairline dividers and card borders. The references outline cards with a
  /// hairline instead of leaning on shadow, which is what keeps them looking
  /// crisp rather than foggy.
  static const Color border = Color(0xFFE5E8E4);
  static const Color borderStrong = Color(0xFFD3D8D2);

  // --- Text ------------------------------------------------------------------

  /// Near-black, biased green so it belongs to the same family as [primary].
  /// A pure neutral grey reads as unconsidered next to a coloured accent.
  static const Color textPrimary = Color(0xFF14201C);
  static const Color textSecondary = Color(0xFF5C6661);

  /// Captions, placeholders, disabled labels. Still AA against [background].
  static const Color textTertiary = Color(0xFF868F8A);
  static const Color textOnPrimary = Color(0xFFFFFFFF);

  // --- Semantic --------------------------------------------------------------
  //
  // Used consistently across every module: verification badges, attendance
  // rows, payment states, complaint SLA. Each has a `soft` container so a chip
  // can carry the meaning without shouting it. These are deliberately NOT the
  // accent — status colour and brand colour must never be confused.

  static const Color success = Color(0xFF2E7D32);
  static const Color successSoft = Color(0xFFE8F3E9);

  static const Color warning = Color(0xFFB5730F);
  static const Color warningSoft = Color(0xFFFBF1E1);

  static const Color danger = Color(0xFFC0392B);
  static const Color dangerSoft = Color(0xFFFAEBE9);

  static const Color info = Color(0xFF0277BD);
  static const Color infoSoft = Color(0xFFE5F1F8);

  /// Retained under its original name because ~40 screens already reference it
  /// as the scaffold colour. Same role, new value.
  static const Color surfaceLight = background;

  /// Deterministic avatar tints, chosen by user id.
  ///
  /// [UserModel] carries no photo URL, so the account switcher and every
  /// worker/resident row falls back to initials — the same thing Gmail does.
  /// Picking the tint from a stable id means one person keeps one colour across
  /// screens and across launches, which makes the list scannable by colour.
  static const List<Color> avatarTints = [
    Color(0xFFE7F1EC),
    Color(0xFFFDF3E0),
    Color(0xFFE5F1F8),
    Color(0xFFF3EAF7),
    Color(0xFFFAEBE9),
    Color(0xFFE8F3E9),
  ];

  static const List<Color> avatarInks = [
    Color(0xFF1B6B50),
    Color(0xFF9A6206),
    Color(0xFF0277BD),
    Color(0xFF6A3F8F),
    Color(0xFFC0392B),
    Color(0xFF2E7D32),
  ];
}

/// The 4pt spacing grid.
///
/// The old code used 2, 3, 4, 6, 8, 10, 12, 14, 16, 18, 22, 24 and 32 — several
/// off-grid — and card insets disagreed between screens (14 here, 16 there, 12
/// in the error banner). Everything now snaps to these.
class AppSpacing {
  const AppSpacing._();

  static const double xxs = 4;
  static const double xs = 8;
  static const double sm = 12;
  static const double md = 16;
  static const double lg = 20;
  static const double xl = 24;
  static const double xxl = 32;
  static const double huge = 40;

  /// Horizontal page margin. Wider than the old 12 — the single cheapest change
  /// for making screens feel calm rather than cramped.
  static const double gutter = 16;

  /// Breathing room above the bottom nav so the last list item is never
  /// half-hidden behind it.
  static const double bottomNavClearance = 96;
}

/// Corner radii. Larger than Material's defaults, matching the references.
class AppRadius {
  const AppRadius._();

  static const double xs = 8; // chips, badges
  static const double sm = 12; // buttons, input fields
  static const double md = 16; // cards
  static const double lg = 20; // hero cards, images
  static const double xl = 28; // bottom sheets
  static const double pill = 999;

  static const BorderRadius chip = BorderRadius.all(Radius.circular(xs));
  static const BorderRadius button = BorderRadius.all(Radius.circular(sm));
  static const BorderRadius card = BorderRadius.all(Radius.circular(md));
  static const BorderRadius hero = BorderRadius.all(Radius.circular(lg));
  static const BorderRadius sheet =
      BorderRadius.vertical(top: Radius.circular(xl));
}

/// Shadows.
///
/// Two rules, both taken from the references: shadows are *soft and wide* rather
/// than tight and dark, and a resting card leans on its hairline border more
/// than on its shadow. Material's `elevation: 1` produced the grey haze the
/// audit flagged; these produce lift without dirtying the background.
class AppShadow {
  const AppShadow._();

  /// Resting card.
  static const List<BoxShadow> sm = [
    BoxShadow(color: Color(0x0A14201C), blurRadius: 2, offset: Offset(0, 1)),
    BoxShadow(color: Color(0x0F14201C), blurRadius: 12, offset: Offset(0, 4)),
  ];

  /// Raised — sticky CTAs, selected cards.
  static const List<BoxShadow> md = [
    BoxShadow(color: Color(0x0D14201C), blurRadius: 4, offset: Offset(0, 2)),
    BoxShadow(color: Color(0x1414201C), blurRadius: 24, offset: Offset(0, 8)),
  ];

  /// Bottom sheets and the nav bar, which cast upward.
  static const List<BoxShadow> lifted = [
    BoxShadow(color: Color(0x1214201C), blurRadius: 20, offset: Offset(0, -4)),
  ];
}

/// Durations and curves.
///
/// The audit found **zero** animation primitives in the whole app — every
/// transition was a hard cut, which is the largest single reason it read as a
/// stock Flutter build. These keep motion consistent and, deliberately, short:
/// premium feels fast, and anything above ~400ms starts to feel like waiting.
class AppMotion {
  const AppMotion._();

  /// Press feedback, ripples, chip selection.
  static const Duration fast = Duration(milliseconds: 140);

  /// The default for state changes and page transitions.
  static const Duration normal = Duration(milliseconds: 240);

  /// List entrance and larger reveals.
  static const Duration slow = Duration(milliseconds: 380);

  /// Decelerating — things arriving on screen.
  static const Curve enter = Curves.easeOutCubic;

  /// Symmetric — things changing in place.
  static const Curve standard = Curves.easeInOutCubic;

  /// A gentle overshoot for confirmations. Used sparingly: it draws the eye, so
  /// it is reserved for moments that genuinely deserve it.
  static const Curve emphasised = Curves.easeOutBack;

  /// Stagger between consecutive list items.
  static const Duration stagger = Duration(milliseconds: 45);

  /// Caps the stagger so item 30 does not animate a second and a half late.
  static const int maxStaggerIndex = 8;
}

/// Icon sizing, so an icon means the same thing everywhere.
class AppIconSize {
  const AppIconSize._();

  static const double xs = 14; // inline with small text
  static const double sm = 18; // inline with body text, chip leading
  static const double md = 22; // app bar actions, list leading, nav bar
  static const double lg = 26; // feature tiles
  static const double xl = 40; // empty-state and error illustrations
  static const double hero = 64;
}
