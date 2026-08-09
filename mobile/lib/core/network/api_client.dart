import 'dart:async';

import 'package:connectivity_plus/connectivity_plus.dart';
import 'package:dio/dio.dart';
import 'package:flutter/foundation.dart';

import '../config/api_endpoints.dart';
import '../config/app_config.dart';
import '../errors/api_exception.dart';
import '../storage/token_storage.dart';

/// The single HTTP entry point for the whole app.
///
/// Centralising this means JWT attachment, silent token refresh, offline
/// detection and error normalisation are each implemented once rather than in
/// every repository across twelve modules.
///
/// Concurrency note: when several requests fail with 401 at the same moment,
/// only the first triggers a refresh. The rest await the same future via
/// [_refreshCompleter], so we never fire N parallel refreshes and invalidate
/// each other's rotated tokens — the backend has ROTATE_REFRESH_TOKENS on, so
/// a race here would log the user out.
///
/// Cold starts: the API sleeps after 15 minutes idle on Render's free tier and
/// takes roughly 50 seconds to wake, during which its router answers 502/503 or
/// simply holds the socket. That is not an error the user did anything to
/// cause, and showing them "couldn't reach the server" for it trains them to
/// distrust an app that was about to work. [_shouldRetryTransient] absorbs it —
/// see that method for which failures are safe to replay and which are not.
class ApiClient {
  ApiClient({
    Dio? dio,
    TokenStorage? tokenStorage,
    Connectivity? connectivity,
    this.onSessionExpired,
  })  : _tokenStorage = tokenStorage ?? TokenStorage(),
        _connectivity = connectivity ?? Connectivity(),
        _dio = dio ?? Dio() {
    _dio
      ..options.baseUrl = AppConfig.apiBaseUrl
      ..options.connectTimeout = AppConfig.apiTimeout
      ..options.receiveTimeout = AppConfig.apiTimeout
      ..options.headers['Content-Type'] = 'application/json';

    _dio.interceptors.add(
      InterceptorsWrapper(
        onRequest: _onRequest,
        onError: _onError,
      ),
    );
  }

  final Dio _dio;
  final TokenStorage _tokenStorage;
  final Connectivity _connectivity;

  /// Invoked when the refresh token is itself rejected and the user must log in
  /// again. Wired to the router by the app shell.
  final void Function()? onSessionExpired;

  Completer<bool>? _refreshCompleter;

  /// Carries the replay count on a request so the recursion terminates.
  static const String _attemptKey = 'sathify.transientAttempt';

  /// Two replays on top of the original. With the timeouts in `.env` that
  /// covers a ~50 s wake without leaving somebody staring at a dead screen if
  /// the service is genuinely down.
  static const int _maxTransientAttempts = 2;

  Dio get raw => _dio;

  // --- Interceptors ---------------------------------------------------------

  Future<void> _onRequest(
    RequestOptions options,
    RequestInterceptorHandler handler,
  ) async {
    // Auth endpoints must not carry a stale Authorization header.
    final isAuthCall = options.path == ApiEndpoints.login ||
        options.path == ApiEndpoints.refresh;

    if (!isAuthCall) {
      final token = await _tokenStorage.readAccessToken();
      if (token != null) {
        options.headers['Authorization'] = 'Bearer $token';
      }
    }
    handler.next(options);
  }

  Future<void> _onError(
    DioException error,
    ErrorInterceptorHandler handler,
  ) async {
    final isUnauthorized = error.response?.statusCode == 401;
    final isRefreshCall = error.requestOptions.path == ApiEndpoints.refresh;

    // A 401 on anything but the refresh call itself is worth one retry with a
    // freshly minted access token.
    if (isUnauthorized && !isRefreshCall) {
      final refreshed = await _refreshAccessToken();
      if (refreshed) {
        try {
          final response = await _retry(error.requestOptions);
          return handler.resolve(response);
        } on DioException catch (e) {
          return handler.next(e);
        }
      }
      await _tokenStorage.clear();
      onSessionExpired?.call();
      return handler.next(error);
    }

    // A sleeping server, most likely. Wait and try again before troubling the
    // user. The replay goes back through this interceptor, so the attempt
    // counter travels in `extra` and the recursion is what bounds the loop.
    final attempt = (error.requestOptions.extra[_attemptKey] as int? ?? 0) + 1;
    if (attempt <= _maxTransientAttempts && _shouldRetryTransient(error)) {
      await Future<void>.delayed(_backoffFor(attempt));
      try {
        final response = await _retry(error.requestOptions, attempt: attempt);
        return handler.resolve(response);
      } on DioException catch (e) {
        return handler.next(e);
      }
    }

    handler.next(error);
  }

