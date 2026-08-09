import 'dart:async';

import 'package:flutter/widgets.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../auth/presentation/providers/auth_provider.dart';
import '../providers/notification_provider.dart';

/// Keeps the unread badge current without depending on push.
///
/// -----------------------------------------------------------------------
/// WHY POLLING, WHEN THERE IS ALREADY FCM
/// -----------------------------------------------------------------------
/// [unreadCountProvider] is deliberately not autoDisposed — the bell sits in
/// app bars that come and go, and refetching per navigation would be a request
/// per screen change. The cost of that choice is that the count is fetched
/// once and then cached for the lifetime of the app, so **something** has to
/// invalidate it when the world changes.
///
/// Until now the only two things that did were a push arriving while the app
/// was open, and the user opening the notification centre. Both are too weak
/// to rely on:
///
/// * `google-services.json` is not in the repository (see `.gitignore`), and
///   `PushService` is explicitly built to run without it — `isAvailable` is
///   false and the `received` stream never emits. Every build without that
///   file therefore had *no* automatic badge at all.
/// * The centre invalidating the count is the user having already found the
///   notification by hand, which is precisely the thing the badge exists to
///   save them from.
///
/// So a worker applying for leave created the notification server-side, the
/// row was there, and the household's badge stayed at zero until they went
/// looking. The server's own [UnreadCountView] docstring says "the home
/// screens poll this" — this is that poll, which had never been written.
///
/// It is cheap on purpose: one indexed `COUNT(*)` scoped to the recipient, and
/// only while the app is in the foreground and signed in.
class NotificationBadgeRefresher extends ConsumerStatefulWidget {
  const NotificationBadgeRefresher({
    required this.child,
    this.interval = const Duration(seconds: 60),
    super.key,
  });

  final Widget child;

  /// How often to re-read the count while the app is foregrounded.
  ///
  /// A minute is a compromise: a badge that lags a notification by up to a
  /// minute still feels immediate, while anything much tighter would keep a
  /// free-tier backend awake for a number that rarely changes.
  final Duration interval;

  @override
  ConsumerState<NotificationBadgeRefresher> createState() =>
      _NotificationBadgeRefresherState();
}

class _NotificationBadgeRefresherState
    extends ConsumerState<NotificationBadgeRefresher>
    with WidgetsBindingObserver {
  Timer? _timer;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    _restartTimer();
  }

  @override
  void dispose() {
    _timer?.cancel();
    WidgetsBinding.instance.removeObserver(this);
    super.dispose();
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state == AppLifecycleState.resumed) {
      // The highest-value refresh of the lot. Everything that happened while
      // the phone was in a pocket lands the moment it comes back out, which is
      // also the moment the user is looking at the bell.
      _refresh();
      _restartTimer();
    } else {
      // Nothing is on screen to update, and a timer firing in the background
      // is a request nobody asked for.
      _timer?.cancel();
    }
  }

  void _restartTimer() {
    _timer?.cancel();
    _timer = Timer.periodic(widget.interval, (_) => _refresh());
  }

  void _refresh() {
    if (!mounted) return;
    // Polling while signed out is a guaranteed 401 on a loop. An unapproved
    // account still polls: the notification telling them they were approved is
    // the one they are actually waiting for.
    final status = ref.read(authProvider).status;
    if (status != AuthStatus.authenticated &&
        status != AuthStatus.pendingApproval) {
      return;
    }
    ref.invalidate(unreadCountProvider);
  }

  @override
  Widget build(BuildContext context) {
    // Refresh on sign-in too, so the badge is right on the first screen after
    // login rather than up to one interval later.
    ref.listen(authProvider, (previous, next) {
      if (previous?.status != next.status || previous?.user?.id != next.user?.id) {
        _refresh();
      }
    });

    return widget.child;
  }
}
