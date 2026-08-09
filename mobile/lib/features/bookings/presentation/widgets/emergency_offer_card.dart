import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../../core/errors/api_exception.dart';
import '../../../../shared/design_system.dart';
import '../../data/models/booking_models.dart';
import '../providers/booking_provider.dart';
import 'category_icon.dart';

/// Module 5.5 — an incoming emergency request, on the worker's dashboard.
///
/// Built for one situation: she has a few minutes, several other people have
/// the same card on their phone, and the first to tap Accept gets the job. So
/// the card leads with the two things that decide whether she wants it — what
/// it pays and where it is — and puts Accept where her thumb already is.
///
/// Losing the race is treated as ordinary, not as an error. It is the expected
/// outcome for most workers on most broadcasts, and a red failure snackbar for
/// the normal case would make the feature feel broken every time it worked.
class EmergencyOfferCard extends ConsumerStatefulWidget {
  const EmergencyOfferCard({required this.offer, super.key});

  final EmergencyOffer offer;

  @override
  ConsumerState<EmergencyOfferCard> createState() => _EmergencyOfferCardState();
}

class _EmergencyOfferCardState extends ConsumerState<EmergencyOfferCard> {
  bool _isBusy = false;
  late int _secondsLeft = widget.offer.secondsLeft;
  Timer? _tick;

  @override
  void initState() {
    super.initState();
    _startCountdown();
  }

  @override
  void didUpdateWidget(EmergencyOfferCard oldWidget) {
    super.didUpdateWidget(oldWidget);
    // Each poll brings a fresh server-side figure. Adopting it rather than
    // letting the local timer drift means the countdown stays honest on a phone
    // whose clock is wrong, which is common on the devices this is built for.
    if (widget.offer.secondsLeft != oldWidget.offer.secondsLeft) {
      setState(() => _secondsLeft = widget.offer.secondsLeft);
    }
  }

  @override
  void dispose() {
    _tick?.cancel();
    super.dispose();
  }

  void _startCountdown() {
    _tick = Timer.periodic(const Duration(seconds: 1), (_) {
      if (!mounted) return;
      if (_secondsLeft <= 0) return;
      setState(() => _secondsLeft -= 1);
    });
  }

  String get _countdownLabel {
    if (_secondsLeft <= 0) return 'Closing';
    final minutes = _secondsLeft ~/ 60;
    final seconds = _secondsLeft % 60;
    return minutes > 0
        ? '$minutes min ${seconds.toString().padLeft(2, '0')}s left'
        : '${seconds}s left';
  }

  Future<void> _accept() async {
    final messenger = ScaffoldMessenger.of(context);
    setState(() => _isBusy = true);

    try {
      await ref
          .read(bookingRepositoryProvider)
          .acceptEmergency(widget.offer.bookingId);
      if (!mounted) return;
      invalidateBookings(ref);
      showAppSnackBarOn(
        messenger,
        'This job is yours. The household has been told.',
        tone: AppTone.success,
      );
    } on ApiException catch (error) {
      if (!mounted) return;
      setState(() => _isBusy = false);
      // Losing is not a failure. Somebody else was a second quicker, which is
      // how the flow is meant to work, and it deserves a neutral tone rather
      // than the red one used for things that actually went wrong.
      final lost = error.code == 'offer_gone';
      invalidateBookings(ref);
      showAppSnackBarOn(
        messenger,
        lost ? 'Someone else took this one.' : error.message,
        tone: lost ? AppTone.info : AppTone.danger,
      );
    }
  }

