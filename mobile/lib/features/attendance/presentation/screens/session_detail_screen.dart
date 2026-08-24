import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../../core/errors/api_exception.dart';
import '../../../../shared/design_system.dart';
import '../../data/models/work_session_models.dart';
import '../providers/work_session_provider.dart';

/// Module 7.7 — one visit, with its arithmetic shown.
///
/// -------------------------------------------------------------------------
/// THE SENTENCE THAT PREVENTS MOST DISPUTES
/// -------------------------------------------------------------------------
/// When a day comes out short, both parties assume the same wrong thing: that
/// the smaller number is a *penalty*. Residents assume the app fined her;
/// workers fear exactly that. It never is — pay tracks time worked and nothing
/// else, and the gap is only the minutes not worked.
///
/// Saying so in words, right where the smaller number appears, is far cheaper
/// than arbitrating it a month later. That is the [_NoPenaltyNote] below, and it
/// is the most load-bearing paragraph on the screen.
class SessionDetailScreen extends ConsumerWidget {
  const SessionDetailScreen({super.key, required this.sessionId});

  final String sessionId;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final month = ref.watch(sessionDayProvider);
    final range = SessionRange.month(month);
    final sessions = ref.watch(sessionHistoryProvider(range));

    return Scaffold(
      appBar: AppBar(title: const Text('Visit')),
      body: sessions.when(
        loading: () => const AppSkeletonList(count: 3),
        error: (error, _) => AppErrorState(
          message: error is ApiException ? error.message : 'Could not load it.',
          onRetry: () => ref.invalidate(sessionHistoryProvider(range)),
        ),
        data: (data) {
          final session = data
              .where((s) => s.id == sessionId)
              .cast<WorkSession?>()
              .firstWhere(
                (s) => s != null,
                orElse: () => null,
              );
          if (session == null) {
            return const AppEmptyState(
              icon: Icons.search_off,
              title: 'Visit not found',
              message: 'It may belong to a different month.',
            );
          }
          return _Body(session: session);
        },
      ),
    );
  }
}

class _Body extends StatelessWidget {
  const _Body({required this.session});

  final WorkSession session;

  int get _scheduledMinutes {
    final start = _minutesOfDay(session.scheduledStart);
    final end = _minutesOfDay(session.scheduledEnd);
    if (start == null || end == null) return 0;
    return end >= start ? end - start : (24 * 60) - start + end;
  }

  int get _fullDayPaise {
    if (_scheduledMinutes == 0) return session.totalPaise;
    final rate = session.billableMinutes == 0
        ? 0
        : (session.timePaise / session.billableMinutes);
    return (rate * _scheduledMinutes).round() + session.visitFeePaise;
  }

  bool get _isShort =>
      _scheduledMinutes > 0 && session.billedMinutes < _scheduledMinutes;

