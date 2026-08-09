import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../../../core/errors/api_exception.dart';
import '../../../../core/routing/app_router.dart';
import '../../../../shared/design_system.dart';
import '../../../auth/data/models/user_model.dart';
import '../../../auth/presentation/providers/auth_provider.dart';
import '../../../bookings/presentation/widgets/booking_card.dart';
import '../../data/models/hiring_models.dart';
import '../providers/hiring_provider.dart';

/// Module 4.4 — the request inbox. For a resident, everything they have asked
/// for; for a worker, everything asked of them.
///
/// -----------------------------------------------------------------------
/// TWO MODELS, ONE LIST
/// -----------------------------------------------------------------------
/// This screen is titled "My requests" for a resident, and it used to show only
/// [HireRequest] rows — proposals to hire somebody for recurring work. One-day
/// bookings, including every emergency, are a different model on a different
/// endpoint, and lived on a separate screen reachable only through the overflow
/// menu above.
///
/// So a household that raised an emergency, paid for it and was waiting for
/// somebody to accept could open "My requests" and see nothing at all. Not a
/// filter excluding them — they had never been in this query, because they are
/// not in this table.
///
/// The two are merged here rather than server-side. A polymorphic endpoint
/// would have to flatten a hire request and a booking into one shape, and they
/// genuinely differ: one carries a proposed rate and a deadline, the other a
/// date, a price and a settlement route. Merging in the client keeps both
/// endpoints honest about what they return, and the sort key — most recent
/// first — is the only thing the two have to agree on.
class HireRequestsScreen extends ConsumerWidget {
  const HireRequestsScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final isWorker = ref.watch(authProvider).user?.role == UserRole.worker;
    final requests = ref.watch(combinedRequestsProvider);

    return Scaffold(
      appBar: AppBar(
        title: Text(isWorker ? 'Hire requests' : 'My requests'),
        actions: [
          // A menu rather than a row of icons: this is the worker's home, so
          // it is the jumping-off point for everything they do — recurring
          // work, one-day jobs, and the availability that makes one-day jobs
          // reachable at all.
          PopupMenuButton<String>(
            icon: const Icon(Icons.more_vert),
            onSelected: (route) => context.push(route),
            itemBuilder: (_) => [
              PopupMenuItem(
                value: Routes.engagements,
                child: ListTile(
                  leading: const Icon(Icons.handshake_outlined),
                  title: Text(isWorker ? 'My regular work' : 'My hires'),
                  contentPadding: EdgeInsets.zero,
                ),
              ),
              const PopupMenuItem(
                value: Routes.myBookings,
                child: ListTile(
                  leading: Icon(Icons.event_note_outlined),
                  title: Text('One-day jobs'),
                  contentPadding: EdgeInsets.zero,
                ),
              ),
              if (isWorker) ...[
                const PopupMenuItem(
                  value: Routes.myAvailability,
                  child: ListTile(
                    leading: Icon(Icons.calendar_month),
                    title: Text('Days I can work'),
                    contentPadding: EdgeInsets.zero,
                  ),
                ),
                const PopupMenuItem(
                  value: Routes.workerOnboarding,
                  child: ListTile(
                    leading: Icon(Icons.badge_outlined),
                    title: Text('My verification'),
                    contentPadding: EdgeInsets.zero,
                  ),
                ),
              ],
            ],
          ),
        ],
      ),
      body: requests.when(
        loading: () => const AppSkeletonList(count: 3, hasAvatar: false),
        error: (error, _) => AppErrorState(
          message: error is ApiException
              ? error.message
              : 'Could not load requests.',
          onRetry: () => invalidateCombinedRequests(ref),
        ),
        data: (items) {
          if (items.isEmpty) {
            return AppEmptyState(
              icon: Icons.mail_outline_rounded,
              title: isWorker ? 'No requests yet' : 'No requests sent',
              message: isWorker
                  ? 'Residents who want to hire you will appear here.'
                  : 'Everything you ask for — regular help, one-day jobs and '
                      'emergencies — appears here.',
            );
          }
          return RefreshIndicator(
            onRefresh: () async => invalidateCombinedRequests(ref),
            child: ListView.builder(
              physics: const AlwaysScrollableScrollPhysics(),
              padding: const EdgeInsets.fromLTRB(
                AppSpacing.gutter,
                AppSpacing.sm,
                AppSpacing.gutter,
                AppSpacing.xxl,
              ),
              itemCount: items.length,
              itemBuilder: (context, index) {
                final entry = items[index];
                return AppFadeIn(
                  index: index,
                  // The two shapes keep their own cards. A booking's actions
                  // (cancel with a refund, mark done, pay) have nothing in
                  // common with a hire request's (accept, decline, withdraw),
                  // and flattening them into one card would mean a switch on
                  // type inside every button.
                  child: entry.booking != null
                      ? BookingCard(
                          booking: entry.booking!,
                          isWorker: isWorker,
                          dense: true,
                        )
                      : _RequestCard(
                          request: entry.hireRequest!,
                          isWorker: isWorker,
                        ),
                );
              },
            ),
          );
        },
      ),
    );
  }
}

