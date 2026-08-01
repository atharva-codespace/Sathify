import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:mocktail/mocktail.dart';
import 'package:sathify/core/errors/api_exception.dart';
import 'package:sathify/core/routing/app_router.dart';
import 'package:sathify/features/auth/data/models/user_model.dart';
import 'package:sathify/features/auth/data/repositories/auth_repository.dart';
import 'package:sathify/features/auth/presentation/providers/auth_provider.dart';

/// `implements` rather than `extends` so no real ApiClient (and therefore no
/// dotenv lookup) is constructed during tests.
class MockAuthRepository extends Mock implements AuthRepository {}

UserModel _user({
  UserRole role = UserRole.resident,
  bool isApproved = true,
}) {
  return UserModel(
    id: 1,
    phoneNumber: '9876543210',
    firstName: 'Anita',
    lastName: 'Desai',
    role: role,
    societyId: 7,
    isApproved: isApproved,
  );
}

/// Builds a container with the auth repository replaced by [mock].
///
/// `restoreSession` fires from `build()`, so the mock must have a stub for
/// `hasSession` before the provider is first read.
ProviderContainer _containerWith(MockAuthRepository mock) {
  final container = ProviderContainer(
    overrides: [authRepositoryProvider.overrideWithValue(mock)],
  );
  addTearDown(container.dispose);
  return container;
}

