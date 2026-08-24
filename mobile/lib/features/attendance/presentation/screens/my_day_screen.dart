import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../../../core/errors/api_exception.dart';
import '../../../../core/routing/app_router.dart';
import '../../../../shared/design_system.dart';
import '../../data/models/work_session_models.dart';
import '../providers/work_session_provider.dart';
import '../widgets/overtime_request_sheet.dart';

/// Module 7.7 — the worker's day, a flat at a time.
///
/// -------------------------------------------------------------------------
/// A STACK OF FLATS, NOT A SINGLE CLOCK
/// -------------------------------------------------------------------------
/// She enters the society once and works four homes, and each home owes its own
/// hours. A single big Start/Stop button — the obvious design — would produce
/// one span for the whole trip that no household could be billed from. So the
/// primary screen is one card per flat, in time order, each with its own state
/// and exactly one action.
///
/// -------------------------------------------------------------------------
/// EARNINGS ARE ON EVERY SCREEN
/// -------------------------------------------------------------------------
/// The running total at the top is the reason she keeps using the app rather
/// than reverting to a cash arrangement. It is the first thing on the screen,
/// and it includes the visit fee, because the fee is a real part of what she is
/// owed and hiding it inside a total makes it look like the app invented it.
class MyDayScreen extends ConsumerStatefulWidget {
  const MyDayScreen({super.key});

  @override
  ConsumerState<MyDayScreen> createState() => _MyDayScreenState();
}

class _MyDayScreenState extends ConsumerState<MyDayScreen> {
  /// Drives the live counter on a running card. One timer for the screen
  /// rather than one per card — four flats would otherwise mean four timers
  /// waking the device every second for a number that changes once a minute.
  Timer? _tick;

  /// Which engagement has a request in flight, so its button can show progress
  /// without the whole screen going into a loading state and losing her place.
  int? _busyEngagement;

  @override
  void initState() {
    super.initState();
    _tick = Timer.periodic(const Duration(seconds: 30), (_) {
      if (mounted) setState(() {});
    });
  }

  @override
  void dispose() {
    _tick?.cancel();
    super.dispose();
  }

  Future<void> _start(TodayCard card) async {
    setState(() => _busyEngagement = card.engagementId);
    try {
      final fix = await bestEffortFix();
      final session =
          await ref.read(workSessionRepositoryProvider).startSession(
                engagementId: card.engagementId,
                latitude: fix.latitude,
                longitude: fix.longitude,
              );
      invalidateSessions(ref);
      if (!mounted) return;

      // She is working either way. The only difference a missing fix makes is
      // that somebody will confirm it later, and saying so is kinder than a
      // silent flag she discovers on her payslip.
      showAppSnackBar(
        context,
        session.needsReview
            ? 'Started. We could not check your location, so '
                '${card.residentName.split(' ').first} will confirm it.'
            : 'Started at ${card.flat}.',
        tone: session.needsReview ? AppTone.warning : AppTone.success,
      );
    } on ApiException catch (error) {
      if (mounted) {
        showAppSnackBar(context, error.message, tone: AppTone.danger);
      }
    } finally {
      if (mounted) setState(() => _busyEngagement = null);
    }
  }

  Future<void> _stop(TodayCard card) async {
    final session = card.session;
    if (session == null) return;

    setState(() => _busyEngagement = card.engagementId);
    try {
      final stopped =
          await ref.read(workSessionRepositoryProvider).stopSession(session.id);
      invalidateSessions(ref);
      if (!mounted) return;
      showAppSnackBar(
        context,
        'Finished at ${card.flat}. ${stopped.totalDisplay} for this visit.',
        tone: AppTone.success,
      );
    } on ApiException catch (error) {
      if (mounted) {
        showAppSnackBar(context, error.message, tone: AppTone.danger);
      }
    } finally {
      if (mounted) setState(() => _busyEngagement = null);
    }
  }

