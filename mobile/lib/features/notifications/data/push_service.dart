import 'dart:async';

import 'package:firebase_core/firebase_core.dart';
import 'package:firebase_messaging/firebase_messaging.dart';
import 'package:flutter/foundation.dart';
import 'package:flutter_local_notifications/flutter_local_notifications.dart';

import '../../../core/device/device_identity.dart';
import 'repositories/notification_repository.dart';

/// Android channel id. Pinned to the value `apps/notifications/push.py` sends as
/// `android.notification.channel_id` — if the two drift, Android silently files
/// server pushes under a channel that does not exist and the user's per-channel
/// settings stop applying to them.
const String kNotificationChannelId = 'sathify_default';
const String kNotificationChannelName = 'Sathify alerts';

/// A push as the app sees it.
class PushMessage {
  const PushMessage({
    required this.title,
    required this.body,
    this.category = '',
    this.route = '',
  });

  final String title;
  final String body;
  final String category;
  final String route;

  /// FCM stringifies every data value (see `push.py::_build_message`), so
  /// everything arrives as a `String` regardless of what the server put in.
  factory PushMessage.fromRemote(RemoteMessage message) {
    final data = message.data;
    return PushMessage(
      title:
          message.notification?.title ?? data['title']?.toString() ?? 'Sathify',
      body: message.notification?.body ?? data['body']?.toString() ?? '',
      category: data['category']?.toString() ?? '',
      route: data['route']?.toString() ?? '',
    );
  }
}

/// Module 10.1 — Firebase Cloud Messaging on the client.
///
/// -----------------------------------------------------------------------
/// THERE IS NO BACKGROUND MESSAGE HANDLER, ON PURPOSE
/// -----------------------------------------------------------------------
/// `push.py::_build_message` puts a `notification` block on every message, so
/// Android and iOS post the tray entry themselves while the app is backgrounded
/// or killed. A `onBackgroundMessage` handler would spin up a second Dart
/// isolate per message to do nothing — or, worse, post the same alert twice.
/// If the server ever starts sending data-only messages, one belongs here.
///
/// -----------------------------------------------------------------------
/// THE APP MUST WORK WITH NO FIREBASE AT ALL
/// -----------------------------------------------------------------------
/// A fresh clone has no `google-services.json`, so `Firebase.initializeApp()`
/// throws. Every step here is therefore wrapped and failure only sets
/// [isAvailable] to false. Push is an *enhancement*: the notification centre
/// (Module 10.3) is the system of record, exactly as the server treats it, and
/// a developer with no Firebase project must still be able to run the app.
///
/// This mirrors the backend, where `push.py` returns a "not available" result
/// rather than raising for precisely the same reason.
class PushService {
  PushService({
    NotificationRepository? repository,
    FlutterLocalNotificationsPlugin? localNotifications,
  })  : _repository = repository ?? NotificationRepository(),
        _local = localNotifications ?? FlutterLocalNotificationsPlugin();

  final NotificationRepository _repository;
  final FlutterLocalNotificationsPlugin _local;

  final StreamController<PushMessage> _received =
      StreamController<PushMessage>.broadcast();
  final StreamController<String> _opened = StreamController<String>.broadcast();

  final List<StreamSubscription<dynamic>> _subscriptions = [];

  bool _available = false;
  bool _started = false;

  /// False when Firebase is absent or refused to start. Screens use this to
  /// explain why alerts may be arriving only in-app.
  bool get isAvailable => _available;

  /// Pushes that landed while the app was in the foreground. Watched so the
  /// unread badge updates without waiting for the next poll.
  Stream<PushMessage> get received => _received.stream;

  /// Routes the user asked for by tapping a notification.
  Stream<String> get opened => _opened.stream;

  /// Boots messaging and registers this device. Safe to call more than once.
  ///
  /// Call after sign-in, not at launch: the token is registered against the
  /// authenticated user, and registering before there is a session would put
  /// this phone's token on nobody's account.
  Future<void> start() async {
    if (_started) {
      // Already running — but the signed-in user may have changed, so the
      // token still needs to be attached to whoever is signed in now.
      await _registerToken();
      return;
    }
    _started = true;

    try {
      await Firebase.initializeApp();
    } catch (error) {
      debugPrint(
        'Push notifications are off: Firebase did not start ($error). '
        'The notification centre still works.',
      );
      _available = false;
      return;
    }

    _available = true;

    await _initialiseLocalNotifications();
    await _requestPermission();

    final messaging = FirebaseMessaging.instance;

    _subscriptions
        .add(FirebaseMessaging.onMessage.listen(_onForegroundMessage));
    _subscriptions.add(
      FirebaseMessaging.onMessageOpenedApp.listen(
        (message) => _emitRoute(PushMessage.fromRemote(message).route),
      ),
    );
    // Firebase rotates tokens on its own schedule. Without this the server
    // keeps pushing to an address that stopped existing.
    _subscriptions.add(
      messaging.onTokenRefresh.listen((token) => unawaited(_sendToken(token))),
    );

    await _registerToken();

    // The app may have been launched *by* a notification, in which case no
    // stream fires and the tap would otherwise be lost.
    final initial = await messaging.getInitialMessage();
    if (initial != null) {
      _emitRoute(PushMessage.fromRemote(initial).route);
    }
  }

