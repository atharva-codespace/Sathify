import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../data/models/payment_models.dart';
import '../../data/repositories/payment_repository.dart';

final paymentRepositoryProvider =
    Provider<PaymentRepository>((ref) => PaymentRepository());

/// Module 8.2 — the ledger, scoped by the server to whoever is asking.
final paymentsProvider = FutureProvider.autoDispose<List<Payment>>(
  (ref) => ref.read(paymentRepositoryProvider).fetchPayments(),
);

/// Payments a resident still owes. The screen leads with these.
final unpaidPaymentsProvider = FutureProvider.autoDispose<List<Payment>>(
  (ref) async {
    final payments = await ref.watch(paymentsProvider.future);
    return payments.where((payment) => payment.isPayable).toList();
  },
);

/// Module 8.9 — the Razorpay-hosted UPI QR for one payment.
///
/// Keyed on the payment id and autoDisposed: the code is single-use and locked
/// to that payment's exact amount, so it must never be shown for another one.
final upiQrProvider = FutureProvider.autoDispose.family<UpiQr, String>(
  (ref, paymentId) => ref.read(paymentRepositoryProvider).fetchUpiQr(paymentId),
);

/// Module 8.3 — one transaction's receipt.
final receiptProvider = FutureProvider.autoDispose.family<Receipt, String>(
  (ref, paymentId) =>
      ref.read(paymentRepositoryProvider).fetchReceipt(paymentId),
);

/// Which month the earnings screen is showing. Held separately so changing it
/// refetches without the screen orchestrating it.
final summaryMonthProvider = StateProvider.autoDispose<DateTime>((ref) {
  final now = DateTime.now();
  return DateTime(now.year, now.month);
});

/// Module 8.3 — a worker's monthly statement.
final monthlySummaryProvider = FutureProvider.autoDispose<MonthlySummary>(
  (ref) {
    final month = ref.watch(summaryMonthProvider);
    return ref
        .read(paymentRepositoryProvider)
        .fetchMonthlySummary(year: month.year, month: month.month);
  },
);

/// Module 8.5 — the replacement pay rule for one engagement.
final replacementSplitProvider =
    FutureProvider.autoDispose.family<ReplacementSplit, int>(
  (ref, engagementId) =>
      ref.read(paymentRepositoryProvider).fetchReplacementSplit(engagementId),
);

/// Module 8.6 — disputes the caller is party to, including ones raised against
/// them: being disputed without being told would be worse than useless.
final paymentDisputesProvider =
    FutureProvider.autoDispose<List<PaymentDispute>>(
  (ref) => ref.read(paymentRepositoryProvider).fetchDisputes(),
);

/// Refreshes everything a payment action could have changed.
///
/// The summary is invalidated alongside the ledger because settling a payment
/// changes what a worker has earned this month, and a stale earnings figure is
/// the one number nobody forgives being wrong.
void invalidatePayments(WidgetRef ref) {
  ref.invalidate(paymentsProvider);
  ref.invalidate(monthlySummaryProvider);
  ref.invalidate(paymentDisputesProvider);
}
