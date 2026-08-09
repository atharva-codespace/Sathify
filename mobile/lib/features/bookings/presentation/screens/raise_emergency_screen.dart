import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../../../core/errors/api_exception.dart';
import '../../../../shared/design_system.dart';
import '../../../payments/presentation/providers/payment_provider.dart';
import '../../../payments/presentation/widgets/pay_sheet.dart';
import '../../data/models/booking_models.dart';
import '../providers/booking_provider.dart';
import '../widgets/category_icon.dart';

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
/// The category is normally already decided: this screen is reached by tapping
/// an emergency card in the service catalogue, so it arrives with
/// [categoryId] and has one button. Arriving without one — from a notification,
/// say — falls back to asking, rather than guessing on the resident's behalf.
///
/// -----------------------------------------------------------------------
/// THE TWO PAYMENTS ARE SAID OUT LOUD, ON THIS SCREEN
/// -----------------------------------------------------------------------
/// The household is about to be charged one amount by Sathify now, and a
/// different amount for the work itself once it is done. Letting somebody
/// discover the second one afterwards would be the single most damaging thing
/// this flow could do, so both are stated here, before the first is collected.
class RaiseEmergencyScreen extends ConsumerStatefulWidget {
  const RaiseEmergencyScreen({this.categoryId, super.key});

  /// Which emergency service was tapped. Null when the screen was reached
  /// without one.
  final int? categoryId;

  @override
  ConsumerState<RaiseEmergencyScreen> createState() =>
      _RaiseEmergencyScreenState();
}

class _RaiseEmergencyScreenState extends ConsumerState<RaiseEmergencyScreen> {
  final _notes = TextEditingController();
  bool _isBusy = false;

  @override
  void dispose() {
    _notes.dispose();
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
      // Fetched back so the sheet can show the amount and open a UPI intent
      // against a real ledger row — the raise response carries only the id.
      final payment =
          await ref.read(paymentRepositoryProvider).fetchPayment(raised.paymentId);

      if (!mounted) return;
      final outcome = await showPaySheet(context, payment);
      if (!mounted) return;

      setState(() => _isBusy = false);
      invalidateBookings(ref);
      invalidatePayments(ref);

      switch (outcome) {
        case PayOutcome.paid:
          showAppSnackBarOn(
            messenger,
            'Sent to everyone free nearby. We will tell you who accepts.',
            tone: AppTone.success,
          );
          router.pop();
        case PayOutcome.pendingUpi:
          // The broadcast is triggered by the payment settling, and a UPI
          // transfer settles when the webhook says so rather than when the
          // sheet closes. So this promises nothing about workers having been
          // contacted — the request card on the schedule reports that when it
          // is true.
          showAppSnackBarOn(
            messenger,
            'Finish in your UPI app. We will send the request the moment it '
            'clears.',
            tone: AppTone.info,
          );
          router.pop();
        case PayOutcome.failed:
        case PayOutcome.cancelled:
        case null:
          showAppSnackBarOn(
            messenger,
            'Request saved. Pay the fee to send it out.',
            tone: AppTone.info,
          );
      }
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
            final urgent = items.where((c) => c.bypassesNoticePeriod).toList();
            if (urgent.isEmpty) {
              return const AppEmptyState(
                icon: Icons.emergency_outlined,
                title: 'No emergency service configured',
                message: 'Your society administrator sets this up.',
              );
            }

            // Narrowed to the card that was tapped, when there was one. A
            // resident who has already chosen "Emergency household assistance"
            // in the catalogue should not be asked to choose it again.
            final chosen = widget.categoryId == null
                ? null
                : urgent.where((c) => c.id == widget.categoryId).firstOrNull;
            final offered = chosen == null ? urgent : [chosen];

            return ListView(
              padding: const EdgeInsets.fromLTRB(
                AppSpacing.gutter,
                AppSpacing.md,
                AppSpacing.gutter,
                AppSpacing.xxl,
              ),
              children: [
                if (chosen != null) ...[
                  _ChosenService(category: chosen),
                  const SizedBox(height: AppSpacing.md),
                ],
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
                for (final category in offered) ...[
                  AppButton(
                    label: _isBusy
                        ? 'Sending…'
                        // One button, one verb, when the service is already
                        // settled. The long "Send <service name> request" only
                        // earns its length when there is more than one to tell
                        // apart.
                        : offered.length == 1
                            ? 'Send request now'
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

/// Confirms which service is about to be sent, when it came from the catalogue.
///
/// Small, and worth the space: the resident tapped a card and then landed on a
/// screen with a completely different shape from every other category's, so it
/// should say plainly that it is still the thing they tapped.
class _ChosenService extends StatelessWidget {
  const _ChosenService({required this.category});

  final ServiceCategory category;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);

    return AppCard(
      child: Row(
        children: [
          Container(
            width: 44,
            height: 44,
            decoration: const BoxDecoration(
              color: AppColors.dangerSoft,
              shape: BoxShape.circle,
            ),
            child: Icon(
              iconForCategory(category.icon),
              size: AppIconSize.md,
              color: AppColors.danger,
            ),
          ),
          const SizedBox(width: AppSpacing.sm),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(category.name, style: theme.textTheme.titleSmall),
                const SizedBox(height: 2),
                Text(
                  'About ${category.durationLabel} · '
                  '${category.priceGuidance.isNotEmpty ? category.priceGuidance : "₹${category.priceMin}"} for the work',
                  style: theme.textTheme.bodySmall,
                ),
              ],
            ),
          ),
        ],
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
            title: 'The work itself is paid after it is done',
            // The sentence this whole screen exists to make unmissable: there
            // is a second, larger amount, and it is not what is being collected
            // right now.
            body: 'You are asked for their charge in the app once they mark the '
                'job complete — not now.',
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
