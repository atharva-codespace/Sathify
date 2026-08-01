import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../../core/errors/api_exception.dart';
import '../../../../shared/design_system.dart';
import '../../data/models/worker_models.dart';
import '../providers/worker_provider.dart';

/// Module 3.5 — the administrator's worker approval queue.
///
/// Approving here is what makes a worker visible to Module 4's search and
/// admissible at the gate, so each row carries the evidence needed to decide:
/// the photo, what OCR read from the card, whether the checksum passed, any
/// duplicate registration, and every reason approval is currently blocked.
///
/// The blockers come from the server as a list rather than being re-derived
/// here, so the button is disabled for exactly the reasons the server would
/// refuse — no guessing, and no refused taps that explain nothing.
class WorkerApprovalQueueScreen extends ConsumerWidget {
  const WorkerApprovalQueueScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final pending = ref.watch(pendingWorkersProvider);

    return Scaffold(
      appBar: AppBar(title: const Text('Workers to verify')),
      body: pending.when(
        loading: () => const AppSkeletonList(),
        error: (error, _) => AppErrorState(
          message: error is ApiException
              ? error.message
              : 'Could not load the queue.',
          onRetry: () => ref.invalidate(pendingWorkersProvider),
        ),
        data: (workers) {
          if (workers.isEmpty) {
            return const _EmptyQueue();
          }
          return RefreshIndicator(
            onRefresh: () async => ref.invalidate(pendingWorkersProvider),
            child: ListView.builder(
              padding: const EdgeInsets.symmetric(vertical: 8),
              itemCount: workers.length,
              itemBuilder: (context, index) =>
                  _WorkerCard(worker: workers[index]),
            ),
          );
        },
      ),
    );
  }
}

class _EmptyQueue extends StatelessWidget {
  const _EmptyQueue();

  @override
  Widget build(BuildContext context) {
    return const AppEmptyState(
      icon: Icons.how_to_reg_outlined,
      title: 'Nothing waiting',
      message: 'Every worker has been reviewed.',
    );
  }
}

class _WorkerCard extends ConsumerStatefulWidget {
  const _WorkerCard({required this.worker});

  final WorkerReview worker;

  @override
  ConsumerState<_WorkerCard> createState() => _WorkerCardState();
}

class _WorkerCardState extends ConsumerState<_WorkerCard> {
  bool _isBusy = false;

