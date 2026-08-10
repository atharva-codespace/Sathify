import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../../../core/routing/app_router.dart';
import '../../../../shared/design_system.dart';
import '../../data/models/saved_account.dart';
import '../../data/models/user_model.dart';
import '../providers/auth_provider.dart';

/// The Account tab — profile, account switching, settings, and the way out.
///
/// -----------------------------------------------------------------------
/// THIS SCREEN HAD TO BE BUILT, NOT JUST REDESIGNED
/// -----------------------------------------------------------------------
/// Three of the four roles had no profile screen at all before this. Only
/// workers had one, and theirs is an *edit form* for KYC rather than a profile.
/// So a resident, guard or administrator had nowhere to see who they were
/// signed in as, no way to switch accounts, and — until Phase 3 — no reachable
/// sign-out whatsoever.
///
/// The quick-actions row is role-aware and deliberately excludes anything that
/// is already a bottom-nav tab for that role. Two routes to the same screen on
/// the same page is the redundancy this redesign exists to remove.
class AccountScreen extends ConsumerWidget {
  const AccountScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final auth = ref.watch(authProvider);
    final user = auth.user;
    final saved = ref.watch(savedAccountsProvider).valueOrNull ?? const [];

    if (user == null) {
      // Only reachable in the instant between a session resolving and the
      // profile landing. A skeleton rather than a spinner keeps it silent.
      return Scaffold(
        appBar: AppBar(title: const Text('Account')),
        body: const AppSkeletonList(count: 3),
      );
    }

    final others = saved.where((a) => a.userId != user.id).toList();
    final actions = _quickActionsFor(user.role);

