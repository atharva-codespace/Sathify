import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../../../core/routing/app_router.dart';
import '../../../../shared/design_system.dart';
import '../../../notifications/presentation/widgets/notification_bell.dart';
import '../../../societies/data/models/society_models.dart';
import '../../../societies/presentation/providers/society_provider.dart';
import '../../../workers/presentation/providers/worker_provider.dart';
import '../../data/models/user_model.dart';
import '../providers/auth_provider.dart';

/// Shown to a signed-in user whose account an administrator has not approved.
///
/// Registration grants nothing on its own (SRS 3.1, 3.2), but leaving the user
/// at a login screen or an empty dashboard would look like a broken app. This
/// screen tells them exactly where they are and what happens next.
class PendingApprovalScreen extends ConsumerWidget {
  const PendingApprovalScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final state = ref.watch(authProvider);
    final theme = Theme.of(context);
    final user = state.user;

    return Scaffold(
      appBar: AppBar(
        title: const Text('Account pending'),
        actions: [
          // The one notification this user is waiting for — approved, or
          // rejected and why — lands in the centre. Reachable from here or it
          // may as well not exist for them.
          const NotificationBell(),
          IconButton(
            icon: const Icon(Icons.logout),
            tooltip: 'Sign out',
            onPressed: () => ref.read(authProvider.notifier).logout(),
          ),
        ],
      ),
      body: RefreshIndicator(
        // Pull-to-refresh re-checks approval status without signing out.
        onRefresh: () => ref.read(authProvider.notifier).restoreSession(),
        child: ListView(
          physics: const AlwaysScrollableScrollPhysics(),
          padding: const EdgeInsets.fromLTRB(
            AppSpacing.gutter,
            AppSpacing.lg,
            AppSpacing.gutter,
            AppSpacing.xxl,
          ),
          children: [
            AppFadeIn(
              child: Column(
                children: [
                  // A soft disc rather than a bare 80px glyph: this screen is
                  // the whole experience for someone waiting, so it should feel
                  // designed rather than like a placeholder.
                  Container(
                    width: 88,
                    height: 88,
                    decoration: const BoxDecoration(
                      color: AppColors.warningSoft,
                      shape: BoxShape.circle,
                    ),
                    child: const Icon(
                      Icons.hourglass_top_rounded,
                      size: 42,
                      color: AppColors.warning,
                    ),
                  ),
                  const SizedBox(height: AppSpacing.lg),
                  Text(
                    'Waiting for approval',
                    textAlign: TextAlign.center,
                    style: theme.textTheme.headlineSmall,
                  ),
                  const SizedBox(height: AppSpacing.xs),
                  Text(
                    _messageFor(user),
                    textAlign: TextAlign.center,
                    style: theme.textTheme.bodyMedium,
                  ),
                ],
              ),
            ),
            const SizedBox(height: AppSpacing.xl),
            AppFadeIn(
              index: 1,
              child: AppCard(
                child: Column(
                  children: [
                    _InfoRow(label: 'Name', value: user?.fullName ?? '—'),
                    const Divider(height: AppSpacing.xl),
                    _InfoRow(label: 'Phone', value: user?.phoneNumber ?? '—'),
                    const Divider(height: AppSpacing.xl),
                    _InfoRow(
                      label: 'Society',
                      value: user?.societyName ?? 'Not set',
                    ),
                  ],
                ),
              ),
            ),
            // A resident cannot be approved until they have claimed a flat —
            // that submission is what the administrator reviews. Surface it
            // here rather than leaving them waiting on a step they have not
            // taken yet.
            if (user?.role == UserRole.resident) ...[
              const SizedBox(height: 24),
              Consumer(
                builder: (context, ref, _) {
                  final profile = ref.watch(myResidentProfileProvider);
                  return profile.when(
                    loading: () => const SizedBox.shrink(),
                    error: (_, __) => const SizedBox.shrink(),
                    data: (resident) {
                      if (resident != null) {
                        return _ClaimedFlatNotice(resident: resident);
                      }
                      return AppButton(
                        label: 'Choose your flat',
                        icon: Icons.home_outlined,
                        onPressed: () async {
                          final claimed =
                              await context.push<bool>(Routes.claimFlat);
                          if (claimed ?? false) {
                            ref.invalidate(myResidentProfileProvider);
                          }
                        },
                      );
                    },
                  );
                },
              ),
            ],

            // Likewise a worker cannot be approved until they have built a
            // profile and uploaded their Aadhaar. Without this the pending
            // screen is a dead end: the account sits unapproved forever because
            // the step that gets it approved was never reachable.
            if (user?.role == UserRole.worker) ...[
              const SizedBox(height: 24),
              Consumer(
                builder: (context, ref, _) {
                  final profile = ref.watch(myWorkerProfileProvider);
                  return profile.when(
                    loading: () => const SizedBox.shrink(),
                    error: (_, __) => const SizedBox.shrink(),
                    data: (worker) {
                      final steps = worker?.remainingSteps ?? const <String>[];
                      return Column(
                        children: [
                          if (steps.isNotEmpty) ...[
                            AppStatusChip(
                              label: steps.length == 1
                                  ? 'One thing left: ${steps.first.toLowerCase()}'
                                  : '${steps.length} things left before you can be verified',
                              tone: AppTone.warning,
                              icon: Icons.checklist_rounded,
                            ),
                            const SizedBox(height: AppSpacing.sm),
                          ],
                          AppButton(
                            label: worker == null
                                ? 'Start getting verified'
                                : 'Finish getting verified',
                            icon: Icons.badge_outlined,
                            onPressed: () async {
                              await context.push<bool>(Routes.workerOnboarding);
                              ref.invalidate(myWorkerProfileProvider);
                            },
                          ),
                        ],
                      );
                    },
                  );
                },
              ),
            ],

            const SizedBox(height: AppSpacing.lg),
            Text(
              'Pull down to refresh once your administrator has approved you.',
              textAlign: TextAlign.center,
              style: theme.textTheme.bodySmall,
            ),
          ],
        ),
      ),
    );
  }

  String _messageFor(UserModel? user) {
    if (user?.role == UserRole.worker) {
      return 'Your society administrator is reviewing your documents. '
          'You will appear in search results once verified.';
    }
    if (user?.role == UserRole.societyAdmin) {
      return 'Register your society to continue. Your account is activated '
          'once the society is verified.';
    }
    return 'Your society administrator is reviewing your registration. '
        'This usually takes a day or two.';
  }
}

