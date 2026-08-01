import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../../../core/errors/api_exception.dart';
import '../../../../core/routing/app_router.dart';
import '../../../../shared/design_system.dart';
import '../../../auth/data/models/user_model.dart';
import '../../../auth/presentation/providers/auth_provider.dart';
import '../../../notifications/presentation/widgets/notification_bell.dart';
import '../../data/models/schedule_models.dart';
import '../providers/schedule_provider.dart';
import 'task_timing_sheet.dart';

/// Module 6.1 — one true schedule.
///
/// Recurring engagements and one-day bookings arrive already merged and ordered
/// from the server, so this screen never sorts or combines anything itself. That
/// is deliberate: the merge rule lives in one place, and a client that
/// re-derived it would eventually disagree with the server about what a
/// worker's day looks like.
class MyScheduleScreen extends ConsumerStatefulWidget {
  const MyScheduleScreen({super.key});

  @override
  ConsumerState<MyScheduleScreen> createState() => _MyScheduleScreenState();
}

class _MyScheduleScreenState extends ConsumerState<MyScheduleScreen>
    with SingleTickerProviderStateMixin {
  late final TabController _tabs = TabController(length: 2, vsync: this);

  @override
  void dispose() {
    _tabs.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final isWorker = ref.watch(authProvider).user?.role == UserRole.worker;

    return Scaffold(
      appBar: AppBar(
        title: Text(isWorker ? 'My schedule' : "Who's coming"),
        actions: [
          const NotificationBell(),
          // This is the worker's home, so it is the jumping-off point for
          // everything else they do.
          PopupMenuButton<String>(
            icon: const Icon(Icons.more_vert),
            onSelected: (route) => context.push(route),
            itemBuilder: (_) => [
              const PopupMenuItem(
                value: Routes.hireRequests,
                child: ListTile(
                  leading: Icon(Icons.mail_outline),
                  title: Text('Hire requests'),
                  contentPadding: EdgeInsets.zero,
                ),
              ),
              const PopupMenuItem(
                value: Routes.engagements,
                child: ListTile(
                  leading: Icon(Icons.handshake_outlined),
                  title: Text('Regular work'),
                  contentPadding: EdgeInsets.zero,
                ),
              ),
              const PopupMenuItem(
                value: Routes.myBookings,
                child: ListTile(
                  leading: Icon(Icons.event_note_outlined),
                  title: Text('One-day jobs'),
                  contentPadding: EdgeInsets.zero,
                ),
              ),
              PopupMenuItem(
                value: isWorker ? Routes.earnings : Routes.payments,
                child: ListTile(
                  leading: const Icon(Icons.payments_outlined),
                  title: Text(isWorker ? 'My earnings' : 'Payments'),
                  contentPadding: EdgeInsets.zero,
                ),
              ),
              const PopupMenuItem(
                value: Routes.rateJobs,
                child: ListTile(
                  leading: Icon(Icons.star_outline_rounded),
                  title: Text('Rate recent work'),
                  contentPadding: EdgeInsets.zero,
                ),
              ),
              const PopupMenuItem(
                value: Routes.myTrustScore,
                child: ListTile(
                  leading: Icon(Icons.verified_outlined),
                  title: Text('My trust score'),
                  contentPadding: EdgeInsets.zero,
                ),
              ),
              if (isWorker) ...[
                const PopupMenuItem(
                  value: Routes.myAvailability,
                  child: ListTile(
                    leading: Icon(Icons.calendar_month),
                    title: Text('Days I can work'),
                    contentPadding: EdgeInsets.zero,
                  ),
                ),
                const PopupMenuItem(
                  value: Routes.workerOnboarding,
                  child: ListTile(
                    leading: Icon(Icons.badge_outlined),
                    title: Text('My verification'),
                    contentPadding: EdgeInsets.zero,
                  ),
                ),
              ],
              // Module 12.2. Placed for workers as much as residents: a worker
              // who wants to know whether February's salary arrived can ask in
              // Hindi rather than navigating four screens in English.
              const PopupMenuItem(
                value: Routes.assistant,
                child: ListTile(
                  leading: Icon(Icons.forum_outlined),
                  title: Text('Ask about my records'),
                  contentPadding: EdgeInsets.zero,
                ),
              ),
              // Module 11.3. A worker with no way to report a household that
              // withholds pay has only the option of leaving, which is the
              // imbalance this platform is meant to reduce — so this sits in
              // the same menu for both sides, not just the resident's.
              const PopupMenuItem(
                value: Routes.complaints,
                child: ListTile(
                  leading: Icon(Icons.report_gmailerrorred_outlined),
                  title: Text('Complaints'),
                  contentPadding: EdgeInsets.zero,
                ),
              ),
              const PopupMenuItem(
                value: Routes.notificationPreferences,
                child: ListTile(
                  leading: Icon(Icons.tune),
                  title: Text('Notification settings'),
                  contentPadding: EdgeInsets.zero,
                ),
              ),
            ],
          ),
        ],
        bottom: TabBar(
          controller: _tabs,
          tabs: const [Tab(text: 'Today'), Tab(text: 'This week')],
        ),
      ),
      body: TabBarView(
        controller: _tabs,
        children: [
          _TodayTab(isWorker: isWorker),
          _WeekTab(isWorker: isWorker),
        ],
      ),
    );
  }
}