  Future<void> _confirm(WorkSession session, {required bool correct}) async {
    try {
      await ref.read(workSessionRepositoryProvider).confirmSession(
            session.id,
            correct: correct,
          );
      invalidateSessions(ref);
      if (!mounted) return;
      showAppSnackBar(
        context,
        correct
            ? 'Thank you — that day is confirmed.'
            : 'We have told the office. Your pay is not affected while it '
                'is checked.',
        tone: AppTone.success,
      );
    } on ApiException catch (error) {
      if (mounted) {
        showAppSnackBar(context, error.message, tone: AppTone.danger);
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final board = ref.watch(todayBoardProvider);

    return Scaffold(
      appBar: AppBar(
        title: const Text('Today'),
        actions: [
          // Today answers "am I owed for this visit"; the month answers "am I
          // owed for this home", which is the question that precedes a query.
          IconButton(
            tooltip: 'This month',
            icon: const Icon(Icons.calendar_month_outlined),
            onPressed: () => context.push(Routes.myWork),
          ),
        ],
      ),
      body: board.when(
        loading: () => const AppSkeletonList(),
        error: (error, _) => AppErrorState(
          message: error is ApiException
              ? error.message
              : 'Could not load your day.',
          onRetry: () => ref.invalidate(todayBoardProvider),
        ),
        data: (data) {
          if (data.cards.isEmpty) {
            return const AppEmptyState(
              icon: Icons.event_available_outlined,
              title: 'Nothing scheduled today',
              message: 'Enjoy the rest. Your next visit will appear here.',
            );
          }

          return RefreshIndicator(
            onRefresh: () async => ref.invalidate(todayBoardProvider),
            child: ListView(
              padding: const EdgeInsets.fromLTRB(
                AppSpacing.gutter,
                AppSpacing.md,
                AppSpacing.gutter,
                AppSpacing.bottomNavClearance,
              ),
              children: [
                _EarnedToday(board: data),
                for (final session in data.needingConfirmation) ...[
                  const SizedBox(height: AppSpacing.md),
                  _ConfirmCard(
                    session: session,
                    onAnswer: (correct) => _confirm(session, correct: correct),
                  ),
                ],
                if (data.done.isNotEmpty) ...[
                  const SizedBox(height: AppSpacing.lg),
                  const AppSectionHeader(title: 'Done'),
                  for (final card in data.done)
                    _FlatCard(card: card, busy: false),
                ],
                if (data.running != null) ...[
                  const SizedBox(height: AppSpacing.lg),
                  const AppSectionHeader(title: 'Now'),
                  _FlatCard(
                    card: data.running!,
                    busy: _busyEngagement == data.running!.engagementId,
                    onStop: () => _stop(data.running!),
                    onAskExtraTime: () => _askForExtraTime(data.running!),
                  ),
                ],
                if (data.upcoming.isNotEmpty) ...[
                  const SizedBox(height: AppSpacing.lg),
                  const AppSectionHeader(title: 'Next'),
                  for (final card in data.upcoming)
                    _FlatCard(
                      card: card,
                      busy: _busyEngagement == card.engagementId,
                      // Only one visit runs at a time: starting a second while
                      // the first is open would produce two overlapping spans
                      // and a bill nobody can explain.
                      onStart: data.running == null ? () => _start(card) : null,
                    ),
                ],
              ],
            ),
          );
        },
      ),
    );
  }

  Future<void> _askForExtraTime(TodayCard card) async {
    final session = card.session;
    if (session == null) return;
    await showModalBottomSheet<void>(
      context: context,
      isScrollControlled: true,
      backgroundColor: Colors.transparent,
      builder: (_) => OvertimeRequestSheet(card: card, session: session),
    );
  }
}

/// The running total, including the visit fee.
class _EarnedToday extends StatelessWidget {
  const _EarnedToday({required this.board});

  final TodayBoard board;

  @override
  Widget build(BuildContext context) {
    final text = Theme.of(context).textTheme;
    final hours = board.billedMinutes ~/ 60;
    final minutes = board.billedMinutes % 60;

    return AppCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text('EARNED TODAY',
              style: text.labelSmall?.copyWith(
                color: AppColors.textTertiary,
                letterSpacing: 1.2,
              ),),
          const SizedBox(height: AppSpacing.xs),
          Row(
            crossAxisAlignment: CrossAxisAlignment.baseline,
            textBaseline: TextBaseline.alphabetic,
            children: [
              Text(board.earnedDisplay,
                  style: text.headlineMedium
                      ?.copyWith(fontWeight: FontWeight.w700),),
              const Spacer(),
              Text('${hours}h ${minutes.toString().padLeft(2, '0')}m',
                  style: text.titleMedium
                      ?.copyWith(color: AppColors.textSecondary),),
            ],
          ),
          const SizedBox(height: AppSpacing.sm),
          ClipRRect(
            borderRadius: BorderRadius.circular(AppRadius.pill),
            child: LinearProgressIndicator(
              value: board.flatsTotal == 0
                  ? 0
                  : board.flatsDone / board.flatsTotal,
              minHeight: 6,
              backgroundColor: AppColors.surfaceMuted,
            ),
          ),
          const SizedBox(height: AppSpacing.xs),
          Text(
            '${board.flatsDone} of ${board.flatsTotal} homes done',
            style: text.bodySmall?.copyWith(color: AppColors.textSecondary),
          ),
        ],
      ),
    );
  }
}

/// One flat's card. State drives the single action it offers.
class _FlatCard extends StatelessWidget {
  const _FlatCard({
    required this.card,
    required this.busy,
    this.onStart,
    this.onStop,
    this.onAskExtraTime,
  });

  final TodayCard card;
  final bool busy;
  final VoidCallback? onStart;
  final VoidCallback? onStop;
  final VoidCallback? onAskExtraTime;

