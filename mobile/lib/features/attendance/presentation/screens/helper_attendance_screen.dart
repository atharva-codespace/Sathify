import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../../core/errors/api_exception.dart';
import '../../../../shared/design_system.dart';
import '../../data/models/work_session_models.dart';
import '../providers/work_session_provider.dart';
import '../widgets/overtime_approval_sheet.dart';
import 'session_detail_screen.dart';

/// Module 7.7 — the resident's view of their helper's attendance.
///
/// Their job here is verification, not administration, so the screen answers
/// one question — *did she come, and for how long* — and then gets out of the
/// way. Everything deeper (why a day was short, how it was priced, how to query
/// it) lives one tap down, on [SessionDetailScreen].
class HelperAttendanceScreen extends ConsumerWidget {
  const HelperAttendanceScreen({super.key, this.engagementId});

  final int? engagementId;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final month = ref.watch(sessionDayProvider);
    final range = SessionRange.month(month, engagementId: engagementId);
    final sessions = ref.watch(sessionHistoryProvider(range));

    return Scaffold(
      appBar: AppBar(title: const Text('Attendance')),
      body: sessions.when(
        loading: () => const AppSkeletonList(),
        error: (error, _) => AppErrorState(
          message: error is ApiException
              ? error.message
              : 'Could not load attendance.',
          onRetry: () => ref.invalidate(sessionHistoryProvider(range)),
        ),
        data: (data) {
          if (data.isEmpty) {
            return const AppEmptyState(
              icon: Icons.calendar_month_outlined,
              title: 'Nothing recorded yet',
              message: 'Visits will appear here as they happen.',
            );
          }

          final running = data.where((s) => s.isRunning).toList();
          final finished = data.where((s) => !s.isRunning).toList()
            ..sort((a, b) => b.visitDate.compareTo(a.visitDate));

          return RefreshIndicator(
            onRefresh: () async =>
                ref.invalidate(sessionHistoryProvider(range)),
            child: ListView(
              padding: const EdgeInsets.fromLTRB(
                AppSpacing.gutter,
                AppSpacing.md,
                AppSpacing.gutter,
                AppSpacing.bottomNavClearance,
              ),
              children: [
                if (running.isNotEmpty)
                  _WorkingNow(
                    session: running.first,
                    onApproveExtraTime: () => showModalBottomSheet<void>(
                      context: context,
                      isScrollControlled: true,
                      backgroundColor: Colors.transparent,
                      builder: (_) =>
                          OvertimeApprovalSheet(session: running.first),
                    ),
                  ),
                if (running.isNotEmpty) const SizedBox(height: AppSpacing.md),
                _MonthSummary(sessions: finished),
                const SizedBox(height: AppSpacing.lg),
                const AppSectionHeader(title: 'Recent visits'),
                for (final session in finished)
                  _VisitRow(
                    session: session,
                    onTap: () => Navigator.of(context).push(
                      MaterialPageRoute<void>(
                        builder: (_) =>
                            SessionDetailScreen(sessionId: session.id),
                      ),
                    ),
                  ),
              ],
            ),
          );
        },
      ),
    );
  }
}

class _WorkingNow extends StatelessWidget {
  const _WorkingNow({required this.session, required this.onApproveExtraTime});

  final WorkSession session;

  /// Rule 5's other half. Without a control here the worker's request has
  /// nowhere to land, and approved overtime — the only overtime that is paid —
  /// could never be set at all.
  final VoidCallback onApproveExtraTime;

  @override
  Widget build(BuildContext context) {
    final text = Theme.of(context).textTheme;

    return AppCard(
      color: AppColors.infoSoft,
      borderColor: AppColors.info.withValues(alpha: 0.25),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const AppStatusChip(
            label: 'Working now',
            tone: AppTone.info,
            icon: Icons.timelapse,
          ),
          const SizedBox(height: AppSpacing.xs),
          Text(
            '${session.workerName} arrived '
            '${_clock(session.startedAt)}',
            style: text.titleMedium?.copyWith(fontWeight: FontWeight.w700),
          ),
          const SizedBox(height: AppSpacing.xxs),
          Text(
            '${_duration(session.elapsedMinutes)} so far · '
            'ends about ${WorkSession.prettyTime(session.scheduledEnd)}',
            style: text.bodyMedium,
          ),
          const SizedBox(height: AppSpacing.xxs),
          // How the record was made, in the resident's words. A record whose
          // provenance is visible is one they can trust or challenge; one
          // without is just an assertion.
          Text(
            'Recorded by ${session.source.label.toLowerCase()}',
            style: text.bodySmall?.copyWith(color: AppColors.textSecondary),
          ),
          const SizedBox(height: AppSpacing.sm),
          AppButton.secondary(
            label: session.approvedOvertimeMinutes > 0
                ? 'Extra time: ${session.approvedOvertimeMinutes} min approved'
                : 'Approve extra time',
            onPressed: onApproveExtraTime,
          ),
        ],
      ),
    );
  }

  static String _clock(DateTime? at) => at == null
      ? '--:--'
      : '${at.hour.toString().padLeft(2, '0')}:'
          '${at.minute.toString().padLeft(2, '0')}';

  static String _duration(int minutes) =>
      '${minutes ~/ 60}h ${(minutes % 60).toString().padLeft(2, '0')}m';
}

