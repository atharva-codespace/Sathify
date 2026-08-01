import 'package:flutter/material.dart';

import '../../core/theme/app_theme.dart';

enum AppButtonVariant {
  /// Filled brand green. One per screen — that is the whole point of rationing
  /// the accent.
  primary,

  /// Outlined on white. The default for anything secondary.
  secondary,

  /// No chrome. Inline actions and "skip"-style choices.
  text,

  /// Filled danger. Sign out, delete, reject.
  destructive,
}

/// The app's button.
///
/// Wraps the themed Material buttons to add two things the theme cannot express:
/// a press-scale micro-interaction, and a loading state that swaps the label for
/// a spinner **without changing the button's size** — a button that shrinks when
/// tapped is the single most common way a form flow feels cheap.
class AppButton extends StatefulWidget {
  const AppButton({
    super.key,
    required this.label,
    required this.onPressed,
    this.variant = AppButtonVariant.primary,
    this.icon,
    this.isLoading = false,
    this.expand = true,
  });

  const AppButton.secondary({
    super.key,
    required this.label,
    required this.onPressed,
    this.icon,
    this.isLoading = false,
    this.expand = true,
  }) : variant = AppButtonVariant.secondary;

  const AppButton.text({
    super.key,
    required this.label,
    required this.onPressed,
    this.icon,
    this.isLoading = false,
    this.expand = false,
  }) : variant = AppButtonVariant.text;

  const AppButton.destructive({
    super.key,
    required this.label,
    required this.onPressed,
    this.icon,
    this.isLoading = false,
    this.expand = true,
  }) : variant = AppButtonVariant.destructive;

  final String label;

  /// Null disables the button. A loading button is also non-interactive, so a
  /// double tap cannot submit a form twice.
  final VoidCallback? onPressed;

  final AppButtonVariant variant;
  final IconData? icon;
  final bool isLoading;

  /// Full width. True for primary actions, false for inline ones.
  final bool expand;

  @override
  State<AppButton> createState() => _AppButtonState();
}

class _AppButtonState extends State<AppButton> {
  bool _pressed = false;

  bool get _enabled => widget.onPressed != null && !widget.isLoading;

  @override
  Widget build(BuildContext context) {
    final reduceMotion = MediaQuery.maybeDisableAnimationsOf(context) ?? false;

    final child = widget.isLoading
        ? SizedBox(
            height: 20,
            width: 20,
            child: CircularProgressIndicator(
              strokeWidth: 2.4,
              color: _spinnerColour,
            ),
          )
        : _label;

    Widget button = switch (widget.variant) {
      AppButtonVariant.primary => ElevatedButton(
          onPressed: _enabled ? widget.onPressed : null,
          child: child,
        ),
      AppButtonVariant.secondary => OutlinedButton(
          onPressed: _enabled ? widget.onPressed : null,
          child: child,
        ),
      AppButtonVariant.text => TextButton(
          onPressed: _enabled ? widget.onPressed : null,
          child: child,
        ),
      AppButtonVariant.destructive => ElevatedButton(
          onPressed: _enabled ? widget.onPressed : null,
          style: ElevatedButton.styleFrom(
            backgroundColor: AppColors.danger,
            foregroundColor: AppColors.surface,
          ),
          child: child,
        ),
    };

    if (!widget.expand) {
      button = Align(alignment: Alignment.centerLeft, child: button);
    }

    return Listener(
      onPointerDown: (_) {
        if (_enabled) setState(() => _pressed = true);
      },
      onPointerUp: (_) => setState(() => _pressed = false),
      onPointerCancel: (_) => setState(() => _pressed = false),
      child: AnimatedScale(
        scale: _pressed && !reduceMotion ? 0.97 : 1,
        duration: AppMotion.fast,
        curve: AppMotion.standard,
        child: button,
      ),
    );
  }

  Color get _spinnerColour => switch (widget.variant) {
        AppButtonVariant.primary => AppColors.textOnPrimary,
        AppButtonVariant.destructive => AppColors.surface,
        _ => AppColors.primary,
      };

  Widget get _label {
    if (widget.icon == null) return Text(widget.label);
    return Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        Icon(widget.icon, size: AppIconSize.sm),
        const SizedBox(width: AppSpacing.xs),
        Flexible(child: Text(widget.label, overflow: TextOverflow.ellipsis)),
      ],
    );
  }
}
