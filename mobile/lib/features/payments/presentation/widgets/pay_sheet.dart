import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:qr_flutter/qr_flutter.dart';

import '../../../../core/errors/api_exception.dart';
import '../../../../shared/design_system.dart';
import '../../data/models/payment_models.dart';
import '../../data/razorpay_checkout.dart';
import '../providers/payment_provider.dart';

/// How a payment was settled, or why it was not.
enum PayOutcome { paid, cancelled, failed, pendingUpi }

/// Modules 8.1 and 8.9 — one place a resident pays, whatever they are paying for.
///
/// -----------------------------------------------------------------------
/// WHY A SHEET AND NOT A BUTTON ON EACH SCREEN
/// -----------------------------------------------------------------------
/// Three screens collect money — the ledger, the emergency surcharge, and the
/// final settlement when notice is given — and each had, or would have needed,
/// its own copy of "open the order, run checkout, hand back the signature".
/// Adding a payment method to three copies is how one of them ends up missing
/// it. So the methods live here and the screens call [showPaySheet].
///
/// -----------------------------------------------------------------------
/// BOTH METHODS NOW SETTLE THEMSELVES — BUT NOT AT THE SAME MOMENT
/// -----------------------------------------------------------------------
/// * **Pay on this phone** runs Razorpay Checkout, which returns a signed
///   response the server verifies. Confirmed by the time the sheet closes, so
///   this reports [PayOutcome.paid].
/// * **Scan the QR** is a Razorpay-hosted UPI code. The money reaches the
///   gateway and a signed `qr_code.credited` webhook settles the payment — but
///   that happens on the server, moments later and out of this screen's sight.
///   So it reports [PayOutcome.pendingUpi], never `paid`: the app has no way to
///   know, and claiming otherwise would let anybody mark their own payment
///   settled by closing a sheet.
///
/// The QR used to point at a bare VPA, which produced no callback at all and
/// needed an administrator to reconcile it by hand. Routing it through Razorpay
/// is what turned "pending forever" into "pending for a few seconds".
class PaySheet extends ConsumerStatefulWidget {
  const PaySheet({required this.payment, super.key});

  final Payment payment;

  @override
  ConsumerState<PaySheet> createState() => _PaySheetState();
}

/// Opens the sheet and resolves once the user is finished with it.
Future<PayOutcome?> showPaySheet(BuildContext context, Payment payment) {
  return showModalBottomSheet<PayOutcome>(
    context: context,
    isScrollControlled: true,
    showDragHandle: true,
    builder: (_) => PaySheet(payment: payment),
  );
}

class _PaySheetState extends ConsumerState<PaySheet> {
  final _checkout = RazorpayCheckout();
  bool _isBusy = false;
  bool _showQr = false;

  @override
  void dispose() {
    _checkout.dispose();
    super.dispose();
  }

  void _tell(String message, {AppTone tone = AppTone.info}) {
    if (!mounted) return;
    showAppSnackBarOn(ScaffoldMessenger.of(context), message, tone: tone);
  }

