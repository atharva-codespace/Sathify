import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import 'package:intl/intl.dart';

import '../../../../core/errors/api_exception.dart';
import '../../../../core/routing/app_router.dart';
import '../../../../shared/design_system.dart';
import '../../data/models/notification_models.dart';
import '../providers/notification_provider.dart';

/// Module 10.3 — the notification centre.
///
/// -----------------------------------------------------------------------
/// THIS IS THE SYSTEM OF RECORD, NOT THE PUSH
/// -----------------------------------------------------------------------
/// The server writes the row before it attempts any delivery, precisely so a
/// message survives a phone that was off, a token that had rotated, or a
/// Firebase project that was never configured. This screen is therefore the
/// place a user can be told to look when they say they never heard about
/// something — and it is why a notification with both channels failed still
/// appears here rather than being lost.
class NotificationCenterScreen extends ConsumerStatefulWidget {
  const NotificationCenterScreen({super.key});

  @override
  ConsumerState<NotificationCenterScreen> createState() =>
      _NotificationCenterScreenState();
}

class _NotificationCenterScreenState
    extends ConsumerState<NotificationCenterScreen> {
  bool _unreadOnly = false;

  @override
  void initState() {
    super.initState();
    // Opening the centre is treated as reading everything currently in it —
    // otherwise the badge on every other screen keeps counting messages the
    // person has, in fact, just looked at, and only tapping each one
    // individually (or the explicit "mark all read" button) would clear it.
    unawaited(_autoMarkAllRead());
  }

  Future<void> _autoMarkAllRead() async {
    try {
      final count =
          await ref.read(notificationRepositoryProvider).markAllRead();
      if (!mounted || count == 0) return;
      invalidateNotifications(ref);
    } on ApiException catch (_) {
      // Silent — this is a courtesy pass on opening the screen, not a
      // user-initiated action. The "mark all read" button in the app bar
      // still works if this one request happens to fail.
    }
  }

  Future<void> _markAllRead() async {
    final messenger = ScaffoldMessenger.of(context);
    try {
      final count =
          await ref.read(notificationRepositoryProvider).markAllRead();
      invalidateNotifications(ref);
      messenger.showSnackBar(
        SnackBar(
          content: Text(
            count == 0 ? 'Nothing was unread.' : 'Marked $count as read.',
          ),
        ),
      );
    } on ApiException catch (error) {
      messenger.showSnackBar(SnackBar(content: Text(error.message)));
    }
  }

  Future<void> _open(AppNotification notification) async {
    // Marked read first so the badge is right even if navigation follows and
    // this screen is disposed mid-request.
    if (!notification.isRead) {
      try {
        await ref
            .read(notificationRepositoryProvider)
            .markRead(notification.id);
        invalidateNotifications(ref);
      } on ApiException catch (_) {
        // Reading is not worth an error message. The next refresh corrects it.
      }
    }

    final route = notification.route;
    // Not awaited: this method's job ends at navigating, and whatever the
    // pushed screen eventually returns is nothing to do with marking read.
    if (route != null && mounted) unawaited(context.push(route));
  }

  @override
  Widget build(BuildContext context) {
    final notifications = ref.watch(
      _unreadOnly ? unreadNotificationsProvider : notificationsProvider,
    );

    return Scaffold(
      appBar: AppBar(
        title: const Text('Notifications'),
        actions: [
          IconButton(
            tooltip: 'Settings',
            icon: const Icon(Icons.tune),
            onPressed: () => context.push(Routes.notificationPreferences),
          ),
          IconButton(
            tooltip: 'Mark all read',
            icon: const Icon(Icons.done_all),
            onPressed: _markAllRead,
          ),
        ],
      ),
      body: Column(
        children: [
          Padding(
            padding: const EdgeInsets.fromLTRB(12, 12, 12, 4),
            child: Row(
              children: [
                ChoiceChip(
                  label: const Text('All'),
                  selected: !_unreadOnly,
                  onSelected: (_) => setState(() => _unreadOnly = false),
                ),
                const SizedBox(width: 8),
                ChoiceChip(
                  label: const Text('Unread'),
                  selected: _unreadOnly,
                  onSelected: (_) => setState(() => _unreadOnly = true),
                ),
              ],
            ),
          ),
          Expanded(
            child: notifications.when(
              loading: () => const AppSkeletonList(),
              error: (error, _) => Center(
                child: Padding(
                  padding: const EdgeInsets.all(24),
                  child: Text(
                    error is ApiException
                        ? error.message
                        : 'Could not load your notifications.',
                    textAlign: TextAlign.center,
                  ),
                ),
              ),
              data: (items) {
                if (items.isEmpty) return _Empty(unreadOnly: _unreadOnly);

                return RefreshIndicator(
                  onRefresh: () async => invalidateNotifications(ref),
                  child: ListView.separated(
                    padding: const EdgeInsets.only(bottom: 24),
                    itemCount: items.length,
                    separatorBuilder: (_, __) => const Divider(height: 1),
                    itemBuilder: (context, index) => _NotificationTile(
                      notification: items[index],
                      onTap: () => _open(items[index]),
                    ),
                  ),
                );
              },
            ),
          ),
        ],
      ),
    );
  }
}

