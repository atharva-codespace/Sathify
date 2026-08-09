import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../../core/errors/api_exception.dart';
import '../../../../shared/design_system.dart';
import '../../../auth/data/models/user_model.dart';
import '../../../auth/presentation/providers/auth_provider.dart';
import '../providers/booking_provider.dart';
import '../widgets/booking_card.dart';

/// Module 5.2 / 5.4 — the caller's one-day bookings.
///
/// One screen serves both sides: the server returns the bookings the caller is
/// party to, and the role decides which actions are offered. A worker confirms
/// or declines; a resident cancels; either may cancel a confirmed job or mark a
/// finished one complete.
class MyBookingsScreen extends ConsumerWidget {
  const MyBookingsScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final bookings = ref.watch(bookingsProvider);
    final isWorker = ref.watch(authProvider).user?.role == UserRole.worker;

    return Scaffold(
      appBar: AppBar(
        titleSpacing: AppSpacing.gutter,
        title: Text(isWorker ? 'My jobs' : 'My bookings'),
      ),
      body: RefreshIndicator(
        onRefresh: () async => ref.invalidate(bookingsProvider),
        child: AppSwitcher(
          child: bookings.when(
            loading: () => const AppSkeletonList(count: 3),
            error: (error, _) => AppErrorState(
              message: error is ApiException
                  ? error.message
                  : 'Could not load bookings.',
              onRetry: () => ref.invalidate(bookingsProvider),
            ),
            data: (items) {
              if (items.isEmpty) {
                return AppEmptyState(
                  icon: Icons.event_busy_outlined,
                  title: 'No bookings yet',
                  message: isWorker
                      ? 'Mark the days you can take one-off work and requests '
                          'will appear here.'
                      : 'Book a one-day service to get started.',
                );
              }

              return ListView.builder(
                physics: const AlwaysScrollableScrollPhysics(),
                padding: const EdgeInsets.fromLTRB(
                  AppSpacing.gutter,
                  AppSpacing.sm,
                  AppSpacing.gutter,
                  AppSpacing.xxl,
                ),
                itemCount: items.length,
                itemBuilder: (context, index) => AppFadeIn(
                  index: index,
                  child: BookingCard(
                    booking: items[index],
                    isWorker: isWorker,
                  ),
                ),
              );
            },
          ),
        ),
      ),
    );
  }
}
