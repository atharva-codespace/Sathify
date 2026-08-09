import '../../../../core/config/api_endpoints.dart';
import '../../../../core/network/api_client.dart';
import '../models/payment_models.dart';

/// All Module 8 endpoints.
///
/// -----------------------------------------------------------------------
/// THE APP NEVER DECIDES THAT A PAYMENT SUCCEEDED
/// -----------------------------------------------------------------------
/// Razorpay Checkout hands the app a signed response. [confirmCheckout] passes
/// that signature to the server, which verifies it against a secret the app has
/// never seen and only then marks the payment paid. The app reporting success
/// on its own would let any resident settle their own payments — so it does not
/// have that power, by design.
class PaymentRepository {
  PaymentRepository({ApiClient? client}) : _client = client ?? ApiClient();

  final ApiClient _client;

  // --- 8.2 Ledger ------------------------------------------------------------

  Future<List<Payment>> fetchPayments({String? status, String? kind}) async {
    final response = await _client.get(
      ApiEndpoints.payments,
      query: {
        if (status != null) 'status': status,
        if (kind != null) 'kind': kind,
        'page_size': 100,
      },
    ) as Map<String, dynamic>;

    return (response['results'] as List)
        .map((row) => Payment.fromJson(row as Map<String, dynamic>))
        .toList();
  }

  Future<Payment> fetchPayment(String paymentId) async {
    final response = await _client.get(ApiEndpoints.payment(paymentId))
        as Map<String, dynamic>;
    return Payment.fromJson(response);
  }

  // --- 8.3 Receipts and summaries -------------------------------------------

  Future<Receipt> fetchReceipt(String paymentId) async {
    final response = await _client.get(ApiEndpoints.paymentReceipt(paymentId))
        as Map<String, dynamic>;
    return Receipt.fromJson(response);
  }

  /// A worker's own month, or — for an administrator — a named worker's.
  Future<MonthlySummary> fetchMonthlySummary({
    int? year,
    int? month,
    int? workerId,
  }) async {
    final response = await _client.get(
      ApiEndpoints.paymentSummary,
      query: {
        if (year != null) 'year': year,
        if (month != null) 'month': month,
        if (workerId != null) 'worker': workerId,
      },
    ) as Map<String, dynamic>;

    return MonthlySummary.fromJson(response);
  }

  // --- 8.1 / 8.4 Paying ------------------------------------------------------

  /// The attendance arithmetic, fetched before the resident commits.
  Future<SalaryBasis> fetchSalaryBasis({
    required int engagementId,
    required DateTime periodStart,
    required DateTime periodEnd,
  }) async {
    final response = await _client.get(
      ApiEndpoints.salaryBasis,
      query: {
        'engagement': engagementId,
        'period_start': _day(periodStart),
        'period_end': _day(periodEnd),
      },
    ) as Map<String, dynamic>;

    return SalaryBasis.fromJson(response);
  }

  /// Opens a salary payment.
  ///
  /// Omit [amountPaise] to accept the attendance-derived suggestion; pass it to
  /// pay a different amount, which the resident is entitled to do.
  Future<Payment> payEngagement({
    required int engagementId,
    required DateTime periodStart,
    required DateTime periodEnd,
    int? amountPaise,
    int tipPaise = 0,
    String note = '',
  }) async {
    final response = await _client.post(
      ApiEndpoints.payEngagement,
      data: {
        'engagement': engagementId,
        'period_start': _day(periodStart),
        'period_end': _day(periodEnd),
        if (amountPaise != null) 'amount_paise': amountPaise,
        if (tipPaise > 0) 'tip_paise': tipPaise,
        if (note.isNotEmpty) 'note': note,
      },
    ) as Map<String, dynamic>;

    return Payment.fromJson(response['payment'] as Map<String, dynamic>);
  }

