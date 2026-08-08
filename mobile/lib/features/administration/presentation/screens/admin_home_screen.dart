import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../../../core/errors/api_exception.dart';
import '../../../../core/routing/app_router.dart';
import '../../../../shared/design_system.dart';
import '../../../auth/presentation/providers/auth_provider.dart';
import '../../../notifications/presentation/providers/notification_provider.dart';
import '../../../notifications/presentation/widgets/notification_bell.dart';
import '../../../ratings/presentation/providers/rating_provider.dart';
import '../../../societies/presentation/providers/society_provider.dart';
import '../../../workers/presentation/providers/worker_provider.dart';
import '../providers/admin_provider.dart';

/// The society administrator's home (Module 11).
///
/// -----------------------------------------------------------------------
/// WAITING WORK FIRST, ALWAYS
/// -----------------------------------------------------------------------
/// Approvals and overdue complaints sit at the top because they are the only
/// things on this screen where somebody else is blocked. Analytics are further
/// down: interesting, but nobody is standing still waiting for them.
///
/// Every count comes from the same provider its destination screen uses, so
/// opening one costs nothing extra and a badge can never disagree with the list
/// behind it.
///
/// The redesign kept that priority order exactly — it is the most valuable
/// thing about this screen — and changed only how it looks: a summary strip so
/// the total waiting count reads at a glance, then the same cards on the new
/// design system.
class AdminHomeScreen extends ConsumerWidget {
  const AdminHomeScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final pendingWorkers = ref.watch(pendingWorkersProvider);
    final pendingResidents = ref.watch(pendingResidentsProvider);
    final flaggedReviews = ref.watch(reviewFlagsProvider);
    final dashboard = ref.watch(adminDashboardProvider);
    final user = ref.watch(authProvider).user;

    final complaints = dashboard.valueOrNull?.complaints;

    return Scaffold(
      appBar: AppBar(
        titleSpacing: AppSpacing.gutter,
        title: const Text('Administration'),
        actions: const [NotificationBell(), SizedBox(width: AppSpacing.xs)],
      ),
      body: RefreshIndicator(
        onRefresh: () async {
          ref.invalidate(pendingWorkersProvider);
          ref.invalidate(pendingResidentsProvider);
          ref.invalidate(reviewFlagsProvider);
          ref.invalidate(adminDashboardProvider);
        },
        child: ListView(
          physics: const AlwaysScrollableScrollPhysics(),
          padding: const EdgeInsets.only(bottom: AppSpacing.xxl),
          children: [
            AppFadeIn(
              child: _SocietyHeader(society: user?.societyName),
            ),

            AppFadeIn(
              index: 1,
              child: _WaitingSummary(
                workers: pendingWorkers.valueOrNull?.length,
                residents: pendingResidents.valueOrNull?.length,
                reviews: flaggedReviews.valueOrNull?.length,
                overdue: complaints?.overdueNow,
              ),
            ),

            const AppSectionHeader(title: 'Waiting for you'),
            AppFadeIn(
              index: 2,
              child: _Card(
                icon: Icons.report_gmailerrorred_outlined,
                title: 'Complaints',
                subtitle: complaints == null
                    ? 'Issues raised by residents and workers'
                    : '${complaints.openNow} open'
                        '${complaints.overdueNow > 0 ? ', ${complaints.overdueNow} past the response window' : ''}',
                count: complaints?.openNow,
                // Overdue is the one count worth alarming about: it means
                // somebody has been waiting longer than they were promised.
                isAlarming: (complaints?.overdueNow ?? 0) > 0,
                onTap: () => context.push(Routes.complaints),
              ),
            ),
            AppFadeIn(
              index: 3,
              child: _Card(
                icon: Icons.badge_outlined,
                title: 'Workers to verify',
                subtitle:
                    'Check documents and approve workers for the platform',
                count: pendingWorkers.valueOrNull?.length,
                onTap: () => context.push(Routes.workerApprovals),
              ),
            ),
            AppFadeIn(
              index: 4,
              child: _Card(
                icon: Icons.home_outlined,
                title: 'Residents to approve',
                subtitle: 'Review proof of residence and admit residents',
                count: pendingResidents.valueOrNull?.length,
                onTap: () => context.push(Routes.residentApprovals),
              ),
            ),
            AppFadeIn(
              index: 5,
              child: _Card(
                icon: Icons.flag_outlined,
                title: 'Reviews to check',
                subtitle: 'Ratings flagged as possibly not genuine',
                count: flaggedReviews.valueOrNull?.length,
                onTap: () => context.push(Routes.reviewFlags),
              ),
            ),

            const AppSectionHeader(title: 'Your society'),
            AppFadeIn(
              index: 6,
              child: _Card(
                icon: Icons.description_outlined,
                title: 'Reports',
                subtitle: 'Attendance, payments and complaints for the record',
                onTap: () => context.push(Routes.adminReports),
              ),
            ),
            AppFadeIn(
              index: 7,
              child: _Card(
                icon: Icons.trending_up_rounded,
                title: 'Unmet demand',
                subtitle: 'Services residents wanted but could not book',
                onTap: () => context.push(Routes.unmetDemand),
              ),
            ),
            AppFadeIn(
              index: 8,
              child: _Card(
                icon: Icons.calendar_today_outlined,
                title: "Today's visits",
                subtitle: 'Every worker expected in the society today',
                onTap: () => context.push(Routes.mySchedule),
              ),
            ),

            const AppSectionHeader(title: 'Housekeeping'),
            // Neither of these needs doing on a healthy deployment: an external
            // pinger drives both, and loading the complaint queue already runs
            // the escalation sweep. They are here for the day the pinger did
            // not run, because the free tier has no scheduler to fall back on.
            AppFadeIn(
              index: 8,
              child: _Card(
                icon: Icons.send_outlined,
                title: 'Send due reminders',
                subtitle: 'Deliver visit reminders that are waiting to go out',
                onTap: () => _deliverDue(context, ref),
              ),
            ),
            AppFadeIn(
              index: 8,
              child: _Card(
                icon: Icons.alarm_outlined,
                title: 'Check for overdue complaints',
                subtitle: 'Escalate anything past its response window',
                onTap: () => _escalate(context, ref),
              ),
            ),
          ],
        ),
      ),
    );
  }
}