  /// Razorpay Checkout. The signature it returns is what settles the payment.
  ///
  /// Its own sheet already offers cards, netbanking, wallets **and** UPI apps
  /// installed on the phone — including FamApp — so there is no separate
  /// app-picker here. One list maintained by Razorpay beats one maintained by
  /// us that goes stale.
  Future<void> _payInApp() async {
    setState(() => _isBusy = true);
    final repository = ref.read(paymentRepositoryProvider);
    final navigator = Navigator.of(context);

    try {
      final payload = await repository.openCheckout(widget.payment.id);
      final outcome = await _checkout.open(payload);
      if (!mounted) return;

      if (outcome.cancelled) {
        setState(() => _isBusy = false);
        return;
      }
      if (!outcome.succeeded) {
        setState(() => _isBusy = false);
        _tell(outcome.message, tone: AppTone.danger);
        return;
      }

      await repository.confirmCheckout(
        widget.payment.id,
        razorpayPaymentId: outcome.razorpayPaymentId,
        signature: outcome.signature,
      );
      if (!mounted) return;
      invalidatePayments(ref);
      navigator.pop(PayOutcome.paid);
    } on ApiException catch (error) {
      if (!mounted) return;
      setState(() => _isBusy = false);
      _tell(error.message, tone: AppTone.danger);
    }
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final qr = ref.watch(upiQrProvider(widget.payment.id));

    return SafeArea(
      child: Padding(
        padding: const EdgeInsets.fromLTRB(
          AppSpacing.gutter,
          0,
          AppSpacing.gutter,
          AppSpacing.lg,
        ),
        child: SingleChildScrollView(
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(
                'Pay ${widget.payment.totalDisplay}',
                style: theme.textTheme.titleMedium,
              ),
              const SizedBox(height: 2),
              Text(widget.payment.kind.label, style: theme.textTheme.bodySmall),
              const SizedBox(height: AppSpacing.md),

              // --- Pay here ------------------------------------------------
              Text('Pay on this phone', style: theme.textTheme.titleSmall),
              const SizedBox(height: AppSpacing.xs),
              Text(
                'Card, netbanking, wallet or a UPI app. Confirms straight away.',
                style: theme.textTheme.bodySmall,
              ),
              const SizedBox(height: AppSpacing.sm),
              AppButton(
                label: 'Pay ${widget.payment.totalDisplay}',
                icon: Icons.lock_outline_rounded,
                isLoading: _isBusy,
                onPressed: _payInApp,
              ),

              // --- Or scan ---------------------------------------------------
              qr.when(
                loading: () => const Padding(
                  padding: EdgeInsets.only(top: AppSpacing.md),
                  child: AppSkeleton(width: double.infinity, height: 48),
                ),
                // Razorpay unconfigured or unreachable. The section is hidden
                // rather than shown broken: paying on this phone still works,
                // and a dead QR where a payment instruction should be is worse
                // than no QR.
                error: (_, __) => const SizedBox.shrink(),
                data: (code) => _QrSection(
                  qr: code,
                  showQr: _showQr,
                  onToggle: () => setState(() => _showQr = !_showQr),
                  onScanned: () =>
                      Navigator.of(context).pop(PayOutcome.pendingUpi),
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

/// The scan-from-another-phone half of the sheet.
class _QrSection extends StatelessWidget {
  const _QrSection({
    required this.qr,
    required this.showQr,
    required this.onToggle,
    required this.onScanned,
  });

  final UpiQr qr;
  final bool showQr;
  final VoidCallback onToggle;
  final VoidCallback onScanned;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        const SizedBox(height: AppSpacing.md),
        const Divider(height: 1),
        const SizedBox(height: AppSpacing.md),
        Text('Or scan from another phone', style: theme.textTheme.titleSmall),
        const SizedBox(height: AppSpacing.xs),
        Text(
          // Naming the apps is the whole of "FamPay support": FamApp has no
          // merchant API, so scanning a standard code was always the only way
          // to pay from it.
          qr.isLocallyDrawn
              ? 'Scanning opens a secure Razorpay page — pay by UPI, card or '
                  'netbanking.'
              : 'Works with ${qr.apps.map((a) => a.label).join(', ')}.',
          style: theme.textTheme.bodySmall,
        ),
        const SizedBox(height: AppSpacing.sm),
        AppButton.secondary(
          label: showQr ? 'Hide QR code' : 'Show QR code',
          icon: Icons.qr_code_2_rounded,
          onPressed: onToggle,
        ),
        if (showQr) ...[
          const SizedBox(height: AppSpacing.sm),
          Center(
            child: Container(
              padding: const EdgeInsets.all(AppSpacing.sm),
              decoration: BoxDecoration(
                // White behind the code, always. A tinted ground drops the
                // contrast scanners rely on, and a QR that will not scan in a
                // dim stairwell is worse than no QR.
                color: Colors.white,
                borderRadius: AppRadius.card,
                border: Border.all(color: AppColors.border),
              ),
              child: Column(
                children: [
                  // Two shapes, one decided by the server.
                  //
                  // A hosted UPI QR is loaded, never redrawn — the string
                  // behind it is Razorpay's, and a locally-encoded copy could
                  // drift from the code the gateway is actually watching. The
                  // payment-link fallback has no hosted image, so the app draws
                  // that one; it is a URL rather than a payment instruction, so
                  // encoding it here changes nothing about the amount.
                  if (qr.isLocallyDrawn)
                    QrImageView(
                      data: qr.payload,
                      version: QrVersions.auto,
                      size: 220,
                      backgroundColor: Colors.white,
                      errorCorrectionLevel: QrErrorCorrectLevel.M,
                    )
                  else
                    Image.network(
                      qr.imageUrl,
                      width: 220,
                      height: 220,
                      fit: BoxFit.contain,
                      loadingBuilder: (context, child, progress) =>
                          progress == null
                              ? child
                              : const SizedBox(
                                  width: 220,
                                  height: 220,
                                  child: Center(
                                    child: CircularProgressIndicator(),
                                  ),
                                ),
                      errorBuilder: (context, _, __) => const SizedBox(
                        width: 220,
                        height: 220,
                        child: Center(
                          child: Text(
                            'Could not load the code.\nPay on this phone instead.',
                            textAlign: TextAlign.center,
                            style: TextStyle(color: AppColors.textSecondary),
                          ),
                        ),
                      ),
                    ),
                  const SizedBox(height: AppSpacing.xs),
                  // The figure the payer should see in their own app before
                  // confirming. The code is locked to it — Razorpay refuses any
                  // other amount — so a mismatch means the wrong code.
                  Text(
                    qr.amountDisplay,
                    style: const TextStyle(
                      fontSize: 18,
                      fontWeight: FontWeight.w700,
                      color: AppColors.textPrimary,
                    ),
                  ),
                ],
              ),
            ),
          ),
          const SizedBox(height: AppSpacing.xs),
          Center(
            child: Text(
              'This code is for this payment only and expires shortly.',
              style: theme.textTheme.bodySmall,
              textAlign: TextAlign.center,
            ),
          ),
          const SizedBox(height: AppSpacing.sm),
          AppButton.secondary(
            label: 'I have scanned it',
            icon: Icons.check_rounded,
            // Closes the sheet without claiming the payment succeeded — the
            // server decides that when the webhook lands.
            onPressed: onScanned,
          ),
        ],
      ],
    );
  }
}