class _RequestCard extends ConsumerStatefulWidget {
  const _RequestCard({required this.request, required this.isWorker});

  final HireRequest request;
  final bool isWorker;

  @override
  ConsumerState<_RequestCard> createState() => _RequestCardState();
}

class _RequestCardState extends ConsumerState<_RequestCard> {
  bool _isBusy = false;

  Future<void> _run(
    Future<void> Function() action,
    String successMessage,
  ) async {
    setState(() => _isBusy = true);
    try {
      await action();
      if (!mounted) return;
      invalidateHiring(ref);
      ScaffoldMessenger.of(context)
          .showSnackBar(SnackBar(content: Text(successMessage)));
    } on ApiException catch (error) {
      if (!mounted) return;
      setState(() => _isBusy = false);
      ScaffoldMessenger.of(context)
          .showSnackBar(SnackBar(content: Text(error.message)));
    }
  }

  Future<void> _accept() => _run(
        () => ref
            .read(hiringRepositoryProvider)
            .acceptHireRequest(widget.request.id),
        'Accepted — the engagement is now active.',
      );

  Future<void> _decline() async {
    final note = await showDialog<String>(
      context: context,
      builder: (_) => const _NoteDialog(
        title: 'Decline this request?',
        hint: 'Optional — e.g. already working those hours',
        confirmLabel: 'Decline',
      ),
    );
    if (note == null) return;

    await _run(
      () => ref
          .read(hiringRepositoryProvider)
          .declineHireRequest(widget.request.id, note: note),
      'Request declined.',
    );
  }

  Future<void> _withdraw() async {
    final reason = await showDialog<String>(
      context: context,
      builder: (_) => const _NoteDialog(
        title: 'Withdraw this request?',
        hint: 'Optional — why are you withdrawing?',
        confirmLabel: 'Withdraw',
      ),
    );
    if (reason == null) return;

    await _run(
      () => ref
          .read(hiringRepositoryProvider)
          .withdrawHireRequest(widget.request.id, reason: reason),
      'Request withdrawn.',
    );
  }

  Color get _statusColour {
    switch (widget.request.status) {
      case HireRequestStatus.accepted:
        return AppColors.success;
      case HireRequestStatus.declined:
      case HireRequestStatus.expired:
        return AppColors.danger;
      case HireRequestStatus.withdrawn:
        return AppColors.textSecondary;
      case HireRequestStatus.pending:
        return AppColors.info;
    }
  }

