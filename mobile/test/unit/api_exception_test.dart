import 'package:flutter_test/flutter_test.dart';
import 'package:sathify/core/errors/api_exception.dart';

/// Verifies the Dart side of the error contract shared with the Django
/// `sathify_exception_handler`. If these break, error handling silently
/// degrades across every screen in the app, so they are worth asserting early.
///
/// NOT YET EXECUTED — the Flutter SDK is not installed on this machine. Run
/// `flutter test` once it is.
void main() {
  group('ApiException.fromJson', () {
    test('parses the standard server error envelope', () {
      final exception = ApiException.fromJson(
        {
          'error': {
            'code': 'validation_error',
            'message': 'One or more fields failed validation.',
            'details': {
              'aadhaar_number': ['Checksum validation failed.'],
            },
          },
        },
        400,
      );

      expect(exception.code, 'validation_error');
      expect(exception.statusCode, 400);
      expect(
        exception.fieldError('aadhaar_number'),
        'Checksum validation failed.',
      );
    });

    test('falls back gracefully on an unexpected body shape', () {
      final exception = ApiException.fromJson({'unexpected': true}, 500);

      expect(exception.code, 'error');
      expect(exception.message, isNotEmpty);
    });

    test('returns null for a field the server did not flag', () {
      final exception = ApiException.fromJson(
        {
          'error': {'code': 'validation_error', 'message': 'bad', 'details': {}},
        },
        400,
      );

      expect(exception.fieldError('name'), isNull);
    });
  });

  group('convenience predicates', () {
    test('offline exceptions are identifiable so callers can queue locally', () {
      // Modules 7 and 13 rely on this: a gate entry decision must never be
      // blocked by connectivity, so offline is handled distinctly from failure.
      const exception = ApiException.offline();

      expect(exception.isOffline, isTrue);
      expect(exception.code, 'offline');
    });

    test('offline and unreachable are told apart for the user', () {
      // The two have different remedies, so they must not share wording:
      // checking the phone's connection does nothing when the server is down.
      const offline = ApiException.offline();
      const unreachable = ApiException.unreachable();

      expect(unreachable.code, 'unreachable');
      expect(unreachable.isUnreachable, isTrue);
      expect(unreachable.isOffline, isFalse);
      expect(offline.isUnreachable, isFalse);
      expect(offline.message, isNot(unreachable.message));
    });

    test('both connection failures still route to the local queue', () {
      // The guard-facing behaviour must not change with the message split:
      // attendance falls back to its cache on isConnectionFailure, so a server
      // that is merely down has to queue exactly like a dead radio does.
      expect(const ApiException.offline().isConnectionFailure, isTrue);
      expect(const ApiException.unreachable().isConnectionFailure, isTrue);
      expect(const ApiException.timeout().isConnectionFailure, isFalse);
      expect(
        const ApiException(code: 'validation_error', message: 'Bad.')
            .isConnectionFailure,
        isFalse,
      );
    });

    test('no connection message claims the action was saved', () {
      // Regression guard for a login screen that said "your action has been
      // queued" while saving nothing. Only the caller knows if it queued.
      expect(const ApiException.offline().message, isNot(contains('queued')));
      expect(
        const ApiException.unreachable().message,
        isNot(contains('queued')),
      );
    });

    test('auth and permission failures are distinguishable', () {
      const authFailure = ApiException(
        code: 'authentication_failed',
        message: 'Token expired.',
      );
      const permissionFailure = ApiException(
        code: 'permission_denied',
        message: 'Guards cannot approve workers.',
      );

      expect(authFailure.isAuthFailure, isTrue);
      expect(authFailure.isPermissionDenied, isFalse);
      expect(permissionFailure.isPermissionDenied, isTrue);
    });
  });
}
