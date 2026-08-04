import 'package:flutter_dotenv/flutter_dotenv.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:sathify/core/config/app_config.dart';

/// Locks in how the API base URL is resolved.
///
/// Worth asserting because four people run this app against four different
/// backends — an emulator, a physical device on a LAN, a desktop debug build,
/// and eventually Render — and the precedence between the places the URL can
/// come from is the difference between "the app loads nothing" and "the app
/// works". README documents this order; these tests are what keep the
/// documentation true.
///
/// The `--dart-define=API_BASE_URL=...` tier is deliberately not asserted here.
/// It is a compile-time constant, so exercising it means running this file with
/// the define set — at which point it correctly overrides every `.env` value
/// below and the rest of the group could not hold. Check it by hand instead:
///
///     flutter test test/unit/app_config_test.dart \
///         --dart-define=API_BASE_URL=http://from-define/api/v1
///
/// Every test below failing is the *expected* result of that run, and is what
/// demonstrates the override wins.
void main() {
  group('AppConfig.apiBaseUrl', () {
    tearDown(dotenv.clean);

    test('uses API_BASE_URL from .env when it is set', () {
      dotenv.testLoad(fileInput: 'API_BASE_URL=http://192.168.1.42:8000/api/v1');

      expect(AppConfig.apiBaseUrl, 'http://192.168.1.42:8000/api/v1');
    });

    test('falls back to the local default when .env has no value', () {
      // The promise the README makes: a fresh clone with an unedited (or
      // absent) .env still talks to a local `manage.py runserver`.
      dotenv.testLoad(fileInput: '');

      expect(AppConfig.apiBaseUrl, AppConfig.defaultApiBaseUrl);
      expect(AppConfig.apiBaseUrl, endsWith(':8000/api/v1'));
    });

    test('treats a blank API_BASE_URL as unset rather than as an empty URL', () {
      // `.env.example` ships keys with empty values, and an empty baseUrl would
      // make Dio issue requests against relative paths that fail confusingly.
      dotenv.testLoad(fileInput: 'API_BASE_URL=\nAPI_TIMEOUT_SECONDS=60');

      expect(AppConfig.apiBaseUrl, AppConfig.defaultApiBaseUrl);
    });

  });

  group('AppConfig.defaultApiBaseUrl', () {
    test('points at the port manage.py runserver uses', () {
      expect(AppConfig.defaultBackendPort, 8000);
      expect(
        AppConfig.defaultApiBaseUrl,
        anyOf(
          'http://10.0.2.2:8000/api/v1', // Android: emulator -> host alias
          'http://127.0.0.1:8000/api/v1', // everything else
        ),
      );
    });
  });
}