  Future<void> _decide({required bool approve}) async {
    String reason = '';

    if (!approve) {
      // The server requires a reason on rejection so the worker knows what to
      // correct; collect it first rather than failing validation.
      final entered = await showDialog<String>(
        context: context,
        builder: (_) => const _RejectionReasonDialog(),
      );
      if (entered == null || entered.trim().isEmpty) return;
      reason = entered.trim();
    }

    setState(() => _isBusy = true);
    try {
      await ref.read(workerRepositoryProvider).decideWorker(
            workerId: widget.worker.id,
            approve: approve,
            rejectionReason: reason,
          );
      if (!mounted) return;
      ref.invalidate(pendingWorkersProvider);
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(
          content: Text(
            approve
                ? '${widget.worker.fullName} approved'
                : '${widget.worker.fullName} rejected',
          ),
        ),
      );
    } on ApiException catch (error) {
      if (!mounted) return;
      setState(() => _isBusy = false);
      ScaffoldMessenger.of(context)
          .showSnackBar(SnackBar(content: Text(error.message)));
    }
  }

  @override
  Widget build(BuildContext context) {
    final worker = widget.worker;
    final theme = Theme.of(context);
    final kyc = worker.latestKyc;

    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                CircleAvatar(
                  radius: 28,
                  backgroundColor: theme.colorScheme.primaryContainer,
                  backgroundImage: worker.photoUrl != null
                      ? NetworkImage(worker.photoUrl!)
                      : null,
                  child: worker.photoUrl == null
                      ? const Icon(Icons.person_off_outlined)
                      : null,
                ),
                const SizedBox(width: 12),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                        worker.fullName.isEmpty ? 'Unnamed' : worker.fullName,
                        style: theme.textTheme.titleMedium
                            ?.copyWith(fontWeight: FontWeight.w600),
                      ),
                      Text(
                        worker.phoneNumber,
                        style: theme.textTheme.bodySmall,
                      ),
                    ],
                  ),
                ),
              ],
            ),
            const SizedBox(height: 12),
            if (worker.serviceTypes.isNotEmpty)
              Wrap(
                spacing: 6,
                runSpacing: 4,
                children: worker.serviceTypes
                    .map(
                      (service) => Chip(
                        label: Text(service.name),
                        visualDensity: VisualDensity.compact,
                        materialTapTargetSize: MaterialTapTargetSize.shrinkWrap,
                      ),
                    )
                    .toList(),
              ),
            if (kyc != null) ...[
              const SizedBox(height: 12),
              _KycEvidence(kyc: kyc),
            ] else ...[
              const SizedBox(height: 12),
              const _Verdict(
                ok: false,
                label: 'No Aadhaar document uploaded',
              ),
            ],
            if (worker.duplicateOf != null) ...[
              const SizedBox(height: 12),
              _DuplicateWarning(duplicate: worker.duplicateOf!),
            ],
            if (worker.approvalBlockers.isNotEmpty) ...[
              const SizedBox(height: 12),
              _Blockers(reasons: worker.approvalBlockers),
            ],
            const SizedBox(height: 16),
            if (_isBusy)
              const Center(child: CircularProgressIndicator())
            else
              Row(
                children: [
                  Expanded(
                    child: OutlinedButton.icon(
                      onPressed: () => _decide(approve: false),
                      icon: const Icon(Icons.close),
                      label: const Text('Reject'),
                      style: OutlinedButton.styleFrom(
                        foregroundColor: AppColors.danger,
                        minimumSize: const Size.fromHeight(48),
                      ),
                    ),
                  ),
                  const SizedBox(width: 12),
                  Expanded(
                    child: ElevatedButton.icon(
                      // Disabled for exactly the reasons the server would
                      // refuse, which are listed above the button.
                      onPressed: worker.canApprove
                          ? () => _decide(approve: true)
                          : null,
                      icon: const Icon(Icons.check),
                      label: const Text('Approve'),
                      style: ElevatedButton.styleFrom(
                        minimumSize: const Size.fromHeight(48),
                      ),
                    ),
                  ),
                ],
              ),
          ],
        ),
      ),
    );
  }
}

class _KycEvidence extends StatelessWidget {
  const _KycEvidence({required this.kyc});

  final KycDocument kyc;

  @override
  Widget build(BuildContext context) {
    return Container(
      width: double.infinity,
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(
        color: Colors.black.withValues(alpha: 0.03),
        borderRadius: BorderRadius.circular(10),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const Text(
            'From their Aadhaar card',
            style: TextStyle(fontWeight: FontWeight.w700, fontSize: 13),
          ),
          const SizedBox(height: 8),
          _Row(label: 'Name', value: kyc.extractedName),
          _Row(label: 'Date of birth', value: kyc.extractedDob),
          _Row(label: 'Aadhaar', value: kyc.maskedAadhaar),
          if (kyc.extractedAge != null)
            _Row(label: 'Age', value: '${kyc.extractedAge}'),
          const SizedBox(height: 8),
          _Verdict(
            ok: kyc.aadhaarChecksumValid,
            label: kyc.aadhaarChecksumValid
                ? 'Aadhaar checksum valid'
                : 'Aadhaar checksum FAILED',
          ),
          if (kyc.isMinor)
            const _Verdict(
              ok: false,
              label: 'Under 18 — automatic rejection, cannot be overridden',
            ),
          if (kyc.hasMismatch)
            const _Verdict(
              ok: false,
              label: 'Card does not match what they typed at registration',
            ),
          if (kyc.lowConfidenceFields.isNotEmpty)
            _Verdict(
              ok: false,
              label: 'Read poorly: ${kyc.lowConfidenceFields.join(', ')}',
            ),
          if (kyc.failed)
            _Verdict(
              ok: false,
              label: kyc.errorMessage.isEmpty
                  ? 'The document could not be read'
                  : kyc.errorMessage,
            ),
        ],
      ),
    );
  }
}

