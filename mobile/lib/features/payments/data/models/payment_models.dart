/// Data models for Module 8 — Payments & Payouts.
///
/// -----------------------------------------------------------------------
/// MONEY IS PAISE, AS INTEGERS
/// -----------------------------------------------------------------------
/// Every amount here is an integer count of paise, matching the server. Dart's
/// `double` has the same problem every binary float does — 0.1 is not
/// representable — so a rupee `double` drifts and eventually disagrees with the
/// ledger about what someone was paid.
///
/// The server sends both `*_paise` and a `*_display` string. Use the integer for
/// arithmetic and comparison, and show the string. Formatting locally would
/// eventually produce an app screen and a PDF receipt that disagree about the
/// same payment, which is exactly what turns into a dispute.
library;

/// Fallback formatter, for amounts the server had no reason to pre-render.
///
/// Prefer a `*_display` field whenever one exists — this exists for locally
/// computed figures like a tip the user is still typing.
String formatPaise(int paise) {
  final rupees = paise ~/ 100;
  final remainder = (paise % 100).toString().padLeft(2, '0');

  // Indian digit grouping: the last three digits, then pairs.
  final digits = rupees.abs().toString();
  String grouped;
  if (digits.length <= 3) {
    grouped = digits;
  } else {
    final lastThree = digits.substring(digits.length - 3);
    var rest = digits.substring(0, digits.length - 3);
    final parts = <String>[];
    while (rest.length > 2) {
      parts.insert(0, rest.substring(rest.length - 2));
      rest = rest.substring(0, rest.length - 2);
    }
    if (rest.isNotEmpty) parts.insert(0, rest);
    grouped = '${parts.join(',')},$lastThree';
  }

  return '₹$grouped.$remainder';
}

enum PaymentKind {
  engagementSalary('engagement_salary', 'Salary'),
  booking('booking', 'One-day booking'),
  tip('tip', 'Tip'),
  refund('refund', 'Refund'),
  replacement('replacement', 'Replacement cover'),

  /// Module 5.5 — Sathify's fee for running an emergency broadcast. The only
  /// kind owed to the platform rather than to a worker.
  emergencySurcharge('emergency_surcharge', 'Emergency fee'),

  /// Module 4.6 — this month's worked days, settled before notice takes effect.
  noticeSettlement('notice_settlement', 'Final settlement');

  const PaymentKind(this.wireValue, this.label);

  final String wireValue;
  final String label;

  static PaymentKind fromWire(String? value) => PaymentKind.values.firstWhere(
        (k) => k.wireValue == value,
        orElse: () => PaymentKind.booking,
      );
}

enum PaymentStatus {
  created('created', 'Not started'),
  pending('pending', 'Awaiting confirmation'),
  paid('paid', 'Paid'),
  failed('failed', 'Failed'),
  refunded('refunded', 'Refunded'),
  cancelled('cancelled', 'Cancelled');

  const PaymentStatus(this.wireValue, this.label);

  final String wireValue;
  final String label;

  static PaymentStatus fromWire(String? value) =>
      PaymentStatus.values.firstWhere(
        (s) => s.wireValue == value,
        orElse: () => PaymentStatus.created,
      );
}

/// One line in the ledger (Module 8.2).
class Payment {
  const Payment({
    required this.id,
    required this.receiptNumber,
    required this.kind,
    required this.status,
    required this.amountPaise,
    this.tipPaise = 0,
    this.refundedPaise = 0,
    this.totalPaise = 0,
    this.netPaise = 0,
    this.totalDisplay = '',
    this.workerId = 0,
    this.workerName = '',
    this.residentName = '',
    this.flatLabel = '',
    this.engagementId,
    this.bookingId,
    this.periodStart,
    this.periodEnd,
    this.note = '',
    this.failureReason = '',
    this.paidAt,
    this.createdAt,
    this.razorpayOrderId = '',
  });

  final String id;
  final String receiptNumber;
  final PaymentKind kind;
  final PaymentStatus status;

  final int amountPaise;
  final int tipPaise;
  final int refundedPaise;
  final int totalPaise;
  final int netPaise;

