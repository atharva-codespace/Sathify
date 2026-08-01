import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../../../core/routing/app_router.dart';
import '../providers/notification_provider.dart';

/// The unread badge, for every role's home app bar.
///
/// Reads the dedicated count endpoint rather than the notification list: this
/// sits in an app bar that rebuilds constantly, and pulling a page of bodies
/// just to call `.length` on it would be a page fetch per rebuild.
///
/// A failed count shows a plain bell. Push and the notification centre are
/// separate paths, and an error badge on a screen the user came to do something
/// else on is noise — the centre itself reports the failure properly.
class NotificationBell extends ConsumerWidget {
  const NotificationBell({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final unread = ref.watch(unreadCountProvider).valueOrNull ?? 0;

    return IconButton(
      tooltip: unread > 0 ? '$unread unread' : 'Notifications',
      onPressed: () => context.push(Routes.notifications),
      icon: unread > 0
          ? Badge.count(
              count: unread,
              child: const Icon(Icons.notifications_outlined),
            )
          : const Icon(Icons.notifications_none_outlined),
    );
  }
}