  Future<Payment> payBooking({
    required int bookingId,
    int tipPaise = 0,
    String note = '',
  }) async {
    final response = await _client.post(
      ApiEndpoints.payBooking,
      data: {
        'booking': bookingId,
        if (tipPaise > 0) 'tip_paise': tipPaise,
        if (note.isNotEmpty) 'note': note,
      },
    ) as Map<String, dynamic>;

    return Payment.fromJson(response['payment'] as Map<String, dynamic>);
  }

  /// Module 8.9 — a Razorpay-hosted UPI QR for a payment.
  ///
  /// Opens one server-side if there is no live code, and reuses the existing one
  /// otherwise, so re-opening the sheet does not invalidate a code somebody has
  /// already photographed.
  ///
  /// A 503 means Razorpay is unconfigured or unreachable. Callers treat that as
  /// "hide the QR and offer in-app checkout" rather than as an error worth
  /// showing — a broken image where a payment instruction should be is worse
  /// than no QR at all.
  Future<UpiQr> fetchUpiQr(String paymentId) async {
    final response = await _client.get(ApiEndpoints.paymentUpi(paymentId))
        as Map<String, dynamic>;
    return UpiQr.fromJson(response);
  }

  /// Opens the Razorpay order server-side and returns the checkout payload.
  Future<CheckoutPayload> openCheckout(String paymentId) async {
    final response = await _client.post(ApiEndpoints.paymentCheckout(paymentId))
        as Map<String, dynamic>;
    return CheckoutPayload.fromJson(
      response['checkout'] as Map<String, dynamic>,
    );
  }

  /// Hands Razorpay's signed response back for verification.
  ///
  /// This is the only thing that settles a payment from the app's side, and it
  /// settles it because of the signature, not because the app said so.
  Future<Payment> confirmCheckout(
    String paymentId, {
    required String razorpayPaymentId,
    required String signature,
  }) async {
    final response = await _client.post(
      ApiEndpoints.paymentConfirm(paymentId),
      data: {
        'razorpay_payment_id': razorpayPaymentId,
        'razorpay_signature': signature,
      },
    ) as Map<String, dynamic>;

    return Payment.fromJson(response['payment'] as Map<String, dynamic>);
  }

  // --- 8.5 Replacement split -------------------------------------------------

  Future<ReplacementSplit> fetchReplacementSplit(int engagementId) async {
    final response =
        await _client.get(ApiEndpoints.replacementSplit(engagementId))
            as Map<String, dynamic>;
    return ReplacementSplit.fromJson(response);
  }

  Future<ReplacementSplit> setReplacementSplit(
    int engagementId, {
    required int replacementSharePercent,
    String note = '',
  }) async {
    final response = await _client.put(
      ApiEndpoints.replacementSplit(engagementId),
      data: {
        'replacement_share_percent': replacementSharePercent,
        if (note.isNotEmpty) 'note': note,
      },
    ) as Map<String, dynamic>;

    return ReplacementSplit.fromJson(response);
  }

  // --- 8.6 Disputes ----------------------------------------------------------

  Future<List<PaymentDispute>> fetchDisputes() async {
    final response = await _client.get(
      ApiEndpoints.paymentDisputes,
      query: {'page_size': 100},
    ) as Map<String, dynamic>;

    return (response['results'] as List)
        .map((row) => PaymentDispute.fromJson(row as Map<String, dynamic>))
        .toList();
  }

  Future<PaymentDispute> raiseDispute(
    String paymentId, {
    required DisputeReason reason,
    required String description,
  }) async {
    final response = await _client.post(
      ApiEndpoints.paymentDispute(paymentId),
      data: {'reason': reason.wireValue, 'description': description},
    ) as Map<String, dynamic>;

    return PaymentDispute.fromJson(response['dispute'] as Map<String, dynamic>);
  }

  String _day(DateTime date) => '${date.year.toString().padLeft(4, '0')}-'
      '${date.month.toString().padLeft(2, '0')}-'
      '${date.day.toString().padLeft(2, '0')}';
}