    return Scaffold(
      appBar: AppBar(
        titleSpacing: AppSpacing.gutter,
        title: const Text('Account'),
      ),
      body: ListView(
        padding: const EdgeInsets.only(bottom: AppSpacing.huge),
        children: [
          AppFadeIn(child: _ProfileHeader(user: user)),

          if (actions.isNotEmpty)
            AppFadeIn(
              index: 1,
              child: Padding(
                padding: const EdgeInsets.fromLTRB(
                  AppSpacing.gutter,
                  AppSpacing.sm,
                  AppSpacing.gutter,
                  0,
                ),
                child: Row(
                  children: [
                    for (var i = 0; i < actions.length; i++) ...[
                      if (i > 0) const SizedBox(width: AppSpacing.sm),
                      Expanded(child: _ActionTile(action: actions[i])),
                    ],
                  ],
                ),
              ),
            ),

          // --- Switch account -------------------------------------------
          const AppEyebrow(text: 'Accounts on this device'),
          AppFadeIn(
            index: 2,
            child: AppCardGroup(
              margin: const EdgeInsets.symmetric(horizontal: AppSpacing.gutter),
              children: [
                for (final account in others)
                  AccountTile(
                    name: account.displayName,
                    subtitle: account.subtitle,
                    seed: account.userId,
                    onTap: auth.isSubmitting
                        ? () {}
                        : () => _switchTo(context, ref, account),
                  ),
                _LinkRow(
                  icon: Icons.person_add_alt_1_outlined,
                  label: others.isEmpty
                      ? 'Sign in to another account'
                      : 'Use a different account',
                  // Signing out lands on the login screen, which already offers
                  // the saved list plus "Use another account". A second control
                  // here that did the same thing would be pure duplication.
                  onTap: auth.isSubmitting
                      ? () {}
                      : () => ref.read(authProvider.notifier).logout(),
                ),
              ],
            ),
          ),

          // --- Your details ---------------------------------------------
          const AppEyebrow(text: 'Your details'),
          AppFadeIn(
            index: 3,
            child: AppCardGroup(
              margin: const EdgeInsets.symmetric(horizontal: AppSpacing.gutter),
              children: [
                _InfoRow(
                  icon: Icons.phone_outlined,
                  label: 'Phone',
                  value: user.phoneNumber,
                  trailing: user.isPhoneVerified
                      ? const AppStatusChip(
                          label: 'Verified',
                          tone: AppTone.success,
                          dense: true,
                        )
                      : null,
                ),
                if (user.email.isNotEmpty)
                  _InfoRow(
                    icon: Icons.mail_outline_rounded,
                    label: 'Email',
                    value: user.email,
                  ),
                if (user.societyName != null && user.societyName!.isNotEmpty)
                  _InfoRow(
                    icon: Icons.apartment_outlined,
                    label: 'Society',
                    value: user.societyName!,
                  ),
                // Only workers have an editable profile — it is the KYC form
                // an administrator reviews. Offering it to other roles would
                // link to a screen that does not apply to them.
                if (user.role == UserRole.worker)
                  _LinkRow(
                    icon: Icons.edit_outlined,
                    label: 'Edit my profile',
                    onTap: () => context.push(Routes.workerProfileEdit),
                  ),
              ],
            ),
          ),

          // --- Settings ---------------------------------------------------
          const AppEyebrow(text: 'Settings'),
          AppFadeIn(
            index: 4,
            child: AppCardGroup(
              margin: const EdgeInsets.symmetric(horizontal: AppSpacing.gutter),
              children: [
                _LinkRow(
                  icon: Icons.notifications_none_rounded,
                  label: 'Notifications',
                  onTap: () => context.push(Routes.notifications),
                ),
                _LinkRow(
                  icon: Icons.tune_rounded,
                  label: 'Notification settings',
                  onTap: () => context.push(Routes.notificationPreferences),
                ),
                _LinkRow(
                  icon: Icons.report_gmailerrorred_outlined,
                  label: 'Complaints',
                  onTap: () => context.push(Routes.complaints),
                ),
                _LinkRow(
                  icon: Icons.forum_outlined,
                  label: 'Ask about my records',
                  onTap: () => context.push(Routes.assistant),
                ),
              ],
            ),
          ),

          // --- Sign out ---------------------------------------------------
          const SizedBox(height: AppSpacing.xl),
          AppFadeIn(
            index: 5,
            child: Padding(
              padding:
                  const EdgeInsets.symmetric(horizontal: AppSpacing.gutter),
              child: Column(
                children: [
                  // Keeps this account in the switcher with a parked refresh
                  // token, so coming back is one tap. See AuthRepository.signOut.
                  AppButton.destructive(
                    label: 'Sign out',
                    icon: Icons.logout_rounded,
                    isLoading: auth.isSubmitting,
                    onPressed: () => ref.read(authProvider.notifier).logout(),
                  ),
                  const SizedBox(height: AppSpacing.xxs),
                  AppButton.text(
                    label: 'Sign out and forget this device',
                    expand: true,
                    onPressed: auth.isSubmitting
                        ? null
                        : () => _confirmForget(context, ref),
                  ),
                ],
              ),
            ),
          ),

          const SizedBox(height: AppSpacing.md),
          const Center(
            child: Text(
              'Sathify',
              style: TextStyle(
                fontSize: 12,
                fontWeight: FontWeight.w700,
                letterSpacing: 1.2,
                color: AppColors.textTertiary,
              ),
            ),
          ),
        ],
      ),
    );
  }

  /// Shortcuts that are *not* already a bottom-nav tab for this role.
  ///
  /// A guard gets none: their whole job is three tabs wide, and inventing
  /// filler here would be worse than the empty space.
  List<_QuickAction> _quickActionsFor(UserRole role) {
    switch (role) {
      case UserRole.resident:
        return const [
          _QuickAction(
            Icons.handshake_outlined,
            'My hires',
            Routes.engagements,
          ),
          _QuickAction(
            Icons.event_note_outlined,
            'Bookings',
            Routes.myBookings,
          ),
          _QuickAction(
            Icons.star_outline_rounded,
            'Rate work',
            Routes.rateJobs,
          ),
        ];
      case UserRole.worker:
        return const [
          _QuickAction(
            Icons.verified_outlined,
            'Trust score',
            Routes.myTrustScore,
          ),
          _QuickAction(
            Icons.badge_outlined,
            'Verification',
            Routes.workerOnboarding,
          ),
          _QuickAction(
            Icons.star_outline_rounded,
            'Rate work',
            Routes.rateJobs,
          ),
        ];
      case UserRole.societyAdmin:
        return const [
          _QuickAction(
            Icons.description_outlined,
            'Reports',
            Routes.adminReports,
          ),
          _QuickAction(
            Icons.badge_outlined,
            'Approvals',
            Routes.workerApprovals,
          ),
          _QuickAction(Icons.flag_outlined, 'Flags', Routes.reviewFlags),
        ];
      case UserRole.guard:
      case UserRole.unknown:
        return const [];
    }
  }

  Future<void> _switchTo(
    BuildContext context,
    WidgetRef ref,
    SavedAccount account,
  ) async {
    final router = GoRouter.of(context);
    final messenger = ScaffoldMessenger.of(context);

    final switched = await ref.read(authProvider.notifier).switchTo(account);
    if (!switched) {
      showAppSnackBarOn(
        messenger,
        'That session expired. Sign in again to get a new code.',
        tone: AppTone.warning,
      );
      return;
    }

    // Land on the new role's home rather than leaving, say, a guard sitting on
    // the Account tab of a resident's tab set.
    final role = ref.read(authProvider).user?.role ?? UserRole.unknown;
    router.go(homeRouteForRole(role));
    showAppSnackBarOn(
      messenger,
      'Signed in as ${account.displayName}.',
      tone: AppTone.success,
    );
  }

  Future<void> _confirmForget(BuildContext context, WidgetRef ref) async {
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (dialogContext) => AlertDialog(
        title: const Text('Forget this account?'),
        content: const Text(
          'You will be signed out and this account will be removed from the '
          'list on this device. Signing back in will need a new code.',
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(dialogContext).pop(false),
            child: const Text('Cancel'),
          ),
          TextButton(
            onPressed: () => Navigator.of(dialogContext).pop(true),
            style: TextButton.styleFrom(foregroundColor: AppColors.danger),
            child: const Text('Sign out and forget'),
          ),
        ],
      ),
    );

    if (confirmed != true) return;
    await ref.read(authProvider.notifier).logout(forget: true);
  }
}

