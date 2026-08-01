import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../../core/errors/api_exception.dart';
import '../../../../shared/design_system.dart';
import '../../data/models/booking_models.dart';
import '../providers/booking_provider.dart';

/// Module 5.3 — the worker marks which days they can take one-off jobs.
///
/// This screen is what makes Module 5 work at all: matching requires an
/// explicit opt-in per date, so a worker who never comes here is never offered
/// a one-day booking. The empty state says so plainly rather than looking like
/// a bug.
class WorkerAvailabilityScreen extends ConsumerWidget {
  const WorkerAvailabilityScreen({super.key});

  Future<void> _addDate(BuildContext context, WidgetRef ref) async {
    final now = DateTime.now();
    final date = await showDatePicker(
      context: context,
      initialDate: now.add(const Duration(days: 1)),
      firstDate: DateTime(now.year, now.month, now.day),
      lastDate: now.add(const Duration(days: 90)),
    );
    if (date == null || !context.mounted) return;

    final window = await showModalBottomSheet<_DayChoice>(
      context: context,
      builder: (_) => _DayChoiceSheet(date: date),
    );
    if (window == null) return;

    try {
      await ref.read(bookingRepositoryProvider).setMyAvailability(
            DayAvailability(
              date: date,
              isAvailable: window.isAvailable,
              startTime: window.startTime,
              endTime: window.endTime,
            ),
          );
      if (!context.mounted) return;
      ref.invalidate(myAvailabilityProvider);
    } on ApiException catch (error) {
      if (!context.mounted) return;
      ScaffoldMessenger.of(context)
          .showSnackBar(SnackBar(content: Text(error.message)));
    }
  }

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final availability = ref.watch(myAvailabilityProvider);

    return Scaffold(
      appBar: AppBar(title: const Text('Days I can work')),
      floatingActionButton: FloatingActionButton.extended(
        onPressed: () => _addDate(context, ref),
        icon: const Icon(Icons.add),
        label: const Text('Add a day'),
      ),
      body: availability.when(
        loading: () => const AppSkeletonList(count: 4),
        error: (error, _) => AppErrorState(
          message: error is ApiException
              ? error.message
              : 'Could not load your availability.',
          onRetry: () => ref.invalidate(myAvailabilityProvider),
        ),
        data: (days) {
          if (days.isEmpty) {
            return const AppEmptyState(
              icon: Icons.calendar_month_outlined,
              title: 'No days marked yet',
              message: 'Residents can only book you for one-day jobs on days '
                  'you have marked here. Add the days you are free.',
            );
          }

          return RefreshIndicator(
            onRefresh: () async => ref.invalidate(myAvailabilityProvider),
            child: ListView.builder(
              physics: const AlwaysScrollableScrollPhysics(),
              padding: const EdgeInsets.fromLTRB(
                AppSpacing.gutter,
                AppSpacing.sm,
                AppSpacing.gutter,
                AppSpacing.bottomNavClearance,
              ),
              itemCount: days.length,
              itemBuilder: (context, index) => AppFadeIn(
                index: index,
                child: _DayTile(day: days[index]),
              ),
            ),
          );
        },
      ),
    );
  }
}

class _DayTile extends ConsumerWidget {
  const _DayTile({required this.day});

  final DayAvailability day;

  Future<void> _toggle(BuildContext context, WidgetRef ref) async {
    try {
      await ref.read(bookingRepositoryProvider).setMyAvailability(
            DayAvailability(
              date: day.date,
              isAvailable: !day.isAvailable,
              startTime: day.startTime,
              endTime: day.endTime,
              note: day.note,
            ),
          );
      if (!context.mounted) return;
      ref.invalidate(myAvailabilityProvider);
    } on ApiException catch (error) {
      if (!context.mounted) return;
      ScaffoldMessenger.of(context)
          .showSnackBar(SnackBar(content: Text(error.message)));
    }
  }

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final colour =
        day.isAvailable ? AppColors.success : AppColors.textSecondary;