  /// Stops pushes to this device. Called on sign-out — otherwise the next
  /// person to hold the phone receives the previous user's gate alerts.
  Future<void> stop() async {
    try {
      final identity = await DeviceIdentity.current();
      await _repository.unregisterDevice(identity.deviceId);
    } catch (error) {
      debugPrint('Could not clear the push token: $error');
    }
  }

  Future<void> dispose() async {
    for (final subscription in _subscriptions) {
      await subscription.cancel();
    }
    _subscriptions.clear();
    await _received.close();
    await _opened.close();
  }

  // --- Internals -------------------------------------------------------------

  Future<void> _initialiseLocalNotifications() async {
    try {
      await _local.initialize(
        const InitializationSettings(
          android: AndroidInitializationSettings('@mipmap/ic_launcher'),
          iOS: DarwinInitializationSettings(),
        ),
        onDidReceiveNotificationResponse: (response) =>
            _emitRoute(response.payload ?? ''),
      );

      // Android 8+ ignores importance set at post time, so the channel has to
      // exist with the right importance before the first message arrives.
      await _local
          .resolvePlatformSpecificImplementation<
              AndroidFlutterLocalNotificationsPlugin>()
          ?.createNotificationChannel(
            const AndroidNotificationChannel(
              kNotificationChannelId,
              kNotificationChannelName,
              description:
                  'Gate entries, visits, payments and account updates.',
              importance: Importance.high,
            ),
          );
    } catch (error) {
      debugPrint('Local notifications unavailable: $error');
    }
  }

  Future<void> _requestPermission() async {
    try {
      await FirebaseMessaging.instance.requestPermission();
      // iOS suppresses banners while the app is open unless asked otherwise.
      // Android is handled by showing a local notification in [_onForegroundMessage].
      await FirebaseMessaging.instance
          .setForegroundNotificationPresentationOptions(
        alert: true,
        badge: true,
        sound: true,
      );
    } catch (error) {
      debugPrint('Notification permission not granted: $error');
    }
  }

  Future<void> _registerToken() async {
    if (!_available) return;
    try {
      final token = await FirebaseMessaging.instance.getToken();
      if (token != null && token.isNotEmpty) await _sendToken(token);
    } catch (error) {
      debugPrint('Could not read the FCM token: $error');
    }
  }

  Future<void> _sendToken(String token) async {
    try {
      final identity = await DeviceIdentity.current();
      await _repository.registerDevice(
        deviceId: identity.deviceId,
        fcmToken: token,
        deviceName: identity.deviceName,
        platform: identity.platform,
      );
    } catch (error) {
      // Never fatal. The next launch tries again, and until then the
      // notification centre still carries everything.
      debugPrint('Could not register this device for push: $error');
    }
  }

  Future<void> _onForegroundMessage(RemoteMessage message) async {
    final push = PushMessage.fromRemote(message);
    if (!_received.isClosed) _received.add(push);

    // Android shows nothing for a message that arrives while the app is open,
    // so it is posted here. A gate refusal seen only after switching screens is
    // a gate refusal missed.
    try {
      await _local.show(
        // Android notification ids are 32-bit signed. Dart hashCodes are not
        // bounded to that on a 64-bit VM, and an out-of-range id throws at the
        // platform channel rather than degrading.
        message.hashCode & 0x7fffffff,
        push.title,
        push.body,
        const NotificationDetails(
          android: AndroidNotificationDetails(
            kNotificationChannelId,
            kNotificationChannelName,
            importance: Importance.high,
            priority: Priority.high,
          ),
          iOS: DarwinNotificationDetails(),
        ),
        payload: push.route,
      );
    } catch (error) {
      debugPrint('Could not show a foreground notification: $error');
    }
  }

  void _emitRoute(String route) {
    if (route.isEmpty || _opened.isClosed) return;
    _opened.add(route);
  }
}
