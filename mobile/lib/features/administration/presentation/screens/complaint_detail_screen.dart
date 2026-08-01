import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:intl/intl.dart';

import '../../../../core/errors/api_exception.dart';
import '../../../../shared/design_system.dart';
import '../../../auth/data/models/user_model.dart';
import '../../../auth/presentation/providers/auth_provider.dart';
import '../../data/models/admin_models.dart';
import '../providers/admin_provider.dart';
import '../widgets/sla_chip.dart';

/// Module 11.3 — one complaint and its history.
///
/// -----------------------------------------------------------------------
/// THE HISTORY IS THE POINT
/// -----------------------------------------------------------------------
/// SRS 5.5 requires complaint actions to be retained, and nothing here offers
/// to delete or edit an entry. A resolution that was rewritten leaves both
/// versions visible, and an escalation that fired stays on the record even
/// after the complaint is resolved.
///
/// Internal notes are already stripped server-side for anyone who is not an
/// administrator, so this screen renders whatever it is given — it does not
/// re-implement the filter, because two places deciding who sees what is how
/// they end up disagreeing.
class ComplaintDetailScreen extends ConsumerStatefulWidget {
  const ComplaintDetailScreen({super.key, required this.complaintId});

  final int complaintId;

  @override
  ConsumerState<ComplaintDetailScreen> createState() =>
      _ComplaintDetailScreenState();
}

class _ComplaintDetailScreenState extends ConsumerState<ComplaintDetailScreen> {
  final _noteController = TextEditingController();
  bool _internal = false;
  bool _busy = false;

  @override
  void dispose() {
    _noteController.dispose();
    super.dispose();
  }

  void _refresh() {
    ref.invalidate(complaintProvider(widget.complaintId));
    invalidateComplaints(ref);
  }

  Future<void> _run(Future<void> Function() action) async {
    setState(() => _busy = true);
    final messenger = ScaffoldMessenger.of(context);
    try {
      await action();
      _refresh();
    } on ApiException catch (error) {
      messenger.showSnackBar(SnackBar(content: Text(error.message)));
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _addNote(bool isAdmin) async {
    final note = _noteController.text.trim();
    if (note.isEmpty) return;

    await _run(() async {
      await ref.read(adminRepositoryProvider).addNote(
            widget.complaintId,
            note: note,
            isInternal: isAdmin && _internal,
          );
      _noteController.clear();
      if (mounted) setState(() => _internal = false);
    });
  }

  Future<void> _close(ComplaintStatus status) async {
    final resolution = await _askForResolution(status);
    if (resolution == null) return;

    await _run(
      () => ref.read(adminRepositoryProvider).closeComplaint(
            widget.complaintId,
            status: status,
            resolution: resolution,
          ),
    );
  }

  /// A note is required for both outcomes.
  ///
  /// Rejection is the outcome most likely to be disputed, and the person who
  /// raised it is entitled to know why — the server refuses a blank one too, so
  /// this is a courtesy rather than the enforcement.
  Future<String?> _askForResolution(ComplaintStatus status) {
    final controller = TextEditingController();
    final isRejection = status == ComplaintStatus.rejected;

    return showDialog<String>(
      context: context,
      builder: (dialogContext) => AlertDialog(
        title: Text(isRejection ? 'Reject this complaint' : 'Mark as resolved'),
        content: TextField(
          controller: controller,
          maxLines: 4,
          autofocus: true,
          decoration: InputDecoration(
            hintText: isRejection
                ? 'Explain why this is being rejected'
                : 'What was done about it?',
          ),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(dialogContext).pop(),
            child: const Text('Cancel'),
          ),
          FilledButton(
            onPressed: () {
              final text = controller.text.trim();
              if (text.isEmpty) return;
              Navigator.of(dialogContext).pop(text);
            },
            child: Text(isRejection ? 'Reject' : 'Resolve'),
          ),
        ],
      ),
    );
  }

  Future<void> _withdraw() async {
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (dialogContext) => AlertDialog(
        title: const Text('Withdraw this complaint?'),
        content: const Text(
          'It stays on the record as withdrawn — nothing is deleted — and your '
          'administrator will stop working on it.',
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(dialogContext).pop(false),
            child: const Text('Keep it open'),
          ),
          FilledButton(
            onPressed: () => Navigator.of(dialogContext).pop(true),
            child: const Text('Withdraw'),
          ),
        ],
      ),
    );

