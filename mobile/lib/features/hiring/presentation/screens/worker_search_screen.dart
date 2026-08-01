import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../../../core/errors/api_exception.dart';
import '../../../../core/routing/app_router.dart';
import '../../../../shared/design_system.dart';
import '../../../auth/presentation/providers/auth_provider.dart';
import '../../../notifications/presentation/widgets/notification_bell.dart';
import '../../data/models/hiring_models.dart';
import '../providers/hiring_provider.dart';
import '../widgets/match_badge.dart';

/// Module 4.1 — the resident's discovery screen, and their home.
///
/// Ranking happens on the server (Module 4.3), so this screen never sorts: it
/// renders the order it was given. Keeping the ordering in one place is what
/// lets Module 12.1 swap the rule-based score for a learned model without any
/// client change.
///
/// The redesign changed presentation only. The debounce, the filter provider,
/// the server-ranked ordering and the navigation targets are all untouched —
/// what moved is that the six destinations previously buried in an overflow
/// menu are now either bottom-nav tabs or visible quick actions.
class WorkerSearchScreen extends ConsumerStatefulWidget {
  const WorkerSearchScreen({super.key});

  @override
  ConsumerState<WorkerSearchScreen> createState() => _WorkerSearchScreenState();
}

class _WorkerSearchScreenState extends ConsumerState<WorkerSearchScreen> {
  final _searchController = TextEditingController();
  Timer? _debounce;

  @override
  void dispose() {
    _debounce?.cancel();
    _searchController.dispose();
    super.dispose();
  }

  /// Debounced so that typing a name is one request, not one per keystroke —
  /// this runs over patchy mobile data against a free-tier backend.
  void _onSearchChanged(String value) {
    // The clear button's visibility depends on the field's contents, and
    // TextField's own onChanged does not rebuild this widget.
    setState(() {});
    _debounce?.cancel();
    _debounce = Timer(const Duration(milliseconds: 400), () {
      ref.read(workerFiltersProvider.notifier).update(
            (filters) => filters.copyWith(query: value.trim()),
          );
    });
  }

  @override
  Widget build(BuildContext context) {
    final results = ref.watch(workerSearchProvider);
    final filters = ref.watch(workerFiltersProvider);
    final user = ref.watch(authProvider).user;

    return Scaffold(
      appBar: AppBar(
        titleSpacing: AppSpacing.gutter,
        title: const Text('Find help'),
        actions: const [NotificationBell(), SizedBox(width: AppSpacing.xs)],
      ),
      body: RefreshIndicator(
        onRefresh: () async => ref.invalidate(workerSearchProvider),
        child: CustomScrollView(
          // Always scrollable so pull-to-refresh works even when the list is
          // empty — which is exactly when a user most wants to retry.
          physics: const AlwaysScrollableScrollPhysics(),
          slivers: [
            SliverToBoxAdapter(
              child: AppFadeIn(
                child: _Greeting(
                  name: user?.firstName ?? '',
                  society: user?.societyName,
                ),
              ),
            ),
            SliverToBoxAdapter(
              child: AppFadeIn(
                index: 1,
                child: _SearchField(
                  controller: _searchController,
                  onChanged: _onSearchChanged,
                ),
              ),
            ),
            const SliverToBoxAdapter(
              child: AppFadeIn(index: 2, child: _QuickActions()),
            ),
            SliverToBoxAdapter(
              child: AppFadeIn(
                index: 3,
                child: AppSectionHeader(
                  title: 'Available near you',
                  subtitle: results.valueOrNull == null
                      ? null
                      : '${results.value!.length} verified '
                          '${results.value!.length == 1 ? 'person' : 'people'}',
                ),
              ),
            ),
            SliverToBoxAdapter(
              child: AppFadeIn(
                index: 4,
                child: _SortBar(
                  sort: filters.sort,
                  onChanged: (sort) => ref
                      .read(workerFiltersProvider.notifier)
                      .update((f) => f.copyWith(sort: sort)),
                ),
              ),
            ),
            ...results.when(
              loading: () => [
                const SliverToBoxAdapter(
                  child: Padding(
                    padding:
                        EdgeInsets.symmetric(horizontal: AppSpacing.gutter),
                    child: Column(
                      children: [
                        AppSkeletonCard(),
                        AppSkeletonCard(),
                        AppSkeletonCard(),
                        AppSkeletonCard(),
                      ],
                    ),
                  ),
                ),
              ],
              error: (error, _) => [
                SliverFillRemaining(
                  hasScrollBody: false,
                  child: AppErrorState(
                    message: error is ApiException
                        ? error.message
                        : 'Could not load workers.',
                    onRetry: () => ref.invalidate(workerSearchProvider),
                  ),
                ),
              ],
              data: (workers) {
                if (workers.isEmpty) {
                  return [
                    const SliverFillRemaining(
                      hasScrollBody: false,
                      child: AppEmptyState(
                        icon: Icons.person_search_outlined,
                        title: 'No one matches that yet',
                        message: 'Try clearing your search or filters. Only '
                            'workers your administrator has verified appear here.',
                      ),
                    ),
                  ];
                }
                return [
                  SliverPadding(
                    padding: const EdgeInsets.fromLTRB(
                      AppSpacing.gutter,
                      0,
                      AppSpacing.gutter,
                      AppSpacing.xxl,
                    ),
                    sliver: SliverList.builder(
                      itemCount: workers.length,
                      itemBuilder: (context, index) => AppFadeIn(
                        index: index,
                        child: _WorkerCard(worker: workers[index]),
                      ),
                    ),
                  ),
                ];
              },
            ),
          ],
        ),
      ),
    );
  }
}