Future<void> _deliverDue(BuildContext context, WidgetRef ref) async {
  final messenger = ScaffoldMessenger.of(context);
  try {
    final sent = await ref.read(notificationRepositoryProvider).deliverDue();
    showAppSnackBarOn(
      messenger,
      sent == 0 ? 'Nothing was waiting to go out.' : '$sent reminder(s) sent.',
      tone: sent == 0 ? AppTone.neutral : AppTone.success,
    );
  } on ApiException catch (error) {
    showAppSnackBarOn(messenger, error.message, tone: AppTone.danger);
  }
}

Future<void> _escalate(BuildContext context, WidgetRef ref) async {
  final messenger = ScaffoldMessenger.of(context);
  try {
    final escalated = await ref.read(adminRepositoryProvider).escalateOverdue();
    ref.invalidate(adminDashboardProvider);
    showAppSnackBarOn(
      messenger,
      escalated == 0
          ? 'Nothing was overdue.'
          : '$escalated complaint(s) escalated.',
      tone: escalated == 0 ? AppTone.neutral : AppTone.warning,
    );
  } on ApiException catch (error) {
    showAppSnackBarOn(messenger, error.message, tone: AppTone.danger);
  }
}

class _SocietyHeader extends StatelessWidget {
  const _SocietyHeader({this.society});

  final String? society;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);

    return Padding(
      padding: const EdgeInsets.fromLTRB(
        AppSpacing.gutter,
        AppSpacing.xs,
        AppSpacing.gutter,
        AppSpacing.md,
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            society?.isNotEmpty == true ? society! : 'Your society',
            style: theme.textTheme.headlineSmall,
          ),
          const SizedBox(height: AppSpacing.xxs),
          Text(
            'Everything waiting on you, first.',
            style: theme.textTheme.bodySmall,
          ),
        ],
      ),
    );
  }
}

/// A single strip answering "how much is waiting?" before any list is read.
///
/// Nulls render as an em dash rather than as zero: a count that has not loaded
/// yet and a count that is genuinely zero mean opposite things to an
/// administrator deciding whether to open a queue.
class _WaitingSummary extends StatelessWidget {
  const _WaitingSummary({
    this.workers,
    this.residents,
    this.reviews,
    this.overdue,
  });

