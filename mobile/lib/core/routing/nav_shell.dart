import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../features/auth/data/models/user_model.dart';
import '../../features/auth/presentation/providers/auth_provider.dart';
import '../../shared/widgets/app_bottom_nav.dart';
import 'nav_destinations.dart';

/// Wraps the app in a persistent bottom navigation bar.
///
/// Installed as a `ShellRoute` in [routerProvider], wrapping the whole route
/// list rather than a re-parented subtree — **not one `path` string moved**, so
/// deep links and push-notification routing are unaffected.
///
/// -----------------------------------------------------------------------
/// THE BAR STAYS UP ON PUSHED SCREENS, AND KEEPS THE SECTION HIGHLIGHTED
/// -----------------------------------------------------------------------
/// [location] is the shell's matched location, which tracks tab-level `go`
/// navigation and does not follow a `push` into a detail screen. So opening a
/// worker profile from Find help leaves the bar up with Find help still lit.
///
/// That is the behaviour we want, and it is worth stating because it is easy to
/// mistake for a bug: the requirement is one-tap movement between sections
/// *from anywhere*, and a bar that vanished on every detail screen would not
/// deliver it. Keeping the originating section lit also answers "where am I?"
/// while reading a pushed screen.
///
/// The consequence for screens with their own pinned bottom action — the
/// worker profile's "Send hire request" — is that they sit directly above this
/// bar, so those actions are styled flat rather than raised. See
/// `worker_detail_screen.dart`.
class NavShell extends ConsumerWidget {
  const NavShell({super.key, required this.child, required this.location});

  final Widget child;
  final String location;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final role = ref.watch(authProvider).user?.role ?? UserRole.unknown;
    final destinations = destinationsForRole(role);

    // An unresolved role has no bar rather than a wrong one. This is a real
    // state, briefly, between a session resolving and the profile arriving.
    if (destinations.isEmpty) return child;

    return Scaffold(
      body: child,
      bottomNavigationBar: AppBottomNav(
        currentIndex: _indexFor(destinations),
        onTap: (index) {
          final target = destinations[index].route;
          // `go` rather than `push`: tabs are lateral moves, and pushing would
          // build a back stack where every tab you ever touched has to be
          // unwound to leave the app.
          if (target != location) context.go(target);
        },
        items: [
          for (final destination in destinations)
            AppNavItem(
              icon: destination.icon,
              activeIcon: destination.activeIcon,
              label: destination.label,
            ),
        ],
      ),
    );
  }

  /// Which tab is highlighted, or -1 for none.
  ///
  /// -1 is a legitimate answer: a shell route can be reached that is not itself
  /// a tab for the current role, and highlighting an unrelated tab would be a
  /// lie about where the user is.
  int _indexFor(List<NavDestination> destinations) {
    for (var i = 0; i < destinations.length; i++) {
      if (destinations[i].route == location) return i;
    }
    return -1;
  }
}
