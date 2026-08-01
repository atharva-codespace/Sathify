import 'dart:async';

import 'package:razorpay_flutter/razorpay_flutter.dart';

import 'models/payment_models.dart';

/// The outcome of a Razorpay Checkout sheet.
///
/// [signature] is the only field that matters for settlement: the server
/// verifies it against a secret this app has never seen. Without it, a
/// "success" here is just a claim.
class CheckoutOutcome {
  const CheckoutOutcome._({
    required this.succeeded,
    this.razorpayPaymentId = '',
    this.signature = '',
    this.cancelled = false,
    this.message = '',
  });

  const CheckoutOutcome.success({
    required String paymentId,
    required String signature,
  }) : this._(
          succeeded: true,
          razorpayPaymentId: paymentId,
          signature: signature,
        );

  const CheckoutOutcome.failure(String message)
      : this._(succeeded: false, message: message);

  /// The user backed out. Not an error, and not worth an error message.
  const CheckoutOutcome.cancelled() : this._(succeeded: false, cancelled: true);

  final bool succeeded;
  final String razorpayPaymentId;
  final String signature;
  final bool cancelled;
  final String message;
}

/// Wraps `razorpay_flutter`'s callback API in a Future.
///
/// The package reports through three separate event listeners, which forces
/// every caller into scattered callbacks and mutable flags. One awaited call is
/// far harder to get wrong — and getting a payment flow wrong means charging
/// someone twice or losing the signature that proves they paid at all.
///
/// One [open] at a time: the sheet is modal, and a second call while one is in
/// flight completes the wrong Completer.
class RazorpayCheckout {
  Razorpay? _razorpay;
  Completer<CheckoutOutcome>? _pending;

  /// Opens the sheet and resolves when the user finishes, fails, or backs out.
  Future<CheckoutOutcome> open(CheckoutPayload payload) {
    if (_pending != null && !_pending!.isCompleted) {
      return Future.value(
        const CheckoutOutcome.failure('A payment is already in progress.'),
      );
    }

    final completer = Completer<CheckoutOutcome>();
    _pending = completer;

    final razorpay = Razorpay();
    _razorpay = razorpay;

    razorpay.on(Razorpay.EVENT_PAYMENT_SUCCESS,
        (PaymentSuccessResponse response) {
      _finish(
        CheckoutOutcome.success(
          paymentId: response.paymentId ?? '',
          signature: response.signature ?? '',
        ),
      );
    });

    razorpay.on(Razorpay.EVENT_PAYMENT_ERROR,
        (PaymentFailureResponse response) {
      // Razorpay reports a user-initiated dismissal through the same channel as
      // a genuine failure. Telling someone their payment failed when they simply
      // changed their mind is needlessly alarming, so the two are separated.
      final message = response.message ?? '';
      if (_looksCancelled(message)) {
        _finish(const CheckoutOutcome.cancelled());
      } else {
        _finish(
          CheckoutOutcome.failure(
            message.isEmpty ? 'The payment did not go through.' : message,
          ),
        );
      }
    });

    razorpay.on(Razorpay.EVENT_EXTERNAL_WALLET,
        (ExternalWalletResponse response) {
      // The user left for a wallet app. Nothing is settled yet; the webhook is
      // what will tell us how it ended.
      _finish(
        const CheckoutOutcome.failure(
          'Continue in your wallet app. We will update this once it completes.',
        ),
      );
    });

    razorpay.open(payload.toRazorpayOptions());
    return completer.future;
  }

  bool _looksCancelled(String message) {
    final lower = message.toLowerCase();
    return lower.contains('cancel') || lower.contains('dismiss');
  }

  void _finish(CheckoutOutcome outcome) {
    final completer = _pending;
    _pending = null;
    _disposeRazorpay();
    if (completer != null && !completer.isCompleted) {
      completer.complete(outcome);
    }
  }

  void _disposeRazorpay() {
    // `clear` removes the listeners; without it they accumulate across payments
    // and an old listener completes a stale Completer.
    _razorpay?.clear();
    _razorpay = null;
  }

  /// Call from the owning widget's dispose.
  void dispose() {
    _disposeRazorpay();
    if (_pending != null && !_pending!.isCompleted) {
      _pending!.complete(const CheckoutOutcome.cancelled());
    }
    _pending = null;
  }
}
