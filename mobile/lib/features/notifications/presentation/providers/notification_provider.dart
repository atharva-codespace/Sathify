import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../data/models/notification_models.dart';
import '../../data/push_service.dart';
import '../../data/repositories/notification_repository.dart';

final notificationRepositoryProvider =
    Provider<NotificationRepository>((ref) => NotificationRepository());

/// Module 10.1 — the FCM client.
///
/// Kept alive for the app's lifetime rather than autoDisposed: it owns stream
/// subscriptions to Firebase, and tearing those down whenever no screen happens
/// to be watching would drop pushes.
final pushServiceProvider = Provider<PushService>((ref) {
  final service = PushService(
    repository: ref.watch(notificationRepositoryProvider),
  );
  ref.onDispose(service.dispose);
  return service;
});

/// Module 10.3 — the notification centre.
final notificationsProvider = FutureProvider.autoDispose<List<AppNotification>>(
  (ref) => ref.read(notificationRepositoryProvider).fetchNotifications(),
);

/// Unread only, for the "new" filter.
final unreadNotificationsProvider =
    FutureProvider.autoDispose<List<AppNotification>>(
  (ref) => ref
      .read(notificationRepositoryProvider)
      .fetchNotifications(unreadOnly: true),
);

/// Drives the badge on every home screen.
///
/// Not autoDisposed: the bell sits in app bars that come and go, and refetching
/// the count on every navigation would be a request per screen change.
final unreadCountProvider = FutureProvider<int>(
  (ref) => ref.read(notificationRepositoryProvider).fetchUnreadCount(),
);

/// Module 10.4 — every category with its mute state and whether it can be muted.
final notificationPreferencesProvider =
    FutureProvider.autoDispose<List<NotificationPreference>>(
  (ref) => ref.read(notificationRepositoryProvider).fetchPreferences(),
);

/// Refreshes everything a read or a new arrival could have changed.
///
/// The count is invalidated alongside the list because they are separate
/// endpoints: marking one read and leaving a stale badge behind is exactly the
/// kind of small wrongness that teaches people to ignore the badge.
void invalidateNotifications(WidgetRef ref) {
  ref.invalidate(notificationsProvider);
  ref.invalidate(unreadNotificationsProvider);
  ref.invalidate(unreadCountProvider);
}
