import 'dart:async';

import 'package:connectivity_plus/connectivity_plus.dart';
import 'package:dio/dio.dart';

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
    }
    handler.next(error);
  }

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

  Future<Response<dynamic>> _retry(RequestOptions requestOptions) async {
    final token = await _tokenStorage.readAccessToken();
    return _dio.request<dynamic>(
      requestOptions.path,
      data: requestOptions.data,
      queryParameters: requestOptions.queryParameters,
      options: Options(
        method: requestOptions.method,
        headers: {
          ...requestOptions.headers,
          if (token != null) 'Authorization': 'Bearer $token',
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
        final data = e.response?.data;
        if (data is Map<String, dynamic>) {
          return ApiException.fromJson(data, e.response?.statusCode);
        }
        return ApiException(
          code: 'error',
          message: e.message ?? 'Unexpected network error.',
          statusCode: e.response?.statusCode,
        );
    }
  }
}