/// Names the person and their society before anything else.
///
/// Every reference app opens this way — Snabbit with the saved address, Urban
/// Company with the delivery window, MyGate with the flat number. It answers
/// "am I looking at the right place?" before the user has to think about it.
class _Greeting extends StatelessWidget {
  const _Greeting({required this.name, this.society});

  final String name;
  final String? society;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final greeting = name.isEmpty ? 'Welcome' : 'Hi $name';

    return Padding(
      padding: const EdgeInsets.fromLTRB(
        AppSpacing.gutter,
        AppSpacing.xs,
        AppSpacing.gutter,
        AppSpacing.md,
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(greeting, style: theme.textTheme.headlineSmall),
          const SizedBox(height: AppSpacing.xxs),
          Row(
            children: [
              const Icon(
                Icons.location_on_outlined,
                size: AppIconSize.sm,
                color: AppColors.textTertiary,
              ),
              const SizedBox(width: AppSpacing.xxs),
              Expanded(
                child: Text(
                  society?.isNotEmpty == true
                      ? society!
                      : 'Trusted help for your home',
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: theme.textTheme.bodySmall,
                ),
              ),
            ],
          ),
        ],
      ),
    );
  }
}

class _SearchField extends StatelessWidget {
  const _SearchField({required this.controller, required this.onChanged});

  final TextEditingController controller;
  final ValueChanged<String> onChanged;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: AppSpacing.gutter),
      child: TextField(
        controller: controller,
        onChanged: onChanged,
        textInputAction: TextInputAction.search,
        decoration: InputDecoration(
          hintText: 'Search by name',
          prefixIcon: const Icon(Icons.search_rounded),
          suffixIcon: controller.text.isEmpty
              ? null
              : IconButton(
                  icon: const Icon(Icons.close_rounded),
                  tooltip: 'Clear search',
                  onPressed: () {
                    controller.clear();
                    onChanged('');
                  },
                ),
        ),
      ),
    );
  }
}

/// The two destinations that lost their home when the overflow menu went.
///
/// "My hires" and "Rate recent work" are not bottom-nav tabs — they are
/// occasional rather than daily — but they were reachable only through the `⋮`
/// menu, so surfacing them here is what keeps them reachable at all. Modelled
/// on MyGate's "Your Actions" strip.
class _QuickActions extends StatelessWidget {
  const _QuickActions();

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.fromLTRB(
        AppSpacing.gutter,
        AppSpacing.md,
        AppSpacing.gutter,
        0,
      ),
      child: Row(
        children: [
          Expanded(
            child: _ActionTile(
              icon: Icons.handshake_outlined,
              label: 'My hires',
              onTap: () => context.push(Routes.engagements),
            ),
          ),
          const SizedBox(width: AppSpacing.sm),
          Expanded(
            child: _ActionTile(
              icon: Icons.star_outline_rounded,
              label: 'Rate work',
              onTap: () => context.push(Routes.rateJobs),
            ),
          ),
          const SizedBox(width: AppSpacing.sm),
          Expanded(
            child: _ActionTile(
              icon: Icons.mail_outline_rounded,
              label: 'Requests',
              onTap: () => context.push(Routes.hireRequests),
            ),
          ),
        ],
      ),
    );
  }
}

class _ActionTile extends StatelessWidget {
  const _ActionTile({
    required this.icon,
    required this.label,
    required this.onTap,
  });

  final IconData icon;
  final String label;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    return AppCard(
      onTap: onTap,
      padding: const EdgeInsets.symmetric(vertical: AppSpacing.sm),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          Container(
            width: 38,
            height: 38,
            decoration: const BoxDecoration(
              color: AppColors.primarySoft,
              shape: BoxShape.circle,
            ),
            child: Icon(icon, size: AppIconSize.md, color: AppColors.primary),
          ),
          const SizedBox(height: AppSpacing.xs),
          Text(
            label,
            maxLines: 1,
            overflow: TextOverflow.ellipsis,
            style: const TextStyle(
              fontSize: 12.5,
              fontWeight: FontWeight.w600,
              color: AppColors.textPrimary,
            ),
          ),
        ],
      ),
    );
  }
}

class _SortBar extends StatelessWidget {
  const _SortBar({required this.sort, required this.onChanged});

  final String sort;
  final ValueChanged<String> onChanged;