  @override
  Widget build(BuildContext context) {
    final text = Theme.of(context).textTheme;
    final session = card.session;
    final schedule =
        '${WorkSession.prettyTime(card.scheduledStart)} – ${WorkSession.prettyTime(card.scheduledEnd)}';

    return Padding(
      padding: const EdgeInsets.only(bottom: AppSpacing.sm),
      child: AppCard(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Expanded(
                  child: Text(
                    card.flat.isEmpty ? card.residentName : card.flat,
                    style:
                        text.titleMedium?.copyWith(fontWeight: FontWeight.w700),
                  ),
                ),
                if (card.isDone)
                  const AppStatusChip(
                    label: 'Done',
                    tone: AppTone.success,
                    icon: Icons.check,
                  )
                else if (card.isRunning)
                  const AppStatusChip(
                    label: 'Working',
                    tone: AppTone.info,
                    icon: Icons.timelapse,
                  ),
              ],
            ),
            const SizedBox(height: AppSpacing.xxs),
            Text(card.residentName,
                style:
                    text.bodySmall?.copyWith(color: AppColors.textSecondary),),
            const SizedBox(height: AppSpacing.xs),
            if (card.isDone && session != null) ...[
              Text(
                '${_clock(session.startedAt)} – ${_clock(session.endedAt)}'
                '   ·   ${_duration(session.billedMinutes)}',
                style: text.bodyMedium,
              ),
              const SizedBox(height: AppSpacing.xxs),
              // The fee is named, not absorbed. She should be able to check the
              // number against what she was told it would be.
              Text(
                '${formatPaise(session.timePaise + session.overtimePaise)} work'
                '  +  ${session.visitFeeDisplay} visit'
                '  =  ${session.totalDisplay}',
                style: text.bodyMedium?.copyWith(fontWeight: FontWeight.w600),
              ),
            ] else if (card.isRunning && session != null) ...[
              Text(
                  'Started ${_clock(session.startedAt)}  ·  '
                  '${_duration(session.elapsedMinutes)} so far',
                  style: text.bodyMedium,),
              const SizedBox(height: AppSpacing.xxs),
              Text('Ends about ${WorkSession.prettyTime(card.scheduledEnd)}',
                  style:
                      text.bodySmall?.copyWith(color: AppColors.textSecondary),),
              const SizedBox(height: AppSpacing.sm),
              AppButton(
                label: 'Stop work',
                onPressed: busy ? null : onStop,
                isLoading: busy,
              ),
              if (onAskExtraTime != null) ...[
                const SizedBox(height: AppSpacing.xs),
                AppButton.secondary(
                  label: 'Ask to stay longer',
                  onPressed: busy ? null : onAskExtraTime,
                ),
              ],
            ] else ...[
              Text(schedule, style: text.bodyMedium),
              const SizedBox(height: AppSpacing.sm),
              AppButton(
                label: 'Start work',
                onPressed: busy ? null : onStart,
                isLoading: busy,
              ),
              if (onStart == null)
                Padding(
                  padding: const EdgeInsets.only(top: AppSpacing.xxs),
                  child: Text(
                    'Finish the home you are in first.',
                    style:
                        text.bodySmall?.copyWith(color: AppColors.textTertiary),
                  ),
                ),
            ],
          ],
        ),
      ),
    );
  }

  static String _clock(DateTime? at) {
    if (at == null) return '--:--';
    return '${at.hour.toString().padLeft(2, '0')}:'
        '${at.minute.toString().padLeft(2, '0')}';
  }

  static String _duration(int minutes) =>
      '${minutes ~/ 60}h ${(minutes % 60).toString().padLeft(2, '0')}m';
}

/// "We closed this for you — is that right?"
///
/// Framed as a question about her own day rather than as a correction she has
/// to dispute. She is asked only what she can actually know (was I here until
/// then?), never to compute what she is owed.
class _ConfirmCard extends StatelessWidget {
  const _ConfirmCard({required this.session, required this.onAnswer});

  final WorkSession session;
  final void Function(bool correct) onAnswer;

  @override
  Widget build(BuildContext context) {
    final text = Theme.of(context).textTheme;

    return AppCard(
      color: AppColors.warningSoft,
      borderColor: AppColors.warning.withValues(alpha: 0.35),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text('${session.flat} — you did not tap Stop',
              style: text.titleSmall?.copyWith(fontWeight: FontWeight.w700),),
          const SizedBox(height: AppSpacing.xxs),
          Text(
            'We used your usual time, '
            '${WorkSession.prettyTime(session.scheduledStart)} to '
            '${WorkSession.prettyTime(session.scheduledEnd)}, '
            'and paid you ${session.totalDisplay}. Is that right?',
            style: text.bodyMedium,
          ),
          const SizedBox(height: AppSpacing.sm),
          Row(
            children: [
              Expanded(
                child: AppButton(
                  label: 'Yes, correct',
                  onPressed: () => onAnswer(true),
                ),
              ),
              const SizedBox(width: AppSpacing.xs),
              Expanded(
                child: AppButton.secondary(
                  label: 'No, check it',
                  onPressed: () => onAnswer(false),
                ),
              ),
            ],
          ),
        ],
      ),
    );
  }
}
