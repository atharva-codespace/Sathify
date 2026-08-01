import '../../../../core/config/api_endpoints.dart';
import '../../../../core/network/api_client.dart';
import '../models/notification_models.dart';

/// All Module 10 endpoints.
class NotificationRepository {
  NotificationRepository({ApiClient? client}) : _client = client ?? ApiClient();

  final ApiClient _client;

  // --- 10.3 Notification centre ---------------------------------------------

  Future<List<AppNotification>> fetchNotifications({
    bool unreadOnly = false,
    NotificationCategory? category,
  }) async {
    final response = await _client.get(
      ApiEndpoints.notifications,
      query: {
        if (unreadOnly) 'unread': 'true',
        if (category != null) 'category': category.wireValue,
        'page_size': 100,
      },
    ) as Map<String, dynamic>;

    return ((response['results'] as List?) ?? const [])
        .map((row) => AppNotification.fromJson(row as Map<String, dynamic>))
        .toList();
  }

  /// Badge count only — deliberately does not pull a page of bodies.
  Future<int> fetchUnreadCount() async {
    final response =
        await _client.get(ApiEndpoints.unreadCount) as Map<String, dynamic>;
    return response['unread'] as int? ?? 0;
  }

  Future<AppNotification> markRead(int notificationId) async {
    final response = await _client.post(
      ApiEndpoints.markNotificationRead(notificationId),
    ) as Map<String, dynamic>;

    return AppNotification.fromJson(response);
  }

  Future<int> markAllRead() async {
    final response =
        await _client.post(ApiEndpoints.markAllRead) as Map<String, dynamic>;
    return response['marked_read'] as int? ?? 0;
  }

  // --- 10.1 Device registration ----------------------------------------------

  /// Registers this installation's FCM token.
  ///
  /// Keyed on [deviceId] server-side, so calling this on every launch and on
  /// every Firebase token rotation replaces the row rather than accumulating
  /// one per launch — which would mean the same push arriving several times.
  Future<void> registerDevice({
    required String deviceId,
    required String fcmToken,
    String deviceName = '',
    String platform = '',
  }) =>
      _client.post(
        ApiEndpoints.notificationDevice,
        data: {
          'device_id': deviceId,
          'fcm_token': fcmToken,
          'device_name': deviceName,
          'platform': platform,
        },
      );

  /// Stops pushes to this device. Called on sign-out, so the next person to use
  /// the phone does not receive the previous user's gate alerts.
  Future<void> unregisterDevice(String deviceId) => _client.delete(
        ApiEndpoints.notificationDevice,
        data: {'device_id': deviceId},
      );

  // --- 10.4 Preferences ------------------------------------------------------

  Future<List<NotificationPreference>> fetchPreferences() async {
    final response = await _client.get(ApiEndpoints.notificationPreferences);

    return (response as List)
        .map(
          (row) => NotificationPreference.fromJson(row as Map<String, dynamic>),
        )
        .toList();
  }

  /// Mutes or unmutes one category.
  ///
  /// The server refuses to mute a safety-critical category. The app should not
  /// have offered the switch at all — [NotificationPreference.canMute] says so
  /// — but the refusal is the authority, not the UI.
  Future<void> setPreference({
    required String category,
    required bool muted,
  }) =>
      _client.put(
        ApiEndpoints.notificationPreferences,
        data: {'category': category, 'muted': muted},
      );

  // --- Reminder queue --------------------------------------------------------

  /// Drains Module 6.4's due reminders. Administrators only.
  ///
  /// Exposed in the app because there is no scheduler on the free tier; an
  /// administrator opening their dashboard is one more chance for a reminder to
  /// go out on a day the external pinger did not run.
  Future<int> deliverDue() async {
    final response = await _client.post(ApiEndpoints.deliverDueNotifications)
        as Map<String, dynamic>;
    return response['reminders_delivered'] as int? ?? 0;
  }
}