  /// Whether [error] is worth replaying.
  ///
  /// The line is "did the server get a chance to act on this request?", because
  /// replaying one it already processed is how a worker ends up with two
  /// identical bookings or a resident pays twice.
  ///
  /// * Connect failures and connect timeouts never reached the application, so
  ///   any method is safe.
  /// * 502/503/504 come from Render's router while the instance boots, so the
  ///   application never saw the body either. 500 is deliberately excluded: the
  ///   server *did* run, and repeating whatever crashed it will crash it again.
  /// * A receive timeout means the request landed and the answer was too slow.
  ///   Only reads may be replayed.
  ///
  /// The refresh endpoint is never retried: it rotates the refresh token, so a
  /// replay can spend a token whose response was merely lost in transit and
  /// sign the user out of their own session.
  bool _shouldRetryTransient(DioException error) {
    if (error.requestOptions.path == ApiEndpoints.refresh) return false;

    final method = error.requestOptions.method.toUpperCase();
    final isRead = method == 'GET' || method == 'HEAD';
    final status = error.response?.statusCode;

    if (status != null) {
      return const {502, 503, 504}.contains(status);
    }

    switch (error.type) {
      case DioExceptionType.connectionError:
      case DioExceptionType.connectionTimeout:
        return true;
      case DioExceptionType.receiveTimeout:
        return isRead;
      default:
        return false;
    }
  }

  /// Grows with each attempt: a waking instance needs tens of seconds, and
  /// hammering it every second only competes with its own boot for CPU.
  Duration _backoffFor(int attempt) =>
      Duration(milliseconds: 800 * (1 << (attempt - 1)));

  /// Exchanges the refresh token for a new access token.
  ///
  /// Returns false when there is no refresh token or the server rejects it, in
  /// which case the session is unrecoverable.
  Future<bool> _refreshAccessToken() async {
    // Another request is already refreshing — wait for its result.
    if (_refreshCompleter != null) return _refreshCompleter!.future;

    final completer = Completer<bool>();
    _refreshCompleter = completer;

    try {
      final refreshToken = await _tokenStorage.readRefreshToken();
      if (refreshToken == null) {
        completer.complete(false);
        return false;
      }

      // A bare Dio instance: using _dio would re-enter this interceptor.
      final response = await Dio(
        BaseOptions(
          baseUrl: AppConfig.apiBaseUrl,
          connectTimeout: AppConfig.apiTimeout,
          receiveTimeout: AppConfig.apiTimeout,
        ),
      ).post<Map<String, dynamic>>(
        ApiEndpoints.refresh,
        data: {'refresh': refreshToken},
      );

      final data = response.data ?? const {};
      final access = data['access'] as String?;
      if (access == null) {
        completer.complete(false);
        return false;
      }

      // The backend rotates refresh tokens, so a new one usually comes back;
      // fall back to the existing one if it does not.
      await _tokenStorage.saveTokens(
        accessToken: access,
        refreshToken: data['refresh'] as String? ?? refreshToken,
      );
      completer.complete(true);
      return true;
    } catch (_) {
      completer.complete(false);
      return false;
    } finally {
      _refreshCompleter = null;
    }
  }