class _MonthSummary extends StatelessWidget {
  const _MonthSummary({required this.sessions});

  final List<WorkSession> sessions;

  @override
  Widget build(BuildContext context) {
    final text = Theme.of(context).textTheme;
    final attended =
        sessions.where((s) => s.status != SessionStatus.noShow).length;
    final minutes = sessions.fold<int>(0, (sum, s) => sum + s.billedMinutes);
    final missed =
        sessions.where((s) => s.status == SessionStatus.noShow).length;

    return AppCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text('THIS MONTH SO FAR',
              style: text.labelSmall?.copyWith(
                color: AppColors.textTertiary,
                letterSpacing: 1.2,
              ),),
          const SizedBox(height: AppSpacing.sm),
          Row(
            children: [
              Expanded(
                child: _Stat(
                  value: '$attended',
                  label: attended == 1 ? 'day' : 'days',
                ),
              ),
              Expanded(
                child: _Stat(
                  value: '${minutes ~/ 60}h '
                      '${(minutes % 60).toString().padLeft(2, '0')}m',
                  label: 'worked',
                ),
              ),
              Expanded(
                child: _Stat(value: '$missed', label: 'missed'),
              ),
            ],
          ),
        ],
      ),
    );
  }
}

class _Stat extends StatelessWidget {
  const _Stat({required this.value, required this.label});

  final String value;
  final String label;

  @override
  Widget build(BuildContext context) {
    final text = Theme.of(context).textTheme;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(value,
            style: text.titleLarge?.copyWith(fontWeight: FontWeight.w700),),
        Text(label,
            style: text.bodySmall?.copyWith(color: AppColors.textSecondary),),
      ],
    );
  }
}

class _VisitRow extends StatelessWidget {
  const _VisitRow({required this.session, required this.onTap});

  final WorkSession session;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final text = Theme.of(context).textTheme;
    final scheduled = _scheduledMinutes(session);
    final isShort = scheduled > 0 && session.billedMinutes < scheduled;

    return Padding(
      padding: const EdgeInsets.only(bottom: AppSpacing.xs),
      child: AppCard(
        onTap: onTap,
        padding: const EdgeInsets.symmetric(
          horizontal: AppSpacing.md,
          vertical: AppSpacing.sm,
        ),
        child: Row(
          children: [
            SizedBox(
              width: 56,
              child: Text(
                '${session.visitDate.day} '
                '${_shortMonth(session.visitDate.month)}',
                style: text.bodyMedium?.copyWith(fontWeight: FontWeight.w600),
              ),
            ),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    session.status == SessionStatus.noShow
                        ? 'Did not come'
                        : '${_clock(session.startedAt)} – '
                            '${_clock(session.endedAt)}',
                    style: text.bodyMedium,
                  ),
                  if (session.needsReview)
                    Text('Needs checking',
                        style:
                            text.bodySmall?.copyWith(color: AppColors.warning),),
                ],
              ),
            ),
            Text(
              _duration(session.billedMinutes),
              style: text.bodyMedium?.copyWith(
                fontWeight: FontWeight.w600,
                color: isShort ? AppColors.warning : AppColors.textPrimary,
              ),
            ),
            const SizedBox(width: AppSpacing.xxs),
            const Icon(Icons.chevron_right,
                size: AppIconSize.sm, color: AppColors.textTertiary,),
          ],
        ),
      ),
    );
  }

  static int _scheduledMinutes(WorkSession session) {
    final start = _minutesOfDay(session.scheduledStart);
    final end = _minutesOfDay(session.scheduledEnd);
    if (start == null || end == null) return 0;
    return end >= start ? end - start : (24 * 60) - start + end;
  }

  static int? _minutesOfDay(String wallClock) {
    final parts = wallClock.split(':');
    if (parts.length < 2) return null;
    final hour = int.tryParse(parts[0]);
    final minute = int.tryParse(parts[1]);
    if (hour == null || minute == null) return null;
    return hour * 60 + minute;
  }

  static String _clock(DateTime? at) => at == null
      ? '--:--'
      : '${at.hour.toString().padLeft(2, '0')}:'
          '${at.minute.toString().padLeft(2, '0')}';

  static String _duration(int minutes) =>
      '${minutes ~/ 60}h ${(minutes % 60).toString().padLeft(2, '0')}m';

  static String _shortMonth(int month) => const [
        'Jan',
        'Feb',
        'Mar',
        'Apr',
        'May',
        'Jun',
        'Jul',
        'Aug',
        'Sep',
        'Oct',
        'Nov',
        'Dec',
      ][month - 1];
}