  @override
  Widget build(BuildContext context) {
    final request = widget.request;
    final theme = Theme.of(context);
    final counterparty =
        widget.isWorker ? request.residentName : request.workerName;
    final hoursLeft = request.hoursRemaining;

    return AppCard(
      margin: const EdgeInsets.only(bottom: AppSpacing.sm),
      child: Padding(
        padding: EdgeInsets.zero,
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Expanded(
                  child: Text(
                    counterparty.isEmpty ? 'Unknown' : counterparty,
                    style: theme.textTheme.titleSmall,
                  ),
                ),
                Container(
                  padding:
                      const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
                  decoration: BoxDecoration(
                    color: _statusColour.withValues(alpha: 0.12),
                    borderRadius: BorderRadius.circular(20),
                  ),
                  child: Text(
                    request.status.label,
                    style: TextStyle(
                      color: _statusColour,
                      fontSize: 12,
                      fontWeight: FontWeight.w700,
                    ),
                  ),
                ),
              ],
            ),
            if (widget.isWorker && request.residentFlat.isNotEmpty)
              Text(request.residentFlat, style: theme.textTheme.bodySmall),
            const SizedBox(height: 12),
            _DetailRow(
              icon: Icons.event_repeat,
              text: request.terms.scheduleLabel,
            ),
            _DetailRow(
              icon: Icons.timelapse,
              text:
                  '${request.terms.expectedDurationMinutes} minutes per visit',
            ),
            _DetailRow(
              icon: Icons.currency_rupee,
              text: '₹${request.terms.monthlyRate} per month',
            ),
            if (request.serviceType != null)
              _DetailRow(
                icon: Icons.cleaning_services_outlined,
                text: request.serviceType!.name,
              ),
            if (request.message.isNotEmpty) ...[
              const SizedBox(height: 8),
              Container(
                width: double.infinity,
                padding: const EdgeInsets.all(12),
                decoration: BoxDecoration(
                  color: AppColors.surfaceMuted,
                  borderRadius: BorderRadius.circular(10),
                ),
                child: Text('“${request.message}”'),
              ),
            ],
            if (request.responseNote.isNotEmpty) ...[
              const SizedBox(height: 8),
              Text(
                'Note: ${request.responseNote}',
                style: theme.textTheme.bodySmall,
              ),
            ],
            if (request.isActionable && hoursLeft != null) ...[
              const SizedBox(height: 10),
              Row(
                children: [
                  const Icon(
                    Icons.hourglass_bottom,
                    size: 16,
                    color: AppColors.warning,
                  ),
                  const SizedBox(width: 6),
                  Text(
                    hoursLeft > 0
                        ? '$hoursLeft hours left to respond'
                        : 'Expiring shortly',
                    style: const TextStyle(
                      fontSize: 13,
                      color: AppColors.warning,
                    ),
                  ),
                ],
              ),
            ],
            if (request.isActionable) ...[
              const SizedBox(height: 14),
              if (_isBusy)
                const Center(child: CircularProgressIndicator())
              else if (widget.isWorker)
                Row(
                  children: [
                    Expanded(
                      child: OutlinedButton.icon(
                        onPressed: _decline,
                        icon: const Icon(Icons.close),
                        label: const Text('Decline'),
                        style: OutlinedButton.styleFrom(
                          foregroundColor: AppColors.danger,
                          minimumSize: const Size.fromHeight(48),
                        ),
                      ),
                    ),
                    const SizedBox(width: 12),
                    Expanded(
                      child: ElevatedButton.icon(
                        onPressed: _accept,
                        icon: const Icon(Icons.check),
                        label: const Text('Accept'),
                        style: ElevatedButton.styleFrom(
                          minimumSize: const Size.fromHeight(48),
                        ),
                      ),
                    ),
                  ],
                )
              else
                OutlinedButton.icon(
                  onPressed: _withdraw,
                  icon: const Icon(Icons.undo),
                  label: const Text('Withdraw request'),
                  style: OutlinedButton.styleFrom(
                    minimumSize: const Size.fromHeight(48),
                  ),
                ),
            ],
          ],
        ),
      ),
    );
  }
}

class _DetailRow extends StatelessWidget {
  const _DetailRow({required this.icon, required this.text});

  final IconData icon;
  final String text;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 3),
      child: Row(
        children: [
          Icon(icon, size: 18, color: AppColors.textSecondary),
          const SizedBox(width: 10),
          Expanded(child: Text(text, style: const TextStyle(fontSize: 14))),
        ],
      ),
    );
  }
}

class _NoteDialog extends StatefulWidget {
  const _NoteDialog({
    required this.title,
    required this.hint,
    required this.confirmLabel,
  });

  final String title;
  final String hint;
  final String confirmLabel;

  @override
  State<_NoteDialog> createState() => _NoteDialogState();
}

class _NoteDialogState extends State<_NoteDialog> {
  final _controller = TextEditingController();

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return AlertDialog(
      title: Text(widget.title),
      content: TextField(
        controller: _controller,
        autofocus: true,
        maxLines: 3,
        decoration: InputDecoration(hintText: widget.hint),
      ),
      actions: [
        TextButton(
          onPressed: () => Navigator.of(context).pop(),
          child: const Text('Cancel'),
        ),
        FilledButton(
          onPressed: () => Navigator.of(context).pop(_controller.text),
          child: Text(widget.confirmLabel),
        ),
      ],
    );
  }
}