  /// Server-rendered. Show this rather than formatting [totalPaise] locally.
  final String totalDisplay;

  final int workerId;
  final String workerName;
  final String residentName;
  final String flatLabel;
  final int? engagementId;
  final int? bookingId;
  final DateTime? periodStart;
  final DateTime? periodEnd;
  final String note;
  final String failureReason;
  final DateTime? paidAt;
  final DateTime? createdAt;

  /// Set once an order has been opened. Lets an abandoned checkout be reopened
  /// rather than a second payment being created for the same thing.
  final String razorpayOrderId;

  bool get isSettled => status == PaymentStatus.paid;

  /// Still payable — the resident can open or reopen checkout.
  bool get isPayable =>
      status == PaymentStatus.created ||
      status == PaymentStatus.pending ||
      status == PaymentStatus.failed;

  bool get hasTip => tipPaise > 0;
  bool get wasRefunded => refundedPaise > 0;

  factory Payment.fromJson(Map<String, dynamic> json) => Payment(
        id: json['id'].toString(),
        receiptNumber: json['receipt_number'] as String? ?? '',
        kind: PaymentKind.fromWire(json['kind'] as String?),
        status: PaymentStatus.fromWire(json['status'] as String?),
        amountPaise: json['amount_paise'] as int? ?? 0,
        tipPaise: json['tip_paise'] as int? ?? 0,
        refundedPaise: json['refunded_paise'] as int? ?? 0,
        totalPaise: json['total_paise'] as int? ?? 0,
        netPaise: json['net_paise'] as int? ?? 0,
        totalDisplay: json['total_display'] as String? ?? '',
        workerId: json['worker'] as int? ?? 0,
        workerName: json['worker_name'] as String? ?? '',
        residentName: json['resident_name'] as String? ?? '',
        flatLabel: json['flat_label'] as String? ?? '',
        engagementId: json['engagement'] as int?,
        bookingId: json['booking'] as int?,
        periodStart: DateTime.tryParse(json['period_start'] as String? ?? ''),
        periodEnd: DateTime.tryParse(json['period_end'] as String? ?? ''),
        note: json['note'] as String? ?? '',
        failureReason: json['failure_reason'] as String? ?? '',
        paidAt: DateTime.tryParse(json['paid_at'] as String? ?? ''),
        createdAt: DateTime.tryParse(json['created_at'] as String? ?? ''),
        razorpayOrderId: json['razorpay_order_id'] as String? ?? '',
      );
}

/// The attendance arithmetic behind a suggested salary (Module 8.1).
///
/// Shown before the resident commits so the figure is accountable — and so a
/// worker can contest something concrete rather than an unexplained number.
class SalaryBasis {
  const SalaryBasis({
    required this.expectedVisits,
    required this.attendedVisits,
    required this.fullRatePaise,
    required this.suggestedPaise,
    required this.isFull,
    this.explanation = '',
  });

  final int expectedVisits;
  final int attendedVisits;
  final int fullRatePaise;
  final int suggestedPaise;
  final bool isFull;

  /// The server's own wording, shown verbatim so app and server never explain
  /// the same number differently.
  final String explanation;

  /// Zero attendance pro-rates to nothing, and a zero payment is refused. The
  /// resident has to choose an amount deliberately in that case.
  bool get suggestsNothing => suggestedPaise == 0;

  factory SalaryBasis.fromJson(Map<String, dynamic> json) => SalaryBasis(
        expectedVisits: json['expected_visits'] as int? ?? 0,
        attendedVisits: json['attended_visits'] as int? ?? 0,
        fullRatePaise: json['full_rate_paise'] as int? ?? 0,
        suggestedPaise: json['suggested_paise'] as int? ?? 0,
        isFull: json['is_full'] as bool? ?? false,
        explanation: json['explanation'] as String? ?? '',
      );
}

/// What the app hands to Razorpay Checkout (Module 8.1).
///
/// [key] is the public key id — it identifies the merchant in the checkout
/// sheet. The key secret never leaves the server, which is why the order was
/// created there.
/// One UPI app the payer might have. Presentational only — a signpost that
/// their app is supported, never a branch. Every one of them scans the same
/// hosted QR.
class UpiApp {
  const UpiApp({required this.key, required this.label});

