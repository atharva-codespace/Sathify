import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:go_router/go_router.dart';
import 'package:mocktail/mocktail.dart';
import 'package:sathify/core/routing/app_router.dart';
import 'package:sathify/features/auth/data/models/user_model.dart';
import 'package:sathify/features/auth/data/repositories/auth_repository.dart';
import 'package:sathify/features/auth/presentation/providers/auth_provider.dart';

/// The router is **constructed** here, and that is the whole point.
///
/// `flutter analyze` cannot see a GoRouter misconfiguration — the constructor
/// asserts at runtime, so an illegal combination of options compiles cleanly,
/// passes every model test, and then throws on the first frame on a real
/// device. That is exactly how "Only one of onException, errorPageBuilder, or
/// errorBuilder can be provided" reached a phone: nothing in the suite had ever
/// built the thing.
///
/// Anything added to `routerProvider` is covered by simply reading it.
class MockAuthRepository extends Mock implements AuthRepository {}

UserModel _resident() => const UserModel(
      id: 1,
      phoneNumber: '9876543210',
      firstName: 'Anita',
      lastName: 'Desai',
      role: UserRole.resident,
      societyId: 7,
      isApproved: true,
    );

/// [signedIn] matters for the not-found test: the router's `redirect` runs
/// before matching, and it bounces a signed-out caller to login for *any*
/// location — so an unroutable path would never reach `errorBuilder`. The real
/// case is somebody signed in who tapped a notification.
ProviderContainer _container({bool signedIn = false}) {
  final repository = MockAuthRepository();
  // `restoreSession` fires from build(), so these must be stubbed before the
  // provider is first read.
  when(repository.hasSession).thenAnswer((_) async => signedIn);
  when(repository.cachedUser).thenAnswer(
    (_) async => signedIn ? _resident() : null,
  );
  when(repository.fetchMe).thenAnswer((_) async => _resident());

  final container = ProviderContainer(
    overrides: [authRepositoryProvider.overrideWithValue(repository)],
  );
  addTearDown(container.dispose);
  return container;
}

void main() {
  group('routerProvider', () {
    test('constructs without tripping a go_router assertion', () {
      final router = _container().read(routerProvider);

      expect(router, isA<GoRouter>());
    });

    test('starts on the splash route', () {
      final router = _container().read(routerProvider);

      expect(
        router.configuration.findMatch(Uri.parse(Routes.splash)),
        isNotNull,
      );
    });

    test('every declared route constant resolves to a real route', () {
      // Guards against a `Routes.foo` that was added to the constants but never
      // registered — a runtime 404 that no compiler catches.
      final router = _container().read(routerProvider);

      for (final location in const [
        Routes.login,
        Routes.registerWorker,
        Routes.registerResident,
        Routes.pendingApproval,
        Routes.account,
        Routes.mySchedule,
        Routes.applyLeave,
        Routes.myBookings,
        Routes.engagements,
        Routes.hireRequests,
        Routes.complaints,
        Routes.raiseComplaint,
        Routes.notifications,
        Routes.payments,
        Routes.earnings,
        Routes.myGatePass,
        Routes.gateScanner,
        Routes.residentHome,
        Routes.workerHome,
        Routes.guardHome,
        Routes.adminHome,
      ]) {
        expect(
          router.configuration.findMatch(Uri.parse(location)),
          isNotNull,
          reason: '$location is declared in Routes but matches no GoRoute',
        );
      }
    });

    test('parameterised routes resolve when built through their helpers', () {
      final router = _container().read(routerProvider);

      for (final location in [
        Routes.leaveDetailPath(7),
        Routes.complaintPath(3),
        Routes.workerDetailPath(11),
        Routes.receiptPath('abc'),
      ]) {
        expect(
          router.configuration.findMatch(Uri.parse(location)),
          isNotNull,
          reason: '$location does not match any GoRoute',
        );
      }
    });
  });

  group('unroutable locations', () {
    testWidgets('land on the not-found screen instead of crashing',
        (tester) async {
      // The original bug: the server sent "/admin/complaints", which this build
      // has never had. Tapping the notification threw GoException.
      final container = _container(signedIn: true);
      final router = container.read(routerProvider);

      await tester.pumpWidget(
        UncontrolledProviderScope(
          container: container,
          child: MaterialApp.router(routerConfig: router),
        ),
      );
      // Explicit pumps, not pumpAndSettle: the splash screen carries an
      // indeterminate progress indicator that never stops animating, so
      // "settle" never arrives.
      await tester.pump(const Duration(milliseconds: 100));

      unawaited(router.push('/admin/complaints'));
      await tester.pump();
      await tester.pump(const Duration(milliseconds: 400));

      expect(find.byType(RouteNotFoundScreen), findsOneWidget);
      expect(find.text('We could not open that'), findsOneWidget);
    });
  });
}
