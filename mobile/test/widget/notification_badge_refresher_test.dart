import 'package:flutter/widgets.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:sathify/features/auth/presentation/providers/auth_provider.dart';
import 'package:sathify/features/notifications/presentation/providers/notification_provider.dart';
import 'package:sathify/features/notifications/presentation/widgets/notification_badge_refresher.dart';

/// Module 10.3 — the unread badge has to move on its own.
///
/// The bug: `unreadCountProvider` is cached for the app's lifetime, and the
/// only things that invalidated it were an FCM push (absent in any build
/// without `google-services.json`) and the user opening the notification
/// centre by hand. So a leave request raised the count server-side and the
/// household's badge stayed on zero.

/// A notifier with a fixed status, so the poll's auth guard can be exercised
/// without touching secure storage or the network.
class _FakeAuth extends AuthNotifier {
  _FakeAuth(this._status);

  final AuthStatus _status;

  @override
  AuthState build() => AuthState(status: _status);
}

void main() {
  /// Pumps the refresher over a counter-backed unread provider and returns a
  /// function reporting how many times the count has been fetched.
  Future<int Function()> pump(
    WidgetTester tester, {
    required AuthStatus status,
    Duration interval = const Duration(seconds: 60),
  }) async {
    var fetches = 0;

    await tester.pumpWidget(
      ProviderScope(
        overrides: [
          authProvider.overrideWith(() => _FakeAuth(status)),
          unreadCountProvider.overrideWith((ref) async {
            fetches += 1;
            return fetches;
          }),
        ],
        child: NotificationBadgeRefresher(
          interval: interval,
          // Something has to watch the provider, or invalidating it is inert.
          child: Consumer(
            builder: (_, ref, __) {
              final count = ref.watch(unreadCountProvider).valueOrNull ?? 0;
              return Directionality(
                textDirection: TextDirection.ltr,
                child: Text('unread $count'),
              );
            },
          ),
        ),
      ),
    );
    await tester.pump();
    return () => fetches;
  }

  testWidgets('the count is re-read on a timer while signed in',
      (tester) async {
    final fetches = await pump(
      tester,
      status: AuthStatus.authenticated,
      interval: const Duration(seconds: 1),
    );

    final initial = fetches();
    expect(initial, greaterThan(0));

    await tester.pump(const Duration(seconds: 1));
    await tester.pump();
    await tester.pump(const Duration(seconds: 1));
    await tester.pump();

    expect(
      fetches(),
      greaterThan(initial),
      reason: 'the badge must refresh without a push and without the user '
          'opening the notification centre',
    );

    // Let the periodic timer go before the test ends.
    await tester.pumpWidget(const SizedBox.shrink());
  });

  testWidgets('coming back to the foreground refreshes immediately',
      (tester) async {
    final fetches = await pump(tester, status: AuthStatus.authenticated);
    final initial = fetches();

    tester.binding.handleAppLifecycleStateChanged(AppLifecycleState.paused);
    tester.binding.handleAppLifecycleStateChanged(AppLifecycleState.resumed);
    await tester.pump();
    await tester.pump();

    expect(fetches(), greaterThan(initial));

    await tester.pumpWidget(const SizedBox.shrink());
  });

  testWidgets('a signed-out app does not poll', (tester) async {
    final fetches = await pump(
      tester,
      status: AuthStatus.unauthenticated,
      interval: const Duration(seconds: 1),
    );

    final initial = fetches();

    await tester.pump(const Duration(seconds: 1));
    await tester.pump();
    await tester.pump(const Duration(seconds: 1));
    await tester.pump();

    expect(
      fetches(),
      initial,
      reason: 'polling while signed out is a 401 on a loop',
    );

    await tester.pumpWidget(const SizedBox.shrink());
  });
}