class _Row extends StatelessWidget {
  const _Row({required this.label, required this.value});

  final String label;
  final String value;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 2),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          SizedBox(
            width: 110,
            child: Text(
              label,
              style:
                  const TextStyle(fontSize: 13, color: AppColors.textSecondary),
            ),
          ),
          Expanded(
            child: Text(
              value.isEmpty ? '—' : value,
              style: const TextStyle(fontSize: 13, fontWeight: FontWeight.w600),
            ),
          ),
        ],
      ),
    );
  }
}

class _Verdict extends StatelessWidget {
  const _Verdict({required this.ok, required this.label});

  final bool ok;
  final String label;

  @override
  Widget build(BuildContext context) {
    final colour = ok ? AppColors.success : AppColors.danger;
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 2),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Icon(
            ok ? Icons.check_circle : Icons.error_outline,
            size: 16,
            color: colour,
          ),
          const SizedBox(width: 6),
          Expanded(
            child: Text(label, style: TextStyle(fontSize: 13, color: colour)),
          ),
        ],
      ),
    );
  }
}

/// The same person moving societies looks identical to a fraudulent double
/// registration, so this is shown for a human to judge, never auto-blocked.
class _DuplicateWarning extends StatelessWidget {
  const _DuplicateWarning({required this.duplicate});

  final DuplicateWorker duplicate;

  @override
  Widget build(BuildContext context) {
    return Container(
      width: double.infinity,
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(
        color: AppColors.warning.withValues(alpha: 0.12),
        borderRadius: BorderRadius.circular(10),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              const Icon(
                Icons.copy_all_outlined,
                size: 18,
                color: AppColors.warning,
              ),
              const SizedBox(width: 8),
              Text(
                'Same Aadhaar as ${duplicate.name}',
                style: const TextStyle(
                  fontWeight: FontWeight.w700,
                  color: AppColors.warning,
                ),
              ),
            ],
          ),
          const SizedBox(height: 4),
          Text(
            duplicate.society.isEmpty
                ? 'Already registered elsewhere on Sathify.'
                : 'Already registered at ${duplicate.society}.',
            style: const TextStyle(fontSize: 13),
          ),
          const SizedBox(height: 4),
          const Text(
            'This may simply be the same person moving societies. Check before '
            'deciding.',
            style: TextStyle(fontSize: 12.5, color: AppColors.textSecondary),
          ),
        ],
      ),
    );
  }
}

class _Blockers extends StatelessWidget {
  const _Blockers({required this.reasons});

  final List<String> reasons;

  @override
  Widget build(BuildContext context) {
    return Container(
      width: double.infinity,
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(
        color: AppColors.danger.withValues(alpha: 0.08),
        borderRadius: BorderRadius.circular(10),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const Text(
            'Cannot approve yet',
            style:
                TextStyle(fontWeight: FontWeight.w700, color: AppColors.danger),
          ),
          const SizedBox(height: 6),
          ...reasons.map(
            (reason) => Padding(
              padding: const EdgeInsets.only(bottom: 2),
              child: Row(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  const Text('• '),
                  Expanded(
                    child: Text(reason, style: const TextStyle(fontSize: 13)),
                  ),
                ],
              ),
            ),
          ),
        ],
      ),
    );
  }
}

class _RejectionReasonDialog extends StatefulWidget {
  const _RejectionReasonDialog();

  @override
  State<_RejectionReasonDialog> createState() => _RejectionReasonDialogState();
}

class _RejectionReasonDialogState extends State<_RejectionReasonDialog> {
  final _controller = TextEditingController();

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return AlertDialog(
      title: const Text('Why are you rejecting?'),
      content: TextField(
        controller: _controller,
        autofocus: true,
        maxLines: 3,
        decoration: const InputDecoration(
          hintText: 'e.g. The photo of the card is too blurred to read',
        ),
      ),
      actions: [
        TextButton(
          onPressed: () => Navigator.of(context).pop(),
          child: const Text('Cancel'),
        ),
        FilledButton(
          onPressed: () => Navigator.of(context).pop(_controller.text),
          child: const Text('Reject'),
        ),
      ],
    );
  }
}