void main() {
  group('role routing', () {
    test('each role maps to its own home route', () {
      expect(homeRouteForRole(UserRole.resident), Routes.residentHome);
      expect(homeRouteForRole(UserRole.worker), Routes.workerHome);
      expect(homeRouteForRole(UserRole.guard), Routes.guardHome);
      expect(homeRouteForRole(UserRole.societyAdmin), Routes.adminHome);
    });

    test('an unrecognised role falls back to login rather than a dashboard', () {
      expect(homeRouteForRole(UserRole.unknown), Routes.login);
    });
  });

  group('UserRole wire values', () {
    test('match the Django Role choices exactly', () {
      // These strings cross the wire and appear as a JWT claim; a mismatch
      // would silently route every user to the wrong dashboard.
      expect(UserRole.resident.wireValue, 'resident');
      expect(UserRole.worker.wireValue, 'worker');
      expect(UserRole.guard.wireValue, 'guard');
      expect(UserRole.societyAdmin.wireValue, 'society_admin');
    });

    test('an unknown value from the server does not crash parsing', () {
      expect(UserRole.fromWire('mayor'), UserRole.unknown);
      expect(UserRole.fromWire(null), UserRole.unknown);
    });
  });

  group('AuthNotifier.login', () {
    late MockAuthRepository repository;

    setUp(() {
      repository = MockAuthRepository();
      when(() => repository.hasSession()).thenAnswer((_) async => false);
    });

    test('an approved user becomes authenticated', () async {
      when(
        () => repository.login(
          phoneNumber: any(named: 'phoneNumber'),
          password: any(named: 'password'),
        ),
      ).thenAnswer((_) async => _user());

      final container = _containerWith(repository);
      await container
          .read(authProvider.notifier)
          .login(phoneNumber: '9876543210', password: 'pw');

      final state = container.read(authProvider);
      expect(state.status, AuthStatus.authenticated);
      expect(state.user?.role, UserRole.resident);
    });

    test('an unapproved user lands on pendingApproval, not authenticated', () async {
      // Registration alone grants nothing (SRS 3.1/3.2) — but the user must
      // still reach an explanatory screen rather than a dead end.
      when(
        () => repository.login(
          phoneNumber: any(named: 'phoneNumber'),
          password: any(named: 'password'),
        ),
      ).thenAnswer((_) async => _user(role: UserRole.worker, isApproved: false));

      final container = _containerWith(repository);
      await container
          .read(authProvider.notifier)
          .login(phoneNumber: '9876543210', password: 'pw');

      expect(container.read(authProvider).status, AuthStatus.pendingApproval);
    });

    test('bad credentials surface a message that does not confirm the account exists',
        () async {
      when(
        () => repository.login(
          phoneNumber: any(named: 'phoneNumber'),
          password: any(named: 'password'),
        ),
      ).thenThrow(
        const ApiException(
          code: 'authentication_failed',
          message: 'No active account found with the given credentials',
          statusCode: 401,
        ),
      );

      final container = _containerWith(repository);
      await container
          .read(authProvider.notifier)
          .login(phoneNumber: '9876543210', password: 'wrong');

      final state = container.read(authProvider);
      expect(state.status, AuthStatus.unauthenticated);
      expect(state.errorMessage, 'Incorrect phone number or password.');
      expect(state.isSubmitting, isFalse);
    });

    test('server field errors are exposed per field for inline display', () async {
      when(
        () => repository.login(
          phoneNumber: any(named: 'phoneNumber'),
          password: any(named: 'password'),
        ),
      ).thenThrow(
        const ApiException(
          code: 'validation_error',
          message: 'One or more fields failed validation.',
          details: {
            'phone_number': ['Enter a valid Indian mobile number.'],
          },
          statusCode: 400,
        ),
      );

      final container = _containerWith(repository);
      await container
          .read(authProvider.notifier)
          .login(phoneNumber: 'bad', password: 'pw');

      expect(
        container.read(authProvider).fieldErrors['phone_number'],
        'Enter a valid Indian mobile number.',
      );
    });
  });

  group('AuthNotifier.restoreSession', () {
    test('no stored session means unauthenticated', () async {
      final repository = MockAuthRepository();
      when(() => repository.hasSession()).thenAnswer((_) async => false);

      final container = _containerWith(repository);
      await container.read(authProvider.notifier).restoreSession();

      expect(container.read(authProvider).status, AuthStatus.unauthenticated);
    });

    test('offline start keeps the cached profile usable', () async {
      // Render's free tier sleeps, so a cold start can fail or take ~50s. A
      // cached profile must not force the user back to the login screen.
      final repository = MockAuthRepository();
      when(() => repository.hasSession()).thenAnswer((_) async => true);
      when(() => repository.cachedUser()).thenAnswer((_) async => _user());
      when(() => repository.fetchMe()).thenThrow(const ApiException.offline());

      final container = _containerWith(repository);
      await container.read(authProvider.notifier).restoreSession();

      final state = container.read(authProvider);
      expect(state.status, AuthStatus.authenticated);
      expect(state.user?.phoneNumber, '9876543210');
    });

    test('a rejected session signs the user out', () async {
      final repository = MockAuthRepository();
      when(() => repository.hasSession()).thenAnswer((_) async => true);
      when(() => repository.cachedUser()).thenAnswer((_) async => _user());
      when(() => repository.fetchMe()).thenThrow(
        const ApiException(
          code: 'authentication_failed',
          message: 'Token is invalid',
          statusCode: 401,
        ),
      );
      when(() => repository.logout()).thenAnswer((_) async {});

      final container = _containerWith(repository);
      await container.read(authProvider.notifier).restoreSession();

      expect(container.read(authProvider).status, AuthStatus.unauthenticated);
      // Not an exact count: AuthNotifier.build() kicks off its own restore, so
      // reading the notifier and then calling restoreSession legitimately runs
      // it twice. What matters is that a rejected session logs out at all —
      // asserting the incidental call count would make this test fail on a
      // change to when restoration is triggered, which is not what it is for.
      verify(() => repository.logout()).called(greaterThanOrEqualTo(1));
    });
  });

  group('UserModel', () {
    test('round-trips through JSON', () {
      final original = _user(role: UserRole.guard);
      final restored = UserModel.fromJson(original.toJson());

      expect(restored.id, original.id);
      expect(restored.role, UserRole.guard);
      expect(restored.societyId, original.societyId);
      expect(restored.isApproved, original.isApproved);
    });

    test('falls back to the phone number when no name is set', () {
      const user = UserModel(
        id: 2,
        phoneNumber: '9800000000',
        role: UserRole.worker,
        isApproved: false,
      );
      expect(user.fullName, '9800000000');
    });
  });
}
