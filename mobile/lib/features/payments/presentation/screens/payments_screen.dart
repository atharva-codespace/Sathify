import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../../../core/errors/api_exception.dart';
import '../../../../core/routing/app_router.dart';
import '../../../../shared/design_system.dart';
import '../../../auth/data/models/user_model.dart';
import '../../../auth/presentation/providers/auth_provider.dart';
import '../../data/models/payment_models.dart';
import '../../data/razorpay_checkout.dart';
import '../providers/payment_provider.dart';

/// Module 8.2 — the ledger, and the place a resident actually pays.
///
/// One screen for both sides. A resident sees what they owe first, because that
/// is what they came for; a worker sees what they have been paid, because that
/// is what they came for. The rows are the same rows.
class PaymentsScreen extends ConsumerWidget {
  const PaymentsScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final payments = ref.watch(paymentsProvider);
    final isWorker = ref.watch(authProvider).user?.role == UserRole.worker;

    return Scaffold(
      appBar: AppBar(
        title: Text(isWorker ? 'My earnings' : 'Payments'),
        actions: [
          if (isWorker)
            IconButton(
              tooltip: 'Monthly statement',
              icon: const Icon(Icons.receipt_long),
              onPressed: () => context.push(Routes.earnings),
            ),
        ],
      ),
      body: payments.when(
        loading: () => const AppSkeletonList(),
        error: (error, _) => AppErrorState(
          message: error is ApiException
              ? error.message
              : 'Could not load payments.',
          onRetry: () => ref.invalidate(paymentsProvider),
        ),
        data: (items) {
          if (items.isEmpty) {
            return _Empty(isWorker: isWorker);
          }

          final outstanding = items.where((p) => p.isPayable).toList();
          final settled = items.where((p) => !p.isPayable).toList();

          return RefreshIndicator(
            onRefresh: () async => invalidatePayments(ref),
            child: ListView(
              padding: const EdgeInsets.only(bottom: 24),
              children: [
                if (outstanding.isNotEmpty && !isWorker) ...[
                  const _SectionHeader('To pay'),
                  ...outstanding.map(
                    (payment) =>
                        _PaymentCard(payment: payment, isWorker: isWorker),
                  ),
                ],
                if (settled.isNotEmpty) ...[
                  _SectionHeader(isWorker ? 'Received' : 'Paid'),
                  ...settled.map(
                    (payment) =>
                        _PaymentCard(payment: payment, isWorker: isWorker),
                  ),
                ],
                // A worker's outstanding rows are shown last: they cannot act
                // on them, and leading with "unpaid" would read as a complaint
                // rather than as information.
                if (outstanding.isNotEmpty && isWorker) ...[
                  const _SectionHeader('Not yet paid'),
                  ...outstanding.map(
                    (payment) =>
                        _PaymentCard(payment: payment, isWorker: isWorker),
                  ),
                ],
              ],
            ),
          );
        },
      ),
    );
  }
}

class _SectionHeader extends StatelessWidget {
  const _SectionHeader(this.title);

  final String title;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.fromLTRB(16, 20, 16, 6),
      child: Text(
        title,
        style: Theme.of(context)
            .textTheme
            .titleMedium
            ?.copyWith(fontWeight: FontWeight.w700),
      ),
    );
  }
}

class _PaymentCard extends ConsumerStatefulWidget {
  const _PaymentCard({required this.payment, required this.isWorker});

  final Payment payment;
  final bool isWorker;

  @override
  ConsumerState<_PaymentCard> createState() => _PaymentCardState();
}

class _PaymentCardState extends ConsumerState<_PaymentCard> {
  final _checkout = RazorpayCheckout();
  bool _isBusy = false;

  @override
  void dispose() {
    _checkout.dispose();
    super.dispose();
  }

  /// Module 8.1 — open the order, run checkout, hand the signature back.
  Future<void> _pay() async {
    setState(() => _isBusy = true);
    final repository = ref.read(paymentRepositoryProvider);

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
        _tell(outcome.message);
        return;
      }

      // The signature is what settles it — the app's own "success" does not.
      await repository.confirmCheckout(
        widget.payment.id,
        razorpayPaymentId: outcome.razorpayPaymentId,
        signature: outcome.signature,
      );