    return AppCard(
      margin: const EdgeInsets.only(bottom: AppSpacing.xs),
      padding: EdgeInsets.zero,
      child: ListTile(
        contentPadding: const EdgeInsets.symmetric(
          horizontal: AppSpacing.md,
          vertical: AppSpacing.xxs,
        ),
        leading: Container(
          width: 42,
          height: 42,
          decoration: BoxDecoration(
            color: colour.withValues(alpha: 0.12),
            shape: BoxShape.circle,
          ),
          child: Icon(
            day.isAvailable ? Icons.check_rounded : Icons.block_rounded,
            color: colour,
            size: AppIconSize.md,
          ),
        ),
        title: Text(
          '${day.date.day}/${day.date.month}/${day.date.year}',
          style: Theme.of(context).textTheme.titleSmall,
        ),
        subtitle: Text(
          day.isAvailable ? day.windowLabel : 'Not available',
          style: TextStyle(color: colour, fontSize: 13.5),
        ),
        trailing: Switch(
          value: day.isAvailable,
          onChanged: (_) => _toggle(context, ref),
        ),
      ),
    );
  }
}

/// Whether the worker is free that day, and any narrower window.
class _DayChoice {
  const _DayChoice({
    required this.isAvailable,
    this.startTime,
    this.endTime,
  });

  final bool isAvailable;
  final String? startTime;
  final String? endTime;
}

class _DayChoiceSheet extends StatefulWidget {
  const _DayChoiceSheet({required this.date});

  final DateTime date;

  @override
  State<_DayChoiceSheet> createState() => _DayChoiceSheetState();
}

class _DayChoiceSheetState extends State<_DayChoiceSheet> {
  bool _allDay = true;
  TimeOfDay _start = const TimeOfDay(hour: 9, minute: 0);
  TimeOfDay _end = const TimeOfDay(hour: 18, minute: 0);
  String? _error;

  String _wire(TimeOfDay time) => '${time.hour.toString().padLeft(2, '0')}:'
      '${time.minute.toString().padLeft(2, '0')}';

  Future<void> _pick({required bool isStart}) async {
    final picked = await showTimePicker(
      context: context,
      initialTime: isStart ? _start : _end,
    );
    if (picked == null) return;
    setState(() {
      if (isStart) {
        _start = picked;
      } else {
        _end = picked;
      }
      _error = null;
    });
  }

  void _submit() {
    if (!_allDay) {
      final startMinutes = _start.hour * 60 + _start.minute;
      final endMinutes = _end.hour * 60 + _end.minute;
      // The server rejects this too; catching it here saves a round trip on a
      // connection that may be poor.
      if (endMinutes <= startMinutes) {
        setState(() => _error = 'The end time must be after the start time.');
        return;
      }
    }

    Navigator.of(context).pop(
      _DayChoice(
        isAvailable: true,
        startTime: _allDay ? null : _wire(_start),
        endTime: _allDay ? null : _wire(_end),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final date = widget.date;

    return Padding(
      padding: const EdgeInsets.all(20),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            '${date.day}/${date.month}/${date.year}',
            style: Theme.of(context)
                .textTheme
                .titleLarge
                ?.copyWith(fontWeight: FontWeight.w700),
          ),
          const SizedBox(height: 16),
          SwitchListTile(
            contentPadding: EdgeInsets.zero,
            title: const Text('Available all day'),
            subtitle: const Text('Turn off to set specific hours'),
            value: _allDay,
            onChanged: (value) => setState(() => _allDay = value),
          ),
          if (!_allDay) ...[
            const SizedBox(height: 8),
            Row(
              children: [
                Expanded(
                  child: OutlinedButton(
                    onPressed: () => _pick(isStart: true),
                    style: OutlinedButton.styleFrom(
                      minimumSize: const Size.fromHeight(52),
                    ),
                    child: Text('From ${_start.format(context)}'),
                  ),
                ),
                const SizedBox(width: 12),
                Expanded(
                  child: OutlinedButton(
                    onPressed: () => _pick(isStart: false),
                    style: OutlinedButton.styleFrom(
                      minimumSize: const Size.fromHeight(52),
                    ),
                    child: Text('To ${_end.format(context)}'),
                  ),
                ),
              ],
            ),
          ],
          if (_error != null) ...[
            const SizedBox(height: 12),
            Text(_error!, style: const TextStyle(color: AppColors.danger)),
          ],
          const SizedBox(height: 20),
          ElevatedButton.icon(
            onPressed: _submit,
            icon: const Icon(Icons.check),
            label: const Text('Mark available'),
          ),
          const SizedBox(height: 8),
          OutlinedButton.icon(
            onPressed: () =>
                Navigator.of(context).pop(const _DayChoice(isAvailable: false)),
            icon: const Icon(Icons.block),
            label: const Text('Mark unavailable'),
            style: OutlinedButton.styleFrom(
              foregroundColor: AppColors.danger,
              minimumSize: const Size.fromHeight(52),
            ),
          ),
        ],
      ),
    );
  }
}
