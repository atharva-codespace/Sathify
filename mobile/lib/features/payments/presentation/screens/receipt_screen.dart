import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../../core/errors/api_exception.dart';
import '../../../../shared/design_system.dart';
import '../../data/models/payment_models.dart';
import '../providers/payment_provider.dart';

/// Module 8.3 — a digital receipt, issued to both parties.
///
/// Also where a dispute is raised (Module 8.6): the receipt is where someone
/// looks when they think a payment is wrong, so the action belongs here rather
/// than buried in a settings menu.
class ReceiptScreen extends ConsumerWidget {
  const ReceiptScreen({required this.paymentId, super.key});

  final String paymentId;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final receipt = ref.watch(receiptProvider(paymentId));

    return Scaffold(
      appBar: AppBar(title: const Text('Receipt')),
      body: receipt.when(
        loading: () => const AppSkeletonList(),
        error: (error, _) => AppErrorState(
          message: error is ApiException
              ? error.message
              : 'Could not load the receipt.',
          onRetry: () => ref.invalidate(receiptProvider(paymentId)),
        ),
        data: (data) => _Receipt(receipt: data, paymentId: paymentId),
      ),
    );
  }
}

class _Receipt extends ConsumerWidget {
  const _Receipt({required this.receipt, required this.paymentId});

  final Receipt receipt;
  final String paymentId;

  Future<void> _raiseDispute(BuildContext context, WidgetRef ref) async {
    final result = await showModalBottomSheet<_DisputeDraft>(
      context: context,
      isScrollControlled: true,
      builder: (_) => const _DisputeSheet(),
    );
    if (result == null) return;

    try {
      await ref.read(paymentRepositoryProvider).raiseDispute(
            paymentId,
            reason: result.reason,
            description: result.description,
          );
      if (!context.mounted) return;
      invalidatePayments(ref);
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(
          content:
              Text('Raised. Your society administrator will look into it.'),
        ),
      );
    } on ApiException catch (error) {
      if (!context.mounted) return;
      ScaffoldMessenger.of(context)
          .showSnackBar(SnackBar(content: Text(error.message)));
    }
  }

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final theme = Theme.of(context);

    return ListView(
      padding: const EdgeInsets.all(20),
      children: [
        Center(
          child: Column(
            children: [
              Icon(
                receipt.status == 'paid'
                    ? Icons.check_circle
                    : Icons.pending_outlined,
                size: 56,
                color: receipt.status == 'paid'
                    ? AppColors.success
                    : AppColors.warning,
              ),
              const SizedBox(height: 12),
              Text(
                receipt.totalDisplay,
                style: theme.textTheme.headlineMedium
                    ?.copyWith(fontWeight: FontWeight.w700),
              ),
              const SizedBox(height: 4),
              Text(
                receipt.description,
                style: const TextStyle(color: AppColors.textSecondary),
              ),
            ],
          ),
        ),
        const SizedBox(height: 28),
        Card(
          margin: EdgeInsets.zero,
          child: Padding(
            padding: const EdgeInsets.all(16),
            child: Column(
              children: [
                _Row(label: 'Receipt', value: receipt.receiptNumber),
                _Row(label: 'Worker', value: receipt.workerName),
                _Row(label: 'Resident', value: receipt.residentName),
                if (receipt.flat.isNotEmpty)
                  _Row(label: 'Flat', value: receipt.flat),
                if (receipt.paidAt != null)
                  _Row(
                    label: 'Paid on',
                    value: '${receipt.paidAt!.day}/${receipt.paidAt!.month}/'
                        '${receipt.paidAt!.year}',
                  ),
              ],
            ),
          ),
        ),
        const SizedBox(height: 16),
        Card(
          margin: EdgeInsets.zero,
          child: Padding(
            padding: const EdgeInsets.all(16),
            child: Column(
              children: [
                _Row(label: 'Amount', value: receipt.amountDisplay),
                if (receipt.hasTip)
                  _Row(
                    label: 'Tip',
                    value: receipt.tipDisplay,
                    highlight: true,
                  ),
                const Divider(height: 20),
                _Row(label: 'Total', value: receipt.totalDisplay, bold: true),
                if (receipt.wasRefunded) ...[
                  const SizedBox(height: 4),
                  _Row(label: 'After refund', value: receipt.netDisplay),
                ],
              ],
            ),
          ),
        ),
        if (receipt.gatewayPaymentId.isNotEmpty) ...[
          const SizedBox(height: 16),
          Text(
            'Payment reference: ${receipt.gatewayPaymentId}',
            style:
                const TextStyle(fontSize: 12, color: AppColors.textSecondary),
            textAlign: TextAlign.center,
          ),
        ],
        const SizedBox(height: 12),
        const Text(
          'Payments are processed by Razorpay. Sathify never sees your card or '
          'bank details.',
          textAlign: TextAlign.center,
          style: TextStyle(fontSize: 12, color: AppColors.textSecondary),
        ),
        const SizedBox(height: 28),
        OutlinedButton.icon(
          onPressed: () => _raiseDispute(context, ref),
          icon: const Icon(Icons.flag_outlined),
          label: const Text('Something is wrong with this payment'),
          style: OutlinedButton.styleFrom(
            foregroundColor: AppColors.danger,
            minimumSize: const Size.fromHeight(52),
          ),
        ),
      ],
    );
  }
}