  Future<void> _decline() async {
    final messenger = ScaffoldMessenger.of(context);
    setState(() => _isBusy = true);
    try {
      await ref
          .read(bookingRepositoryProvider)
          .declineEmergency(widget.offer.bookingId);
      if (!mounted) return;
      invalidateBookings(ref);
    } on ApiException catch (error) {
      if (!mounted) return;
      setState(() => _isBusy = false);
      showAppSnackBarOn(messenger, error.message, tone: AppTone.danger);
    }
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final offer = widget.offer;

    return AppCard(
      margin: const EdgeInsets.only(bottom: AppSpacing.sm),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Container(
                width: 44,
                height: 44,
                decoration: const BoxDecoration(
                  color: AppColors.dangerSoft,
                  shape: BoxShape.circle,
                ),
                child: Icon(
                  iconForCategory(offer.categoryIcon),
                  size: AppIconSize.md,
                  color: AppColors.danger,
                ),
              ),
              const SizedBox(width: AppSpacing.sm),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(offer.categoryName, style: theme.textTheme.titleSmall),
                    Text(
                      offer.flatLabel.isEmpty ? 'Nearby' : offer.flatLabel,
                      style: theme.textTheme.bodySmall,
                    ),
                  ],
                ),
              ),
              AppStatusChip(
                label: _countdownLabel,
                tone: _secondsLeft <= 60 ? AppTone.danger : AppTone.warning,
                dense: true,
              ),
            ],
          ),
          const SizedBox(height: AppSpacing.sm),
          Row(
            children: [
              // The pay is the number she is deciding on, so it is the largest
              // thing on the card.
              Text(
                '₹${offer.quotedPrice}',
                style: const TextStyle(
                  fontSize: 22,
                  fontWeight: FontWeight.w700,
                  color: AppColors.textPrimary,
                ),
              ),
              const SizedBox(width: AppSpacing.xs),
              Text(
                'cash · ${offer.durationMinutes} min · from '
                '${offer.startTimeLabel}',
                style: theme.textTheme.bodySmall,
              ),
            ],
          ),
          if (offer.notes.isNotEmpty) ...[
            const SizedBox(height: AppSpacing.xs),
            Container(
              width: double.infinity,
              padding: const EdgeInsets.all(AppSpacing.xs + 2),
              decoration: const BoxDecoration(
                color: AppColors.surfaceMuted,
                borderRadius: AppRadius.chip,
              ),
              child: Text(offer.notes, style: theme.textTheme.bodySmall),
            ),
          ],
          const SizedBox(height: AppSpacing.sm),
          Row(
            children: [
              Expanded(
                child: AppButton.secondary(
                  label: 'Pass',
                  onPressed: _isBusy ? null : _decline,
                ),
              ),
              const SizedBox(width: AppSpacing.sm),
              Expanded(
                flex: 2,
                child: AppButton(
                  label: 'Accept',
                  icon: Icons.bolt_rounded,
                  isLoading: _isBusy,
                  onPressed: _accept,
                ),
              ),
            ],
          ),
        ],
      ),
    );
  }
}

/// The strip of incoming requests above the worker's day.
///
/// Renders nothing at all when there is nothing to answer, which is almost
/// always. A permanently visible empty "emergencies" section would train people
/// to stop looking at exactly the part of the screen that occasionally matters
/// most.
class EmergencyOfferStrip extends ConsumerWidget {
  const EmergencyOfferStrip({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final offers = ref.watch(myEmergencyOffersProvider);
    if (offers.isEmpty) return const SizedBox.shrink();

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Padding(
          padding: const EdgeInsets.only(bottom: AppSpacing.xs),
          child: Row(
            children: [
              const Icon(
                Icons.bolt_rounded,
                size: AppIconSize.sm,
                color: AppColors.danger,
              ),
              const SizedBox(width: AppSpacing.xxs),
              Text(
                offers.length == 1
                    ? 'Urgent job available'
                    : '${offers.length} urgent jobs available',
                style: Theme.of(context).textTheme.titleSmall,
              ),
            ],
          ),
        ),
        for (final offer in offers)
          EmergencyOfferCard(key: ValueKey(offer.bookingId), offer: offer),
        const SizedBox(height: AppSpacing.sm),
      ],
    );
  }
}
