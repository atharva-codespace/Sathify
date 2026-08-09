import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../../../core/errors/api_exception.dart';
import '../../../../shared/design_system.dart';
import '../../../payments/data/razorpay_checkout.dart';
import '../../../payments/presentation/providers/payment_provider.dart';
import '../../data/models/booking_models.dart';
import '../providers/booking_provider.dart';

/// Module 5.5 — the resident raises an emergency.
///
/// -----------------------------------------------------------------------
/// ONE SCREEN, AND ALMOST NOTHING TO FILL IN
/// -----------------------------------------------------------------------
/// The ordinary booking flow is three screens: pick a category, pick a slot,
/// pick a worker. That is the right shape when there is time to choose. This is
/// the shape for somebody holding a phone in one hand with water coming through
/// a ceiling: no worker to choose (that is the point of a broadcast), no time to
/// choose (it means now), and one thing to confirm — the fee.
///
/// -----------------------------------------------------------------------
/// THE TWO PAYMENTS ARE SAID OUT LOUD, ON THIS SCREEN
/// -----------------------------------------------------------------------
/// The household is about to be charged one amount by Sathify and a different
/// amount, later, in cash, by the worker. Letting somebody discover the second
/// one at the door would be the single most damaging thing this flow could do,
/// so both are stated here, before the first is collected.
class RaiseEmergencyScreen extends ConsumerStatefulWidget {
  const RaiseEmergencyScreen({super.key});

  @override
  ConsumerState<RaiseEmergencyScreen> createState() =>
      _RaiseEmergencyScreenState();
}

class _RaiseEmergencyScreenState extends ConsumerState<RaiseEmergencyScreen> {
  final _notes = TextEditingController();
  final _checkout = RazorpayCheckout();
  bool _isBusy = false;

  @override
  void dispose() {
    _notes.dispose();
    _checkout.dispose();
    super.dispose();
  }

  /// Raise the request, then take the fee, then let the server broadcast.
  ///
  /// The order matters and is enforced server-side: the request is created in
  /// `payment_pending` and there is no route out of that state except a settled
  /// surcharge. So a failure here — a dismissed checkout sheet, a dead network —
  /// leaves a request nobody has been bothered about, which the resident can
  /// finish paying for later or simply abandon.
  Future<void> _raise(ServiceCategory category) async {
    final messenger = ScaffoldMessenger.of(context);
    final router = GoRouter.of(context);
    setState(() => _isBusy = true);

    try {
      final raised = await ref.read(bookingRepositoryProvider).raiseEmergency(
            categoryId: category.id,
            notes: _notes.text.trim(),
          );

      final payments = ref.read(paymentRepositoryProvider);
      final payload = await payments.openCheckout(raised.paymentId);
      final outcome = await _checkout.open(payload);

      if (!mounted) return;

      if (outcome.cancelled) {
        setState(() => _isBusy = false);
        invalidateBookings(ref);
        showAppSnackBarOn(
          messenger,
          'Request saved. Pay the fee to send it out.',
          tone: AppTone.info,
        );
        return;
      }
      if (!outcome.succeeded) {
        setState(() => _isBusy = false);
        invalidateBookings(ref);
        showAppSnackBarOn(messenger, outcome.message, tone: AppTone.danger);
        return;
      }

      // The signature is what settles it, and settling is what broadcasts.
      await payments.confirmCheckout(
        raised.paymentId,
        razorpayPaymentId: outcome.razorpayPaymentId,
        signature: outcome.signature,
      );

      if (!mounted) return;
      invalidateBookings(ref);
      invalidatePayments(ref);
      showAppSnackBarOn(
        messenger,
        'Sent to everyone free nearby. We will tell you who accepts.',
        tone: AppTone.success,
      );
      router.pop();
    } on ApiException catch (error) {
      if (!mounted) return;
      setState(() => _isBusy = false);
      showAppSnackBarOn(messenger, error.message, tone: AppTone.danger);
    }
  }

