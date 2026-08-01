import 'package:flutter/material.dart';

import '../../core/theme/app_theme.dart';

/// One destination in the bottom navigation bar.
class AppNavItem {
  const AppNavItem({
    required this.icon,
    required this.label,
    IconData? activeIcon,
    this.badgeCount,
  }) : activeIcon = activeIcon ?? icon;

  final IconData icon;

  /// Usually the filled variant of [icon]. Shape carries the active state as
  /// well as colour, which keeps the bar legible for colour-blind users and in
  /// direct sunlight — this app gets used at a gate, outdoors.
  final IconData activeIcon;

  final String label;

  /// Drives the red dot. Null or zero shows nothing.
  final int? badgeCount;
}

/// The persistent bottom navigation bar.
///
/// Purely presentational: it knows the current index and reports taps. Phase 4
/// wires it to a `StatefulShellRoute` so each tab keeps its own navigation
/// stack, and no route path in `Routes` changes.
///
/// Built rather than themed from `NavigationBar` because Material 3's version
/// insists on a pill indicator sized to its own metrics and will not sit at the
/// 64dp height the references use — Book My Bai, Urban Company and Snabbit all
/// run a compact bar with the label always visible.
class AppBottomNav extends StatelessWidget {
  const AppBottomNav({
    super.key,
    required this.items,
    required this.currentIndex,
    required this.onTap,
  });

  final List<AppNavItem> items;
  final int currentIndex;
  final ValueChanged<int> onTap;

  @override
  Widget build(BuildContext context) {
    return Container(
      decoration: const BoxDecoration(
        color: AppColors.surface,
        border: Border(top: BorderSide(color: AppColors.border)),
        boxShadow: AppShadow.lifted,
      ),
      child: SafeArea(
        top: false,
        child: SizedBox(
          height: 62,
          child: Row(
            children: [
              for (var i = 0; i < items.length; i++)
                Expanded(
                  child: _NavTab(
                    item: items[i],
                    selected: i == currentIndex,
                    onTap: () => onTap(i),
                  ),
                ),
            ],
          ),
        ),
      ),
    );
  }
}

class _NavTab extends StatelessWidget {
  const _NavTab({
    required this.item,
    required this.selected,
    required this.onTap,
  });

  final AppNavItem item;
  final bool selected;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final colour = selected ? AppColors.primary : AppColors.textTertiary;

    return Semantics(
      selected: selected,
      button: true,
      label: item.label,
      child: InkWell(
        onTap: onTap,
        // A circular splash on a 60dp-wide tab bleeds into its neighbours.
        splashColor: AppColors.primarySoft,
        highlightColor: Colors.transparent,
        child: Column(
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            // The tinted pill behind the active icon, as on Book My Bai's bar.
            // Animating its width rather than cross-fading two widgets keeps the
            // icon from jumping as the indicator grows.
            AnimatedContainer(
              duration: AppMotion.normal,
              curve: AppMotion.standard,
              padding: EdgeInsets.symmetric(
                horizontal: selected ? AppSpacing.md : AppSpacing.xs,
                vertical: AppSpacing.xxs,
              ),
              decoration: BoxDecoration(
                color: selected ? AppColors.primarySoft : Colors.transparent,
                borderRadius: BorderRadius.circular(AppRadius.pill),
              ),
              child: _IconWithBadge(
                icon: selected ? item.activeIcon : item.icon,
                colour: colour,
                badgeCount: item.badgeCount,
              ),
            ),
            const SizedBox(height: 3),
            AnimatedDefaultTextStyle(
              duration: AppMotion.normal,
              curve: AppMotion.standard,
              style: TextStyle(
                fontSize: 11.5,
                fontWeight: selected ? FontWeight.w700 : FontWeight.w500,
                color: colour,
              ),
              child: Text(item.label,
                  maxLines: 1, overflow: TextOverflow.ellipsis,),
            ),
          ],
        ),
      ),
    );
  }
}

class _IconWithBadge extends StatelessWidget {
  const _IconWithBadge({
    required this.icon,
    required this.colour,
    this.badgeCount,
  });

  final IconData icon;
  final Color colour;
  final int? badgeCount;

  @override
  Widget build(BuildContext context) {
    final glyph = Icon(icon, size: AppIconSize.md, color: colour);
    final count = badgeCount ?? 0;
    if (count <= 0) return glyph;

    return Stack(
      clipBehavior: Clip.none,
      children: [
        glyph,
        Positioned(
          right: -5,
          top: -3,
          child: Container(
            padding: const EdgeInsets.symmetric(horizontal: 4.5, vertical: 1),
            constraints: const BoxConstraints(minWidth: 16),
            decoration: BoxDecoration(
              color: AppColors.danger,
              borderRadius: BorderRadius.circular(AppRadius.pill),
              // Separates the badge from the icon behind it without a shadow.
              border: Border.all(color: AppColors.surface, width: 1.5),
            ),
            child: Text(
              count > 9 ? '9+' : '$count',
              textAlign: TextAlign.center,
              style: const TextStyle(
                color: Colors.white,
                fontSize: 9.5,
                fontWeight: FontWeight.w700,
                height: 1.25,
              ),
            ),
          ),
        ),
      ],
    );
  }
}
