import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../../../core/errors/api_exception.dart';
import '../../../../core/routing/app_router.dart';
import '../../../../shared/design_system.dart';
import '../../../auth/data/models/user_model.dart';
import '../../../auth/presentation/providers/auth_provider.dart';
import '../../data/models/rating_models.dart';
import '../providers/rating_provider.dart';
import '../widgets/rate_sheet.dart';
import '../widgets/rating_card.dart';

/// Module 9.1 — rate a completed job, and read what rating produced.
///
/// -----------------------------------------------------------------------
/// WHY THIS SCREEN HAS TWO TABS AND NOT ONE
/// -----------------------------------------------------------------------
/// It listed only what was still owed, so the common case — somebody who has
/// rated everything — got a blank page, and there was nowhere in the app to see
/// a rating you had given or one you had been given. That made the flow a dead
/// end in both directions: you could not check what you said, and you could not
/// see what was said about you, which is the half of a two-way system that
/// makes it worth participating in.
///
/// Both sides use this screen. The server decides which direction a rating runs
/// from the caller's role, so the app cannot rate on the wrong side, and the
/// copy is the only thing that differs.
class RateJobScreen extends ConsumerWidget {
  const RateJobScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    // The count is what makes an unrated job findable — the tab label is the
    // only place in the app that says work is waiting on you.
    final waiting = ref.watch(pendingRatingsProvider).valueOrNull?.length ?? 0;

    return DefaultTabController(
      length: 2,
      child: Scaffold(
        appBar: AppBar(
          title: const Text('Rate your recent work'),
          bottom: TabBar(
            tabs: [
              Tab(text: waiting == 0 ? 'To rate' : 'To rate ($waiting)'),
              const Tab(text: 'Your ratings'),
            ],
          ),
        ),
        body: const TabBarView(
          children: [_PendingTab(), _MyRatingsTab()],
        ),
      ),
    );
  }
}

// ---------------------------------------------------------------------------
// To rate
// ---------------------------------------------------------------------------

class _PendingTab extends ConsumerWidget {
  const _PendingTab();

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final pending = ref.watch(pendingRatingsProvider);

    return pending.when(
      loading: () => const AppSkeletonList(),
      error: (error, _) => AppErrorState(
        // A non-API failure — a parse error, a null where the server
        // promised a value — used to collapse into "Could not load your
        // jobs", which names no cause and leaves nothing to report. The
        // text is what makes the difference between a bug someone can chase
        // and a screen that is simply broken.
        message: error is ApiException
            ? error.message
            : 'Could not load your jobs.\n$error',
        onRetry: () => ref.invalidate(pendingRatingsProvider),
      ),
      data: (jobs) {
        if (jobs.isEmpty) {
          return const _NothingToRate();
        }
        return RefreshIndicator(
          onRefresh: () async => ref.invalidate(pendingRatingsProvider),
          child: ListView.builder(
            // Without this a short list is not scrollable, so the
            // RefreshIndicator wrapping it can never be pulled — every other
            // list in the app sets it for exactly that reason.
            physics: const AlwaysScrollableScrollPhysics(),
            padding: const EdgeInsets.symmetric(vertical: 8),
            itemCount: jobs.length,
            itemBuilder: (context, index) => _JobCard(job: jobs[index]),
          ),
        );
      },
    );
  }
}

/// The empty state, which is the state most users will see most of the time.
///
/// It says where a rateable job comes from and offers the screen it comes from,
/// because "Nothing to rate" on its own reads as a broken page rather than as
/// an answer — that was the original complaint about this screen.
class _NothingToRate extends ConsumerWidget {
  const _NothingToRate();

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final isWorker = ref.watch(authProvider).user?.role == UserRole.worker;

    return AppEmptyState(
      icon: Icons.star_border_rounded,
      title: 'Nothing to rate',
      message: isWorker
          ? 'A job lands here once a booking is marked complete, or once a '
              'regular arrangement ends. Rating the household counts towards '
              'their trust score, the same way theirs counts towards yours.'
          : 'A job lands here once a booking is marked complete, or once a '
              'regular arrangement ends. What you write is what the next '
              'household reads before they hire.',
      actionLabel: isWorker ? 'See your schedule' : 'See your bookings',
      onAction: () =>
          context.push(isWorker ? Routes.mySchedule : Routes.myBookings),
    );
  }
}

class _JobCard extends ConsumerWidget {
  const _JobCard({required this.job});

  final RateableJob job;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final theme = Theme.of(context);