  final String key;
  final String label;

  factory UpiApp.fromJson(Map<String, dynamic> json) => UpiApp(
        key: json['key'] as String? ?? '',
        label: json['label'] as String? ?? '',
      );
}

/// Module 8.9 — a Razorpay-hosted UPI QR for one payment.
///
/// -----------------------------------------------------------------------
/// THE APP NO LONGER DRAWS THIS CODE, AND THAT IS THE POINT
/// -----------------------------------------------------------------------
/// It used to: the server built a `upi://pay` string against a plain VPA and
/// the phone rendered it. Every UPI app scanned it — and the money landed in a
/// bank account with no callback, so nothing could mark the payment paid except
/// an administrator reading a statement.
///
/// Razorpay now hosts the code at [imageUrl] and watches it. The payer's
/// experience is identical; ours is not, because a scan produces a signed
/// `qr_code.credited` webhook and the payment settles by itself.
///
/// FamPay still works for the same reason it ever did — FamApp is a consumer
/// UPI app with no merchant API, so the only way to pay from it was always to
/// hand it something standard to scan. [apps] names it so a resident can see
/// their app is supported; nothing here is per-app.
class UpiQr {
  const UpiQr({
    required this.amountPaise,
    this.kind = 'razorpay_qr',
    this.imageUrl = '',
    this.payload = '',
    this.qrCodeId = '',
    this.amountDisplay = '',
    this.reference = '',
    this.expiresAt,
    this.apps = const [],
  });

  /// `razorpay_qr` — Razorpay hosts the image, and the payer scans straight
  /// into their UPI app. `payment_link` — the fallback for accounts without the
  /// QR Codes API: the app encodes [payload] and scanning opens Razorpay's
  /// hosted page. Both settle through a signed webhook.
  final String kind;

  /// The URL to encode locally, on the `payment_link` path. Empty otherwise.
  ///
  /// It is a link rather than a payment instruction, which is why drawing it
  /// here is safe in a way re-encoding a `upi://` string would not be — the
  /// amount lives on Razorpay's side of it.
  final String payload;

  /// Where Razorpay serves the code. Loaded rather than encoded locally: the
  /// string behind it is the gateway's now, and a code drawn here could drift
  /// from the one Razorpay is actually watching.
  final String imageUrl;

  /// Razorpay's id for the code. Not shown; it is how a credit webhook finds
  /// this payment again.
  final String qrCodeId;

  final int amountPaise;
  final String amountDisplay;

  /// The payment's own id, for a support conversation.
  final String reference;

  /// The code is single-use and closes itself, so it does not live long.
  final DateTime? expiresAt;

  final List<UpiApp> apps;

  bool get hasExpired =>
      expiresAt != null && DateTime.now().isAfter(expiresAt!);

  /// Whether the app draws the code itself rather than loading a hosted one.
  bool get isLocallyDrawn => imageUrl.isEmpty && payload.isNotEmpty;

  /// Nothing to show at all — neither a hosted image nor something to encode.
  bool get isEmpty => imageUrl.isEmpty && payload.isEmpty;

  factory UpiQr.fromJson(Map<String, dynamic> json) => UpiQr(
        kind: json['kind'] as String? ?? 'razorpay_qr',
        imageUrl: json['image_url'] as String? ?? '',
        payload: json['payload'] as String? ?? '',
        qrCodeId: json['qr_code_id'] as String? ?? '',
        amountPaise: json['amount_paise'] as int? ?? 0,
        amountDisplay: json['amount_display'] as String? ?? '',
        reference: json['reference'] as String? ?? '',
        expiresAt: DateTime.tryParse(json['expires_at'] as String? ?? ''),
        apps: ((json['apps'] as List?) ?? const [])
            .map((row) => UpiApp.fromJson(row as Map<String, dynamic>))
            .toList(),
      );
}

class CheckoutPayload {
  const CheckoutPayload({
    required this.key,
    required this.orderId,
    required this.amountPaise,
    this.currency = 'INR',
    this.name = 'Sathify',
    this.description = '',
    this.testMode = true,
  });

