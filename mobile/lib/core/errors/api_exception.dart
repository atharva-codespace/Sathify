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

    // ---------------------------------------------------------------------
    // NOT THE ENVELOPE. NEVER SHOW THE BODY.
    // ---------------------------------------------------------------------
    // This used to be `message: json.toString()`, which put whatever the
    // server sent straight onto the screen — and the bodies that land here
    // are precisely the ones nobody wrote for a reader. An unhandled
    // exception returns `None` from `sathify_exception_handler`, so Django's
    // own 500 handling answers, and with DEBUG on that is a page of traceback.
    // Dumping it into a `Text` widget shows a resident a Python stack trace
    // and leaks server internals to anyone who can trigger a 500.
    //
    // A `detail` string is the one exception worth keeping: DRF writes it and
    // it is meant to be read.
    final detail = json['detail'];
    return ApiException(
      code: 'error',
      message: detail is String && detail.isNotEmpty
          ? detail
          : _friendlyFor(statusCode),
      statusCode: statusCode,
    );
  }

  /// What to say when the server did not say anything usable.
  ///
  /// Split by status because the remedies genuinely differ: a 5xx is ours to
  /// fix and retrying may work, while a 4xx that reached here is a request the
  /// server rejected without explaining, and retrying it unchanged will not.
  static String _friendlyFor(int? statusCode) {
    if (statusCode != null && statusCode >= 500) {
      return 'Something went wrong on our side. Please try again in a moment.';
    }
    return 'Something went wrong. Please try again.';
  }

  /// Builds an exception for a response whose body was not JSON at all.
  ///
  /// The body is deliberately not carried into [message]: an HTML error page
  /// is the other shape a 500 arrives in, and it is no more readable than a
  /// traceback.
  ApiException.unreadableBody(int? statusCode)
      : code = 'error',
        message = _friendlyFor(statusCode),
        details = const {},
        statusCode = statusCode;

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