class _QuickAction {
  const _QuickAction(this.icon, this.label, this.route);

  final IconData icon;
  final String label;
  final String route;
}

class _ActionTile extends StatelessWidget {
  const _ActionTile({required this.action});

  final _QuickAction action;

  @override
  Widget build(BuildContext context) {
    return AppCard(
      onTap: () => context.push(action.route),
      padding: const EdgeInsets.symmetric(vertical: AppSpacing.sm),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          Container(
            width: 38,
            height: 38,
            decoration: const BoxDecoration(
              color: AppColors.primarySoft,
              shape: BoxShape.circle,
            ),
            child: Icon(
              action.icon,
              size: AppIconSize.md,
              color: AppColors.primary,
            ),
          ),
          const SizedBox(height: AppSpacing.xs),
          Text(
            action.label,
            maxLines: 1,
            overflow: TextOverflow.ellipsis,
            style: const TextStyle(
              fontSize: 12.5,
              fontWeight: FontWeight.w600,
              color: AppColors.textPrimary,
            ),
          ),
        ],
      ),
    );
  }
}

/// The identity block.
///
/// Reads as a single confident statement of who you are — large portrait, name,
/// role and society — rather than a form. Approval state sits here because it
/// is the single fact that most changes what the rest of the app will let this
/// person do.
class _ProfileHeader extends StatelessWidget {
  const _ProfileHeader({required this.user});

  final UserModel user;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final society = user.societyName;

    return Padding(
      padding: const EdgeInsets.fromLTRB(
        AppSpacing.gutter,
        AppSpacing.xs,
        AppSpacing.gutter,
        0,
      ),
      child: AppCard(
        padding: const EdgeInsets.all(AppSpacing.lg),
        child: Row(
          children: [
            AppAvatar(
              name: user.fullName,
              seed: user.id,
              size: 72,
              showRing: true,
            ),
            const SizedBox(width: AppSpacing.md),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                mainAxisSize: MainAxisSize.min,
                children: [
                  Text(
                    user.fullName,
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style: theme.textTheme.titleLarge,
                  ),
                  const SizedBox(height: 2),
                  Text(
                    society == null || society.isEmpty
                        ? _roleLabel(user.role)
                        : '${_roleLabel(user.role)} · $society',
                    maxLines: 2,
                    overflow: TextOverflow.ellipsis,
                    style: theme.textTheme.bodySmall,
                  ),
                  const SizedBox(height: AppSpacing.xs),
                  if (user.isApproved)
                    const AppStatusChip(
                      label: 'Verified',
                      tone: AppTone.success,
                      icon: Icons.verified_rounded,
                      dense: true,
                    )
                  else
                    const AppStatusChip(
                      label: 'Awaiting approval',
                      tone: AppTone.warning,
                      icon: Icons.hourglass_top_rounded,
                      dense: true,
                    ),
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }

  String _roleLabel(UserRole role) => switch (role) {
        UserRole.resident => 'Resident',
        UserRole.worker => 'Worker',
        UserRole.guard => 'Security guard',
        UserRole.societyAdmin => 'Administrator',
        UserRole.unknown => 'Account',
      };
}

/// A read-only fact about the account.
class _InfoRow extends StatelessWidget {
  const _InfoRow({
    required this.icon,
    required this.label,
    required this.value,
    this.trailing,
  });

  final IconData icon;
  final String label;
  final String value;
  final Widget? trailing;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(
        horizontal: AppSpacing.md,
        vertical: AppSpacing.sm,
      ),
      child: Row(
        children: [
          Icon(icon, size: AppIconSize.md, color: AppColors.textTertiary),
          const SizedBox(width: AppSpacing.sm),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              mainAxisSize: MainAxisSize.min,
              children: [
                Text(
                  label,
                  style: const TextStyle(
                    fontSize: 12,
                    fontWeight: FontWeight.w600,
                    color: AppColors.textTertiary,
                  ),
                ),
                Text(
                  value,
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: Theme.of(context).textTheme.titleSmall,
                ),
              ],
            ),
          ),
          if (trailing != null) trailing!,
        ],
      ),
    );
  }
}

class _LinkRow extends StatelessWidget {
  const _LinkRow({
    required this.icon,
    required this.label,
    required this.onTap,
  });

  final IconData icon;
  final String label;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    return Material(
      color: Colors.transparent,
      child: InkWell(
        onTap: onTap,
        child: Padding(
          padding: const EdgeInsets.symmetric(
            horizontal: AppSpacing.md,
            vertical: AppSpacing.md,
          ),
          child: Row(
            children: [
              Icon(icon, size: AppIconSize.md, color: AppColors.textSecondary),
              const SizedBox(width: AppSpacing.sm),
              Expanded(
                child:
                    Text(label, style: Theme.of(context).textTheme.titleSmall),
              ),
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