class _Empty extends StatelessWidget {
  const _Empty({required this.unreadOnly});

  final bool unreadOnly;

  @override
  Widget build(BuildContext context) {
    return AppEmptyState(
      icon: Icons.notifications_none_rounded,
      title: unreadOnly ? 'Nothing unread' : 'No notifications yet',
      message: 'Gate entries, visits, payments and account updates appear here.',
    );
  }
}

class _NotificationTile extends StatelessWidget {
  const _NotificationTile({required this.notification, required this.onTap});

  final AppNotification notification;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final unread = !notification.isRead;

    return ListTile(
      onTap: onTap,
      contentPadding: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
      leading: CircleAvatar(
        backgroundColor:
            _colourFor(notification.category).withValues(alpha: 0.15),
        foregroundColor: _colourFor(notification.category),
        child: Icon(_iconFor(notification.category)),
      ),
      title: Text(
        notification.title,
        style: TextStyle(
          fontWeight: unread ? FontWeight.w700 : FontWeight.w500,
        ),
      ),
      subtitle: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const SizedBox(height: 2),
          Text(notification.body),
          const SizedBox(height: 6),
          Row(
            children: [
              Text(
                _formatWhen(notification.createdAt),
                style: const TextStyle(
                  fontSize: 12,
                  color: AppColors.textSecondary,
                ),
              ),
              if (notification.arrivedOnlyInApp) ...[
                const SizedBox(width: 8),
                // Shown rather than hidden: somebody who insists they were never
                // told deserves to see that the message never left the server,
                // and support can answer that without database access.
                const Icon(
                  Icons.cloud_off,
                  size: 13,
                  color: AppColors.textSecondary,
                ),
                const SizedBox(width: 4),
                const Text(
                  'In-app only',
                  style: TextStyle(
                    fontSize: 12,
                    color: AppColors.textSecondary,
                  ),
                ),
              ],
            ],
          ),
        ],
      ),
      trailing: unread
          ? const Icon(Icons.circle, size: 10, color: AppColors.primary)
          : (notification.route != null
              ? const Icon(Icons.chevron_right, color: AppColors.textSecondary)
              : null),
    );
  }
}

IconData _iconFor(NotificationCategory category) {
  switch (category) {
    case NotificationCategory.account:
      return Icons.verified_user_outlined;
    case NotificationCategory.hire:
      return Icons.handshake_outlined;
    case NotificationCategory.booking:
      return Icons.event_available_outlined;
    case NotificationCategory.schedule:
      return Icons.schedule_outlined;
    case NotificationCategory.attendance:
      return Icons.how_to_reg_outlined;
    case NotificationCategory.gateEntry:
      return Icons.meeting_room_outlined;
    case NotificationCategory.urgentLeave:
      return Icons.warning_amber_outlined;
    case NotificationCategory.payment:
      return Icons.payments_outlined;
    case NotificationCategory.rating:
      return Icons.star_outline;
    case NotificationCategory.complaint:
      return Icons.report_gmailerrorred_outlined;
  }
}

Color _colourFor(NotificationCategory category) {
  switch (category) {
    case NotificationCategory.gateEntry:
    case NotificationCategory.urgentLeave:
      return AppColors.danger;
    case NotificationCategory.payment:
      return AppColors.success;
    case NotificationCategory.complaint:
      return AppColors.warning;
    case NotificationCategory.account:
    case NotificationCategory.hire:
    case NotificationCategory.booking:
    case NotificationCategory.schedule:
    case NotificationCategory.attendance:
    case NotificationCategory.rating:
      return AppColors.info;
  }
}

/// Relative for anything recent, absolute once "3 days ago" stops being useful.
String _formatWhen(DateTime? when) {
  if (when == null) return '';

  final local = when.toLocal();
  final elapsed = DateTime.now().difference(local);

  if (elapsed.inMinutes < 1) return 'Just now';
  if (elapsed.inMinutes < 60) return '${elapsed.inMinutes} min ago';
  if (elapsed.inHours < 24) return '${elapsed.inHours} h ago';
  if (elapsed.inDays < 7) return '${elapsed.inDays} d ago';
  return DateFormat('d MMM').format(local);
}