    if (confirmed ?? false) {
      await _run(
        () => ref
            .read(adminRepositoryProvider)
            .withdrawComplaint(widget.complaintId),
      );
    }
  }

  @override
  Widget build(BuildContext context) {
    final user = ref.watch(authProvider).user;
    final isAdmin = user?.role == UserRole.societyAdmin;
    final complaint = ref.watch(complaintProvider(widget.complaintId));

    return Scaffold(
      appBar: AppBar(title: const Text('Complaint')),
      body: complaint.when(
        loading: () => const AppSkeletonList(),
        error: (error, _) => Center(
          child: Padding(
            padding: const EdgeInsets.all(24),
            child: Text(
              error is ApiException
                  ? error.message
                  : 'Could not load this complaint.',
              textAlign: TextAlign.center,
            ),
          ),
        ),
        data: (item) => ListView(
          padding: const EdgeInsets.all(16),
          children: [
            _Header(complaint: item),
            const SizedBox(height: 16),
            _Body(complaint: item),
            const SizedBox(height: 16),
            _History(updates: item.updates),
            const SizedBox(height: 16),
            if (item.isOpen) ...[
              _NoteComposer(
                controller: _noteController,
                isAdmin: isAdmin,
                internal: _internal,
                busy: _busy,
                onInternalChanged: (value) => setState(() => _internal = value),
                onSend: () => _addNote(isAdmin),
              ),
              const SizedBox(height: 16),
              _Actions(
                complaint: item,
                isAdmin: isAdmin,
                isRaiser: user != null && item.raisedById == user.id,
                busy: _busy,
                onStart: () => _run(
                  () => ref
                      .read(adminRepositoryProvider)
                      .startComplaint(widget.complaintId),
                ),
                onResolve: () => _close(ComplaintStatus.resolved),
                onReject: () => _close(ComplaintStatus.rejected),
                onWithdraw: _withdraw,
              ),
            ] else
              _Outcome(complaint: item),
          ],
        ),
      ),
    );
  }
}

class _Header extends StatelessWidget {
  const _Header({required this.complaint});

  final Complaint complaint;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Expanded(
              child: Text(
                complaint.subject,
                style: theme.textTheme.titleLarge
                    ?.copyWith(fontWeight: FontWeight.w700),
              ),
            ),
            const SizedBox(width: 8),
            SlaChip(complaint: complaint),
          ],
        ),
        const SizedBox(height: 6),
        Text(
          '${complaint.reference}  ·  ${complaint.category.label}',
          style: const TextStyle(color: AppColors.textSecondary),
        ),
        if (complaint.wasEscalated) ...[
          const SizedBox(height: 10),
          Row(
            children: [
              const Icon(Icons.trending_up, size: 16, color: AppColors.danger),
              const SizedBox(width: 6),
              Text(
                'Escalated ${_formatDate(complaint.escalatedAt)} — no answer '
                'within the response window',
                style: const TextStyle(fontSize: 12, color: AppColors.danger),
              ),
            ],
          ),
        ],
      ],
    );
  }
}

class _Body extends StatelessWidget {
  const _Body({required this.complaint});

  final Complaint complaint;

  @override
  Widget build(BuildContext context) {
    return Card(
      margin: EdgeInsets.zero,
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            _Row(label: 'Raised by', value: complaint.raisedByName),
            const Divider(height: 20),
            _Row(label: 'About', value: complaint.about),
            const Divider(height: 20),
            _Row(label: 'Raised', value: _formatDate(complaint.createdAt)),
            const Divider(height: 20),
            _Row(
              label: 'Answer due',
              value: _formatDate(complaint.slaDueAt),
            ),
            const SizedBox(height: 14),
            Text(complaint.description),
            if (complaint.photoUrl != null) ...[
              const SizedBox(height: 14),
              ClipRRect(
                borderRadius: BorderRadius.circular(10),
                child: Image.network(
                  complaint.photoUrl!,
                  fit: BoxFit.cover,
                  // A missing photo must not blank the complaint text above it.
                  errorBuilder: (_, __, ___) => const Padding(
                    padding: EdgeInsets.all(12),
                    child: Text(
                      'The attached photo could not be loaded.',
                      style: TextStyle(color: AppColors.textSecondary),
                    ),
                  ),
                ),
              ),
            ],
          ],
        ),
      ),
    );
  }
}

class _History extends StatelessWidget {
  const _History({required this.updates});

  final List<ComplaintUpdate> updates;

  @override
  Widget build(BuildContext context) {
    if (updates.isEmpty) return const SizedBox.shrink();

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(
          'History',
          style: Theme.of(context)
              .textTheme
              .titleMedium
              ?.copyWith(fontWeight: FontWeight.w700),
        ),
        const SizedBox(height: 8),
        for (final entry in updates) _HistoryEntry(entry: entry),
      ],
    );
  }
}

class _HistoryEntry extends StatelessWidget {
  const _HistoryEntry({required this.entry});

