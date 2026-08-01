import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../../core/errors/api_exception.dart';
import '../../../../shared/design_system.dart';
import '../../data/models/schedule_models.dart';
import '../providers/schedule_provider.dart';

/// Module 6.2 — the resident sets what they expect of a visit.
///
/// The grace period is the point of this screen, not the arrival time: the
/// engagement already says when the worker comes. What the resident is deciding
/// here is how much lateness is acceptable, which is what attendance (Module 7)
/// and the trust score (Module 9) are then measured against. The copy says so
/// plainly, because a grace window set carelessly becomes someone's reliability
/// record.
class TaskTimingSheet extends ConsumerStatefulWidget {
  const TaskTimingSheet({required this.engagementId, super.key});

  final int engagementId;

  @override
  ConsumerState<TaskTimingSheet> createState() => _TaskTimingSheetState();
}

class _TaskTimingSheetState extends ConsumerState<TaskTimingSheet> {
  final _notesController = TextEditingController();

  TimeOfDay? _arrival;
  TimeOfDay? _departure;
  int _grace = 15;
  bool _remindersEnabled = true;
  int _reminderLead = 60;

  bool _loaded = false;
  bool _isSaving = false;
  String? _error;

  @override
  void dispose() {
    _notesController.dispose();
    super.dispose();
  }

  /// Seeds the form from whatever is currently in force — which the server
  /// always supplies, whether or not the resident has customised it before.
  void _seed(TaskTiming timing) {
    if (_loaded) return;
    _loaded = true;

    _arrival = _parse(timing.expectedArrival);
    _departure = _parse(timing.expectedDeparture);
    _grace = timing.arrivalGraceMinutes;
    _remindersEnabled = timing.remindersEnabled;
    _reminderLead = timing.reminderLeadMinutes;
    _notesController.text = timing.taskNotes;
  }

  TimeOfDay? _parse(String value) {
    final parts = value.split(':');
    if (parts.length < 2) return null;
    final hour = int.tryParse(parts[0]);
    final minute = int.tryParse(parts[1]);
    if (hour == null || minute == null) return null;
    return TimeOfDay(hour: hour, minute: minute);
  }

  String _wire(TimeOfDay time) => '${time.hour.toString().padLeft(2, '0')}:'
      '${time.minute.toString().padLeft(2, '0')}';

  Future<void> _pick({required bool isArrival}) async {
    final picked = await showTimePicker(
      context: context,
      initialTime: (isArrival ? _arrival : _departure) ??
          const TimeOfDay(hour: 9, minute: 0),
    );
    if (picked == null) return;
    setState(() {
      if (isArrival) {
        _arrival = picked;
      } else {
        _departure = picked;
      }
      _error = null;
    });
  }

