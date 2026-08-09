import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../../../core/errors/api_exception.dart';
import '../../../../core/routing/app_router.dart';
import '../../../../shared/design_system.dart';
import '../../../auth/data/models/user_model.dart';
import '../../../auth/presentation/providers/auth_provider.dart';
import '../../../bookings/presentation/providers/booking_provider.dart';
import '../../../bookings/presentation/widgets/emergency_offer_card.dart';
import '../../../bookings/presentation/widgets/emergency_request_strip.dart';
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
      // Module 6.5 — a worker telling the household they cannot come is urgent
      // and one-handed, often on the way to somewhere else. It gets the primary
      // action on their home screen rather than a menu item three taps deep:
      // every tap between the worker and this button is time the household does
      // not get to arrange cover.
      floatingActionButton: isWorker
          ? FloatingActionButton.extended(
              onPressed: () => context.push(Routes.applyLeave),
              icon: const Icon(Icons.event_busy_outlined),
              label: const Text('Take a day off'),
            )
          : null,
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
          // Module 5.5 — incoming emergency work sits above the day, for both
          // sides: a worker sees requests she can claim, a household sees the
          // one it just raised and who picked it up. It renders nothing when
          // there is nothing in flight, so an ordinary day looks exactly as it
          // did before.
          final banner = isWorker
              ? const EmergencyOfferStrip()
              : const EmergencyRequestStrip();

          if (items.isEmpty) {
            return RefreshIndicator(
              onRefresh: () async => ref.invalidate(todayScheduleProvider),
              child: ListView(
                physics: const AlwaysScrollableScrollPhysics(),
                padding: const EdgeInsets.fromLTRB(
                  AppSpacing.gutter,
                  AppSpacing.sm,
                  AppSpacing.gutter,
                  AppSpacing.xxl,
                ),
                children: [banner, _emptyDay(isWorker)],
              ),
            );
          }

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
              itemCount: items.length + 1,
              itemBuilder: (context, index) {
                if (index == 0) return banner;
                return AppFadeIn(
                  index: index - 1,
                  child: _ScheduleCard(
                    item: items[index - 1],
                    isWorker: isWorker,
                  ),
                );
              },
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

class _ScheduleCard extends ConsumerStatefulWidget {
  const _ScheduleCard({required this.item, required this.isWorker});

  final ScheduleItem item;
  final bool isWorker;

  @override
  ConsumerState<_ScheduleCard> createState() => _ScheduleCardState();
}

class _ScheduleCardState extends ConsumerState<_ScheduleCard> {
  bool _isBusy = false;

  ScheduleItem get item => widget.item;
  bool get isWorker => widget.isWorker;

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

  /// Module 6.6 — the worker says the day's work is done.
  ///
  /// The optional note is asked for in the same sheet rather than as a second
  /// step: a worker finishing a job is on their way out of the door, and a
  /// two-dialog flow is a flow that gets abandoned halfway.
  Future<void> _markDone() async {
    // Captured before the dialog opens: reading it afterwards would be a
    // BuildContext use across an async gap, and this card can legitimately be
    // disposed while the dialog is up (a pull-to-refresh rebuilds the list).
    final messenger = ScaffoldMessenger.of(context);

    final note = await showDialog<String>(
      context: context,
      builder: (_) => _NoteDialog(
        title: 'Mark this work as done?',
        hint: 'Optional — anything the household should know',
        confirmLabel: 'Mark as done',
        // Say what the button actually does before she taps it, and say it
        // differently for the three cases, because they are different: a
        // recurring visit moves no money, an ordinary booking opens an in-app
        // charge, and an emergency is settled in cash on the spot. Telling her
        // the household "will be asked to pay" on a cash job would have her
        // waiting for a transfer that is never coming.
        footnote: item.isRecurring
            ? 'The household will be told straight away.'
            : item.isCashSettled
                ? 'Collect the payment in cash. Both of you will be sent the '
                    'amount owed.'
                : 'The household will be asked to pay for this job.',
      ),
    );
    if (note == null) return;

    setState(() => _isBusy = true);

    try {
      await ref.read(scheduleRepositoryProvider).markVisitComplete(
            source: item.source,
            sourceId: item.sourceId,
            visitDate: item.date,
            note: note,
          );
      if (!mounted) return;
      // The card is rebuilt from the server's own visit_status rather than
      // flipped locally, so what the worker sees is what was actually recorded.
      invalidateSchedule(ref);
      showAppSnackBarOn(
        messenger,
        item.isRecurring
            ? 'Marked done. The household has been told.'
            : item.isCashSettled
                ? 'Marked done. Collect the payment in cash.'
                : 'Marked done. The household has been asked to pay.',
        tone: AppTone.success,
      );
    } on ApiException catch (error) {
      if (!mounted) return;
      showAppSnackBarOn(messenger, error.message, tone: AppTone.danger);
    } finally {
      // Always reset, on every branch. A spinner left spinning after a refused
      // request is the bug this app has already been bitten by elsewhere.
      if (mounted) setState(() => _isBusy = false);
    }
  }

