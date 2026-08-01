import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../../../core/errors/api_exception.dart';
import '../../../../core/routing/app_router.dart';
import '../../../../shared/design_system.dart';
import '../../../auth/data/models/user_model.dart';
import '../../../auth/presentation/providers/auth_provider.dart';
import '../../data/models/admin_models.dart';
import '../providers/admin_provider.dart';
import '../widgets/sla_chip.dart';

/// Module 11.3 — the complaint list.
///
/// -----------------------------------------------------------------------
/// ONE SCREEN, TWO AUDIENCES
/// -----------------------------------------------------------------------
/// An administrator sees their society's queue; everybody else sees what they
/// raised and what was raised about them. The server decides which, so this
/// screen never asks — it only changes its title and drops the filters that
/// would be meaningless on a list of three.
class ComplaintsScreen extends ConsumerWidget {
  const ComplaintsScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final isAdmin = ref.watch(authProvider).user?.role == UserRole.societyAdmin;
    final complaints = ref.watch(complaintsProvider);
    final filters = ref.watch(complaintFiltersProvider);

    return Scaffold(
      appBar: AppBar(
        title: Text(isAdmin ? 'Complaints' : 'My complaints'),
        actions: [
          if (isAdmin)
            IconButton(
              tooltip: 'Check for overdue',
              icon: const Icon(Icons.alarm),
              onPressed: () => _escalate(context, ref),
            ),
        ],
      ),
      floatingActionButton: isAdmin
          ? null
          : FloatingActionButton.extended(
              onPressed: () async {
                final raised = await context.push<bool>(Routes.raiseComplaint);
                if (raised ?? false) invalidateComplaints(ref);
              },
              icon: const Icon(Icons.add),
              label: const Text('Raise a complaint'),
            ),
      body: Column(
        children: [
          _FilterBar(filters: filters, showOverdue: isAdmin),
          Expanded(
            child: complaints.when(
              loading: () => const AppSkeletonList(),
              error: (error, _) => Center(
                child: Padding(
                  padding: const EdgeInsets.all(24),
                  child: Text(
                    error is ApiException
                        ? error.message
                        : 'Could not load complaints.',
                    textAlign: TextAlign.center,
                  ),
                ),
              ),
              data: (items) {
                if (items.isEmpty) {
                  return _Empty(isAdmin: isAdmin, filters: filters);
                }
                return RefreshIndicator(
                  onRefresh: () async => invalidateComplaints(ref),
                  child: ListView.builder(
                    padding: const EdgeInsets.only(bottom: 88),
                    itemCount: items.length,
                    itemBuilder: (context, index) =>
                        _ComplaintCard(complaint: items[index]),
                  ),
                );
              },
            ),
          ),
        ],
      ),
    );
  }

  Future<void> _escalate(BuildContext context, WidgetRef ref) async {
    final messenger = ScaffoldMessenger.of(context);
    try {
      final escalated =
          await ref.read(adminRepositoryProvider).escalateOverdue();
      invalidateComplaints(ref);
      messenger.showSnackBar(
        SnackBar(
          content: Text(
            escalated == 0
                ? 'Nothing was overdue.'
                : '$escalated complaint(s) escalated.',
          ),
        ),
      );
    } on ApiException catch (error) {
      messenger.showSnackBar(SnackBar(content: Text(error.message)));
    }
  }
}

class _FilterBar extends ConsumerWidget {
  const _FilterBar({required this.filters, required this.showOverdue});

  final ComplaintFilters filters;
  final bool showOverdue;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    void update(ComplaintFilters next) =>
        ref.read(complaintFiltersProvider.notifier).state = next;

