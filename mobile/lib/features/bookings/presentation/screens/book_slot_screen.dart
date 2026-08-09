import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../../core/errors/api_exception.dart';
import '../../../../shared/design_system.dart';
import '../../../hiring/data/models/hiring_models.dart' show WorkerSearchResult;
import '../../../hiring/presentation/widgets/match_badge.dart';
import '../../data/models/booking_models.dart';
import '../providers/booking_provider.dart';

/// Modules 5.2 and 5.3 — pick a slot, then pick from the workers free for it.
///
/// The slot comes first and the workers second, which is the opposite of Module
/// 4's flow. That is deliberate: a one-day booking is driven by *when* the
/// resident needs help, and showing workers before a date is known would list
/// people who cannot make it.
///
/// -----------------------------------------------------------------------
/// WHY QUICK-PICK CHIPS RATHER THAN TWO PICKER BUTTONS
/// -----------------------------------------------------------------------
/// The screen previously opened Material's date and time dialogs behind two
/// buttons, so booking "tomorrow morning" — overwhelmingly the common case —
/// cost four taps and two modal round trips. A strip of the next fortnight and
/// a strip of working hours makes that two taps with nothing covering the
/// screen, and the dialogs stay available behind "Other" for the rest.
///
/// The three numbered steps are a genuine sequence, not decoration: workers
/// cannot be listed before a slot exists, and the booking cannot be sent before
/// a worker is chosen.
class BookSlotScreen extends ConsumerStatefulWidget {
  const BookSlotScreen({required this.categoryId, super.key});

  final int categoryId;

  @override
  ConsumerState<BookSlotScreen> createState() => _BookSlotScreenState();
}

class _BookSlotScreenState extends ConsumerState<BookSlotScreen> {
  DateTime? _date;
  TimeOfDay _startTime = const TimeOfDay(hour: 10, minute: 0);
  bool _searched = false;

  ServiceCategory? get _category {
    final categories = ref.read(serviceCategoriesProvider).value;
    if (categories == null) return null;
    for (final category in categories) {
      if (category.id == widget.categoryId) return category;
    }
    return null;
  }

  String get _wireStartTime => '${_startTime.hour.toString().padLeft(2, '0')}:'
      '${_startTime.minute.toString().padLeft(2, '0')}';

  BookingSlot? get _slot {
    if (_date == null) return null;
    return BookingSlot(
      categoryId: widget.categoryId,
      date: _date!,
      startTime: _wireStartTime,
      durationMinutes: _category?.expectedDurationMinutes,
    );
  }

  Future<void> _pickDate() async {
    final now = DateTime.now();
    final picked = await showDatePicker(
      context: context,
      initialDate: _date ?? now.add(const Duration(days: 1)),
      // Today stays selectable even though this screen no longer serves
      // emergencies (those broadcast instead, from the catalogue card): a
      // society may set a short notice window, and the server is the authority
      // on whether today is allowed rather than this picker.
      firstDate: DateTime(now.year, now.month, now.day),
      lastDate: now.add(const Duration(days: 90)),
    );
    if (picked != null) setState(() => _date = picked);
  }

  Future<void> _pickTime() async {
    final picked =
        await showTimePicker(context: context, initialTime: _startTime);
    if (picked != null) setState(() => _startTime = picked);
  }

  @override
  Widget build(BuildContext context) {
    final category = _category;
    final slot = _slot;

    return Scaffold(
      appBar: AppBar(
        titleSpacing: AppSpacing.gutter,
        title: Text(category?.name ?? 'Book a service'),
      ),
      body: ListView(
        padding: const EdgeInsets.only(bottom: AppSpacing.huge),
        children: [
          if (category != null)
            AppFadeIn(child: _CategorySummary(category: category)),
          const AppFadeIn(
            index: 1,
            child: _StepHeader(step: 1, title: 'Choose a date'),
          ),
          AppFadeIn(
            index: 1,
            child: _DateStrip(
              selected: _date,
              onSelect: (date) => setState(() => _date = date),
              onOther: _pickDate,
            ),
          ),
          const AppFadeIn(
            index: 2,
            child: _StepHeader(step: 2, title: 'Choose a start time'),
          ),
          AppFadeIn(
            index: 2,
            child: _TimeStrip(
              selected: _startTime,
              onSelect: (time) => setState(() => _startTime = time),
              onOther: _pickTime,
            ),
          ),
          Padding(
            padding: const EdgeInsets.fromLTRB(
              AppSpacing.gutter,
              AppSpacing.lg,
              AppSpacing.gutter,
              0,
            ),
            child: AppButton(
              label:
                  _searched ? 'Update availability' : 'Find available workers',
              icon: Icons.search_rounded,
              onPressed:
                  _date == null ? null : () => setState(() => _searched = true),
            ),
          ),
          if (_date == null)
            const Padding(
              padding: EdgeInsets.only(top: AppSpacing.xl),
              child: AppEmptyState(
                icon: Icons.event_available_outlined,
                title: 'Pick a date to continue',
                message: 'Workers choose which days they take one-off jobs, so '
                    'availability depends on the date you need.',
              ),
            )
          else if (_searched && slot != null) ...[
            const AppFadeIn(
              index: 3,
              child: _StepHeader(step: 3, title: 'Choose who comes'),
            ),
            _MatchResults(slot: slot, category: category),
          ],
        ],
      ),
    );
  }
}

