import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:sathify/core/errors/api_exception.dart';
import 'package:sathify/core/theme/app_theme.dart';
import 'package:sathify/features/auth/data/models/user_model.dart';
import 'package:sathify/features/auth/presentation/providers/auth_provider.dart';
import 'package:sathify/features/ratings/data/models/rating_models.dart';
import 'package:sathify/features/ratings/presentation/providers/rating_provider.dart';
import 'package:sathify/features/ratings/presentation/screens/rate_job_screen.dart';

/// Module 9.1 — the Rate Work screen must always render *something*.
///
/// Every pump overrides both providers and the signed-in user. The tab that is
/// off screen is not built, but a test that overrode only the visible one would
/// reach the live repository the moment somebody swiped — which is how a widget
/// test starts making network calls.
void main() {
  const resident = UserModel(
    id: 7,
    phoneNumber: '9800000002',
    firstName: 'Rohit',
    lastName: 'Kulkarni',
    role: UserRole.resident,
    isApproved: true,
  );

  const worker = UserModel(
    id: 3,
    phoneNumber: '9800000003',
    firstName: 'Sunita',
    lastName: 'Pawar',
    role: UserRole.worker,
    isApproved: true,
  );

  Future<void> pump(
    WidgetTester tester, {
    Override? pending,
    Override? mine,
    UserModel user = resident,
  }) async {
    await tester.pumpWidget(
      ProviderScope(
        overrides: [
          pending ??
              pendingRatingsProvider
                  .overrideWith((ref) async => <RateableJob>[]),
          mine ?? myRatingsProvider.overrideWith((ref) async => <Rating>[]),
          authProvider.overrideWith(() => _StubAuth(user)),
        ],
        // AppTheme.light, not a bare MaterialApp.
        //
        // These tests passed against the default Material theme while the
        // screen rendered nothing at all on a device: the app's own theme makes
        // every button full-width, which broke the ListTile these rows are
        // built from. A screen test that does not use the app's theme is not
        // testing the screen the user gets.
        child: MaterialApp(theme: AppTheme.light, home: const RateJobScreen()),
      ),
    );
  }

  RateableJob job(Map<String, dynamic> overrides) => RateableJob.fromJson({
        'kind': 'booking',
        'id': 1,
        'title': 'Festival or deep cleaning',
        'counterparty_name': 'Sunita D',
        'flat_label': 'A-301',
        'finished_on': '2026-08-01',
        ...overrides,
      });

  Rating rating(Map<String, dynamic> overrides) => Rating.fromJson({
        'id': 1,
        'stars': 4,
        'direction': 'resident_to_worker',
        'review': 'On time and thorough.',
        'rater': 7,
        'rater_name': 'Rohit K',
        'worker_name': 'Sunita D',
        'resident_name': 'Rohit K',
        'created_at': '2026-08-02T10:00:00Z',
        ...overrides,
      });

  testWidgets('shows the title bar while loading', (tester) async {
    await pump(
      tester,
      pending: pendingRatingsProvider.overrideWith((ref) => Future.any([])),
    );
    await tester.pump();

    expect(find.text('Rate your recent work'), findsOneWidget);
  });

  testWidgets('shows the empty state when there is nothing to rate',
      (tester) async {
    await pump(tester);
    await tester.pumpAndSettle();

    expect(find.text('Nothing to rate'), findsOneWidget);
    // The original complaint about this screen was that it said nothing and
    // offered nothing. The way out matters as much as the explanation.
    expect(find.text('See your bookings'), findsOneWidget);
  });

  testWidgets('offers a worker their own way out of the empty state',
      (tester) async {
    await pump(tester, user: worker);
    await tester.pumpAndSettle();

    expect(find.text('See your schedule'), findsOneWidget);
  });

  testWidgets('lists a rateable job and counts it in the tab', (tester) async {
    await pump(
      tester,
      pending:
          pendingRatingsProvider.overrideWith((ref) async => [job(const {})]),
    );
    await tester.pumpAndSettle();

    expect(find.text('Sunita D'), findsOneWidget);
    expect(find.text('Rate'), findsOneWidget);
    expect(find.text('To rate (1)'), findsOneWidget);
  });

  testWidgets('a full list actually lays out under the real theme',
      (tester) async {
    // The regression that made this screen useless. The rows laid out fine
    // against Material's defaults and failed against AppTheme, where the
    // trailing button wants infinite width; the error boundary swallowed one
    // failure per row, so the screen came out blank while the tab label still
    // reported the true count. A count with nothing under it is the signature.
    await pump(
      tester,
      pending: pendingRatingsProvider.overrideWith(
        (ref) async => [
          for (var index = 0; index < 7; index++) job({'id': index + 1}),
        ],
      ),
    );
    await tester.pumpAndSettle();

    expect(tester.takeException(), isNull);
    expect(find.text('To rate (7)'), findsOneWidget);
    // Not just "no exception" — rows have to be on screen. `findsWidgets`
    // rather than a count, because a ListView only builds what is visible.
    expect(find.text('Rate'), findsWidgets);
    expect(find.byType(ListTile), findsWidgets);
  });

  testWidgets('shows a readable error instead of a blank screen',
      (tester) async {
    await pump(
      tester,
      pending: pendingRatingsProvider.overrideWith(
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
      pending: pendingRatingsProvider.overrideWith((ref) async => [bare]),
    );
    await tester.pumpAndSettle();

    expect(tester.takeException(), isNull);
    expect(find.text('Rate'), findsOneWidget);
  });

  group('your ratings', () {
    Future<void> openTab(WidgetTester tester) async {
      await tester.pumpAndSettle();
      await tester.tap(find.text('Your ratings'));
      await tester.pumpAndSettle();
    }

    testWidgets('separates what you gave from what was said about you',
        (tester) async {
      await pump(
        tester,
        mine: myRatingsProvider.overrideWith(
          (ref) async => [
            rating(const {'id': 1, 'rater': 7}),
            rating(const {'id': 2, 'rater': 9, 'rater_name': 'Sunita D'}),
          ],
        ),
      );
      await openTab(tester);

      expect(find.text('You rated Sunita D'), findsOneWidget);
      expect(find.text('Sunita D rated you'), findsOneWidget);
    });

    testWidgets('says a withheld rating is not counted yet', (tester) async {
      await pump(
        tester,
        mine: myRatingsProvider.overrideWith(
          (ref) async => [
            rating(const {'rater': 7, 'is_flagged': true, 'is_withheld': true}),
          ],
        ),
      );
      await openTab(tester);

      expect(find.text('Under review before it counts'), findsOneWidget);
    });

    testWidgets('a dismissed flag no longer says "under review"',
        (tester) async {
      // `is_flagged` stays set forever as a historical marker; `is_withheld` is
      // what an administrator actually cleared. Keying the notice on the wrong
      // one tells somebody their rating is suppressed after it was restored.
      await pump(
        tester,
        mine: myRatingsProvider.overrideWith(
          (ref) async => [
            rating(
              const {'rater': 7, 'is_flagged': true, 'is_withheld': false},
            ),
          ],
        ),
      );
      await openTab(tester);

      expect(find.text('Under review before it counts'), findsNothing);
    });

    testWidgets('shows an empty state rather than a blank tab', (tester) async {
      await pump(tester);
      await openTab(tester);

      expect(find.text('No ratings yet'), findsOneWidget);
    });
  });
}

/// A signed-in user, without touching secure storage or the network.
class _StubAuth extends AuthNotifier {
  _StubAuth(this._user);

  final UserModel _user;

  @override
  AuthState build() => AuthState(status: AuthStatus.authenticated, user: _user);
}
