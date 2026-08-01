/// One import for the whole design system.
///
///     import '../../../../shared/design_system.dart';
///
/// Pulls in the tokens (via `app_theme.dart`, which re-exports them) plus every
/// shared component, so a screen never needs a column of widget imports and
/// there is exactly one place to look for "does a component for this already
/// exist?" before inventing another one.
library;

export '../core/theme/app_theme.dart';
export 'widgets/account_tile.dart';
export 'widgets/app_avatar.dart';
export 'widgets/app_bottom_nav.dart';
export 'widgets/app_button.dart';
export 'widgets/app_card.dart';
export 'widgets/app_chip.dart';
export 'widgets/app_entrance.dart';
export 'widgets/app_section_header.dart';
export 'widgets/app_skeleton.dart';
export 'widgets/app_snackbar.dart';
export 'widgets/app_state_views.dart';