class _TodayTab extends ConsumerWidget {
  const _TodayTab({required this.isWorker});

  final bool isWorker;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final schedule = ref.watch(todayScheduleProvider);

    return AppSwitcher(
      child: schedule.when(
        loading: () => const AppSkeletonList(count: 4, hasAvatar: false),
        error: (error, _) => AppErrorState(
          message:
              error is ApiException ? error.message : 'Could not load today.',
          onRetry: () => ref.invalidate(todayScheduleProvider),
        ),
        data: (items) {
          if (items.isEmpty) return _emptyDay(isWorker);
          return RefreshIndicator(
            onRefresh: () async => ref.invalidate(todayScheduleProvider),
            child: ListView.builder(
              physics: const AlwaysScrollableScrollPhysics(),
              padding: const EdgeInsets.fromLTRB(
                AppSpacing.gutter,
                AppSpacing.sm,
                AppSpacing.gutter,
                AppSpacing.xxl,
              ),
              itemCount: items.length,
              itemBuilder: (context, index) => AppFadeIn(
                index: index,
                child: _ScheduleCard(item: items[index], isWorker: isWorker),
              ),
            ),
          );
        },
      ),
    );
  }
}

class _WeekTab extends ConsumerWidget {
  const _WeekTab({required this.isWorker});

  final bool isWorker;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final range = AgendaRange.week();
    final agenda = ref.watch(agendaProvider(range));

    return agenda.when(
      loading: () => const AppSkeletonList(count: 5, hasAvatar: false),
      error: (error, _) => AppErrorState(
        message:
            error is ApiException ? error.message : 'Could not load the week.',
        onRetry: () => ref.invalidate(agendaProvider(range)),
      ),
      data: (items) {
        if (items.isEmpty) return _emptyDay(isWorker);

        // The server returns one flat, ordered list; the only grouping done
        // here is inserting a header when the date changes.
        final widgets = <Widget>[];
        DateTime? lastDate;

        for (final item in items) {
          if (lastDate == null || item.date != lastDate) {
            widgets.add(_DateHeader(date: item.date));
            lastDate = item.date;
          }
          widgets.add(_ScheduleCard(item: item, isWorker: isWorker));
        }

        return RefreshIndicator(
          onRefresh: () async => ref.invalidate(agendaProvider(range)),
          child: ListView(
            physics: const AlwaysScrollableScrollPhysics(),
            padding: const EdgeInsets.fromLTRB(
              AppSpacing.gutter,
              0,
              AppSpacing.gutter,
              AppSpacing.xxl,
            ),
            children: [
              for (var i = 0; i < widgets.length; i++)
                AppFadeIn(index: i, child: widgets[i]),
            ],
          ),
        );
      },
    );
  }
}

class _DateHeader extends StatelessWidget {
  const _DateHeader({required this.date});

  final DateTime date;

  static const _weekdays = [
    'Monday',
    'Tuesday',
    'Wednesday',
    'Thursday',
    'Friday',
    'Saturday',
    'Sunday',
  ];

  @override
  Widget build(BuildContext context) {
    final now = DateTime.now();
    final isToday =
        date.year == now.year && date.month == now.month && date.day == now.day;

    return Padding(
      padding: const EdgeInsets.fromLTRB(0, AppSpacing.lg, 0, AppSpacing.xs),
      child: Row(
        children: [
          Text(
            isToday ? 'Today' : _weekdays[date.weekday - 1],
            style: Theme.of(context).textTheme.titleMedium,
          ),
          const SizedBox(width: AppSpacing.xs),
          Text(
            '${date.day}/${date.month}',
            style: Theme.of(context).textTheme.bodySmall,
          ),
        ],
      ),
    );
  }
}

class _ScheduleCard extends ConsumerWidget {
  const _ScheduleCard({required this.item, required this.isWorker});

  final ScheduleItem item;
  final bool isWorker;