  Future<void> _save() async {
    if (_arrival == null || _departure == null) {
      setState(() => _error = 'Set both an arrival and a departure time.');
      return;
    }

    final arrivalMinutes = _arrival!.hour * 60 + _arrival!.minute;
    final departureMinutes = _departure!.hour * 60 + _departure!.minute;
    // The server rejects this too; catching it here saves a round trip on a
    // connection that may be poor.
    if (departureMinutes <= arrivalMinutes) {
      setState(() => _error = 'Departure must be after arrival.');
      return;
    }

    setState(() {
      _isSaving = true;
      _error = null;
    });

    try {
      await ref.read(scheduleRepositoryProvider).setTaskTiming(
            widget.engagementId,
            TaskTiming(
              expectedArrival: _wire(_arrival!),
              expectedDeparture: _wire(_departure!),
              arrivalGraceMinutes: _grace,
              taskNotes: _notesController.text.trim(),
              remindersEnabled: _remindersEnabled,
              reminderLeadMinutes: _reminderLead,
            ),
          );
      if (mounted) Navigator.of(context).pop(true);
    } on ApiException catch (error) {
      if (!mounted) return;
      setState(() {
        _isSaving = false;
        _error = error.message;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final timing = ref.watch(taskTimingProvider(widget.engagementId));

    return Padding(
      padding: EdgeInsets.only(
        left: 20,
        right: 20,
        top: 20,
        bottom: MediaQuery.of(context).viewInsets.bottom + 20,
      ),
      child: timing.when(
        loading: () => const SizedBox(
          height: 180,
          child: Center(child: CircularProgressIndicator()),
        ),
        error: (error, _) => SizedBox(
          height: 180,
          child: Center(
            child: Text(
              error is ApiException
                  ? error.message
                  : 'Could not load the expected times.',
              textAlign: TextAlign.center,
            ),
          ),
        ),
        data: (current) {
          _seed(current);
          return _form(context);
        },
      ),
    );
  }

  Widget _form(BuildContext context) {
    final theme = Theme.of(context);

    return SingleChildScrollView(
      child: Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            'Expected times',
            style: theme.textTheme.titleLarge
                ?.copyWith(fontWeight: FontWeight.w700),
          ),
          const SizedBox(height: 4),
          const Text(
            'Attendance is measured against these.',
            style: TextStyle(color: AppColors.textSecondary),
          ),
          const SizedBox(height: 20),
          Row(
            children: [
              Expanded(
                child: OutlinedButton.icon(
                  onPressed: () => _pick(isArrival: true),
                  icon: const Icon(Icons.login, size: 18),
                  label: Text(
                    _arrival == null ? 'Arrival' : _arrival!.format(context),
                  ),
                  style: OutlinedButton.styleFrom(
                    minimumSize: const Size.fromHeight(52),
                  ),
                ),
              ),
              const SizedBox(width: 12),
              Expanded(
                child: OutlinedButton.icon(
                  onPressed: () => _pick(isArrival: false),
                  icon: const Icon(Icons.logout, size: 18),
                  label: Text(
                    _departure == null
                        ? 'Departure'
                        : _departure!.format(context),
                  ),
                  style: OutlinedButton.styleFrom(
                    minimumSize: const Size.fromHeight(52),
                  ),
                ),
              ),
            ],
          ),
          const SizedBox(height: 24),
          Text(
            'How late is late?',
            style: theme.textTheme.titleSmall
                ?.copyWith(fontWeight: FontWeight.w700),
          ),
          const SizedBox(height: 4),
          const Text(
            'Arrivals within this window count as on time. This affects the '
            "worker's reliability record, so allow for traffic.",
            style: TextStyle(fontSize: 13, color: AppColors.textSecondary),
          ),
          const SizedBox(height: 8),
          Wrap(
            spacing: 8,
            children: [0, 5, 10, 15, 30]
                .map(
                  (minutes) => ChoiceChip(
                    label: Text(minutes == 0 ? 'Exact' : '$minutes min'),
                    selected: _grace == minutes,
                    onSelected: (_) => setState(() => _grace = minutes),
                  ),
                )
                .toList(),
          ),
          const SizedBox(height: 20),
          TextField(
            controller: _notesController,
            maxLines: 3,
            maxLength: 1000,
            decoration: const InputDecoration(
              labelText: 'What should be done? (optional)',
              hintText: 'Shown to the worker before every visit',
            ),
          ),
          SwitchListTile(
            contentPadding: EdgeInsets.zero,
            title: const Text('Remind before each visit'),
            subtitle: Text(
              _remindersEnabled ? '$_reminderLead minutes ahead' : 'Off',
            ),
            value: _remindersEnabled,
            onChanged: (value) => setState(() => _remindersEnabled = value),
          ),
          if (_remindersEnabled)
            Wrap(
              spacing: 8,
              children: [15, 30, 60, 120]
                  .map(
                    (minutes) => ChoiceChip(
                      label: Text(
                        minutes < 60 ? '$minutes min' : '${minutes ~/ 60} hr',
                      ),
                      selected: _reminderLead == minutes,
                      onSelected: (_) =>
                          setState(() => _reminderLead = minutes),
                    ),
                  )
                  .toList(),
            ),
          if (_error != null) ...[
            const SizedBox(height: 12),
            Text(_error!, style: const TextStyle(color: AppColors.danger)),
          ],
          const SizedBox(height: 20),
          ElevatedButton.icon(
            onPressed: _isSaving ? null : _save,
            icon: _isSaving
                ? const SizedBox(
                    width: 20,
                    height: 20,
                    child: CircularProgressIndicator(strokeWidth: 2),
                  )
                : const Icon(Icons.check),
            label: Text(_isSaving ? 'Saving…' : 'Save'),
          ),
          TextButton(
            onPressed:
                _isSaving ? null : () => Navigator.of(context).pop(false),
            child: const Text('Cancel'),
          ),
        ],
      ),
    );
  }
}
