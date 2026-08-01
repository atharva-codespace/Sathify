import 'package:flutter/material.dart';

import '../../core/theme/app_theme.dart';

/// Fades and lifts its child into place once, on first build.
///
/// The audit found no animation primitives anywhere in the app — every list
/// appeared as a hard cut. Wrapping list items in this is the cheapest way to
/// buy the "premium" feel the brief asks for, because the eye reads staggered
/// arrival as responsiveness.
///
/// [index] staggers consecutive items. It is capped by
/// [AppMotion.maxStaggerIndex] so item 30 in a long list does not animate a
/// second and a half after item 1 — past about the eighth item the user has
/// already scrolled and the delay just looks broken.
class AppFadeIn extends StatefulWidget {
  const AppFadeIn({
    super.key,
    required this.child,
    this.index = 0,
    this.offset = 12,
    this.duration = AppMotion.slow,
  });

  final Widget child;
  final int index;

  /// How far the child rises, in logical pixels. Small on purpose: a large
  /// travel distance reads as a page transition rather than as content settling.
  final double offset;

  final Duration duration;

  @override
  State<AppFadeIn> createState() => _AppFadeInState();
}

class _AppFadeInState extends State<AppFadeIn>
    with SingleTickerProviderStateMixin {
  late final AnimationController _controller = AnimationController(
    vsync: this,
    duration: widget.duration,
  );

  late final Animation<double> _animation = CurvedAnimation(
    parent: _controller,
    curve: AppMotion.enter,
  );

  @override
  void initState() {
    super.initState();
    final steps = widget.index.clamp(0, AppMotion.maxStaggerIndex);
    final delay = AppMotion.stagger * steps;

    if (delay == Duration.zero) {
      _controller.forward();
    } else {
      Future.delayed(delay, () {
        // The list may have been scrolled away and disposed during the delay.
        if (mounted) _controller.forward();
      });
    }
  }

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    if (MediaQuery.maybeDisableAnimationsOf(context) ?? false) {
      return widget.child;
    }

    return AnimatedBuilder(
      animation: _animation,
      builder: (context, child) => Opacity(
        opacity: _animation.value,
        child: Transform.translate(
          offset: Offset(0, (1 - _animation.value) * widget.offset),
          child: child,
        ),
      ),
      child: widget.child,
    );
  }
}

/// Cross-fades between whatever is currently being shown and its replacement.
///
/// Used at the boundary between a screen's loading skeleton and its real
/// content, so data arriving does not snap. Without this the skeleton work is
/// wasted — a hard swap draws more attention to the wait than no skeleton at all.
class AppSwitcher extends StatelessWidget {
  const AppSwitcher({super.key, required this.child});

  final Widget child;

  @override
  Widget build(BuildContext context) {
    return AnimatedSwitcher(
      duration: AppMotion.normal,
      switchInCurve: AppMotion.enter,
      switchOutCurve: AppMotion.standard,
      // The default layout builder stacks children centred, which makes a
      // full-height list jump while the outgoing child fades. Aligning to the
      // top keeps content anchored where it already was.
      layoutBuilder: (current, previous) => Stack(
        alignment: Alignment.topCenter,
        children: [...previous, if (current != null) current],
      ),
      child: child,
    );
  }
}