  Future<void> _openTiming(BuildContext context, WidgetRef ref) async {
    final changed = await showModalBottomSheet<bool>(
      context: context,
      isScrollControlled: true,
      builder: (_) => TaskTimingSheet(engagementId: item.sourceId),
    );

    if (changed == true && context.mounted) {
      invalidateSchedule(ref);
      ref.invalidate(taskTimingProvider(item.sourceId));
    }
  }

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final theme = Theme.of(context);
    final counterparty = isWorker ? item.flatLabel : item.workerName;

    // Only a resident sets expectations, and only for a recurring engagement —
    // a one-day booking's times were agreed when it was made.
    final canEditTiming = !isWorker && item.isRecurring;

    return AppCard(
      margin: const EdgeInsets.only(bottom: AppSpacing.sm),
      onTap: canEditTiming ? () => _openTiming(context, ref) : null,
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          _TimeColumn(item: item),
          const SizedBox(width: AppSpacing.sm),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(
                  children: [
                    Expanded(
                      child: Text(
                        item.title,
                        style: theme.textTheme.titleSmall,
                      ),
                    ),
                    _SourceChip(item: item),
                  ],
                ),
                const SizedBox(height: AppSpacing.xxs),
                Text(
                  counterparty.isEmpty ? '—' : counterparty,
                  style: theme.textTheme.bodySmall,
                ),
                if (item.needsResponse) ...[
                  const SizedBox(height: AppSpacing.xs),
                  const _Flag(
                    icon: Icons.hourglass_bottom,
                    label: 'Awaiting your confirmation',
                    colour: AppColors.warning,
                  ),
                ],
                if (item.graceMinutes > 0) ...[
                  const SizedBox(height: AppSpacing.xxs + 2),
                  _Flag(
                    icon: Icons.timer_outlined,
                    label: '${item.graceMinutes} min grace',
                    colour: AppColors.textTertiary,
                  ),
                ],
                if (item.taskNotes.isNotEmpty) ...[
                  const SizedBox(height: AppSpacing.xs),
                  Container(
                    width: double.infinity,
                    padding: const EdgeInsets.all(AppSpacing.xs + 2),
                    decoration: const BoxDecoration(
                      color: AppColors.surfaceMuted,
                      borderRadius: AppRadius.chip,
                    ),
                    child: Text(
                      item.taskNotes,
                      style: Theme.of(context).textTheme.bodySmall,
                    ),
                  ),
                ],
              ],
            ),
          ),
          if (canEditTiming)
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

class _TimeColumn extends StatelessWidget {
  const _TimeColumn({required this.item});

  final ScheduleItem item;

  @override
  Widget build(BuildContext context) {
    // Tabular figures so the start times line up down the list — a column of
    // times that jitters is noticeably harder to scan.
    return SizedBox(
      width: 58,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            item.startTimeLabel,
            style: const TextStyle(
              fontSize: 16,
              fontWeight: FontWeight.w700,
              fontFeatures: [FontFeature.tabularFigures()],
              color: AppColors.textPrimary,
            ),
          ),
          const SizedBox(height: 2),
          Text(
            '${item.durationMinutes} min',
            style: const TextStyle(
              fontSize: 12,
              color: AppColors.textTertiary,
              fontFeatures: [FontFeature.tabularFigures()],
            ),
          ),
        ],
      ),
    );
  }
}

class _SourceChip extends StatelessWidget {
  const _SourceChip({required this.item});

  final ScheduleItem item;

  @override
  Widget build(BuildContext context) {
    return AppStatusChip(
      label: item.source.label,
      tone: item.isRecurring ? AppTone.brand : AppTone.info,
      dense: true,
    );
  }
}

class _Flag extends StatelessWidget {
  const _Flag({required this.icon, required this.label, required this.colour});

  final IconData icon;
  final String label;
  final Color colour;

  @override
  Widget build(BuildContext context) {
    return Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        Icon(icon, size: AppIconSize.sm, color: colour),
        const SizedBox(width: AppSpacing.xxs),
        Text(
          label,
          style: TextStyle(
            fontSize: 12.5,
            fontWeight: FontWeight.w500,
            color: colour,
          ),
        ),
      ],
    );
  }
}

/// Both tabs share one empty state, worded for whichever side is reading it.
///
/// The private `_EmptyDay` and `_ScheduleError` widgets this replaced were two
/// of the hand-rolled state views the audit counted across the app; they now
/// come from the design system so an empty schedule looks like every other
/// empty list in Sathify.
Widget _emptyDay(bool isWorker) {
  return AppEmptyState(
    icon: Icons.event_available_outlined,
    title: 'Nothing scheduled',
    message: isWorker
        ? 'Your regular visits and one-day jobs will appear here.'
        : 'Once you hire someone, their visits appear here.',
  );
}