/// Confirms which flat was submitted, and surfaces a rejection reason if the
/// administrator sent it back for correction.
class _ClaimedFlatNotice extends StatelessWidget {
  const _ClaimedFlatNotice({required this.resident});

  final ResidentProfile resident;

  @override
  Widget build(BuildContext context) {
    if (resident.wasRejected) {
      return AppCard(
        color: AppColors.dangerSoft,
        borderColor: AppColors.dangerSoft,
        shadow: const [],
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                const Icon(
                  Icons.error_outline_rounded,
                  color: AppColors.danger,
                  size: AppIconSize.md,
                ),
                const SizedBox(width: AppSpacing.xs),
                Text(
                  'Your registration needs attention',
                  style: Theme.of(context).textTheme.titleSmall?.copyWith(
                        color: AppColors.danger,
                      ),
                ),
              ],
            ),
            const SizedBox(height: AppSpacing.xs),
            Text(
              resident.rejectionReason,
              style: Theme.of(context).textTheme.bodyMedium,
            ),
          ],
        ),
      );
    }

    return AppCard(
      child: Row(
        children: [
          const Icon(
            Icons.check_circle_rounded,
            color: AppColors.success,
            size: AppIconSize.md,
          ),
          const SizedBox(width: AppSpacing.sm),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  'Flat ${resident.flatLabel} submitted',
                  style: Theme.of(context).textTheme.titleSmall,
                ),
                Text(
                  'Waiting for your administrator to review it.',
                  style: Theme.of(context).textTheme.bodySmall,
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}

class _InfoRow extends StatelessWidget {
  const _InfoRow({required this.label, required this.value});

  final String label;
  final String value;

  @override
  Widget build(BuildContext context) {
    return Row(
      mainAxisAlignment: MainAxisAlignment.spaceBetween,
      children: [
        Text(label, style: Theme.of(context).textTheme.bodySmall),
        Flexible(
          child: Text(
            value,
            textAlign: TextAlign.right,
            style: Theme.of(context).textTheme.titleSmall,
          ),
        ),
      ],
    );
  }
}
