import 'package:flutter/material.dart';

import '../../core/theme/app_theme.dart';

/// The standard container for grouped content.
///
/// Replaces Material's `Card`, whose `elevation: 1` produced the grey haze the
/// audit flagged. The references outline a resting card with a hairline and let
/// a soft, wide shadow do the lifting — crisp rather than foggy.
///
/// When [onTap] is given the card scales down very slightly on press. That one
/// micro-interaction, applied consistently, does more for perceived quality
/// than any amount of shadow tuning.
class AppCard extends StatefulWidget {
  const AppCard({
    super.key,
    required this.child,
    this.onTap,
    this.padding = const EdgeInsets.all(AppSpacing.md),
    this.margin = EdgeInsets.zero,
    this.color,
    this.borderColor,
    this.borderRadius = AppRadius.card,
    this.shadow,
    this.clip = false,
  });

  final Widget child;
  final VoidCallback? onTap;
  final EdgeInsetsGeometry padding;
  final EdgeInsetsGeometry margin;
  final Color? color;
  final Color? borderColor;
  final BorderRadius borderRadius;
  final List<BoxShadow>? shadow;

  /// Set when the card contains an image that must be clipped to the radius.
  /// Off by default — clipping forces a saveLayer, which is not free on the
  /// low-end devices this runs on.
  final bool clip;

  @override
  State<AppCard> createState() => _AppCardState();
}

class _AppCardState extends State<AppCard> {
  bool _pressed = false;

  @override
  Widget build(BuildContext context) {
    final reduceMotion = MediaQuery.maybeDisableAnimationsOf(context) ?? false;

    Widget card = AnimatedContainer(
      duration: AppMotion.fast,
      curve: AppMotion.standard,
      padding: widget.padding,
      decoration: BoxDecoration(
        color: widget.color ?? AppColors.surface,
        borderRadius: widget.borderRadius,
        border: Border.all(color: widget.borderColor ?? AppColors.border),
        boxShadow: _pressed ? null : (widget.shadow ?? AppShadow.sm),
      ),
      clipBehavior: widget.clip ? Clip.antiAlias : Clip.none,
      child: widget.child,
    );

    if (widget.onTap != null) {
      card = AnimatedScale(
        scale: _pressed && !reduceMotion ? 0.985 : 1,
        duration: AppMotion.fast,
        curve: AppMotion.standard,
        child: card,
      );
    }

    if (widget.onTap == null) {
      return Padding(padding: widget.margin, child: card);
    }

    return Padding(
      padding: widget.margin,
      child: GestureDetector(
        onTap: widget.onTap,
        onTapDown: (_) => setState(() => _pressed = true),
        onTapUp: (_) => setState(() => _pressed = false),
        onTapCancel: () => setState(() => _pressed = false),
        // Opaque so taps land on the padding, not just on the child.
        behavior: HitTestBehavior.opaque,
        child: card,
      ),
    );
  }
}

/// A full-bleed section of cards sharing one heading.
///
/// Urban Company's account screen groups rows this way: one white block with
/// hairline separators, rather than one bordered card per row. Cheaper visually
/// and much easier to scan for settings-style lists.
class AppCardGroup extends StatelessWidget {
  const AppCardGroup(
      {super.key, required this.children, this.margin = EdgeInsets.zero,});

  final List<Widget> children;
  final EdgeInsetsGeometry margin;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: margin,
      child: Container(
        decoration: BoxDecoration(
          color: AppColors.surface,
          borderRadius: AppRadius.card,
          border: Border.all(color: AppColors.border),
          boxShadow: AppShadow.sm,
        ),
        clipBehavior: Clip.antiAlias,
        child: Column(
          children: [
            for (var i = 0; i < children.length; i++) ...[
              if (i > 0)
                const Divider(
                    height: 1, indent: AppSpacing.md, endIndent: AppSpacing.md,),
              children[i],
            ],
          ],
        ),
      ),
    );
  }
}