  Future<Response<dynamic>> _retry(
    RequestOptions requestOptions, {
    int attempt = 0,
  }) async {
    final token = await _tokenStorage.readAccessToken();
    final data = requestOptions.data;

    return _dio.request<dynamic>(
      requestOptions.path,
      // A FormData's byte streams are consumed by the attempt that failed, so
      // re-sending the same instance uploads nothing. This is what makes a
      // retried Aadhaar or gate photo actually arrive.
      data: data is FormData ? data.clone() : data,
      queryParameters: requestOptions.queryParameters,
      options: Options(
        method: requestOptions.method,
        contentType: requestOptions.contentType,
        responseType: requestOptions.responseType,
        headers: {
          ...requestOptions.headers,
          if (token != null) 'Authorization': 'Bearer $token',
        },
        extra: {
          ...requestOptions.extra,
          if (attempt > 0) _attemptKey: attempt,
        },
      ),
    );
  }

  // --- Public verbs ---------------------------------------------------------

  Future<dynamic> get(String path, {Map<String, dynamic>? query}) =>
      _send(() => _dio.get<dynamic>(path, queryParameters: query));

  Future<dynamic> post(String path, {Object? data}) =>
      _send(() => _dio.post<dynamic>(path, data: data));

  Future<dynamic> patch(String path, {Object? data}) =>
      _send(() => _dio.patch<dynamic>(path, data: data));

  /// Idempotent upsert. Used by Module 5.3's day availability, where a worker
  /// re-tapping the same date on a flaky connection must converge on one answer
  /// rather than create a second, contradictory row.
  Future<dynamic> put(String path, {Object? data}) =>
      _send(() => _dio.put<dynamic>(path, data: data));

  Future<dynamic> delete(String path, {Object? data}) =>
      _send(() => _dio.delete<dynamic>(path, data: data));

  /// Multipart upload used for Aadhaar documents (Module 3) and the live gate
  /// photo (Module 7).
  Future<dynamic> upload(
    String path, {
    required FormData formData,
    void Function(int sent, int total)? onProgress,
  }) =>
      _send(
        () => _dio.post<dynamic>(
          path,
          data: formData,
          onSendProgress: onProgress,
          options: Options(contentType: 'multipart/form-data'),
        ),
      );

  /// Runs [request], translating every failure into an [ApiException].
  ///
  /// Checking connectivity first means callers get a clean `offline` code they
  /// can queue on, rather than an opaque socket error. A failure that gets past
  /// this check and dies on the wire anyway becomes `unreachable` instead — see
  /// [_translate]. Callers that only care whether the request landed should test
  /// `isConnectionFailure`, which covers both.
  Future<dynamic> _send(Future<Response<dynamic>> Function() request) async {
    final connections = await _connectivity.checkConnectivity();
    if (connections.every((c) => c == ConnectivityResult.none)) {
      throw const ApiException.offline();
    }

    try {
      final response = await request();
      return response.data;
    } on DioException catch (e) {
      throw _translate(e);
    }
  }

  ApiException _translate(DioException e) {
    switch (e.type) {
      case DioExceptionType.connectionTimeout:
      case DioExceptionType.receiveTimeout:
      case DioExceptionType.sendTimeout:
        return const ApiException.timeout();
      case DioExceptionType.connectionError:
        // The pre-flight check in _send already cleared the device's own
        // connectivity, so reaching here means the network is up and the
        // *server* did not answer — a different problem, and a different
        // message. Both still satisfy isConnectionFailure, so queueing and
        // cache fallbacks behave exactly as before.
        return const ApiException.unreachable();
      default:
        final status = e.response?.statusCode;
        final data = e.response?.data;
        if (data is Map<String, dynamic>) {
          return ApiException.fromJson(data, status);
        }
        if (status != null) {
          // A body that is not JSON — overwhelmingly Django's HTML technical
          // 500 page, which `sathify_exception_handler` deliberately does not
          // intercept so the traceback still reaches the logs. It must reach
          // the *logs* and not the screen: the raw page is printed for a
          // developer here, and the user gets a sentence.
          assert(() {
            debugPrint('HTTP $status body (not shown to the user): $data');
            return true;
          }());
          return ApiException.unreadableBody(status);
        }
        return ApiException(
          code: 'error',
          message: e.message ?? 'Unexpected network error.',
        );
    }
  }
}
