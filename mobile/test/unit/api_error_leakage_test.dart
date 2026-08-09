import 'package:flutter_test/flutter_test.dart';
import 'package:sathify/core/errors/api_exception.dart';

/// A server error must never put the server's own words on the screen.
///
/// `sathify_exception_handler` deliberately returns `None` for an unhandled
/// exception so Django's 500 handling runs and the traceback reaches the logs.
/// With DEBUG on that response carries the whole traceback, and
/// `ApiException.message` is rendered verbatim by every error state in the app
/// — so whatever lands in it is on a resident's screen.
void main() {
  /// A DEBUG-style 500 body, of the shape that reaches `fromJson`.
  const tracebackBody = <String, dynamic>{
    'exception': "IntegrityError at /api/v1/societies/claim-flat/",
    'traceback': 'File "/app/apps/societies/views.py", line 88, in post\n'
        '    resident.save()\n'
        'django.db.utils.IntegrityError: null value in column "flat_id"',
  };

  group('a non-enveloped error body', () {
    test('never leaks the body into the user-facing message', () {
      final error = ApiException.fromJson(tracebackBody, 500);

      expect(error.message, isNot(contains('traceback')));
      expect(error.message, isNot(contains('IntegrityError')));
      expect(error.message, isNot(contains('views.py')));
      expect(error.message, isNot(contains('django')));
    });

    test('says something a resident can act on instead', () {
      final error = ApiException.fromJson(tracebackBody, 500);

      expect(error.message, contains('our side'));
      expect(error.statusCode, 500);
    });

    test('distinguishes a 4xx, where retrying unchanged will not help', () {
      final error = ApiException.fromJson(const {'weird': 'shape'}, 400);

      expect(error.message, isNot(contains('our side')));
      expect(error.message, contains('try again'));
    });

    test("keeps DRF's own detail string, which is written to be read", () {
      final error = ApiException.fromJson(
        const {'detail': 'Authentication credentials were not provided.'},
        401,
      );

      expect(error.message, 'Authentication credentials were not provided.');
    });
  });

  group('the standard envelope still works', () {
    test('code, message and details survive', () {
      final error = ApiException.fromJson(
        const {
          'error': {
            'code': 'validation_error',
            'message': 'One or more fields failed validation.',
            'details': {
              'flat': ['This flat is already claimed.'],
            },
          },
        },
        400,
      );

      expect(error.code, 'validation_error');
      expect(error.message, 'One or more fields failed validation.');
      expect(error.fieldError('flat'), 'This flat is already claimed.');
    });
  });

  group('a body that was not JSON at all', () {
    test('an HTML error page produces a sentence, not markup', () {
      final error = ApiException.unreadableBody(500);

      expect(error.message, isNot(contains('<')));
      expect(error.message, contains('our side'));
      expect(error.statusCode, 500);
    });
  });
}