  final String key;
  final String orderId;
  final int amountPaise;
  final String currency;
  final String name;
  final String description;

  /// True while the project is on Razorpay test keys. Surfaced in the UI so
  /// nobody mistakes a sandbox charge for a real one.
  final bool testMode;

  /// The options map razorpay_flutter expects.
  Map<String, dynamic> toRazorpayOptions() => {
        'key': key,
        'order_id': orderId,
        'amount': amountPaise,
        'currency': currency,
        'name': name,
        'description': description,
      };

  factory CheckoutPayload.fromJson(Map<String, dynamic> json) =>
      CheckoutPayload(
        key: json['key'] as String? ?? '',
        orderId: json['order_id'] as String? ?? '',
        amountPaise: json['amount'] as int? ?? 0,
        currency: json['currency'] as String? ?? 'INR',
        name: json['name'] as String? ?? 'Sathify',
        description: json['description'] as String? ?? '',
        testMode: json['test_mode'] as bool? ?? true,
      );
}

/// A single transaction's receipt (Module 8.3).
class Receipt {
  const Receipt({
    required this.receiptNumber,
    required this.totalDisplay,
    this.status = '',
    this.kind = '',
    this.description = '',
    this.paidAt,
    this.workerName = '',
    this.residentName = '',
    this.flat = '',
    this.amountDisplay = '',
    this.tipDisplay = '',
    this.netDisplay = '',
    this.tipPaise = 0,
    this.refundedPaise = 0,
    this.gatewayPaymentId = '',
  });

  final String receiptNumber;
  final String status;
  final String kind;
  final String description;
  final DateTime? paidAt;
  final String workerName;
  final String residentName;
  final String flat;
  final String amountDisplay;
  final String tipDisplay;
  final String totalDisplay;
  final String netDisplay;
  final int tipPaise;
  final int refundedPaise;

  /// For a support conversation with Razorpay. Not a secret, and not a
  /// signature — the app never sees one of those.
  final String gatewayPaymentId;

  bool get hasTip => tipPaise > 0;
  bool get wasRefunded => refundedPaise > 0;

  factory Receipt.fromJson(Map<String, dynamic> json) => Receipt(
        receiptNumber: json['receipt_number'] as String? ?? '',
        status: json['status'] as String? ?? '',
        kind: json['kind'] as String? ?? '',
        description: json['description'] as String? ?? '',
        paidAt: DateTime.tryParse(json['paid_at'] as String? ?? ''),
        workerName: json['worker_name'] as String? ?? '',
        residentName: json['resident_name'] as String? ?? '',
        flat: json['flat'] as String? ?? '',
        amountDisplay: json['amount_display'] as String? ?? '',
        tipDisplay: json['tip_display'] as String? ?? '',
        totalDisplay: json['total_display'] as String? ?? '',
        netDisplay: json['net_display'] as String? ?? '',
        tipPaise: json['tip_paise'] as int? ?? 0,
        refundedPaise: json['refunded_paise'] as int? ?? 0,
        gatewayPaymentId: json['gateway_payment_id'] as String? ?? '',
      );
}

/// One line on a monthly statement.
class SummaryLine {
  const SummaryLine({
    required this.paymentId,
    required this.date,
    required this.receiptNumber,
    required this.description,
    required this.netDisplay,
    this.netPaise = 0,
  });

  /// What addresses the payment. [receiptNumber] is for a human to read and is
  /// not a usable identifier — opening a receipt needs this.
  final String paymentId;
  final DateTime date;
  final String receiptNumber;
  final String description;
  final String netDisplay;
  final int netPaise;

  factory SummaryLine.fromJson(Map<String, dynamic> json) => SummaryLine(
        paymentId: json['payment_id']?.toString() ?? '',
        date: DateTime.parse(json['date'] as String),
        receiptNumber: json['receipt_number'] as String? ?? '',
        description: json['description'] as String? ?? '',
        netDisplay: json['net_display'] as String? ?? '',
        netPaise: json['net_paise'] as int? ?? 0,
      );
}

