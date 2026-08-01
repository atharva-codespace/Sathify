import 'package:flutter/material.dart';

import '../../core/theme/app_theme.dart';

/// A shimmering placeholder block.
///
/// Replaces the 65 bare `Center(child: CircularProgressIndicator())` bodies the
/// audit found. A centred spinner throws away the layout you already know you
/// are about to draw, so every load reads as a full-screen flash.
///
/// This matters more here than in most apps: the backend runs on a free tier
/// where a cold request can take tens of seconds. A placeholder that matches the
/// real geometry makes the identical wait feel dramatically shorter, because the
/// screen looks like it is filling in rather than stalled.
///
/// Written by hand rather than pulling in the `shimmer` package — it is ~40
/// lines and adds no dependency to an app that already ships 20.
class AppSkeleton extends StatefulWidget {
  const AppSkeleton({
    super.key,
    this.width,
    this.height = 14,
    this.borderRadius = AppRadius.chip,
    this.circle = false,
  });

  /// A circular placeholder, for avatars.
  const AppSkeleton.circle({super.key, required double size})
      : width = size,
        height = size,
        borderRadius = AppRadius.chip,
        circle = true;

  final double? width;
  final double height;
  final BorderRadius borderRadius;
  final bool circle;

  @override
  State<AppSkeleton> createState() => _AppSkeletonState();
}

class _AppSkeletonState extends State<AppSkeleton>
    with SingleTickerProviderStateMixin {
  late final AnimationController _controller = AnimationController(
    vsync: this,
    duration: const Duration(milliseconds: 1250),
  );

  @override
  void initState() {
    super.initState();
    _controller.repeat();
  }

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final reduceMotion = MediaQuery.maybeDisableAnimationsOf(context) ?? false;

    final shape = BoxDecoration(
      color: AppColors.surfaceMuted,
      borderRadius: widget.circle ? null : widget.borderRadius,
      shape: widget.circle ? BoxShape.circle : BoxShape.rectangle,
    );

    if (reduceMotion) {
      return Container(
          width: widget.width, height: widget.height, decoration: shape,);
    }

    return AnimatedBuilder(
      animation: _controller,
      builder: (context, _) {
        // Sweep the highlight from just off the left edge to just off the
        // right, so the band never appears to pop in or out.
        final t = -0.3 + _controller.value * 1.6;
        return Container(
          width: widget.width,
          height: widget.height,
          decoration: shape.copyWith(
            gradient: LinearGradient(
              begin: Alignment.centerLeft,
              end: Alignment.centerRight,
              colors: const [
                AppColors.surfaceMuted,
                Color(0xFFF7F9F7),
                AppColors.surfaceMuted,
              ],
              stops: [
                (t - 0.3).clamp(0.0, 1.0),
                t.clamp(0.0, 1.0),
                (t + 0.3).clamp(0.0, 1.0),
              ],
            ),
          ),
        );
      },
    );
  }
}

/// A placeholder shaped like the worker/service/booking cards used across the
/// app: avatar, two lines of text, a short meta row.
///
/// Screens should prefer this over inventing their own, so a load looks the
/// same everywhere.
class AppSkeletonCard extends StatelessWidget {
  const AppSkeletonCard({super.key, this.hasAvatar = true});

  final bool hasAvatar;

  @override
  Widget build(BuildContext context) {
    return Container(
      margin: const EdgeInsets.only(bottom: AppSpacing.sm),
      padding: const EdgeInsets.all(AppSpacing.md),
      decoration: BoxDecoration(
        color: AppColors.surface,
        borderRadius: AppRadius.card,
        border: Border.all(color: AppColors.border),
      ),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          if (hasAvatar) ...[
            const AppSkeleton.circle(size: 48),
            const SizedBox(width: AppSpacing.sm),
          ],
          const Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                AppSkeleton(width: 150, height: 16),
                SizedBox(height: AppSpacing.xs + 2),
                AppSkeleton(width: 210, height: 12),
                SizedBox(height: AppSpacing.xs),
                Row(
                  children: [
                    AppSkeleton(width: 54, height: 12),
                    SizedBox(width: AppSpacing.sm),
                    AppSkeleton(width: 54, height: 12),
                  ],
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}

/// A full-body loading state: several [AppSkeletonCard]s down the page.
class AppSkeletonList extends StatelessWidget {
  const AppSkeletonList({super.key, this.count = 5, this.hasAvatar = true});

  final int count;
  final bool hasAvatar;

  @override
  Widget build(BuildContext context) {
    return ListView.builder(
      padding: const EdgeInsets.fromLTRB(
        AppSpacing.gutter,
        AppSpacing.sm,
        AppSpacing.gutter,
        AppSpacing.xl,
      ),
      // The placeholder is not interactive and must not steal the scroll.
      physics: const NeverScrollableScrollPhysics(),
      itemCount: count,
      itemBuilder: (_, __) => AppSkeletonCard(hasAvatar: hasAvatar),
    );
  }
}
