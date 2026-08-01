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

  /// Base URL of the Django REST API, including the `/api/v1` prefix.
  static String get apiBaseUrl =>
      dotenv.env['API_BASE_URL'] ?? 'http://10.0.2.2:8000/api/v1';

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