  static const _options = {
    'recommended': 'Recommended',
    'rating': 'Top rated',
    'trust': 'Most trusted',
    'rate_asc': 'Lowest rate',
    'experience': 'Most experienced',
  };

  @override
  Widget build(BuildContext context) {
    return SizedBox(
      height: 52,
      child: ListView.separated(
        scrollDirection: Axis.horizontal,
        padding: const EdgeInsets.fromLTRB(
          AppSpacing.gutter,
          0,
          AppSpacing.gutter,
          AppSpacing.sm,
        ),
        itemCount: _options.length,
        separatorBuilder: (_, __) => const SizedBox(width: AppSpacing.xs),
        itemBuilder: (context, index) {
          final entry = _options.entries.elementAt(index);
          return AppFilterChip(
            label: entry.value,
            selected: sort == entry.key,
            onTap: () => onChanged(entry.key),
          );
        },
      ),
    );
  }
}

class _WorkerCard extends StatelessWidget {
  const _WorkerCard({required this.worker});

  final WorkerSearchResult worker;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);

    return AppCard(
      margin: const EdgeInsets.only(bottom: AppSpacing.sm),
      onTap: () => context.push(Routes.workerDetailPath(worker.id)),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              AppAvatar(
                name: worker.fullName,
                imageUrl: worker.photoUrl,
                seed: worker.id,
                size: 56,
              ),
              const SizedBox(width: AppSpacing.sm),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Row(
                      children: [
                        Expanded(
                          child: Text(
                            worker.fullName,
                            maxLines: 1,
                            overflow: TextOverflow.ellipsis,
                            style: theme.textTheme.titleMedium,
                          ),
                        ),
                        if (worker.matchPercentage != null)
                          MatchBadge(percentage: worker.matchPercentage!),
                      ],
                    ),
                    const SizedBox(height: AppSpacing.xxs),
                    Row(
                      children: [
                        // A worker with no ratings yet is shown as "New", never
                        // as 0.0 stars — the score already accounts for the
                        // absence of history, and the label should too.
                        Icon(
                          Icons.star_rounded,
                          size: AppIconSize.sm,
                          color: worker.hasRating
                              ? AppColors.accent
                              : AppColors.textTertiary,
                        ),
                        const SizedBox(width: 3),
                        Text(
                          worker.hasRating
                              ? worker.averageRating.toStringAsFixed(1)
                              : 'New',
                          style: const TextStyle(
                            fontSize: 13.5,
                            fontWeight: FontWeight.w700,
                            color: AppColors.textPrimary,
                          ),
                        ),
                        if (worker.completedEngagements > 0) ...[
                          const _Dot(),
                          Text(
                            '${worker.completedEngagements} jobs',
                            style: theme.textTheme.bodySmall,
                          ),
                        ],
                        if (worker.yearsOfExperience > 0) ...[
                          const _Dot(),
                          Flexible(
                            child: Text(
                              '${worker.yearsOfExperience} yrs exp',
                              maxLines: 1,
                              overflow: TextOverflow.ellipsis,
                              style: theme.textTheme.bodySmall,
                            ),
                          ),
                        ],
                      ],
                    ),
                  ],
                ),
              ),
            ],
          ),
          if (worker.serviceTypes.isNotEmpty) ...[
            const SizedBox(height: AppSpacing.sm),
            Wrap(
              spacing: AppSpacing.xxs + 2,
              runSpacing: AppSpacing.xxs + 2,
              children: [
                for (final service in worker.serviceTypes)
                  AppStatusChip(label: service.name, dense: true),
              ],
            ),
          ],
          if (worker.availabilityLabel.isNotEmpty ||
              worker.expectedMonthlyRate != null) ...[
            const SizedBox(height: AppSpacing.sm),
            const Divider(height: 1),
            const SizedBox(height: AppSpacing.xs + 2),
            Row(
              children: [
                if (worker.availabilityLabel.isNotEmpty) ...[
                  const Icon(
                    Icons.schedule_rounded,
                    size: AppIconSize.sm,
                    color: AppColors.textTertiary,
                  ),
                  const SizedBox(width: AppSpacing.xxs),
                  Expanded(
                    child: Text(
                      worker.availabilityLabel,
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: theme.textTheme.bodySmall,
                    ),
                  ),
                ] else
                  const Spacer(),
                if (worker.expectedMonthlyRate != null)
                  Text(
                    '₹${worker.expectedMonthlyRate}/mo',
                    style: const TextStyle(
                      fontSize: 15,
                      fontWeight: FontWeight.w700,
                      color: AppColors.primary,
                    ),
                  ),
              ],
            ),
          ],
        ],
      ),
    );
  }
}

/// The separator between inline stats. A dot rather than a pipe: lighter, and
/// it does not compete with the text either side of it.
class _Dot extends StatelessWidget {
  const _Dot();

  @override
  Widget build(BuildContext context) {
    return const Padding(
      padding: EdgeInsets.symmetric(horizontal: AppSpacing.xs),
      child: Text(
        '·',
        style: TextStyle(color: AppColors.textTertiary, fontSize: 13),
      ),
    );
  }
}