/// A worker's month (Module 8.3).
class MonthlySummary {
  const MonthlySummary({
    required this.year,
    required this.month,
    required this.monthName,
    required this.totalDisplay,
    this.workerName = '',
    this.societyName = '',
    this.paymentCount = 0,
    this.totalPaise = 0,
    this.tipsPaise = 0,
    this.tipsDisplay = '',
    this.lines = const [],
  });

  final int year;
  final int month;
  final String monthName;
  final String workerName;
  final String societyName;
  final int paymentCount;
  final int totalPaise;
  final String totalDisplay;
  final int tipsPaise;
  final String tipsDisplay;
  final List<SummaryLine> lines;

  bool get isEmpty => paymentCount == 0;
  bool get hasTips => tipsPaise > 0;

  factory MonthlySummary.fromJson(Map<String, dynamic> json) => MonthlySummary(
        year: json['year'] as int? ?? 0,
        month: json['month'] as int? ?? 0,
        monthName: json['month_name'] as String? ?? '',
        workerName: json['worker_name'] as String? ?? '',
        societyName: json['society_name'] as String? ?? '',
        paymentCount: json['payment_count'] as int? ?? 0,
        totalPaise: json['total_paise'] as int? ?? 0,
        totalDisplay: json['total_display'] as String? ?? '',
        tipsPaise: json['tips_paise'] as int? ?? 0,
        tipsDisplay: json['tips_display'] as String? ?? '',
        lines: ((json['lines'] as List?) ?? const [])
            .map((row) => SummaryLine.fromJson(row as Map<String, dynamic>))
            .toList(),
      );
}

/// Module 8.5 — how a same-day replacement is paid.
class ReplacementSplit {
  const ReplacementSplit({
    required this.replacementSharePercent,
    required this.originalSharePercent,
    this.note = '',
    this.isCustomised = false,
  });

  final int replacementSharePercent;
  final int originalSharePercent;
  final String note;

  /// False means nothing was agreed and the default applies: the replacement
  /// is paid in full.
  final bool isCustomised;

  factory ReplacementSplit.fromJson(Map<String, dynamic> json) =>
      ReplacementSplit(
        replacementSharePercent:
            json['replacement_share_percent'] as int? ?? 100,
        originalSharePercent: json['original_share_percent'] as int? ?? 0,
        note: json['note'] as String? ?? '',
        isCustomised: json['is_customised'] as bool? ?? false,
      );
}

enum DisputeReason {
  notPaid('not_paid', 'The payment never arrived'),
  wrongAmount('wrong_amount', 'The amount is wrong'),
  hoursDisputed('hours_disputed', 'The hours worked are disputed'),
  notProvided('not_provided', 'The service was not provided'),
  other('other', 'Something else');

  const DisputeReason(this.wireValue, this.label);

  final String wireValue;
  final String label;

  static DisputeReason fromWire(String? value) =>
      DisputeReason.values.firstWhere(
        (r) => r.wireValue == value,
        orElse: () => DisputeReason.other,
      );
}

/// Module 8.6 — a raised dispute, on its way to the administrator's queue.
class PaymentDispute {
  const PaymentDispute({
    required this.id,
    required this.reason,
    required this.description,
    required this.status,
    this.receiptNumber = '',
    this.raisedByName = '',
    this.isOpen = true,
    this.resolution = '',
    this.createdAt,
  });

  final int id;
  final DisputeReason reason;
  final String description;
  final String status;
  final String receiptNumber;
  final String raisedByName;
  final bool isOpen;
  final String resolution;
  final DateTime? createdAt;

  factory PaymentDispute.fromJson(Map<String, dynamic> json) => PaymentDispute(
        id: json['id'] as int,
        reason: DisputeReason.fromWire(json['reason'] as String?),
        description: json['description'] as String? ?? '',
        status: json['status'] as String? ?? 'open',
        receiptNumber: json['receipt_number'] as String? ?? '',
        raisedByName: json['raised_by_name'] as String? ?? '',
        isOpen: json['is_open'] as bool? ?? true,
        resolution: json['resolution'] as String? ?? '',
        createdAt: DateTime.tryParse(json['created_at'] as String? ?? ''),
      );
}
