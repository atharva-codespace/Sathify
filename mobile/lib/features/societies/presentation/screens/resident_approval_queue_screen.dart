import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../../core/errors/api_exception.dart';
import '../../../../shared/design_system.dart';
import '../../data/models/society_models.dart';
import '../providers/society_provider.dart';

/// Module 2.3 — the administrator's resident approval queue.
///
/// Approving here is what grants platform access, so each row shows the
/// evidence an administrator needs to decide: who, which flat, what
/// relationship, and whether proof of residence was attached.
class ResidentApprovalQueueScreen extends ConsumerWidget {
  const ResidentApprovalQueueScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final pending = ref.watch(pendingResidentsProvider);

    return Scaffold(
      appBar: AppBar(title: const Text('Pending residents')),
      body: pending.when(
        loading: () => const AppSkeletonList(),
        error: (error, _) => AppErrorState(
          message: error is ApiException
              ? error.message
              : 'Could not load the queue.',
          onRetry: () => ref.invalidate(pendingResidentsProvider),
        ),
        data: (residents) {
          if (residents.isEmpty) {
            return const _EmptyQueue();
          }
          return RefreshIndicator(
            onRefresh: () async => ref.invalidate(pendingResidentsProvider),
            child: ListView.builder(
              padding: const EdgeInsets.symmetric(vertical: 8),
              itemCount: residents.length,
              itemBuilder: (context, index) =>
                  _ResidentCard(resident: residents[index]),
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
      icon: Icons.inbox_outlined,
      title: 'Nothing waiting',
      message: 'All residents have been reviewed.',
    );
  }
}

class _ResidentCard extends ConsumerStatefulWidget {
  const _ResidentCard({required this.resident});

  final ResidentProfile resident;

  @override
  ConsumerState<_ResidentCard> createState() => _ResidentCardState();
}

class _ResidentCardState extends ConsumerState<_ResidentCard> {
  bool _isBusy = false;

  Future<void> _decide({required bool approve}) async {
    // Captured before the first await: the reason dialog is an async gap, and
    // the invalidate further down removes this card from the list.
    final messenger = ScaffoldMessenger.of(context);

    String? reason;

    if (!approve) {
      // The server requires a reason on rejection so the resident knows what to
      // correct; collect it before calling rather than failing validation.
      reason = await showDialog<String>(
        context: context,
        builder: (_) => const _RejectionReasonDialog(),
      );
      if (reason == null || reason.trim().isEmpty) return;
    }

    setState(() => _isBusy = true);
    try {
      await ref.read(societyRepositoryProvider).decideResident(
            residentId: widget.resident.id,
            approve: approve,
            rejectionReason: reason,
          );
      if (!mounted) return;
      ref.invalidate(pendingResidentsProvider);
      messenger.showSnackBar(
        SnackBar(
          content: Text(
            approve
                ? '${widget.resident.fullName} approved'
                : '${widget.resident.fullName} rejected',
          ),
        ),
      );
    } on ApiException catch (error) {
      messenger.showSnackBar(SnackBar(content: Text(error.message)));
    } catch (error, stackTrace) {
      debugPrint('Resident decision failed: $error\n$stackTrace');
      messenger.showSnackBar(
        const SnackBar(content: Text('Could not save that. Please try again.')),
      );
    } finally {
      // The same missing reset as the worker approval queue: only the error
      // branch cleared it, so a successful decision left the card showing a
      // spinner in place of its buttons for ever. See that screen for why
      // invalidating the list does not stand in for this.
      if (mounted) setState(() => _isBusy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final resident = widget.resident;
    final theme = Theme.of(context);

    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                CircleAvatar(
                  backgroundColor: theme.colorScheme.primaryContainer,
                  child: Text(
                    resident.fullName.isNotEmpty
                        ? resident.fullName[0].toUpperCase()
                        : '?',
                  ),
                ),
                const SizedBox(width: 12),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                        resident.fullName,
                        style: theme.textTheme.titleMedium
                            ?.copyWith(fontWeight: FontWeight.w600),
                      ),
                      Text(
                        resident.phoneNumber,
                        style: theme.textTheme.bodySmall,
                      ),
                    ],
                  ),
                ),
                Chip(
                  label: Text(resident.flatLabel),
                  visualDensity: VisualDensity.compact,
                ),
              ],
            ),
            const SizedBox(height: 12),
            Wrap(
              spacing: 8,
              runSpacing: 4,
              children: [
                _Tag(
                  icon: Icons.badge_outlined,
                  label: resident.relationship.label,
                ),
                if (resident.householdSize > 1)
                  _Tag(
                    icon: Icons.groups_outlined,
                    label: '${resident.householdSize} in household',
                  ),
                _Tag(
                  icon: resident.proofDocumentUrl != null
                      ? Icons.verified_outlined
                      : Icons.warning_amber_outlined,
                  label: resident.proofDocumentUrl != null
                      ? 'Proof attached'
                      : 'No proof attached',
                  colour: resident.proofDocumentUrl != null
                      ? AppColors.success
                      : AppColors.warning,
                ),
              ],
            ),
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
                      onPressed: () => _decide(approve: true),
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

class _Tag extends StatelessWidget {
  const _Tag({required this.icon, required this.label, this.colour});

  final IconData icon;
  final String label;
  final Color? colour;

  @override
  Widget build(BuildContext context) {
    final effective = colour ?? AppColors.textSecondary;
    return Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        Icon(icon, size: 16, color: effective),
        const SizedBox(width: 4),
        Text(label, style: TextStyle(fontSize: 13, color: effective)),
      ],
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
          hintText: 'e.g. Proof of residence is unreadable',
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