    return Card(
      child: ListTile(
        contentPadding:
            const EdgeInsets.symmetric(horizontal: 16, vertical: 10),
        title: Text(
          job.counterpartyName.isEmpty ? job.title : job.counterpartyName,
          style: theme.textTheme.titleMedium
              ?.copyWith(fontWeight: FontWeight.w600),
        ),
        subtitle: Text(
          [
            job.title,
            if (job.flatLabel.isNotEmpty) job.flatLabel,
            if (job.finishedOn != null)
              'finished ${job.finishedOn!.day}/${job.finishedOn!.month}',
          ].join(' · '),
        ),
        trailing: FilledButton(
          // -----------------------------------------------------------------
          // WHY THIS BUTTON OVERRIDES ITS OWN MINIMUM SIZE
          // -----------------------------------------------------------------
          // AppTheme gives every FilledButton `minimumSize:
          // Size.fromHeight(minTouchTarget)`, and `Size.fromHeight` leaves the
          // *width* at `double.infinity` — deliberate, because buttons in this
          // app are full-width by default. As a `ListTile.trailing` that eats
          // the entire tile, and ListTile responds by asserting rather than
          // laying out.
          //
          // The row then renders as nothing at all, and note *why* the error
          // boundary does not save it: `ErrorWidget.builder` only substitutes
          // for exceptions thrown during build, and this one comes out of
          // layout. So the render object never gets a size, nothing is painted
          // for it, and the only trace is what `FlutterError.onError` logs. The
          // list came out blank while still reporting its true length — which is
          // exactly what "7 jobs to rate, empty screen" looked like.
          //
          // Only the width is relaxed. The height is the minimum touch target
          // and SRS 5.4 is the reason it exists.
          style: FilledButton.styleFrom(
            minimumSize: const Size(0, AppTheme.minTouchTarget),
          ),
          onPressed: () => showRateJobSheet(context, ref, job),
          child: const Text('Rate'),
        ),
      ),
    );
  }
}

// ---------------------------------------------------------------------------
// Your ratings
// ---------------------------------------------------------------------------

class _MyRatingsTab extends ConsumerWidget {
  const _MyRatingsTab();

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final ratings = ref.watch(myRatingsProvider);
    final myUserId = ref.watch(authProvider).user?.id ?? 0;

    return ratings.when(
      loading: () => const AppSkeletonList(),
      error: (error, _) => AppErrorState(
        message: error is ApiException
            ? error.message
            : 'Could not load your ratings.\n$error',
        onRetry: () => ref.invalidate(myRatingsProvider),
      ),
      data: (all) {
        if (all.isEmpty) {
          return const AppEmptyState(
            icon: Icons.reviews_outlined,
            title: 'No ratings yet',
            message: 'Ratings you give, and ratings people give you, both '
                'appear here — including any still being checked.',
          );
        }

        return RefreshIndicator(
          onRefresh: () async => ref.invalidate(myRatingsProvider),
          child: ListView(
            physics: const AlwaysScrollableScrollPhysics(),
            padding: const EdgeInsets.only(bottom: 24),
            children: _sections(all, myUserId),
          ),
        );
      },
    );
  }

  /// Splits the list into what was said about you and what you said.
  ///
  /// Falls back to one undivided list when the signed-in user is not known,
  /// rather than guessing: the endpoint returns both directions in one list,
  /// and labelling a rating "you rated them" when it is the reverse would
  /// invert its meaning entirely.
  List<Widget> _sections(List<Rating> all, int myUserId) {
    if (myUserId == 0) {
      return [
        const AppSectionHeader(title: 'Your ratings'),
        ...all.map((rating) => RatingCard(rating: rating, showStatus: true)),
      ];
    }

    final received =
        all.where((rating) => rating.raterId != myUserId).toList();
    final given = all.where((rating) => rating.raterId == myUserId).toList();

    return [
      if (received.isNotEmpty) ...[
        const AppSectionHeader(title: 'About you'),
        ...received.map(
          (rating) => RatingCard(
            rating: rating,
            attribution: rating.raterName.isEmpty
                ? 'Someone rated you'
                : '${rating.raterName} rated you',
            showStatus: true,
          ),
        ),
      ],
      if (given.isNotEmpty) ...[
        const AppSectionHeader(title: 'You rated'),
        ...given.map(
          (rating) => RatingCard(
            rating: rating,
            attribution: _givenLine(rating),
            showStatus: true,
          ),
        ),
      ],
    ];
  }

  String _givenLine(Rating rating) {
    // Who the rating was *about* is the far side of its direction, not
    // `subject_is_worker` read off the row — that says the same thing, but this
    // is the field the label is actually describing.
    final subject = rating.direction == RatingDirection.residentToWorker
        ? rating.workerName
        : rating.residentName;
    return subject.isEmpty ? 'You rated this job' : 'You rated $subject';
  }
}
