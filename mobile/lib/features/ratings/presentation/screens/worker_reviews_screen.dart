import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../../../core/errors/api_exception.dart';
import '../../../../core/routing/app_router.dart';
import '../../../../shared/design_system.dart';
import '../providers/rating_provider.dart';
import '../widgets/rating_card.dart';

/// Modules 9.1 and 9.2 — what residents have said about a worker.
///
/// Withheld ratings never arrive here; the server excludes them. A rating under
/// review has not been judged genuine, and showing it would let the flagging
/// system be bypassed simply by the rating being visible.
class WorkerReviewsScreen extends ConsumerWidget {
  const WorkerReviewsScreen({required this.workerId, super.key});

  final int workerId;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final reviews = ref.watch(workerRatingsProvider(workerId));

    return Scaffold(
      appBar: AppBar(
        title: const Text('Reviews'),
        actions: [
          IconButton(
            tooltip: 'Trust score',
            icon: const Icon(Icons.verified_outlined),
            onPressed: () => context.push(Routes.workerTrustPath(workerId)),
          ),
        ],
      ),
      body: reviews.when(
        loading: () => const AppSkeletonList(),
        error: (error, _) => AppErrorState(
          message:
              error is ApiException ? error.message : 'Could not load reviews.',
          onRetry: () => ref.invalidate(workerRatingsProvider(workerId)),
        ),
        data: (items) {
          if (items.isEmpty) {
            return const Center(
              child: Padding(
                padding: EdgeInsets.all(32),
                child: Text(
                  'No reviews yet.\n\nA new worker has no history — that is not '
                  'the same as a poor one.',
                  textAlign: TextAlign.center,
                ),
              ),
            );
          }
          return RefreshIndicator(
            onRefresh: () async =>
                ref.invalidate(workerRatingsProvider(workerId)),
            child: ListView.builder(
              padding: const EdgeInsets.symmetric(vertical: 8),
              itemCount: items.length,
              itemBuilder: (context, index) =>
                  RatingCard(rating: items[index]),
            ),
          );
        },
      ),
    );
  }
}
