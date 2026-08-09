import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../../../core/errors/api_exception.dart';
import '../../../../core/routing/app_router.dart';
import '../../../../shared/design_system.dart';
import '../../data/models/booking_models.dart';
import '../providers/booking_provider.dart';
import '../widgets/category_icon.dart';

/// Module 5.1 — the service catalogue, and the entry point to booking.
///
/// Duration and price guidance are shown on the card rather than behind a tap:
/// the spec asks for them "up front", and a resident deciding whether to book
/// at all needs the rough cost before they invest in picking a date.
///
/// Laid out as a two-column grid rather than the list it was. Every reference
/// app presents its catalogue this way, and the reason is practical rather than
/// decorative: a grid puts roughly twice as many services on one screen, and
/// choosing a category is a scanning task, not a reading one.
class ServiceCatalogueScreen extends ConsumerWidget {
  const ServiceCatalogueScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final categories = ref.watch(serviceCategoriesProvider);

    return Scaffold(
      appBar: AppBar(
        titleSpacing: AppSpacing.gutter,
        title: const Text('Book a service'),
        actions: [
          IconButton(
            tooltip: 'My bookings',
            icon: const Icon(Icons.event_note_outlined),
            onPressed: () => context.push(Routes.myBookings),
          ),
          const SizedBox(width: AppSpacing.xs),
        ],
      ),
      body: RefreshIndicator(
        onRefresh: () async => ref.invalidate(serviceCategoriesProvider),
        child: AppSwitcher(
          child: categories.when(
            loading: () => const _CatalogueSkeleton(),
            error: (error, _) => AppErrorState(
              message: error is ApiException
                  ? error.message
                  : 'Could not load the service list.',
              onRetry: () => ref.invalidate(serviceCategoriesProvider),
            ),
            data: (items) {
              if (items.isEmpty) {
                return const AppEmptyState(
                  icon: Icons.home_repair_service_outlined,
                  title: 'No services yet',
                  message: 'Your society administrator configures which '
                      'services are offered here.',
                );
              }
              return CustomScrollView(
                physics: const AlwaysScrollableScrollPhysics(),
                slivers: [
                  // Module 5.5 — the way out of this screen for somebody who
                  // cannot use it. Choosing a category, then a date, then a
                  // worker is three screens of decisions, and a household with
                  // water coming through the ceiling has none to spare. It sits
                  // above the grid because that is where somebody in a hurry
                  // looks first.
                  const SliverToBoxAdapter(child: AppFadeIn(child: _UrgentBanner())),
                  const SliverToBoxAdapter(
                    child: AppFadeIn(
                      child: AppSectionHeader(
                        title: 'What do you need?',
                        subtitle: 'One-day help, booked for a date and time',
                        padding: EdgeInsets.fromLTRB(
                          AppSpacing.gutter,
                          AppSpacing.xs,
                          AppSpacing.gutter,
                          AppSpacing.md,
                        ),
                      ),
                    ),
                  ),
                  SliverPadding(
                    padding: const EdgeInsets.fromLTRB(
                      AppSpacing.gutter,
                      0,
                      AppSpacing.gutter,
                      AppSpacing.xxl,
                    ),
                    sliver: SliverGrid.builder(
                      gridDelegate:
                          const SliverGridDelegateWithFixedCrossAxisCount(
                        crossAxisCount: 2,
                        mainAxisSpacing: AppSpacing.sm,
                        crossAxisSpacing: AppSpacing.sm,
                        // Taller than wide: the name can run to two lines and
                        // the price row still has room beneath it.
                        childAspectRatio: 0.86,
                      ),
                      itemCount: items.length,
                      itemBuilder: (context, index) => AppFadeIn(
                        index: index,
                        child: _CategoryCard(category: items[index]),
                      ),
                    ),
                  ),
                ],
              );
            },
          ),
        ),
      ),
    );
  }
}

