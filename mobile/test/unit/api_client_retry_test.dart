import 'dart:typed_data';

import 'package:connectivity_plus/connectivity_plus.dart';
import 'package:dio/dio.dart';
import 'package:flutter_dotenv/flutter_dotenv.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:mocktail/mocktail.dart';
import 'package:sathify/core/errors/api_exception.dart';
import 'package:sathify/core/network/api_client.dart';
import 'package:sathify/core/storage/token_storage.dart';

/// What the client does when the server is asleep rather than broken.
///
/// The API sleeps after 15 minutes idle on Render's free tier and takes about
/// 50 seconds to wake, answering 502/503 or refusing the socket while it does.
/// These tests pin the two halves of the policy that matter: a waking server is
/// absorbed silently, and a request the server may already have acted on is
/// never replayed — that is the difference between a smooth launch and a
/// resident being charged twice.
class MockTokenStorage extends Mock implements TokenStorage {}

class MockConnectivity extends Mock implements Connectivity {}

/// A transport that answers from a script, one entry per attempt.
///
/// An `int` is an HTTP status; a [DioExceptionType] is a transport failure.
/// The last entry repeats, so a script of one entry means "always this".
class _ScriptedAdapter implements HttpClientAdapter {
  _ScriptedAdapter(this.script);

  final List<Object> script;
  int calls = 0;

  @override
  Future<ResponseBody> fetch(
    RequestOptions options,
    Stream<Uint8List>? requestStream,
    Future<void>? cancelFuture,
  ) async {
    final step = script[calls < script.length ? calls : script.length - 1];
    calls++;

    if (step is DioExceptionType) {
      throw DioException(requestOptions: options, type: step);
    }
    return ResponseBody.fromString(
      '{"ok": true}',
      step as int,
      headers: {
        Headers.contentTypeHeader: [Headers.jsonContentType],
      },
    );
  }

  @override
  void close({bool force = false}) {}
}

ApiClient _clientWith(_ScriptedAdapter adapter) {
  final storage = MockTokenStorage();
  when(storage.readAccessToken).thenAnswer((_) async => null);

  final connectivity = MockConnectivity();
  when(connectivity.checkConnectivity)
      .thenAnswer((_) async => [ConnectivityResult.wifi]);

  return ApiClient(
    dio: Dio()..httpClientAdapter = adapter,
    tokenStorage: storage,
    connectivity: connectivity,
  );
}

void main() {
  setUpAll(() {
    dotenv.testLoad(fileInput: 'API_BASE_URL=http://test.local/api/v1');
  });

  group('cold-start retries', () {
    test('a waking server is absorbed rather than shown to the user', () async {
      final adapter = _ScriptedAdapter([503, 503, 200]);

      final result = await _clientWith(adapter).get('/anything/');

      expect(result, {'ok': true});
      expect(adapter.calls, 3, reason: 'two replays on top of the original');
    });

    test('a connection failure is replayed for a write too', () async {
      // Nothing reached the application, so replaying cannot double anything.
      final adapter = _ScriptedAdapter([DioExceptionType.connectionError, 200]);

      await _clientWith(adapter).post('/bookings/', data: {'slot': 1});

      expect(adapter.calls, 2);
    });

    test('gives up rather than retrying forever', () async {
      final adapter = _ScriptedAdapter([503]);

      await expectLater(
        _clientWith(adapter).get('/anything/'),
        throwsA(isA<ApiException>()),
      );
      expect(adapter.calls, 3);
    });
  });

  group('what must never be replayed', () {
    test('a 500 is the server failing, not waking', () async {
      // It ran and crashed. Sending it again crashes it again, more slowly.
      final adapter = _ScriptedAdapter([500]);

      await expectLater(
        _clientWith(adapter).get('/anything/'),
        throwsA(isA<ApiException>()),
      );
      expect(adapter.calls, 1);
    });

    test('a write that timed out waiting for the answer', () async {
      // The request landed. It may have created the booking and simply been
      // too slow to say so, and a replay would create a second one.
      final adapter = _ScriptedAdapter([DioExceptionType.receiveTimeout]);

      await expectLater(
        _clientWith(adapter).post('/bookings/', data: {'slot': 1}),
        throwsA(isA<ApiException>()),
      );
      expect(adapter.calls, 1);
    });

    test('a read that timed out is safe, and is replayed', () async {
      final adapter = _ScriptedAdapter([DioExceptionType.receiveTimeout, 200]);

      await _clientWith(adapter).get('/schedule/');

      expect(adapter.calls, 2);
    });
  });
}
