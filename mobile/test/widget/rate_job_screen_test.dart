import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:sathify/core/errors/api_exception.dart';
import 'package:sathify/features/ratings/data/models/rating_models.dart';
import 'package:sathify/features/ratings/presentation/providers/rating_provider.dart';
import 'package:sathify/features/ratings/presentation/screens/rate_job_screen.dart';

/// Module 9.1 — the Rate Work screen must always render *something*.
void main() {
  Future<void> pump(
    WidgetTester tester,
    Override override,
  ) async {
    await tester.pumpWidget(
      ProviderScope(
        overrides: [override],
        child: const MaterialApp(home: RateJobScreen()),
      ),
    );
  }

  RateableJob job(Map<String, dynamic> overrides) =>
      RateableJob.fromJson({
        'kind': 'booking',
        'id': 1,
        'title': 'Festival or deep cleaning',
        'counterparty_name': 'Sunita D',
        'flat_label': 'A-301',
        'finished_on': '2026-08-01',
        ...overrides,
      });

  testWidgets('shows the title bar while loading', (tester) async {
    await pump(
      tester,
      pendingRatingsProvider.overrideWith((ref) => Future.any([])),
    );
    await tester.pump();

    expect(find.text('Rate your recent work'), findsOneWidget);
  });

  testWidgets('shows the empty state when there is nothing to rate',
      (tester) async {
    await pump(
      tester,
      pendingRatingsProvider.overrideWith((ref) async => <RateableJob>[]),
    );
    await tester.pumpAndSettle();

    expect(find.text('Nothing to rate'), findsOneWidget);
  });

  testWidgets('lists a rateable job', (tester) async {
    await pump(
      tester,
      pendingRatingsProvider.overrideWith((ref) async => [job(const {})]),
    );
    await tester.pumpAndSettle();

    expect(find.text('Sunita D'), findsOneWidget);
    expect(find.text('Rate'), findsOneWidget);
  });

  testWidgets('shows a readable error instead of a blank screen',
      (tester) async {
    await pump(
      tester,
      pendingRatingsProvider.overrideWith(
        (ref) async =>
            throw const ApiException(code: 'error', message: 'Server error'),
      ),
    );
    await tester.pumpAndSettle();

    expect(find.text('Rate your recent work'), findsOneWidget);
    expect(find.textContaining('Server error'), findsOneWidget);
  });

  testWidgets('a job with every optional field missing still renders',
      (tester) async {
    // The blank-screen candidate: a row where the server sent nothing but the
    // bare minimum. Every optional getter must tolerate it.
    final bare = RateableJob.fromJson(const {'kind': 'booking', 'id': 2});

    await pump(
      tester,
      pendingRatingsProvider.overrideWith((ref) async => [bare]),
    );
    await tester.pumpAndSettle();

    expect(tester.takeException(), isNull);
    expect(find.text('Rate'), findsOneWidget);
  });
}
