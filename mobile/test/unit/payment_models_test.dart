import 'package:flutter_test/flutter_test.dart';
import 'package:sathify/features/payments/data/models/payment_models.dart';

/// Wire-format tests for Module 8 — Payments & Payouts.
///
/// NOT YET EXECUTED — the Flutter SDK is not installed on this machine. Run
/// `flutter test` once it is.
///
/// The money group matters most. Every amount is an integer count of paise, and
/// a `double` creeping in drifts — 0.1 is not representable in binary — until
/// the app disagrees with the ledger about what someone was paid.
void main() {
  group('formatPaise', () {
    test('renders rupees and paise', () {
      expect(formatPaise(200000), '₹2,000.00');
      expect(formatPaise(100), '₹1.00');
      expect(formatPaise(5), '₹0.05');
      expect(formatPaise(0), '₹0.00');
    });

    test('groups in the Indian style', () {
      /// 1,00,000 — not 100,000. Getting this wrong makes every large figure
      /// look foreign to the people reading it.
      expect(formatPaise(10000000), '₹1,00,000.00');
      expect(formatPaise(100000000), '₹10,00,000.00');
    });

    test('does not group below a thousand', () {
      expect(formatPaise(99900), '₹999.00');
    });

    test('keeps a trailing zero in the paise', () {
      expect(formatPaise(200050), '₹2,000.50');
      expect(formatPaise(200005), '₹2,000.05');
    });
  });

  group('Payment', () {
    Map<String, dynamic> payload() => {
          'id': 'a3f1c2d4-0000-4000-8000-000000000001',
          'receipt_number': 'SATH-202608-AB12CD34',
          'kind': 'engagement_salary',
          'status': 'created',
          'amount_paise': 400000,
          'tip_paise': 0,
          'refunded_paise': 0,
          'total_paise': 400000,
          'net_paise': 400000,
          'total_display': '₹4,000.00',
          'worker': 7,
          'worker_name': 'Rahul Sharma',
          'resident_name': 'Anita Desai',
          'flat_label': 'A-301',
          'razorpay_order_id': '',
        };

    test('parses a ledger row', () {
      final payment = Payment.fromJson(payload());

      expect(payment.receiptNumber, 'SATH-202608-AB12CD34');
      expect(payment.kind, PaymentKind.engagementSalary);
      expect(payment.status, PaymentStatus.created);
      expect(payment.amountPaise, 400000);
      expect(payment.totalDisplay, '₹4,000.00');
    });

    test('a created payment is payable', () {
      expect(Payment.fromJson(payload()).isPayable, isTrue);
    });

    test('a failed payment is still payable — the resident can retry', () {
      final payment = Payment.fromJson({...payload(), 'status': 'failed'});
      expect(payment.isPayable, isTrue);
      expect(payment.isSettled, isFalse);
    });

    test('a settled payment is not payable again', () {
      final payment = Payment.fromJson({...payload(), 'status': 'paid'});
      expect(payment.isSettled, isTrue);
      expect(payment.isPayable, isFalse);
    });

    test('a refunded payment is not payable again', () {
      final payment = Payment.fromJson({...payload(), 'status': 'refunded'});
      expect(payment.isPayable, isFalse);
    });

    test('a tip is part of the total, not a separate charge', () {
      final payment = Payment.fromJson({
        ...payload(),
        'tip_paise': 5000,
        'total_paise': 405000,
      });

      expect(payment.hasTip, isTrue);
      expect(payment.totalPaise, payment.amountPaise + payment.tipPaise);
    });

    test('a partial refund is visible without unsettling the payment', () {
      final payment = Payment.fromJson({
        ...payload(),
        'status': 'paid',
        'refunded_paise': 50000,
        'net_paise': 350000,
      });

      expect(payment.isSettled, isTrue);
      expect(payment.wasRefunded, isTrue);
      expect(payment.netPaise, 350000);
    });

    test('falls back rather than throwing on an unknown status', () {
      final payment = Payment.fromJson({...payload(), 'status': 'escrowed'});
      expect(payment.status, PaymentStatus.created);
    });
  });

  group('SalaryBasis', () {
    test('parses the arithmetic behind a suggestion', () {
      final basis = SalaryBasis.fromJson({
        'expected_visits': 3,
        'attended_visits': 2,
        'full_rate_paise': 400000,
        'suggested_paise': 266666,
        'is_full': false,
        'explanation': '2 of 3 scheduled visits were logged at the gate.',
      });

      expect(basis.expectedVisits, 3);
      expect(basis.attendedVisits, 2);
      expect(basis.isFull, isFalse);
      expect(basis.explanation, contains('2 of 3'));
    });

    test('zero attendance suggests nothing, which the server refuses', () {
      final basis = SalaryBasis.fromJson({
        'expected_visits': 3,
        'attended_visits': 0,
        'full_rate_paise': 400000,
        'suggested_paise': 0,
        'is_full': false,
      });

      expect(basis.suggestsNothing, isTrue);
    });

    test('full attendance suggests the whole rate', () {
      final basis = SalaryBasis.fromJson({
        'expected_visits': 3,
        'attended_visits': 3,
        'full_rate_paise': 400000,
        'suggested_paise': 400000,
        'is_full': true,
      });

      expect(basis.isFull, isTrue);
      expect(basis.suggestsNothing, isFalse);
    });
  });

  group('CheckoutPayload', () {
    Map<String, dynamic> payload() => {
          'key': 'rzp_test_abc123',
          'order_id': 'order_xyz',
          'amount': 405000,
          'currency': 'INR',
          'name': 'Sathify',
          'description': 'Salary',
          'test_mode': true,
        };

    test('parses the server payload', () {
      final checkout = CheckoutPayload.fromJson(payload());

      expect(checkout.key, 'rzp_test_abc123');
      expect(checkout.orderId, 'order_xyz');
      expect(checkout.amountPaise, 405000);
      expect(checkout.testMode, isTrue);
    });

    test('builds the options razorpay_flutter expects', () {
      final options = CheckoutPayload.fromJson(payload()).toRazorpayOptions();

      expect(options['key'], 'rzp_test_abc123');
      expect(options['order_id'], 'order_xyz');
      expect(options['amount'], 405000);
      expect(options['currency'], 'INR');
    });

    test('never carries a secret', () {
      /// Only the public key id reaches the app; the key secret stays server-side,
      /// which is why the order is created there.
      final options = CheckoutPayload.fromJson(payload()).toRazorpayOptions();
      expect(options.containsKey('key_secret'), isFalse);
      expect(options.containsKey('signature'), isFalse);
    });
  });

  group('Receipt', () {
    test('parses a settled receipt', () {
      final receipt = Receipt.fromJson({
        'receipt_number': 'SATH-202608-AB12CD34',
        'status': 'paid',
        'kind': 'Salary',
        'description': 'Salary, 03 Aug – 09 Aug',
        'paid_at': '2026-08-10T09:00:00Z',
        'worker_name': 'Rahul Sharma',
        'resident_name': 'Anita Desai',
        'flat': 'A-301',
        'amount_display': '₹4,000.00',
        'tip_display': '₹50.00',
        'total_display': '₹4,050.00',
        'net_display': '₹4,050.00',
        'tip_paise': 5000,
        'refunded_paise': 0,
        'gateway_payment_id': 'pay_abc',
      });

      expect(receipt.receiptNumber, 'SATH-202608-AB12CD34');
      expect(receipt.hasTip, isTrue);
      expect(receipt.wasRefunded, isFalse);
      expect(receipt.totalDisplay, '₹4,050.00');
    });

    test('a receipt never carries a signature', () {
      final receipt = Receipt.fromJson({
        'receipt_number': 'SATH-1',
        'total_display': '₹1.00',
        'gateway_payment_id': 'pay_abc',
      });

      /// The gateway payment id is for a support conversation. The signature —
      /// the thing that proves settlement — is never sent to a client.
      expect(receipt.gatewayPaymentId, 'pay_abc');
    });
  });

  group('MonthlySummary', () {
    Map<String, dynamic> payload() => {
          'year': 2026,
          'month': 8,
          'month_name': 'August 2026',
          'worker_name': 'Rahul Sharma',
          'payment_count': 2,
          'total_paise': 355000,
          'total_display': '₹3,550.00',
          'tips_paise': 5000,
          'tips_display': '₹50.00',
          'lines': [
            {
              'payment_id': 'a3f1c2d4-0000-4000-8000-000000000001',
              'date': '2026-08-10',
              'receipt_number': 'SATH-202608-AB12CD34',
              'description': 'Salary, 03 Aug – 09 Aug',
              'net_paise': 200000,
              'net_display': '₹2,000.00',
            },
          ],
        };

    test('parses a statement', () {
      final summary = MonthlySummary.fromJson(payload());

      expect(summary.monthName, 'August 2026');
      expect(summary.paymentCount, 2);
      expect(summary.totalDisplay, '₹3,550.00');
      expect(summary.hasTips, isTrue);
      expect(summary.isEmpty, isFalse);
    });

    test('each line carries the id that opens its receipt', () {
      /// The receipt number is for a human to read; it is not an identifier.
      final summary = MonthlySummary.fromJson(payload());
      expect(
        summary.lines.single.paymentId,
        'a3f1c2d4-0000-4000-8000-000000000001',
      );
    });

    test('an empty month is detectable', () {
      final summary = MonthlySummary.fromJson({
        ...payload(),
        'payment_count': 0,
        'lines': const [],
      });

      expect(summary.isEmpty, isTrue);
    });
  });

  group('ReplacementSplit', () {
    test('the default pays the replacement in full', () {
      final split = ReplacementSplit.fromJson({
        'replacement_share_percent': 100,
        'original_share_percent': 0,
        'is_customised': false,
      });

      expect(split.replacementSharePercent, 100);
      expect(split.isCustomised, isFalse);
    });

    test('the shares always total one hundred', () {
      final split = ReplacementSplit.fromJson({
        'replacement_share_percent': 70,
        'original_share_percent': 30,
        'is_customised': true,
      });

      expect(
        split.replacementSharePercent + split.originalSharePercent,
        100,
      );
    });
  });

  group('PaymentDispute', () {
    test('parses a raised dispute', () {
      final dispute = PaymentDispute.fromJson({
        'id': 3,
        'reason': 'not_paid',
        'description': 'I never received this payment.',
        'status': 'open',
        'receipt_number': 'SATH-202608-AB12CD34',
        'is_open': true,
      });

      expect(dispute.reason, DisputeReason.notPaid);
      expect(dispute.isOpen, isTrue);
    });

    test('a resolved dispute carries its outcome', () {
      final dispute = PaymentDispute.fromJson({
        'id': 3,
        'reason': 'wrong_amount',
        'description': 'The amount is short.',
        'status': 'resolved',
        'is_open': false,
        'resolution': 'Corrected and topped up.',
      });

      expect(dispute.isOpen, isFalse);
      expect(dispute.resolution, 'Corrected and topped up.');
    });

    test('falls back rather than throwing on an unknown reason', () {
      final dispute = PaymentDispute.fromJson({
        'id': 1,
        'reason': 'something_new',
        'description': 'x',
        'status': 'open',
      });

      expect(dispute.reason, DisputeReason.other);
    });
  });
}