  /// Module 5.4 — the worker answers a one-day request from her own dashboard.
  ///
  /// -----------------------------------------------------------------------
  /// WHY THIS LIVES HERE AND NOT ONLY ON THE BOOKINGS SCREEN
  /// -----------------------------------------------------------------------
  /// Accept and Decline existed only on "One-day jobs", three taps into the
  /// overflow menu. This screen — the worker's home — showed the same request
  /// with the words "Awaiting your confirmation" on it and no way whatsoever to
  /// confirm it: no buttons, and the card was not even tappable, because its
  /// only tap targets are the leave detail and the resident's timing sheet.
  ///
  /// So the app told her, on the first screen she sees, that something needed
  /// her answer, and then gave her nowhere to answer it. That is bad for any
  /// booking and unusable for an emergency, where the entire value of the
  /// request is that it is answered in the next few minutes.
  Future<void> _respond({required bool accept}) async {
    final messenger = ScaffoldMessenger.of(context);

    final note = accept
        ? ''
        : await showDialog<String>(
            context: context,
            builder: (_) => const _NoteDialog(
              title: 'Decline this job?',
              hint: 'Optional — e.g. already committed that day',
              confirmLabel: 'Decline',
            ),
          );
    // Only a cancelled *dialog* means "changed my mind". Accepting never opens
    // one, so an empty note there is a real answer rather than an abandonment.
    if (!accept && note == null) return;

    setState(() => _isBusy = true);

    try {
      final bookings = ref.read(bookingRepositoryProvider);
      if (accept) {
        await bookings.confirmBooking(item.sourceId);
      } else {
        await bookings.declineBooking(item.sourceId, note: note ?? '');
      }
      if (!mounted) return;
      // Both lists move: the schedule redraws the card, and the bookings screen
      // is showing the same request from the other side.
      invalidateSchedule(ref);
      invalidateBookings(ref);
      showAppSnackBarOn(
        messenger,
        accept ? 'Accepted. The household has been told.' : 'Declined.',
        tone: accept ? AppTone.success : AppTone.info,
      );
    } on ApiException catch (error) {
      if (!mounted) return;
      // Most often: the request lapsed, or the household cancelled, while the
      // card was on screen. Refreshing is what makes the card tell the truth
      // again, so it happens on the failure path too.
      invalidateSchedule(ref);
      showAppSnackBarOn(messenger, error.message, tone: AppTone.danger);
    } finally {
      if (mounted) setState(() => _isBusy = false);
    }
  }

  /// Whether to offer Accept/Decline on this card.
  ///
  /// The role check is the client's own concern — a resident must never be
  /// offered a control that answers on the worker's behalf. The deadline is the
  /// server's, and is read from it rather than re-derived here.
  bool get _canRespond => isWorker && item.canRespond;

  /// Whether to offer "Mark as done" on this card.
  ///
  /// -----------------------------------------------------------------------
  /// THIS DELIBERATELY ASKS THE SERVER RATHER THAN WORKING IT OUT
  /// -----------------------------------------------------------------------
  /// It used to reconstruct the rule here — worker, not complete, not on leave,
  /// not awaiting a response, and dated before tomorrow — while the server
  /// applied its own, different rule when the request actually arrived. The two
  /// drifted, and both directions of the drift were user-visible: the button was
  /// drawn on visits the server would refuse, and hidden on visits it would have
  /// accepted. An emergency booking managed to hit both, which is why the maid
  /// had no working way to close one out.
  ///
  /// So the only thing left here is the role check, which is genuinely a client
  /// concern (this card is rendered for the household too, and a resident should
  /// never be shown a control that makes a statement about somebody else's
  /// work). Everything else is [ScheduleItem.canMarkDone], straight from the
  /// server that will decide the request.
  bool get _canMarkDone => isWorker && item.canMarkDone;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final counterparty = isWorker ? item.flatLabel : item.workerName;

    // Only a resident sets expectations, and only for a recurring engagement —
    // a one-day booking's times were agreed when it was made.
    final canEditTiming = !isWorker && item.isRecurring;

    // A day with leave on it opens the leave, not the timing sheet: whether
    // somebody is coming at all is a more urgent question than what time they
    // were expected.
    final leaveId = item.leaveRequestId;
    final opensLeave = leaveId > 0;

