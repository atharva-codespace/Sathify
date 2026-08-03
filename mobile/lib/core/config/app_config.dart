import 'package:flutter/foundation.dart';
import 'package:flutter_dotenv/flutter_dotenv.dart';

/// Typed access to everything in `.env`.
///
/// Read configuration through this class rather than calling `dotenv.env[...]`
/// at call sites: a typo in a raw string key fails silently at runtime, whereas
/// a typo in a getter name fails at compile time.
///
/// Remember that `.env` ships inside the APK and is readable by anyone who
/// downloads it. Nothing secret belongs here — see `.env.example`.
class AppConfig {
  const AppConfig._();

  /// `--dart-define=API_BASE_URL=...`, or empty when not passed.
  ///
  /// `String.fromEnvironment` must be a compile-time constant, so this cannot
  /// be folded into a generic helper keyed by name. Only the base URL gets the
  /// treatment: it is the one value that legitimately differs per *run* rather
  /// than per developer.
  static const String _apiBaseUrlOverride =
      String.fromEnvironment('API_BASE_URL');

  /// Base URL of the Django REST API, including the `/api/v1` prefix.
  ///
  /// Resolved most-specific-first:
  ///
  /// 1. `--dart-define=API_BASE_URL=...` — a per-run override that edits no
  ///    file. This is how you point a physical-device build at your laptop's
  ///    LAN IP, or a release build at Render, without touching the `.env` that
  ///    the rest of your work depends on.
  /// 2. `API_BASE_URL` in `.env` — the normal per-developer setting.
  /// 3. [defaultApiBaseUrl] — so the app still talks to a local
  ///    `manage.py runserver` with an unedited `.env`.
  static String get apiBaseUrl {
    if (_apiBaseUrlOverride.isNotEmpty) return _apiBaseUrlOverride;

    final fromEnv = dotenv.env['API_BASE_URL'];
    if (fromEnv != null && fromEnv.isNotEmpty) return fromEnv;

    return defaultApiBaseUrl;
  }

  /// The port `manage.py runserver` listens on out of the box.
  static const int defaultBackendPort = 8000;

  /// Where the API is assumed to live when nothing has been configured.
  ///
  /// Derived rather than written down as one literal, because no single
  /// literal is right: an Android emulator reaches the host machine at
  /// `10.0.2.2`, while a web or desktop debug build reaches it at `127.0.0.1`.
  /// Hardcoding either one sends half the team debugging a connection failure
  /// that is really a config default.
  ///
  /// A *physical* Android device can match neither — it needs your laptop's
  /// LAN IP, which nothing can infer. That case is what `.env` and the
  /// `--dart-define` override above exist for.
  static String get defaultApiBaseUrl {
    final host =
        defaultTargetPlatform == TargetPlatform.android ? '10.0.2.2' : '127.0.0.1';
    return 'http://$host:$defaultBackendPort/api/v1';
  }

  /// Generous by default: Render's free tier sleeps after 15 minutes idle, and
  /// the first request after a cold start can take around 50 seconds.
  static Duration get apiTimeout => Duration(
        seconds: int.tryParse(dotenv.env['API_TIMEOUT_SECONDS'] ?? '') ?? 60,
      );

  /// Razorpay PUBLIC key id (test mode). The secret never leaves the server.
  static String get razorpayKeyId => dotenv.env['RAZORPAY_KEY_ID'] ?? '';

  static bool get faceVerificationEnabled =>
      (dotenv.env['ENABLE_FACE_VERIFICATION'] ?? 'true').toLowerCase() ==
      'true';

  static bool get aiChatbotEnabled =>
      (dotenv.env['ENABLE_AI_CHATBOT'] ?? 'true').toLowerCase() == 'true';

  /// Radius within which a resident may GPS self check-in a worker when no
  /// guard is on duty (Module 13.3, secondary attendance tier).
  static double get geofenceRadiusMeters =>
      double.tryParse(dotenv.env['GEOFENCE_RADIUS_METERS'] ?? '') ?? 150;
}