  @override
  Widget build(BuildContext context) {
    final categories = ref.watch(serviceCategoriesProvider);
    final quote = ref.watch(surchargeQuoteProvider);

    return Scaffold(
      appBar: AppBar(
        titleSpacing: AppSpacing.gutter,
        title: const Text('Get help now'),
      ),
      body: AppSwitcher(
        child: categories.when(
          loading: () => const AppSkeletonList(count: 3),
          error: (error, _) => AppErrorState(
            message: error is ApiException
                ? error.message
                : 'Could not load emergency services.',
            onRetry: () => ref.invalidate(serviceCategoriesProvider),
          ),
          data: (items) {
            final urgent =
                items.where((c) => c.bypassesNoticePeriod).toList();
            if (urgent.isEmpty) {
              return const AppEmptyState(
                icon: Icons.emergency_outlined,
                title: 'No emergency service configured',
                message: 'Your society administrator sets this up.',
              );
            }

            return ListView(
              padding: const EdgeInsets.fromLTRB(
                AppSpacing.gutter,
                AppSpacing.md,
                AppSpacing.gutter,
                AppSpacing.xxl,
              ),
              children: [
                _HowItWorks(
                  quote: quote.maybeWhen(
                    data: (value) => value,
                    orElse: () => null,
                  ),
                ),
                const SizedBox(height: AppSpacing.md),
                TextField(
                  controller: _notes,
                  maxLines: 3,
                  maxLength: 300,
                  decoration: const InputDecoration(
                    hintText: 'What has happened? (optional)',
                  ),
                ),
                const SizedBox(height: AppSpacing.sm),
                for (final category in urgent) ...[
                  AppButton(
                    label: _isBusy
                        ? 'Sending…'
                        : 'Send ${category.name.toLowerCase()} request',
                    icon: Icons.bolt_rounded,
                    isLoading: _isBusy,
                    onPressed: () => _raise(category),
                  ),
                  const SizedBox(height: AppSpacing.sm),
                ],
              ],
            );
          },
        ),
      ),
    );
  }
}

/// The three facts somebody needs before they press the button.
class _HowItWorks extends StatelessWidget {
  const _HowItWorks({required this.quote});

  final SurchargeQuote? quote;

  @override
  Widget build(BuildContext context) {
    final fee = quote?.rupees;

    return AppCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const _Step(
            icon: Icons.podcasts_rounded,
            title: 'Everyone free nearby gets it at once',
            body: 'You do not pick anybody. The first to accept comes.',
          ),
          const SizedBox(height: AppSpacing.sm),
          _Step(
            icon: Icons.credit_card,
            title: fee == null
                ? 'A small Sathify fee, paid now'
                : 'A ₹$fee Sathify fee, paid now',
            body: quote?.rationale ??
                'This covers finding somebody at short notice.',
          ),
          const SizedBox(height: AppSpacing.sm),
          const _Step(
            icon: Icons.payments_outlined,
            title: 'The worker is paid separately, in cash',
            // The sentence this whole screen exists to make unmissable.
            body: 'Their charge is agreed with you and paid directly to them '
                'when the job is done. Sathify does not collect it.',
          ),
          const SizedBox(height: AppSpacing.sm),
          const _Step(
            icon: Icons.replay_rounded,
            title: 'If nobody accepts, the fee comes back',
            body: 'You are told within a few minutes and refunded in full.',
          ),
        ],
      ),
    );
  }
}

class _Step extends StatelessWidget {
  const _Step({required this.icon, required this.title, required this.body});

  final IconData icon;
  final String title;
  final String body;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Row(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Icon(icon, size: AppIconSize.sm, color: AppColors.primary),
        const SizedBox(width: AppSpacing.sm),
        Expanded(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(title, style: theme.textTheme.titleSmall),
              const SizedBox(height: 2),
              Text(body, style: theme.textTheme.bodySmall),
            ],
          ),
        ),
      ],
    );
  }
}
