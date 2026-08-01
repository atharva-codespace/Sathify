import 'package:flutter/material.dart';

import '../../core/theme/app_theme.dart';
import 'app_avatar.dart';

/// A row in the saved-accounts list on the login screen, and in the account
/// switcher on the profile screen.
///
/// Takes plain fields rather than a `SavedAccount` model so this component does
/// not depend on Phase 3's storage layer — the design system stays buildable and
/// reviewable on its own.
///
/// Layout follows Gmail's switcher: avatar, name, then the disambiguating
/// detail underneath. Here that detail is role and society rather than an email
/// address, which is genuinely more useful — the same person may hold a resident
/// account in one society and an admin account in another, and
/// "Priya · Resident, Green Valley" separates those where a phone number cannot.
class AccountTile extends StatelessWidget {
  const AccountTile({
    super.key,
    required this.name,
    required this.subtitle,
    required this.onTap,
    this.seed = 0,
    this.imageUrl,
    this.isActive = false,
    this.onForget,
    this.trailing,
  });

  final String name;
  final String subtitle;
  final VoidCallback onTap;
  final int seed;
  final String? imageUrl;

  /// Marks the currently signed-in account when this appears in the switcher.
  final bool isActive;

  /// Shows the "forget this account" affordance. Null hides it — the active
  /// account cannot be forgotten from under itself.
  final VoidCallback? onForget;

  /// Overrides the default trailing widget (chevron, or tick when [isActive]).
  final Widget? trailing;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);

    return Material(
      color: Colors.transparent,
      child: InkWell(
        onTap: onTap,
        child: Padding(
          padding: const EdgeInsets.symmetric(
            horizontal: AppSpacing.md,
            vertical: AppSpacing.sm,
          ),
          child: Row(
            children: [
              AppAvatar(
                name: name,
                seed: seed,
                imageUrl: imageUrl,
                size: 46,
                showRing: isActive,
              ),
              const SizedBox(width: AppSpacing.sm),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    Text(
                      name,
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: theme.textTheme.titleSmall,
                    ),
                    const SizedBox(height: 1),
                    Text(
                      subtitle,
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: theme.textTheme.bodySmall,
                    ),
                  ],
                ),
              ),
              const SizedBox(width: AppSpacing.xs),
              if (trailing != null)
                trailing!
              else if (isActive)
                const Icon(
                  Icons.check_circle_rounded,
                  color: AppColors.primary,
                  size: AppIconSize.md,
                )
              else if (onForget != null)
                IconButton(
                  onPressed: onForget,
                  tooltip: 'Forget this account',
                  icon: const Icon(Icons.close_rounded),
                  iconSize: AppIconSize.sm,
                  color: AppColors.textTertiary,
                  // Keeps the 44px tap target without widening the row.
                  constraints:
                      const BoxConstraints.tightFor(width: 44, height: 44),
                )
              else
                const Icon(
                  Icons.chevron_right_rounded,
                  color: AppColors.textTertiary,
                  size: AppIconSize.md,
                ),
            ],
          ),
        ),
      ),
    );
  }
}
