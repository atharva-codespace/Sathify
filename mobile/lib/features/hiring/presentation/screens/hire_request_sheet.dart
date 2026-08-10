import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../../core/errors/api_exception.dart';
import '../../../../shared/design_system.dart';
import '../../data/models/hiring_models.dart';
import '../providers/hiring_provider.dart';

/// Module 4.4 — the resident proposes terms.
///
/// The days, time, duration and pay collected here become the engagement
/// verbatim if the worker accepts, so the sheet states that plainly rather than
/// presenting the numbers as an opening offer.
class HireRequestSheet extends ConsumerStatefulWidget {
  const HireRequestSheet({required this.worker, super.key});

  final WorkerDetail worker;

  @override
  ConsumerState<HireRequestSheet> createState() => _HireRequestSheetState();
}

class _HireRequestSheetState extends ConsumerState<HireRequestSheet> {
  final _formKey = GlobalKey<FormState>();
  final _messageController = TextEditingController();

  /// Weekdays by default — the common case for domestic help.
  final Set<int> _selectedDays = {0, 1, 2, 3, 4};
  TimeOfDay _startTime = const TimeOfDay(hour: 9, minute: 0);
  int _durationMinutes = 60;
  ServiceType? _serviceType;

  bool _isSubmitting = false;
  String? _formError;

  @override
  void initState() {
    super.initState();
    final services = widget.worker.summary.serviceTypes;
    if (services.isNotEmpty) _serviceType = services.first;
  }

  @override
  void dispose() {
    _messageController.dispose();
    super.dispose();
  }

  /// The platform rate, as the server reported it on this worker.
  ///
  /// Read from the profile rather than kept as a constant in the app: pricing
  /// is the server's decision, and a copy here would keep quoting the old
  /// figure on every phone that had not been updated. The server stamps the
  /// rate on again when the request lands, so a stale value cannot be
  /// submitted — it could only be *displayed*, which is what this avoids.
  int? get _monthlyRate => widget.worker.summary.expectedMonthlyRate;

  String get _wireStartTime => '${_startTime.hour.toString().padLeft(2, '0')}:'
      '${_startTime.minute.toString().padLeft(2, '0')}';

  Future<void> _pickStartTime() async {
    final picked =
        await showTimePicker(context: context, initialTime: _startTime);
    if (picked != null) setState(() => _startTime = picked);
  }