/// The shortcut into Module 5.5's broadcast flow.
class _UrgentBanner extends StatelessWidget {
  const _UrgentBanner();

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.fromLTRB(
        AppSpacing.gutter,
        AppSpacing.md,
        AppSpacing.gutter,
        0,
      ),
      child: AppCard(
        onTap: () => context.push(Routes.emergency),
        child: Row(
          children: [
            Container(
              width: 44,
              height: 44,
              decoration: const BoxDecoration(
                color: AppColors.dangerSoft,
                shape: BoxShape.circle,
              ),
              child: const Icon(
                Icons.bolt_rounded,
                size: AppIconSize.md,
                color: AppColors.danger,
              ),
            ),
            const SizedBox(width: AppSpacing.sm),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    'Need someone right now?',
                    style: Theme.of(context).textTheme.titleSmall,
                  ),
                  const SizedBox(height: 2),
                  Text(
                    'Sent to everyone free nearby. No need to choose.',
                    style: Theme.of(context).textTheme.bodySmall,
                  ),
                ],
              ),
            ),
            const Icon(
              Icons.chevron_right_rounded,
              color: AppColors.textTertiary,
              size: AppIconSize.md,
            ),
          ],
        ),
      ),
    );
  }
}

class _CategoryCard extends StatelessWidget {
  const _CategoryCard({required this.category});

  final ServiceCategory category;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);

    return AppCard(
      onTap: () => context.push(Routes.bookSlotPath(category.id)),
      padding: const EdgeInsets.all(AppSpacing.sm),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Container(
                width: 46,
                height: 46,
                decoration: const BoxDecoration(
                  color: AppColors.primarySoft,
                  shape: BoxShape.circle,
                ),
                child: Icon(
                  iconForCategory(category.icon),
                  size: AppIconSize.lg - 2,
                  color: AppColors.primary,
                ),
              ),
              const Spacer(),
              if (category.bypassesNoticePeriod)
                const AppStatusChip(
                  label: 'Urgent',
                  tone: AppTone.danger,
                  dense: true,
                ),
            ],
          ),
          const SizedBox(height: AppSpacing.xs + 2),
          Text(
            category.name,
            maxLines: 2,
            overflow: TextOverflow.ellipsis,
            style: theme.textTheme.titleSmall,
          ),
          const SizedBox(height: 2),
          Text(
            'About ${category.durationLabel}',
            maxLines: 1,
            overflow: TextOverflow.ellipsis,
            style: theme.textTheme.bodySmall,
          ),
          const Spacer(),
          Text(
            category.priceGuidance.isNotEmpty
                ? category.priceGuidance
                : '₹${category.priceMin}–₹${category.priceMax}',
            maxLines: 1,
            overflow: TextOverflow.ellipsis,
            style: const TextStyle(
              fontSize: 14,
              fontWeight: FontWeight.w700,
              color: AppColors.primary,
            ),
          ),
        ],
      ),
    );
  }
}

/// Matches the grid geometry so the page does not reflow when data lands.
class _CatalogueSkeleton extends StatelessWidget {
  const _CatalogueSkeleton();

  @override
  Widget build(BuildContext context) {
    return GridView.builder(
      padding: const EdgeInsets.fromLTRB(
        AppSpacing.gutter,
        AppSpacing.huge,
        AppSpacing.gutter,
        AppSpacing.xxl,
      ),
      physics: const NeverScrollableScrollPhysics(),
      gridDelegate: const SliverGridDelegateWithFixedCrossAxisCount(
        crossAxisCount: 2,
        mainAxisSpacing: AppSpacing.sm,
        crossAxisSpacing: AppSpacing.sm,
        childAspectRatio: 0.86,
      ),
      itemCount: 6,
      itemBuilder: (_, __) => Container(
        padding: const EdgeInsets.all(AppSpacing.sm),
        decoration: BoxDecoration(
          color: AppColors.surface,
          borderRadius: AppRadius.card,
          border: Border.all(color: AppColors.border),
        ),
        child: const Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            AppSkeleton.circle(size: 46),
            SizedBox(height: AppSpacing.sm),
            AppSkeleton(width: 96, height: 14),
            SizedBox(height: AppSpacing.xs),
            AppSkeleton(width: 70, height: 11),
            Spacer(),
            AppSkeleton(width: 84, height: 13),
          ],
        ),
      ),
    );
  }
}