    return SingleChildScrollView(
      scrollDirection: Axis.horizontal,
      padding: const EdgeInsets.fromLTRB(12, 12, 12, 4),
      child: Row(
        children: [
          FilterChip(
            label: const Text('Open'),
            selected: filters.openOnly,
            onSelected: (selected) =>
                update(filters.copyWith(openOnly: selected)),
          ),
          if (showOverdue) ...[
            const SizedBox(width: 8),
            FilterChip(
              label: const Text('Overdue'),
              selected: filters.overdueOnly,
              onSelected: (selected) =>
                  update(filters.copyWith(overdueOnly: selected)),
            ),
          ],
          const SizedBox(width: 8),
          for (final category in ComplaintCategory.values) ...[
            FilterChip(
              label: Text(category.label),
              selected: filters.category == category,
              onSelected: (selected) => update(
                selected
                    ? filters.copyWith(category: category)
                    : filters.copyWith(clearCategory: true),
              ),
            ),
            const SizedBox(width: 8),
          ],
        ],
      ),
    );
  }
}

class _Empty extends StatelessWidget {
  const _Empty({required this.isAdmin, required this.filters});

  final bool isAdmin;
  final ComplaintFilters filters;

  @override
  Widget build(BuildContext context) {
    final filtered = filters.overdueOnly || filters.category != null;

    return AppEmptyState(
      icon: Icons.check_circle_outline,
      title: filtered ? 'Nothing matches that' : 'Nothing to deal with',
      message: filtered
                  ? 'Try removing a filter.'
                  : isAdmin
                      ? 'Complaints raised by residents and workers land here.'
                      : 'If something goes wrong, raise it here and your '
                          'administrator will see it.',
    );
  }
}

class _ComplaintCard extends StatelessWidget {
  const _ComplaintCard({required this.complaint});

  final Complaint complaint;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);

    return Card(
      child: InkWell(
        borderRadius: BorderRadius.circular(14),
        onTap: () => context.push(Routes.complaintPath(complaint.id)),
        child: Padding(
          padding: const EdgeInsets.all(16),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Row(
                children: [
                  Expanded(
                    child: Text(
                      complaint.subject,
                      maxLines: 2,
                      overflow: TextOverflow.ellipsis,
                      style: theme.textTheme.titleMedium
                          ?.copyWith(fontWeight: FontWeight.w600),
                    ),
                  ),
                  const SizedBox(width: 8),
                  SlaChip(complaint: complaint),
                ],
              ),
              const SizedBox(height: 6),
              Text(
                '${complaint.reference}  ·  ${complaint.category.label}',
                style: const TextStyle(
                  fontSize: 12,
                  color: AppColors.textSecondary,
                ),
              ),
              const SizedBox(height: 10),
              Wrap(
                spacing: 8,
                runSpacing: 6,
                crossAxisAlignment: WrapCrossAlignment.center,
                children: [
                  _Pill(
                    label: complaint.status.label,
                    colour: _statusColour(complaint.status),
                  ),
                  if (complaint.about.isNotEmpty)
                    Text(
                      'About: ${complaint.about}',
                      style: theme.textTheme.bodySmall,
                    ),
                  if (complaint.awaitingFirstResponse)
                    const _Pill(
                      label: 'Not yet answered',
                      colour: AppColors.warning,
                    ),
                  if (complaint.cameFromPaymentDispute)
                    const _Pill(
                      label: 'From a payment dispute',
                      colour: AppColors.info,
                    ),
                ],
              ),
            ],
          ),
        ),
      ),
    );
  }
}

class _Pill extends StatelessWidget {
  const _Pill({required this.label, required this.colour});

  final String label;
  final Color colour;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 3),
      decoration: BoxDecoration(
        color: colour.withValues(alpha: 0.13),
        borderRadius: BorderRadius.circular(20),
      ),
      child: Text(
        label,
        style: TextStyle(
          fontSize: 12,
          fontWeight: FontWeight.w600,
          color: colour,
        ),
      ),
    );
  }
}

Color _statusColour(ComplaintStatus status) {
  switch (status) {
    case ComplaintStatus.open:
      return AppColors.warning;
    case ComplaintStatus.inProgress:
      return AppColors.info;
    case ComplaintStatus.resolved:
      return AppColors.success;
    case ComplaintStatus.rejected:
      return AppColors.danger;
    case ComplaintStatus.withdrawn:
      return AppColors.textSecondary;
  }
}