  Future<void> _submit() async {
    setState(() => _formError = null);

    if (!_formKey.currentState!.validate()) return;
    if (_selectedDays.isEmpty) {
      setState(() => _formError = 'Choose at least one day.');
      return;
    }
    if (_serviceType == null) {
      setState(() => _formError = 'Choose the service you need.');
      return;
    }

    setState(() => _isSubmitting = true);
    try {
      await ref.read(hiringRepositoryProvider).sendHireRequest(
            workerId: widget.worker.id,
            serviceTypeId: _serviceType!.id,
            terms: RecurringTerms(
              daysOfWeek: _selectedDays.toList()..sort(),
              startTime: _wireStartTime,
              expectedDurationMinutes: _durationMinutes,
              // Sent for completeness; the server ignores it and stamps the
              // platform rate on itself, so this cannot underpay anybody.
              monthlyRate: _monthlyRate ?? 0,
            ),
            message: _messageController.text.trim(),
          );
      if (mounted) Navigator.of(context).pop(true);
    } on ApiException catch (error) {
      if (!mounted) return;
      setState(() {
        _isSubmitting = false;
        // The server flags the offending field where it can — surface that
        // rather than a generic failure.
        _formError = error.fieldError('worker') ??
            error.fieldError('service_type') ??
            error.message;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final services = widget.worker.summary.serviceTypes;

    return Padding(
      padding: EdgeInsets.only(
        left: AppSpacing.lg,
        right: AppSpacing.lg,
        bottom: MediaQuery.of(context).viewInsets.bottom + AppSpacing.lg,
      ),
      child: SingleChildScrollView(
        child: Form(
          key: _formKey,
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Row(
                children: [
                  AppAvatar(
                    name: widget.worker.fullName,
                    imageUrl: widget.worker.summary.photoUrl,
                    seed: widget.worker.id,
                    size: 44,
                  ),
                  const SizedBox(width: AppSpacing.sm),
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text(
                          'Hire ${widget.worker.fullName}',
                          style: theme.textTheme.titleLarge,
                        ),
                        Text(
                          'These terms become the agreement if they accept.',
                          style: theme.textTheme.bodySmall,
                        ),
                      ],
                    ),
                  ),
                ],
              ),
              const SizedBox(height: AppSpacing.lg),
              if (services.length > 1) ...[
                const _Label('Service'),
                const SizedBox(height: AppSpacing.xs),
                Wrap(
                  spacing: AppSpacing.xs,
                  runSpacing: AppSpacing.xs,
                  children: services
                      .map(
                        (service) => AppFilterChip(
                          label: service.name,
                          selected: _serviceType?.id == service.id,
                          onTap: () => setState(() => _serviceType = service),
                        ),
                      )
                      .toList(),
                ),
                const SizedBox(height: AppSpacing.lg),
              ],
              const _Label('Which days'),
              const SizedBox(height: AppSpacing.xs),
              Wrap(
                spacing: AppSpacing.xxs + 2,
                runSpacing: AppSpacing.xxs + 2,
                children: Weekday.values
                    .map(
                      (day) => AppFilterChip(
                        label: day.shortLabel,
                        selected: _selectedDays.contains(day.wireValue),
                        onTap: () => setState(() {
                          if (_selectedDays.contains(day.wireValue)) {
                            _selectedDays.remove(day.wireValue);
                          } else {
                            _selectedDays.add(day.wireValue);
                          }
                        }),
                      ),
                    )
                    .toList(),
              ),
              const SizedBox(height: AppSpacing.lg),
              Row(
                children: [
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        const _Label('Start time'),
                        const SizedBox(height: AppSpacing.xs),
                        AppButton.secondary(
                          label: _startTime.format(context),
                          icon: Icons.schedule_rounded,
                          onPressed: _pickStartTime,
                        ),
                      ],
                    ),
                  ),
                  const SizedBox(width: 12),
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        const _Label('Duration'),
                        const SizedBox(height: AppSpacing.xs),
                        DropdownButtonFormField<int>(
                          // `value`, not `initialValue`. The replacement landed
                          // after Flutter 3.33 and pubspec declares a 3.27
                          // floor, so switching would raise the minimum SDK for
                          // the whole app to remove one lint. Deliberate.
                          // ignore: deprecated_member_use
                          value: _durationMinutes,
                          items: const [30, 45, 60, 90, 120, 180, 240]
                              .map(
                                (minutes) => DropdownMenuItem(
                                  value: minutes,
                                  child: Text(
                                    minutes < 60
                                        ? '$minutes min'
                                        : '${(minutes / 60).toStringAsFixed(minutes % 60 == 0 ? 0 : 1)} hr',
                                  ),
                                ),
                              )
                              .toList(),
                          onChanged: (value) =>
                              setState(() => _durationMinutes = value ?? 60),
                        ),
                      ],
                    ),
                  ),
                ],
              ),
              const SizedBox(height: 20),
              const _Label('Monthly pay'),
              const SizedBox(height: 8),
              // Shown, not asked. Sathify quotes one rate for every helper, so
              // there is nothing to type and nothing to haggle over — stating
              // it plainly is more honest than a prefilled box that looks
              // editable and is overruled by the server.
              Container(
                width: double.infinity,
                padding: const EdgeInsets.symmetric(
                  horizontal: AppSpacing.sm,
                  vertical: AppSpacing.sm,
                ),
                decoration: BoxDecoration(
                  color: AppColors.surfaceMuted,
                  borderRadius: BorderRadius.circular(AppRadius.md),
                ),
                child: Row(
                  children: [
                    const Icon(Icons.currency_rupee, size: AppIconSize.md),
                    const SizedBox(width: AppSpacing.xs),
                    Expanded(
                      child: Text(
                        _monthlyRate == null
                            ? 'Set by Sathify'
                            : '$_monthlyRate per month',
                        style: Theme.of(context).textTheme.titleMedium,
                      ),
                    ),
                    Text(
                      'Fixed price',
                      style: Theme.of(context).textTheme.bodySmall,
                    ),
                  ],
                ),
              ),
              const SizedBox(height: 20),
              const _Label('Message (optional)'),
              const SizedBox(height: 8),
              TextFormField(
                controller: _messageController,
                maxLines: 3,
                maxLength: 500,
                decoration: const InputDecoration(
                  hintText: 'Anything they should know before accepting',
                ),
              ),
              if (_formError != null) ...[
                const SizedBox(height: AppSpacing.xs),
                AppErrorBanner(message: _formError!),
                const SizedBox(height: AppSpacing.sm),
              ],
              AppButton(
                label: 'Send request',
                icon: Icons.send_rounded,
                isLoading: _isSubmitting,
                onPressed: _submit,
              ),
              const SizedBox(height: AppSpacing.xxs),
              AppButton.text(
                label: 'Cancel',
                expand: true,
                onPressed: _isSubmitting
                    ? null
                    : () => Navigator.of(context).pop(false),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

class _Label extends StatelessWidget {
  const _Label(this.text);

  final String text;

  @override
  Widget build(BuildContext context) =>
      Text(text, style: Theme.of(context).textTheme.titleSmall);
}
