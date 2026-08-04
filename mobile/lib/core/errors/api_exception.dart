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

  /// Raised when the device itself has no network at all.
  ///
  /// The message deliberately does not promise that the action was queued.
  /// Only the caller knows whether it queued anything — Module 7's gate
  /// decisions do, a login does not — and claiming otherwise on a screen that
  /// saved nothing tells the user their work is safe when it is gone.
  const ApiException.offline()
      : code = 'offline',
        message = 'No internet connection.',
        details = const {},
        statusCode = null;

  /// Raised when the device has a working network but the server never
  /// answered: wrong host, server down, or the connection refused outright.
  ///
  /// Kept distinct from [ApiException.offline] because the remedy differs.
  /// Reporting this as "no internet connection" sends the user off to check a
  /// phone that was never the problem.
  const ApiException.unreachable()
      : code = 'unreachable',
        message = "Couldn't reach the server. Please try again in a moment.",
        details = const {},
        statusCode = null;

  const ApiException.timeout()
      : code = 'timeout',
        message = 'The server took too long to respond. Please try again.',
        details = const {},
        statusCode = null;

  bool get isOffline => code == 'offline';
  bool get isUnreachable => code == 'unreachable';

  /// True when the request never reached the server, for either reason.
  ///
  /// This — not [isOffline] — is what local queue and cache fallbacks branch
  /// on. A request that died on the wire is equally unanswered whether the
  /// radio was off or the host refused the connection, and both recover the
  /// same way: hold it locally and retry. Splitting the two codes is a change
  /// to what the *user* is told, not to what the app does about it.
  bool get isConnectionFailure => isOffline || isUnreachable;

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