    return AppCard(
      margin: const EdgeInsets.only(bottom: AppSpacing.sm),
      onTap: opensLeave
          ? () => context.push(Routes.leaveDetailPath(leaveId))
          : canEditTiming
              ? () => _openTiming(context, ref)
              : null,
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
                  // Two different facts, and they used to share one label. A
                  // request that can still be answered is a call to action; one
                  // whose deadline has passed is a dead row, and telling
                  // somebody it is "awaiting your confirmation" when nothing
                  // will accept that confirmation is how this screen came to be
                  // reported as broken.
                  _Flag(
                    icon: item.responseLapsed
                        ? Icons.timer_off_outlined
                        : Icons.hourglass_bottom,
                    label: item.responseLapsed
                        ? 'No longer available'
                        : isWorker
                            ? 'Awaiting your answer'
                            : 'Awaiting their confirmation',
                    colour: item.responseLapsed
                        ? AppColors.textTertiary
                        : AppColors.warning,
                  ),
                ],
                // Module 6.5 — the visit stays listed and says what happened to
                // it. An absence that silently removed the row would leave a
                // household wondering whether they had misremembered the day.
                if (item.isCover) ...[
                  const SizedBox(height: AppSpacing.xs),
                  _Flag(
                    icon: Icons.swap_horiz_rounded,
                    label: 'Covering for ${item.coveringForName}',
                    colour: AppColors.success,
                  ),
                ] else if (item.onLeave) ...[
                  const SizedBox(height: AppSpacing.xs),
                  _Flag(
                    icon: item.isUncovered
                        ? Icons.event_busy_outlined
                        : Icons.how_to_reg_outlined,
                    label: item.isUncovered
                        ? (isWorker ? 'You are on leave' : 'On leave — no cover yet')
                        : '${item.coverWorkerName} is covering',
                    colour: item.isUncovered
                        ? AppColors.warning
                        : AppColors.success,
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

                // --- Module 6.6 -------------------------------------------
                // Finished work reads as a fact on both sides' cards; the
                // button underneath is the worker's only.
                if (item.isComplete) ...[
                  const SizedBox(height: AppSpacing.xs),
                  const _Flag(
                    icon: Icons.task_alt_rounded,
                    label: 'Work marked done',
                    colour: AppColors.success,
                  ),
                ] else if (item.isInProgress) ...[
                  const SizedBox(height: AppSpacing.xs),
                  const _Flag(
                    icon: Icons.timelapse_rounded,
                    label: 'In progress',
                    colour: AppColors.info,
                  ),
                ],
                // Answering comes before finishing: a request she has not
                // accepted cannot be one she has completed, so the two are
                // mutually exclusive and the answer is the more urgent of them.
                if (_canRespond) ...[
                  const SizedBox(height: AppSpacing.sm),
                  Row(
                    children: [
                      Expanded(
                        child: AppButton.secondary(
                          label: 'Decline',
                          icon: Icons.close_rounded,
                          onPressed: _isBusy ? null : () => _respond(accept: false),
                        ),
                      ),
                      const SizedBox(width: AppSpacing.sm),
                      Expanded(
                        flex: 2,
                        child: AppButton(
                          label: 'Accept',
                          icon: Icons.check_rounded,
                          isLoading: _isBusy,
                          onPressed: () => _respond(accept: true),
                        ),
                      ),
                    ],
                  ),
                ] else if (_canMarkDone) ...[
                  const SizedBox(height: AppSpacing.sm),
                  AppButton.secondary(
                    label: 'Mark as done',
                    icon: Icons.task_alt_rounded,
                    isLoading: _isBusy,
                    onPressed: _markDone,
                  ),
                ],
              ],
            ),
          ),
          if (canEditTiming || opensLeave)
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

/// Confirms a completion, and collects the optional note in the same step.
class _NoteDialog extends StatefulWidget {
  const _NoteDialog({
    required this.title,
    required this.hint,
    required this.confirmLabel,
    this.footnote = '',
  });

  final String title;
  final String hint;
  final String confirmLabel;
  final String footnote;

  @override
  State<_NoteDialog> createState() => _NoteDialogState();
}

class _NoteDialogState extends State<_NoteDialog> {
  final _controller = TextEditingController();

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return AlertDialog(
      title: Text(widget.title),
      content: Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          TextField(
            controller: _controller,
            autofocus: true,
            maxLines: 3,
            maxLength: 300,
            decoration: InputDecoration(hintText: widget.hint),
          ),
          if (widget.footnote.isNotEmpty)
            Text(
              widget.footnote,
              style: Theme.of(context).textTheme.bodySmall,
            ),
        ],
      ),
      actions: [
        TextButton(
          onPressed: () => Navigator.of(context).pop(),
          child: const Text('Not yet'),
        ),
        FilledButton(
          onPressed: () => Navigator.of(context).pop(_controller.text),
          child: Text(widget.confirmLabel),
        ),
      ],
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
            item.displayTimeLabel,
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