class _Row extends StatelessWidget {
  const _Row({
    required this.label,
    required this.value,
    this.bold = false,
    this.highlight = false,
  });

  final String label;
  final String value;
  final bool bold;
  final bool highlight;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 4),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Expanded(
            child: Text(
              label,
              style:
                  const TextStyle(color: AppColors.textSecondary, fontSize: 14),
            ),
          ),
          Text(
            value.isEmpty ? '—' : value,
            style: TextStyle(
              fontWeight: bold ? FontWeight.w700 : FontWeight.w600,
              fontSize: bold ? 16 : 14,
              color: highlight ? AppColors.success : null,
            ),
          ),
        ],
      ),
    );
  }
}

class _DisputeDraft {
  const _DisputeDraft({required this.reason, required this.description});

  final DisputeReason reason;
  final String description;
}

/// Module 8.6 — raising a dispute.
class _DisputeSheet extends StatefulWidget {
  const _DisputeSheet();

  @override
  State<_DisputeSheet> createState() => _DisputeSheetState();
}

class _DisputeSheetState extends State<_DisputeSheet> {
  DisputeReason _reason = DisputeReason.notPaid;
  final _controller = TextEditingController();
  String? _error;

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  void _submit() {
    final description = _controller.text.trim();
    // The server enforces this too; catching it here saves a round trip and
    // explains why up front.
    if (description.length < 10) {
      setState(() => _error = 'Please describe what went wrong.');
      return;
    }
    Navigator.of(context).pop(
      _DisputeDraft(reason: _reason, description: description),
    );
  }

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: EdgeInsets.only(
        left: 20,
        right: 20,
        top: 20,
        bottom: MediaQuery.of(context).viewInsets.bottom + 20,
      ),
      child: SingleChildScrollView(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              'What went wrong?',
              style: Theme.of(context)
                  .textTheme
                  .titleLarge
                  ?.copyWith(fontWeight: FontWeight.w700),
            ),
            const SizedBox(height: 4),
            const Text(
              'Your society administrator will look into this.',
              style: TextStyle(color: AppColors.textSecondary),
            ),
            const SizedBox(height: 16),
            ...DisputeReason.values.map(
              (reason) => RadioListTile<DisputeReason>(
                contentPadding: EdgeInsets.zero,
                value: reason,
                groupValue: _reason,
                onChanged: (value) =>
                    setState(() => _reason = value ?? _reason),
                title: Text(reason.label),
              ),
            ),
            const SizedBox(height: 8),
            TextField(
              controller: _controller,
              maxLines: 4,
              maxLength: 1000,
              decoration: const InputDecoration(
                labelText: 'What happened?',
                hintText: 'A few words so it can be looked into',
              ),
            ),
            if (_error != null)
              Text(_error!, style: const TextStyle(color: AppColors.danger)),
            const SizedBox(height: 12),
            ElevatedButton.icon(
              onPressed: _submit,
              icon: const Icon(Icons.send),
              label: const Text('Raise it'),
            ),
            TextButton(
              onPressed: () => Navigator.of(context).pop(),
              child: const Text('Cancel'),
            ),
          ],
        ),
      ),
    );
  }
}