      if (!mounted) return;
      invalidatePayments(ref);
      _tell('Payment confirmed.');
    } on ApiException catch (error) {
      if (!mounted) return;
      setState(() => _isBusy = false);
      _tell(error.message);
    }
  }

  void _tell(String message) {
    ScaffoldMessenger.of(context)
        .showSnackBar(SnackBar(content: Text(message)));
  }

  Color get _statusColour {
    switch (widget.payment.status) {
      case PaymentStatus.paid:
        return AppColors.success;
      case PaymentStatus.failed:
        return AppColors.danger;
      case PaymentStatus.refunded:
      case PaymentStatus.cancelled:
        return AppColors.textSecondary;
      case PaymentStatus.created:
      case PaymentStatus.pending:
        return AppColors.warning;
    }
  }

  @override
  Widget build(BuildContext context) {
    final payment = widget.payment;
    final theme = Theme.of(context);
    final counterparty =
        widget.isWorker ? payment.residentName : payment.workerName;

    return Card(
      child: InkWell(
        borderRadius: BorderRadius.circular(14),
        onTap: () => context.push(Routes.receiptPath(payment.id)),
        child: Padding(
          padding: const EdgeInsets.all(16),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Row(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text(
                          payment.kind.label,
                          style: theme.textTheme.titleMedium
                              ?.copyWith(fontWeight: FontWeight.w600),
                        ),
                        const SizedBox(height: 2),
                        Text(
                          counterparty.isEmpty ? '—' : counterparty,
                          style: theme.textTheme.bodySmall,
                        ),
                      ],
                    ),
                  ),
                  Column(
                    crossAxisAlignment: CrossAxisAlignment.end,
                    children: [
                      Text(
                        // Server-rendered — never formatted locally.
                        payment.totalDisplay,
                        style: theme.textTheme.titleMedium
                            ?.copyWith(fontWeight: FontWeight.w700),
                      ),
                      const SizedBox(height: 4),
                      Container(
                        padding: const EdgeInsets.symmetric(
                          horizontal: 8,
                          vertical: 2,
                        ),
                        decoration: BoxDecoration(
                          color: _statusColour.withValues(alpha: 0.12),
                          borderRadius: BorderRadius.circular(20),
                        ),
                        child: Text(
                          payment.status.label,
                          style: TextStyle(
                            color: _statusColour,
                            fontSize: 11,
                            fontWeight: FontWeight.w700,
                          ),
                        ),
                      ),
                    ],
                  ),
                ],
              ),
              const SizedBox(height: 8),
              Text(
                payment.receiptNumber,
                style: const TextStyle(
                  fontSize: 12,
                  color: AppColors.textSecondary,
                ),
              ),
              if (payment.hasTip) ...[
                const SizedBox(height: 4),
                Text(
                  'Includes a tip of ${formatPaise(payment.tipPaise)}',
                  style:
                      const TextStyle(fontSize: 12.5, color: AppColors.success),
                ),
              ],
              if (payment.wasRefunded) ...[
                const SizedBox(height: 4),
                Text(
                  '${formatPaise(payment.refundedPaise)} refunded',
                  style:
                      const TextStyle(fontSize: 12.5, color: AppColors.danger),
                ),
              ],
              if (payment.failureReason.isNotEmpty) ...[
                const SizedBox(height: 4),
                Text(
                  payment.failureReason,
                  style:
                      const TextStyle(fontSize: 12.5, color: AppColors.danger),
                ),
              ],
              if (!widget.isWorker && payment.isPayable) ...[
                const SizedBox(height: 14),
                if (_isBusy)
                  const Center(child: CircularProgressIndicator())
                else
                  ElevatedButton.icon(
                    onPressed: _pay,
                    icon: const Icon(Icons.lock_outline),
                    label: Text('Pay ${payment.totalDisplay}'),
                    style: ElevatedButton.styleFrom(
                      minimumSize: const Size.fromHeight(50),
                    ),
                  ),
              ],
            ],
          ),
        ),
      ),
    );
  }
}

class _Empty extends StatelessWidget {
  const _Empty({required this.isWorker});

  final bool isWorker;

  @override
  Widget build(BuildContext context) {
    return AppEmptyState(
      icon: Icons.receipt_long_outlined,
      title: 'Nothing yet',
      message: isWorker
                  ? 'Payments from residents will appear here.'
                  : 'Payments you owe will appear here once work is done.',
    );
  }
}