  final ComplaintUpdate entry;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 12),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Padding(
            padding: const EdgeInsets.only(top: 3),
            child: Icon(
              entry.isSystem
                  ? Icons.settings_outlined
                  : entry.isTransition
                      ? Icons.flag_outlined
                      : Icons.chat_bubble_outline,
              size: 16,
              color: AppColors.textSecondary,
            ),
          ),
          const SizedBox(width: 10),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(
                  children: [
                    Text(
                      entry.authorName,
                      style: const TextStyle(
                        fontWeight: FontWeight.w600,
                        fontSize: 13,
                      ),
                    ),
                    const SizedBox(width: 8),
                    Text(
                      _formatDate(entry.createdAt),
                      style: const TextStyle(
                        fontSize: 12,
                        color: AppColors.textSecondary,
                      ),
                    ),
                    if (entry.isInternal) ...[
                      const SizedBox(width: 8),
                      const Icon(
                        Icons.visibility_off_outlined,
                        size: 13,
                        color: AppColors.warning,
                      ),
                      const SizedBox(width: 3),
                      const Text(
                        'Internal',
                        style:
                            TextStyle(fontSize: 11, color: AppColors.warning),
                      ),
                    ],
                  ],
                ),
                const SizedBox(height: 2),
                Text(entry.note),
              ],
            ),
          ),
        ],
      ),
    );
  }
}

class _NoteComposer extends StatelessWidget {
  const _NoteComposer({
    required this.controller,
    required this.isAdmin,
    required this.internal,
    required this.busy,
    required this.onInternalChanged,
    required this.onSend,
  });

  final TextEditingController controller;
  final bool isAdmin;
  final bool internal;
  final bool busy;
  final ValueChanged<bool> onInternalChanged;
  final VoidCallback onSend;

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        TextField(
          controller: controller,
          maxLines: 3,
          decoration: const InputDecoration(hintText: 'Add a note'),
        ),
        if (isAdmin)
          CheckboxListTile(
            value: internal,
            onChanged: (value) => onInternalChanged(value ?? false),
            contentPadding: EdgeInsets.zero,
            controlAffinity: ListTileControlAffinity.leading,
            title: const Text('Internal note'),
            subtitle: const Text('Not shown to the person who raised this'),
          ),
        const SizedBox(height: 8),
        ElevatedButton.icon(
          onPressed: busy ? null : onSend,
          icon: const Icon(Icons.send),
          label: const Text('Post note'),
        ),
      ],
    );
  }
}

class _Actions extends StatelessWidget {
  const _Actions({
    required this.complaint,
    required this.isAdmin,
    required this.isRaiser,
    required this.busy,
    required this.onStart,
    required this.onResolve,
    required this.onReject,
    required this.onWithdraw,
  });

  final Complaint complaint;
  final bool isAdmin;
  final bool isRaiser;
  final bool busy;
  final VoidCallback onStart;
  final VoidCallback onResolve;
  final VoidCallback onReject;
  final VoidCallback onWithdraw;

  @override
  Widget build(BuildContext context) {
    if (isAdmin) {
      return Column(
        children: [
          if (complaint.status == ComplaintStatus.open)
            OutlinedButton.icon(
              onPressed: busy ? null : onStart,
              icon: const Icon(Icons.play_arrow),
              label: const Text("I'm looking into this"),
            ),
          const SizedBox(height: 8),
          ElevatedButton.icon(
            onPressed: busy ? null : onResolve,
            icon: const Icon(Icons.check),
            label: const Text('Mark as resolved'),
          ),
          const SizedBox(height: 8),
          TextButton.icon(
            onPressed: busy ? null : onReject,
            icon: const Icon(Icons.close),
            label: const Text('Reject'),
            style: TextButton.styleFrom(foregroundColor: AppColors.danger),
          ),
        ],
      );
    }

    // Only the person who raised it can withdraw it. The server enforces this
    // too; hiding the button just avoids offering an action that would fail.
    if (!isRaiser) return const SizedBox.shrink();

    return TextButton.icon(
      onPressed: busy ? null : onWithdraw,
      icon: const Icon(Icons.undo),
      label: const Text('Withdraw this complaint'),
    );
  }
}

class _Outcome extends StatelessWidget {
  const _Outcome({required this.complaint});

  final Complaint complaint;

  @override
  Widget build(BuildContext context) {
    return Card(
      margin: EdgeInsets.zero,
      color: Theme.of(context).colorScheme.surfaceContainerHighest,
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              complaint.status.label,
              style: const TextStyle(fontWeight: FontWeight.w700),
            ),
            const SizedBox(height: 6),
            Text(
              complaint.resolution.isEmpty
                  ? 'No note was recorded.'
                  : complaint.resolution,
            ),
            const SizedBox(height: 8),
            Text(
              'Closed ${_formatDate(complaint.resolvedAt)} · open for '
              '${complaint.ageActiveHours.toStringAsFixed(1)} working hours',
              style: const TextStyle(
                fontSize: 12,
                color: AppColors.textSecondary,
              ),
            ),
          ],
        ),
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
    return Row(
      mainAxisAlignment: MainAxisAlignment.spaceBetween,
      children: [
        Text(label, style: const TextStyle(color: AppColors.textSecondary)),
        Flexible(
          child: Text(
            value.isEmpty ? '—' : value,
            textAlign: TextAlign.right,
            style: const TextStyle(fontWeight: FontWeight.w600),
          ),
        ),
      ],
    );
  }
}

String _formatDate(DateTime? value) {
  if (value == null) return '—';
  return DateFormat('d MMM, HH:mm').format(value.toLocal());
}
