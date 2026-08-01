import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../../../core/errors/api_exception.dart';
import '../../../../core/routing/app_router.dart';
import '../../../../shared/design_system.dart';
import '../providers/admin_provider.dart';

/// Module 11.1 — the society directory.
///
/// The mobile counterpart to the Django admin screens. Both exist because an
/// administrator at a desk and one standing at a gate need the same data
/// through different doors, and only one of those has a keyboard.
class DirectoryScreen extends ConsumerStatefulWidget {
  const DirectoryScreen({super.key});

  @override
  ConsumerState<DirectoryScreen> createState() => _DirectoryScreenState();
}

class _DirectoryScreenState extends ConsumerState<DirectoryScreen>
    with SingleTickerProviderStateMixin {
  late final TabController _tabs = TabController(length: 2, vsync: this);
  final _searchController = TextEditingController();
  Timer? _debounce;

  @override
  void dispose() {
    _debounce?.cancel();
    _searchController.dispose();
    _tabs.dispose();
    super.dispose();
  }

  /// Debounced: typing a name is one request, not one per keystroke. This runs
  /// over patchy mobile data against a free-tier backend.
  void _onSearchChanged(String value) {
    _debounce?.cancel();
    _debounce = Timer(const Duration(milliseconds: 400), () {
      final query = value.trim();
      ref.read(workerDirectorySearchProvider.notifier).state = query;
      ref.read(residentDirectorySearchProvider.notifier).state = query;
    });
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('Directory'),
        bottom: TabBar(
          controller: _tabs,
          tabs: const [Tab(text: 'Workers'), Tab(text: 'Residents')],
        ),
      ),
      body: Column(
        children: [
          Padding(
            padding: const EdgeInsets.fromLTRB(12, 12, 12, 8),
            child: TextField(
              controller: _searchController,
              onChanged: _onSearchChanged,
              textInputAction: TextInputAction.search,
              decoration: const InputDecoration(
                hintText: 'Search by name, phone or flat',
                prefixIcon: Icon(Icons.search),
              ),
            ),
          ),
          Expanded(
            child: TabBarView(
              controller: _tabs,
              children: const [_WorkerTab(), _ResidentTab()],
            ),
          ),
        ],
      ),
    );
  }
}

class _WorkerTab extends ConsumerWidget {
  const _WorkerTab();

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final workers = ref.watch(workerDirectoryProvider);

    return workers.when(
      loading: () => const AppSkeletonList(),
      error: (error, _) => _Error(error: error),
      data: (rows) {
        if (rows.isEmpty) return const _Empty(what: 'workers');

        return RefreshIndicator(
          onRefresh: () async => ref.invalidate(workerDirectoryProvider),
          child: ListView.separated(
            itemCount: rows.length,
            separatorBuilder: (_, __) => const Divider(height: 1),
            itemBuilder: (context, index) {
              final worker = rows[index];
              return ListTile(
                onTap: () => context.push(Routes.workerDetailPath(worker.id)),
                title: Text(worker.fullName),
                subtitle: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(worker.phoneNumber),
                    const SizedBox(height: 4),
                    Wrap(
                      spacing: 6,
                      runSpacing: 4,
                      children: [
                        if (worker.services.isNotEmpty)
                          _Tag(label: worker.services.join(', ')),
                        if (!worker.isApproved)
                          const _Tag(
                            label: 'Not verified',
                            colour: AppColors.warning,
                          ),
                        if (!worker.isAvailable)
                          const _Tag(
                            label: 'Unavailable',
                            colour: AppColors.textSecondary,
                          ),
                        if (worker.openComplaints > 0)
                          _Tag(
                            label: '${worker.openComplaints} open complaint'
                                '${worker.openComplaints == 1 ? '' : 's'}',
                            colour: AppColors.danger,
                          ),
                      ],
                    ),
                  ],
                ),
                isThreeLine: true,
                trailing: _TrustBadge(
                  score: worker.trustScore,
                  isRated: worker.isRated,
                ),
              );
            },
          ),
        );
      },
    );
  }
}

class _ResidentTab extends ConsumerWidget {
  const _ResidentTab();

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final residents = ref.watch(residentDirectoryProvider);

    return residents.when(
      loading: () => const AppSkeletonList(),
      error: (error, _) => _Error(error: error),
      data: (rows) {
        if (rows.isEmpty) return const _Empty(what: 'residents');

        return RefreshIndicator(
          onRefresh: () async => ref.invalidate(residentDirectoryProvider),
          child: ListView.separated(
            itemCount: rows.length,
            separatorBuilder: (_, __) => const Divider(height: 1),
            itemBuilder: (context, index) {
              final resident = rows[index];
              return ListTile(
                title: Text(resident.fullName),
                subtitle: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text('${resident.flat} · ${resident.phoneNumber}'),
                    const SizedBox(height: 4),
                    Wrap(
                      spacing: 6,
                      runSpacing: 4,
                      children: [
                        if (resident.isPrimary) const _Tag(label: 'Primary'),
                        if (!resident.isApproved)
                          const _Tag(
                            label: 'Not approved',
                            colour: AppColors.warning,
                          ),
                        if (resident.openComplaints > 0)
                          _Tag(
                            label: '${resident.openComplaints} open complaint'
                                '${resident.openComplaints == 1 ? '' : 's'}',
                            colour: AppColors.danger,
                          ),
                      ],
                    ),
                  ],
                ),
                isThreeLine: true,
                trailing: _TrustBadge(
                  score: resident.trustScore,
                  isRated: resident.isRated,
                ),
              );
            },
          ),
        );
      },
    );
  }
}

/// A trust score, or an honest admission that there is not one yet.
///
/// An unrated worker scores zero because nothing has happened, not because they
/// did badly. Rendering that as "0" next to somebody who genuinely earned a low
/// score would cost a new worker their first job.
class _TrustBadge extends StatelessWidget {
  const _TrustBadge({required this.score, required this.isRated});

  final double score;
  final bool isRated;

  @override
  Widget build(BuildContext context) {
    if (!isRated) {
      return const Text(
        'New',
        style: TextStyle(fontSize: 12, color: AppColors.textSecondary),
      );
    }

    final colour = score >= 70
        ? AppColors.success
        : score >= 40
            ? AppColors.warning
            : AppColors.danger;

    return Text(
      score.toStringAsFixed(0),
      style: TextStyle(
        fontSize: 18,
        fontWeight: FontWeight.w700,
        color: colour,
      ),
    );
  }
}

class _Tag extends StatelessWidget {
  const _Tag({required this.label, this.colour = AppColors.info});

  final String label;
  final Color colour;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 2),
      decoration: BoxDecoration(
        color: colour.withValues(alpha: 0.13),
        borderRadius: BorderRadius.circular(20),
      ),
      child: Text(
        label,
        style:
            TextStyle(fontSize: 11, fontWeight: FontWeight.w600, color: colour),
      ),
    );
  }
}

class _Empty extends StatelessWidget {
  const _Empty({required this.what});

  final String what;

  @override
  Widget build(BuildContext context) {
    return AppEmptyState(
      icon: Icons.search_off_rounded,
      title: 'No $what matched',
      message: 'Try a different name, or clear the search.',
    );
  }
}

class _Error extends StatelessWidget {
  const _Error({required this.error});

  final Object error;

  @override
  Widget build(BuildContext context) {
    return AppErrorState(
      message: error is ApiException
          ? (error as ApiException).message
          : 'Could not load the directory.',
    );
  }
}
