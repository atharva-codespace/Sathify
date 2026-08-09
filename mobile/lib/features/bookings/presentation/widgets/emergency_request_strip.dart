import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../../../core/errors/api_exception.dart';
import '../../../../core/routing/app_router.dart';
import '../../../../shared/design_system.dart';
import '../../data/models/booking_models.dart';
import '../providers/booking_provider.dart';

/// Module 5.5 — the household's own emergency request, while it is live.
///
/// Answers one question at a time, in the order a waiting person asks them:
/// has it gone out, is anybody taking it, and who is coming. It disappears once
/// the job is under way, because from then on it is an ordinary booking and the
/// schedule card below already says everything.
class EmergencyRequestStrip extends ConsumerWidget {
  const EmergencyRequestStrip({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final requests = ref
        .watch(myEmergencyRequestsProvider)
        .where((booking) => booking.isSeekingWorker || _justClaimed(booking))
        .toList();

    if (requests.isEmpty) return const SizedBox.shrink();

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        for (final request in requests)
          _RequestCard(key: ValueKey(request.id), booking: request),
        const SizedBox(height: AppSpacing.sm),
      ],
    );
  }

  /// Confirmed, but the household may not have seen who took it yet.
  ///
  /// Kept on screen through the moment of the claim on purpose: "Sunita is on
  /// her way" is the single most valuable thing this strip ever says, and a
  /// filter that only matched *unclaimed* requests would drop the card at
  /// exactly the instant it had something worth showing.
  bool _justClaimed(Booking booking) =>
      booking.isEmergency &&
      booking.status == BookingStatus.confirmed &&
      !booking.canMarkDone;
}

class _RequestCard extends ConsumerStatefulWidget {
  const _RequestCard({required this.booking, super.key});

  final Booking booking;

  @override
  ConsumerState<_RequestCard> createState() => _RequestCardState();
}

class _RequestCardState extends ConsumerState<_RequestCard> {
  bool _isBusy = false;

  Future<void> _cancel() async {
    final messenger = ScaffoldMessenger.of(context);
    setState(() => _isBusy = true);
    try {
      // Nobody has accepted, so the fee is zero and the surcharge comes back —
      // the server decides both, and passing the acknowledged figure keeps the
      // same "never charged more than you were shown" guarantee the ordinary
      // cancel flow has.
      await ref.read(bookingRepositoryProvider).cancelBooking(
            widget.booking.id,
            acknowledgedFee: 0,
            reason: 'No longer needed',
          );
      if (!mounted) return;
      invalidateBookings(ref);
      showAppSnackBarOn(
        messenger,
        'Request cancelled. Your emergency fee has been refunded.',
        tone: AppTone.success,
      );
    } on ApiException catch (error) {
      if (!mounted) return;
      setState(() => _isBusy = false);
      showAppSnackBarOn(messenger, error.message, tone: AppTone.danger);
    }
  }

  Future<void> _pay() async {
    // The surcharge is opened but unsettled. The payments screen already owns
    // the tested Razorpay flow, so this hands off rather than duplicating it.
    await GoRouter.of(context).push(Routes.payments);
    if (mounted) invalidateBookings(ref);
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final booking = widget.booking;

    final (icon, tone, headline, detail) = switch (booking.status) {
      BookingStatus.paymentPending => (
          Icons.lock_clock_outlined,
          AppTone.warning,
          'Pay the emergency fee to send this out',
          'Nobody is contacted until this is paid.',
        ),
      BookingStatus.broadcast => (
          Icons.podcasts_rounded,
          AppTone.info,
          'Finding someone now',
          'Sent to everyone free nearby. The first to accept gets it.',
        ),
      _ => (
          Icons.directions_walk_rounded,
          AppTone.success,
          '${booking.workerName} is on the way',
          '₹${booking.quotedPrice} payable in the app once the job is done.',
        ),
    };

    return AppCard(
      margin: const EdgeInsets.only(bottom: AppSpacing.sm),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Icon(icon, size: AppIconSize.md, color: _colourFor(tone)),
              const SizedBox(width: AppSpacing.sm),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(headline, style: theme.textTheme.titleSmall),
                    const SizedBox(height: AppSpacing.xxs),
                    Text(detail, style: theme.textTheme.bodySmall),
                  ],
                ),
              ),
            ],
          ),
          if (booking.status == BookingStatus.paymentPending) ...[
            const SizedBox(height: AppSpacing.sm),
            AppButton(
              label: 'Pay ₹${booking.emergencySurchargePaise ~/ 100} fee',
              icon: Icons.payments_outlined,
              isLoading: _isBusy,
              onPressed: _pay,
            ),
          ] else if (booking.status == BookingStatus.broadcast) ...[
            const SizedBox(height: AppSpacing.sm),
            AppButton.secondary(
              label: 'Cancel request',
              icon: Icons.close_rounded,
              isLoading: _isBusy,
              onPressed: _cancel,
            ),
          ],
        ],
      ),
    );
  }

  Color _colourFor(AppTone tone) => switch (tone) {
        AppTone.success => AppColors.success,
        AppTone.warning => AppColors.warning,
        AppTone.danger => AppColors.danger,
        _ => AppColors.info,
      };
}
