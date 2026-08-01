/// Dart mirror of the uniform error envelope produced by the Django
/// `sathify_exception_handler`:
///
/// ```json
/// {"error": {"code": "validation_error", "message": "...", "details": {...}}}
/// ```
///
/// Because the server guarantees this one shape for every failure in every
/// module, UI code can switch on [code] instead of parsing per-endpoint errors.
class ApiException implements Exception {
  const ApiException({
    required this.code,
    required this.message,
    this.details = const {},
    this.statusCode,
  });

  final String code;
  final String message;
  final Map<String, dynamic> details;
  final int? statusCode;

  /// Builds an exception from a decoded error response body.
  ///
  /// Both maps are read with `is Map` and copied rather than cast to
  /// `Map<String, dynamic>`. Nested maps do not always arrive with that generic
  /// type — an empty `{}` and several decoder paths produce
  /// `Map<dynamic, dynamic>` — and a hard cast here would throw *while handling
  /// an error*, turning the server's "validation failed" into an unhandled
  /// TypeError. This is the one funnel every API failure in the app passes
  /// through, so it degrades rather than throws.
  factory ApiException.fromJson(Map<String, dynamic> json, int? statusCode) {
    final error = json['error'];
    if (error is Map) {
      final details = error['details'];
      return ApiException(
        code: error['code'] as String? ?? 'error',
        message: error['message'] as String? ?? 'Something went wrong.',
        details: details is Map ? Map<String, dynamic>.from(details) : const {},
        statusCode: statusCode,
      );
    }
    return ApiException(
      code: 'error',
      message: json.toString(),
      statusCode: statusCode,
    );
  }

  /// Raised when the device is offline. Callers in Modules 7 and 13 catch this
  /// specifically to queue the action locally instead of surfacing an error —
  /// a gate entry decision must never be blocked by connectivity.
  const ApiException.offline()
      : code = 'offline',
        message = 'No internet connection. Your action has been queued.',
        details = const {},
        statusCode = null;

  const ApiException.timeout()
      : code = 'timeout',
        message = 'The server took too long to respond. Please try again.',
        details = const {},
        statusCode = null;

  bool get isOffline => code == 'offline';
  bool get isAuthFailure => code == 'authentication_failed';
  bool get isPermissionDenied => code == 'permission_denied';

  /// First human-readable message for [field], if the server flagged it.
  String? fieldError(String field) {
    final value = details[field];
    if (value is List && value.isNotEmpty) return value.first.toString();
    if (value is String) return value;
    return null;
  }

  @override
  String toString() => 'ApiException($code): $message';
}