class _CategorySummary extends StatelessWidget {
  const _CategorySummary({required this.category});

  final ServiceCategory category;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.fromLTRB(
        AppSpacing.gutter,
        AppSpacing.xs,
        AppSpacing.gutter,
        0,
      ),
      child: AppCard(
        color: AppColors.primarySoft,
        borderColor: AppColors.primarySoft,
        shadow: const [],
        child: Row(
          children: [
            const Icon(
              Icons.info_outline_rounded,
              size: AppIconSize.md,
              color: AppColors.primary,
            ),
            const SizedBox(width: AppSpacing.sm),
            Expanded(
              child: Text(
                'About ${category.durationLabel} · ${category.priceGuidance}',
                style: const TextStyle(
                  fontSize: 14,
                  height: 1.4,
                  fontWeight: FontWeight.w600,
                  color: AppColors.primaryDark,
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class _StepHeader extends StatelessWidget {
  const _StepHeader({required this.step, required this.title});

  final int step;
  final String title;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.fromLTRB(
        AppSpacing.gutter,
        AppSpacing.lg,
        AppSpacing.gutter,
        AppSpacing.sm,
      ),
      child: Row(
        children: [
          Container(
            width: 24,
            height: 24,
            alignment: Alignment.center,
            decoration: const BoxDecoration(
              color: AppColors.primary,
              shape: BoxShape.circle,
            ),
            child: Text(
              '$step',
              style: const TextStyle(
                color: AppColors.textOnPrimary,
                fontSize: 12.5,
                fontWeight: FontWeight.w700,
              ),
            ),
          ),
          const SizedBox(width: AppSpacing.xs + 2),
          Text(title, style: Theme.of(context).textTheme.titleMedium),
        ],
      ),
    );
  }
}

/// The next fortnight as tappable day cards.
class _DateStrip extends StatelessWidget {
  const _DateStrip({
    required this.selected,
    required this.onSelect,
    required this.onOther,
  });

  final DateTime? selected;
  final ValueChanged<DateTime> onSelect;
  final VoidCallback onOther;

  static const _weekdays = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
  static const _months = [
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
  ];

  bool _isSameDay(DateTime a, DateTime b) =>
      a.year == b.year && a.month == b.month && a.day == b.day;

  @override
  Widget build(BuildContext context) {
    final now = DateTime.now();
    final today = DateTime(now.year, now.month, now.day);
    final days = List.generate(14, (i) => today.add(Duration(days: i)));

    // A date chosen from the dialog may fall outside the fortnight shown. It
    // still has to appear selected somewhere, so it is appended as its own card.
    final chosen = selected;
    final outsideStrip =
        chosen != null && !days.any((d) => _isSameDay(d, chosen));

    return SizedBox(
      height: 84,
      child: ListView(
        scrollDirection: Axis.horizontal,
        padding: const EdgeInsets.symmetric(horizontal: AppSpacing.gutter),
        children: [
          for (final day in days) ...[
            _DayCard(
              label:
                  _isSameDay(day, today) ? 'Today' : _weekdays[day.weekday - 1],
              day: '${day.day}',
              month: _months[day.month - 1],
              selected: chosen != null && _isSameDay(day, chosen),
              onTap: () => onSelect(day),
            ),
            const SizedBox(width: AppSpacing.xs),
          ],
          if (outsideStrip) ...[
            _DayCard(
              label: _weekdays[chosen.weekday - 1],
              day: '${chosen.day}',
              month: _months[chosen.month - 1],
              selected: true,
              onTap: () {},
            ),
            const SizedBox(width: AppSpacing.xs),
          ],
          _OtherCard(icon: Icons.calendar_month_rounded, onTap: onOther),
        ],
      ),
    );
  }
}

class _DayCard extends StatelessWidget {
  const _DayCard({
    required this.label,
    required this.day,
    required this.month,
    required this.selected,
    required this.onTap,
  });

  final String label;
  final String day;
  final String month;
  final bool selected;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    return Semantics(
      selected: selected,
      button: true,
      child: GestureDetector(
        onTap: onTap,
        behavior: HitTestBehavior.opaque,
        child: AnimatedContainer(
          duration: AppMotion.fast,
          curve: AppMotion.standard,
          width: 64,
          padding: const EdgeInsets.symmetric(vertical: AppSpacing.xs),
          decoration: BoxDecoration(
            color: selected ? AppColors.primary : AppColors.surface,
            borderRadius: AppRadius.button,
            border: Border.all(
              color: selected ? AppColors.primary : AppColors.border,
            ),
          ),
          child: Column(
            mainAxisAlignment: MainAxisAlignment.center,
            children: [
              Text(
                label,
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
                style: TextStyle(
                  fontSize: 11.5,
                  fontWeight: FontWeight.w600,
                  color: selected
                      ? AppColors.textOnPrimary.withValues(alpha: 0.85)
                      : AppColors.textTertiary,
                ),
              ),
              const SizedBox(height: 2),
              Text(
                day,
                style: TextStyle(
                  fontSize: 19,
                  fontWeight: FontWeight.w700,
                  height: 1.1,
                  color: selected
                      ? AppColors.textOnPrimary
                      : AppColors.textPrimary,
                ),
              ),
              Text(
                month,
                style: TextStyle(
                  fontSize: 11,
                  fontWeight: FontWeight.w600,
                  color: selected
                      ? AppColors.textOnPrimary.withValues(alpha: 0.85)
                      : AppColors.textTertiary,
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

/// Working hours as chips, with the dialog behind "Other".
class _TimeStrip extends StatelessWidget {
  const _TimeStrip({
    required this.selected,
    required this.onSelect,
    required this.onOther,
  });

  final TimeOfDay selected;
  final ValueChanged<TimeOfDay> onSelect;
  final VoidCallback onOther;

  /// The hours domestic work actually starts. Deliberately not a full 24 —
  /// offering 3am would be noise on every booking anyone ever makes.
  static const _hours = [7, 8, 9, 10, 11, 12, 14, 15, 16, 17, 18];

  @override
  Widget build(BuildContext context) {
    final custom = !_hours.contains(selected.hour) || selected.minute != 0;

    return SizedBox(
      height: 52,
      child: ListView(
        scrollDirection: Axis.horizontal,
        padding: const EdgeInsets.symmetric(horizontal: AppSpacing.gutter),
        children: [
          for (final hour in _hours) ...[
            AppFilterChip(
              label: TimeOfDay(hour: hour, minute: 0).format(context),
              selected: !custom && selected.hour == hour,
              onTap: () => onSelect(TimeOfDay(hour: hour, minute: 0)),
            ),
            const SizedBox(width: AppSpacing.xs),
          ],
          if (custom) ...[
            AppFilterChip(
              label: selected.format(context),
              selected: true,
              onTap: onOther,
            ),
            const SizedBox(width: AppSpacing.xs),
          ],
          _OtherCard(icon: Icons.schedule_rounded, onTap: onOther),
        ],
      ),
    );
  }
}

/// The escape hatch to the full Material picker, at the end of either strip.
class _OtherCard extends StatelessWidget {
  const _OtherCard({required this.icon, required this.onTap});

  final IconData icon;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    return GestureDetector(
      onTap: onTap,
      behavior: HitTestBehavior.opaque,
      child: Container(
        width: 64,
        padding: const EdgeInsets.symmetric(vertical: AppSpacing.xs),
        decoration: BoxDecoration(
          color: AppColors.surface,
          borderRadius: AppRadius.button,
          border: Border.all(color: AppColors.borderStrong),
        ),
        child: Column(
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            Icon(icon, size: AppIconSize.md, color: AppColors.textSecondary),
            const SizedBox(height: 2),
            const Text(
              'Other',
              style: TextStyle(
                fontSize: 11,
                fontWeight: FontWeight.w600,
                color: AppColors.textSecondary,
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class _MatchResults extends ConsumerWidget {
  const _MatchResults({required this.slot, required this.category});

  final BookingSlot slot;
  final ServiceCategory? category;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final matches = ref.watch(matchedWorkersProvider(slot));

    return AppSwitcher(
      child: matches.when(
        loading: () => const Padding(
          padding: EdgeInsets.symmetric(horizontal: AppSpacing.gutter),
          child: Column(
            children: [AppSkeletonCard(), AppSkeletonCard(), AppSkeletonCard()],
          ),
        ),
        error: (error, _) => AppErrorState(
          message: error is ApiException
              ? error.message
              : 'Could not check availability.',
          onRetry: () => ref.invalidate(matchedWorkersProvider(slot)),
        ),
        data: (workers) {
          if (workers.isEmpty) {
            return const AppEmptyState(
              icon: Icons.person_off_outlined,
              title: 'Nobody is free then',
              message: 'Workers choose which days they take one-off jobs. '
                  'Try another date or time.',
            );
          }
          return Padding(
            padding: const EdgeInsets.symmetric(horizontal: AppSpacing.gutter),
            child: Column(
              children: [
                for (var i = 0; i < workers.length; i++)
                  AppFadeIn(
                    index: i,
                    child: _MatchedWorkerCard(
                      worker: workers[i],
                      slot: slot,
                      category: category,
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

class _MatchedWorkerCard extends ConsumerStatefulWidget {
  const _MatchedWorkerCard({
    required this.worker,
    required this.slot,
    required this.category,
  });

  final WorkerSearchResult worker;
  final BookingSlot slot;
  final ServiceCategory? category;

  @override
  ConsumerState<_MatchedWorkerCard> createState() => _MatchedWorkerCardState();
}

class _MatchedWorkerCardState extends ConsumerState<_MatchedWorkerCard> {
  bool _isBusy = false;

  Future<void> _book() async {
    // Captured before the sheet opens: reading it afterwards would be a
    // BuildContext use across an async gap, and this widget can legitimately
    // be disposed while the sheet is up.
    final messenger = ScaffoldMessenger.of(context);

    final confirmed = await showModalBottomSheet<_BookingTerms>(
      context: context,
      isScrollControlled: true,
      builder: (_) => _ConfirmBookingSheet(
        worker: widget.worker,
        category: widget.category,
        slot: widget.slot,
      ),
    );
    if (confirmed == null) return;

    setState(() => _isBusy = true);
    try {
      await ref.read(bookingRepositoryProvider).createBooking(
            workerId: widget.worker.id,
            categoryId: widget.slot.categoryId,
            slot: widget.slot,
            quotedPrice: confirmed.price,
            notes: confirmed.notes,
          );
      if (!mounted) return;
      invalidateBookings(ref);
      // The worker is now taken for this slot, so the list they came from is
      // stale the moment the booking lands.
      ref.invalidate(matchedWorkersProvider(widget.slot));
      showAppSnackBarOn(
        messenger,
        'Requested ${widget.worker.fullName}. They will confirm shortly.',
        tone: AppTone.success,
      );
    } on ApiException catch (error) {
      if (!mounted) return;
      setState(() => _isBusy = false);
      showAppSnackBarOn(messenger, error.message, tone: AppTone.danger);
    }
  }

  @override
  Widget build(BuildContext context) {
    final worker = widget.worker;
    final theme = Theme.of(context);

    return AppCard(
      margin: const EdgeInsets.only(bottom: AppSpacing.sm),
      child: Row(
        children: [
          AppAvatar(
            name: worker.fullName,
            imageUrl: worker.photoUrl,
            seed: worker.id,
            size: 52,
          ),
          const SizedBox(width: AppSpacing.sm),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(
                  children: [
                    Expanded(
                      child: Text(
                        worker.fullName,
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                        style: theme.textTheme.titleSmall,
                      ),
                    ),
                    if (worker.matchPercentage != null)
                      MatchBadge(percentage: worker.matchPercentage!),
                  ],
                ),
                const SizedBox(height: 2),
                Text(
                  worker.hasRating
                      ? '${worker.averageRating.toStringAsFixed(1)} ★ · '
                          '${worker.completedEngagements} jobs'
                      : 'New to Sathify',
                  style: theme.textTheme.bodySmall,
                ),
              ],
            ),
          ),
          const SizedBox(width: AppSpacing.xs),
          SizedBox(
            width: 84,
            child: AppButton(
              label: 'Book',
              isLoading: _isBusy,
              onPressed: _book,
            ),
          ),
        ],
      ),
    );
  }
}

/// What the resident agreed to in the confirmation sheet.
class _BookingTerms {
  const _BookingTerms({required this.price, required this.notes});

  final int price;
  final String notes;
}

class _ConfirmBookingSheet extends StatefulWidget {
  const _ConfirmBookingSheet({
    required this.worker,
    required this.category,
    required this.slot,
  });

  final WorkerSearchResult worker;
  final ServiceCategory? category;
  final BookingSlot slot;

  @override
  State<_ConfirmBookingSheet> createState() => _ConfirmBookingSheetState();
}

class _ConfirmBookingSheetState extends State<_ConfirmBookingSheet> {
  final _formKey = GlobalKey<FormState>();
  final _priceController = TextEditingController();
  final _notesController = TextEditingController();

  @override
  void initState() {
    super.initState();
    // Prefill with the catalogue's floor, which is what the server would use
    // anyway if the field were left out — so the number shown is the number
    // that would be charged.
    final suggested = widget.category?.priceMin;
    if (suggested != null) _priceController.text = '$suggested';
  }

  @override
  void dispose() {
    _priceController.dispose();
    _notesController.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final slot = widget.slot;

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
                    imageUrl: widget.worker.photoUrl,
                    seed: widget.worker.id,
                    size: 44,
                  ),
                  const SizedBox(width: AppSpacing.sm),
                  Expanded(
                    child: Text(
                      'Book ${widget.worker.fullName}',
                      style: theme.textTheme.titleLarge,
                    ),
                  ),
                ],
              ),
              const SizedBox(height: AppSpacing.md),

              // The whole agreement in one block, so the resident confirms
              // against what they picked rather than from memory.
              Container(
                width: double.infinity,
                padding: const EdgeInsets.all(AppSpacing.sm),
                decoration: const BoxDecoration(
                  color: AppColors.surfaceMuted,
                  borderRadius: AppRadius.card,
                ),
                child: Column(
                  children: [
                    _SummaryRow(
                      icon: Icons.home_repair_service_outlined,
                      label: widget.category?.name ?? 'Service',
                    ),
                    _SummaryRow(
                      icon: Icons.event_outlined,
                      label:
                          '${slot.date.day}/${slot.date.month}/${slot.date.year}'
                          ' at ${slot.startTime}',
                    ),
                    if (widget.category != null)
                      _SummaryRow(
                        icon: Icons.schedule_outlined,
                        label: 'About ${widget.category!.durationLabel}',
                      ),
                  ],
                ),
              ),
              const SizedBox(height: AppSpacing.lg),

              Text('Agreed price', style: theme.textTheme.titleSmall),
              const SizedBox(height: AppSpacing.xs),
              TextFormField(
                controller: _priceController,
                keyboardType: TextInputType.number,
                decoration: InputDecoration(
                  prefixIcon: const Icon(Icons.currency_rupee),
                  helperText: widget.category == null
                      ? null
                      : 'Guide: ${widget.category!.priceGuidance}',
                ),
                validator: (value) {
                  final amount = int.tryParse((value ?? '').trim());
                  if (amount == null || amount <= 0) {
                    return 'Enter the agreed price.';
                  }
                  return null;
                },
              ),
              const SizedBox(height: AppSpacing.sm),
              TextFormField(
                controller: _notesController,
                maxLines: 3,
                maxLength: 500,
                decoration: const InputDecoration(
                  labelText: 'Notes (optional)',
                  hintText: 'Anything they should know before arriving',
                ),
              ),
              const SizedBox(height: AppSpacing.xxs),
              AppButton(
                label: 'Send booking request',
                icon: Icons.check_rounded,
                onPressed: () {
                  if (!_formKey.currentState!.validate()) return;
                  Navigator.of(context).pop(
                    _BookingTerms(
                      price: int.parse(_priceController.text.trim()),
                      notes: _notesController.text.trim(),
                    ),
                  );
                },
              ),
              const SizedBox(height: AppSpacing.xxs),
              AppButton.text(
                label: 'Cancel',
                expand: true,
                onPressed: () => Navigator.of(context).pop(),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

class _SummaryRow extends StatelessWidget {
  const _SummaryRow({required this.icon, required this.label});

  final IconData icon;
  final String label;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: AppSpacing.xxs),
      child: Row(
        children: [
          Icon(icon, size: AppIconSize.sm, color: AppColors.textSecondary),
          const SizedBox(width: AppSpacing.xs + 2),
          Expanded(
            child: Text(
              label,
              style: const TextStyle(
                fontSize: 14,
                fontWeight: FontWeight.w500,
                color: AppColors.textPrimary,
              ),
            ),
          ),
        ],
      ),
    );
  }
}
