import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'core/routing/app_router.dart';
import 'core/theme/app_theme.dart';
import 'features/auth/presentation/providers/auth_provider.dart';
import 'features/bookings/presentation/widgets/emergency_live_refresher.dart';
import 'features/notifications/data/push_service.dart';
import 'features/notifications/presentation/providers/notification_provider.dart';
import 'features/notifications/presentation/widgets/notification_badge_refresher.dart';

/// Root widget: theme, localisation, routing, and Module 10's push plumbing.
class SathifyApp extends ConsumerStatefulWidget {
  const SathifyApp({super.key});

  @override
  ConsumerState<SathifyApp> createState() => _SathifyAppState();
}

class _SathifyAppState extends ConsumerState<SathifyApp> {
  late final StreamSubscription<String> _opened;
  late final StreamSubscription<PushMessage> _received;

  @override
  void initState() {
    super.initState();

    final push = ref.read(pushServiceProvider);

    // A tapped notification carries the route the server suggested.
    _opened = push.opened.listen(_openRoute);

    // A push that lands while the app is open would otherwise leave the badge
    // stale until the next cold start.
    _received = push.received.listen((_) => invalidateNotifications(ref));
  }

  @override
  void dispose() {
    unawaited(_opened.cancel());
    unawaited(_received.cancel());
    super.dispose();
  }

  void _openRoute(String route) {
    if (!mounted || route.isEmpty) return;
    // `push` rather than `go`: the user is mid-task somewhere and should be
    // able to come back. On a cold start the router has already resolved the
    // initial location by the time Firebase reports the launch message, so
    // there is a stack to push onto.
    unawaited(ref.read(routerProvider).push(route));
  }

  @override
  Widget build(BuildContext context) {
    // Push registration happens after sign-in, never at launch: the token is
    // stored against the authenticated user, and registering it before there is
    // a session would attach this phone to nobody's account.
    //
    // Unapproved accounts register too — the notification telling them they were
    // approved is the one they most need to receive.
    ref.listen(authProvider, (previous, next) {
      if (previous?.status == next.status) return;
      if (next.status == AuthStatus.authenticated ||
          next.status == AuthStatus.pendingApproval) {
        unawaited(ref.read(pushServiceProvider).start());
      }
    });

    // Wraps the whole app rather than sitting on one screen: the badge is in
    // every role's app bar, and push is not guaranteed to be available at all
    // (a build without google-services.json has none). See the widget.
    // Both refreshers wrap the whole app rather than sitting on one screen.
    //
    // For the emergency one that is load-bearing, not tidiness: a worker who is
    // on her earnings screen when a request goes out still has to see it, and a
    // resident who navigated away from the request they just raised still has to
    // be told who accepted it. Anchoring the poll to a screen would mean the
    // update only arrives for people who happened to be looking at the right
    // place. It is idle-cheap by construction — see the widget.
    return NotificationBadgeRefresher(
      child: EmergencyLiveRefresher(
      child: MaterialApp.router(
        title: 'Sathify',
        debugShowCheckedModeBanner: false,
        theme: AppTheme.light,
        darkTheme: AppTheme.dark,
        // Light only, by design. The palette is built around a soft off-white
        // ground with the brand green rationed to calls to action, and a dark
        // inversion of that would need its own set of decisions rather than a
        // mechanical flip. Pinning the mode also stops a device in dark mode
        // from rendering a half-adapted screen.
        themeMode: ThemeMode.light,
        routerConfig: ref.watch(routerProvider),

        // Multilingual support is core MVP scope: many workers do not read
        // English comfortably (see SRS 5.4).
        supportedLocales: const [
          Locale('en'),
          Locale('hi'),
          Locale('mr'),
        ],
        localizationsDelegates: const [
          GlobalMaterialLocalizations.delegate,
          GlobalWidgetsLocalizations.delegate,
          GlobalCupertinoLocalizations.delegate,
        ],
      ),
      ),
    );
  }
}