  final int? workers;
  final int? residents;
  final int? reviews;
  final int? overdue;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: AppSpacing.gutter),
      child: AppCard(
        padding: const EdgeInsets.symmetric(
          horizontal: AppSpacing.md,
          vertical: AppSpacing.md,
        ),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            // Every number below is "still waiting on you", not a total — a
            // fully-approved, fully-caught-up society shows zeroes across the
            // whole row, and that is the good outcome, not a sign nobody is
            // registered. Said once here rather than lengthening each label
            // individually, which just made the row wrap on narrow phones.
            const Text(
              'Waiting on you',
              style: TextStyle(
                fontSize: 11.5,
                fontWeight: FontWeight.w600,
                color: AppColors.textTertiary,
              ),
            ),
            const SizedBox(height: AppSpacing.xs),
            Row(
              children: [
                _Metric(label: 'Workers', value: workers),
                const _Rule(),
                _Metric(label: 'Residents', value: residents),
                const _Rule(),
                _Metric(label: 'Reviews', value: reviews),
                const _Rule(),
                _Metric(
                  label: 'Overdue',
                  value: overdue,
                  tone: (overdue ?? 0) > 0 ? AppTone.danger : AppTone.neutral,
                ),
              ],
            ),
          ],
        ),
      ),
    );
  }
}

class _Metric extends StatelessWidget {
  const _Metric({required this.label, this.value, this.tone = AppTone.neutral});

  final String label;
  final int? value;
  final AppTone tone;

  @override
  Widget build(BuildContext context) {
    final isZero = value == 0;
    return Expanded(
      child: Column(
        children: [
          Text(
            value == null ? '—' : '$value',
            style: TextStyle(
              fontSize: 22,
              fontWeight: FontWeight.w700,
              letterSpacing: -0.5,
              // A zero is good news here, so it recedes rather than shouts.
              color:
                  isZero || value == null ? AppColors.textTertiary : tone.ink,
            ),
          ),
          const SizedBox(height: 2),
          Text(
            label,
            style: const TextStyle(
              fontSize: 11.5,
              fontWeight: FontWeight.w600,
              color: AppColors.textTertiary,
            ),
          ),
        ],
      ),
    );
  }
}

class _Rule extends StatelessWidget {
  const _Rule();

  @override
  Widget build(BuildContext context) {
    return Container(width: 1, height: 30, color: AppColors.border);
  }
}

class _Card extends StatelessWidget {
  const _Card({
    required this.icon,
    required this.title,
    required this.subtitle,
    required this.onTap,
    this.count,
    this.isAlarming = false,
  });

  final IconData icon;
  final String title;
  final String subtitle;
  final VoidCallback onTap;

  /// Null while loading — shown as nothing rather than as a misleading zero.
  final int? count;

  final bool isAlarming;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final waiting = (count ?? 0) > 0;
    final tone = isAlarming ? AppTone.danger : AppTone.warning;

    return AppCard(
      margin: const EdgeInsets.fromLTRB(
        AppSpacing.gutter,
        0,
        AppSpacing.gutter,
        AppSpacing.sm,
      ),
      onTap: onTap,
      child: Row(
        children: [
          Container(
            width: 44,
            height: 44,
            decoration: const BoxDecoration(
              color: AppColors.primarySoft,
              shape: BoxShape.circle,
            ),
            child: Icon(icon, size: AppIconSize.md, color: AppColors.primary),
          ),
          const SizedBox(width: AppSpacing.sm),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(title, style: theme.textTheme.titleSmall),
                const SizedBox(height: 1),
                Text(
                  subtitle,
                  style: theme.textTheme.bodySmall,
                ),
              ],
            ),
          ),
          if (waiting) ...[
            const SizedBox(width: AppSpacing.xs),
            AppStatusChip(label: '$count', tone: tone),
          ],
          const Icon(
            Icons.chevron_right_rounded,
            color: AppColors.textTertiary,
            size: AppIconSize.md,
          ),
        ],
      ),
    );
  }
}