  @override
  Widget build(BuildContext context) {
    final text = Theme.of(context).textTheme;

    return ListView(
      padding: const EdgeInsets.fromLTRB(
        AppSpacing.gutter,
        AppSpacing.md,
        AppSpacing.gutter,
        AppSpacing.xxl,
      ),
      children: [
        AppCard(
          color: _isShort ? AppColors.warningSoft : AppColors.surface,
          borderColor: _isShort
              ? AppColors.warning.withValues(alpha: 0.3)
              : AppColors.border,
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Row(
                children: [
                  Expanded(
                    child: Text(
                      _isShort ? 'Short day' : 'Full day',
                      style: text.titleMedium
                          ?.copyWith(fontWeight: FontWeight.w700),
                    ),
                  ),
                  Text(_duration(session.billedMinutes),
                      style: text.titleMedium,),
                ],
              ),
              const SizedBox(height: AppSpacing.xxs),
              Text(session.totalDisplay,
                  style: text.headlineSmall
                      ?.copyWith(fontWeight: FontWeight.w700),),
            ],
          ),
        ),
        const SizedBox(height: AppSpacing.md),
        const AppSectionHeader(title: 'What happened'),
        AppCard(
          child: Column(
            children: [
              _Line(
                label: 'Scheduled',
                value: '${WorkSession.prettyTime(session.scheduledStart)} – '
                    '${WorkSession.prettyTime(session.scheduledEnd)}',
              ),
              _Line(label: 'Arrived', value: _clock(session.startedAt)),
              _Line(label: 'Left', value: _clock(session.endedAt)),
              const Divider(height: AppSpacing.lg),
              _Line(
                label: 'Time',
                value: formatPaise(session.timePaise),
              ),
              if (session.overtimePaise > 0)
                _Line(
                  label: 'Approved extra time',
                  value: formatPaise(session.overtimePaise),
                ),
              // Never folded into an hourly figure. The resident is charged it,
              // so the resident gets to see it.
              _Line(
                label: 'Visit fee',
                value: session.visitFeeDisplay,
              ),
              const Divider(height: AppSpacing.lg),
              _Line(
                label: 'Billed',
                value: session.totalDisplay,
                emphasise: true,
              ),
              if (_isShort)
                _Line(
                  label: 'A full day would be',
                  value: formatPaise(_fullDayPaise),
                  muted: true,
                ),
            ],
          ),
        ),
        if (_isShort) ...[
          const SizedBox(height: AppSpacing.md),
          _NoPenaltyNote(
            difference: _fullDayPaise - session.totalPaise,
            minutesShort: _scheduledMinutes - session.billedMinutes,
          ),
        ],
        if (session.unbilledExtraMinutes > 0) ...[
          const SizedBox(height: AppSpacing.md),
          AppCard(
            color: AppColors.successSoft,
            borderColor: AppColors.success.withValues(alpha: 0.25),
            child: Text(
              'She worked ${session.unbilledExtraMinutes} minutes past the '
              'scheduled finish without asking. You have not been charged for '
              'it.',
              style: text.bodyMedium,
            ),
          ),
        ],
        const SizedBox(height: AppSpacing.md),
        const AppSectionHeader(title: 'How this was recorded'),
        AppCard(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Row(
                children: [
                  Expanded(child: Text(session.source.label)),
                  AppStatusChip(
                    label: 'Tier ${session.source.tier}',
                    tone: session.source.isTrusted
                        ? AppTone.success
                        : AppTone.warning,
                    dense: true,
                  ),
                ],
              ),
              if (!session.source.isTrusted) ...[
                const SizedBox(height: AppSpacing.xxs),
                Text(
                  'This visit was worked out rather than observed. If it looks '
                  'wrong, say so — she is paid while it is checked.',
                  style:
                      text.bodySmall?.copyWith(color: AppColors.textSecondary),
                ),
              ],
              if (session.reviewNote.isNotEmpty) ...[
                const SizedBox(height: AppSpacing.xs),
                Text(session.reviewNote,
                    style: text.bodySmall
                        ?.copyWith(color: AppColors.textSecondary),),
              ],
            ],
          ),
        ),
      ],
    );
  }

  static String _clock(DateTime? at) => at == null
      ? '--:--'
      : '${at.hour.toString().padLeft(2, '0')}:'
          '${at.minute.toString().padLeft(2, '0')}';

  static String _duration(int minutes) =>
      '${minutes ~/ 60}h ${(minutes % 60).toString().padLeft(2, '0')}m';

  static int? _minutesOfDay(String wallClock) {
    final parts = wallClock.split(':');
    if (parts.length < 2) return null;
    final hour = int.tryParse(parts[0]);
    final minute = int.tryParse(parts[1]);
    if (hour == null || minute == null) return null;
    return hour * 60 + minute;
  }
}

/// The paragraph this screen exists for.
class _NoPenaltyNote extends StatelessWidget {
  const _NoPenaltyNote({
    required this.difference,
    required this.minutesShort,
  });

  final int difference;
  final int minutesShort;

  @override
  Widget build(BuildContext context) {
    return AppCard(
      color: AppColors.surfaceMuted,
      child: Text(
        'You are charged for time worked. There is no late fee and no penalty '
        'on top — the ${formatPaise(difference)} difference is simply the '
        '$minutesShort minutes that were not worked.',
        style: Theme.of(context).textTheme.bodyMedium,
      ),
    );
  }
}

class _Line extends StatelessWidget {
  const _Line({
    required this.label,
    required this.value,
    this.emphasise = false,
    this.muted = false,
  });

  final String label;
  final String value;
  final bool emphasise;
  final bool muted;

  @override
  Widget build(BuildContext context) {
    final text = Theme.of(context).textTheme;
    final style = emphasise
        ? text.titleSmall?.copyWith(fontWeight: FontWeight.w700)
        : text.bodyMedium?.copyWith(
            color: muted ? AppColors.textTertiary : AppColors.textPrimary,
          );

    return Padding(
      padding: const EdgeInsets.symmetric(vertical: AppSpacing.xxs),
      child: Row(
        children: [
          Expanded(
            child: Text(label,
                style:
                    text.bodyMedium?.copyWith(color: AppColors.textSecondary),),
          ),
          Text(value, style: style),
        ],
      ),
    );
  }
}
