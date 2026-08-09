import 'dart:async';

import 'package:flutter/widgets.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../auth/presentation/providers/auth_provider.dart';
import '../providers/booking_provider.dart';

/// Keeps an in-flight emergency request current on both dashboards.
///
/// -----------------------------------------------------------------------
/// WHY POLLING, AND WHY IT IS NOT EXPENSIVE
/// -----------------------------------------------------------------------
/// A broadcast request goes to several workers at once and exactly one gets it.
/// The moment that happens, the card has to disappear from everybody else's
/// screen — otherwise six people tap Accept on a job that is gone and are told
/// they lost a race they could not see. And the household has to be told who is
/// coming without having to pull to refresh.
///
/// That is a socket-shaped problem, and this backend cannot have a socket: one
/// free Render web service, no Channels, no Redis, no second process
/// (`docs/free-tier-constraints.md` §7). Rather than pretend otherwise, this
/// polls a deliberately tiny endpoint — and does so **only while something is
/// actually happening**:
///
/// * a worker with no open offer polls at the idle interval, which is what
///   notices a new request arriving,
/// * anybody with a live request polls at the fast interval, which is what makes
///   a claim feel immediate, and
/// * a backgrounded app polls not at all, because nothing is on screen to
///   update and a timer firing in a pocket is a request nobody asked for.
///
/// The result is that second-level freshness costs something only during the
/// few minutes an emergency is in flight, which is the only window in which
/// anybody wants it.
///
/// Mirrors [NotificationBadgeRefresher], deliberately: two refreshers that
/// behaved differently under backgrounding would be two sets of bugs.
class EmergencyLiveRefresher extends ConsumerStatefulWidget {
  const EmergencyLiveRefresher({
    required this.child,
    this.liveInterval = const Duration(seconds: 5),
    this.idleInterval = const Duration(seconds: 30),
    super.key,
  });

  final Widget child;

  /// While a request is open or an offer is on screen. Short enough that a
  /// claimed job leaves the other dashboards before anybody taps it.
  final Duration liveInterval;

  /// While nothing is in flight. This is the interval that notices a new
  /// request arriving on a phone whose push never landed — which, without
  /// `google-services.json` in the build, is every phone.
  final Duration idleInterval;

  @override
  ConsumerState<EmergencyLiveRefresher> createState() =>
      _EmergencyLiveRefresherState();
}

class _EmergencyLiveRefresherState
    extends ConsumerState<EmergencyLiveRefresher> with WidgetsBindingObserver {
  Timer? _timer;
  Duration? _current;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    _schedule(widget.idleInterval);
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
      // The highest-value refresh of the lot: everything that happened while
      // the phone was face-down lands the moment it comes back out.
      _refresh();
      _schedule(_current ?? widget.idleInterval);
    } else {
      _timer?.cancel();
      _timer = null;
    }
  }

  void _schedule(Duration interval) {
    if (_timer != null && _current == interval) return;
    _timer?.cancel();
    _current = interval;
    _timer = Timer.periodic(interval, (_) => _refresh());
  }

  void _refresh() {
    if (!mounted) return;
    // Polling while signed out is a guaranteed 401 on a loop.
    if (ref.read(authProvider).status != AuthStatus.authenticated) return;
    ref.invalidate(emergencyLiveProvider);
  }

  @override
  Widget build(BuildContext context) {
    // Re-reads the pace from whatever the last poll returned. Watching rather
    // than reading is what makes the interval adapt without the screens having
    // to tell this widget anything.
    final live = ref.watch(emergencyLiveProvider);
    final busy = live.maybeWhen(
      data: (state) => state.hasLiveWork,
      orElse: () => false,
    );

    // Scheduled after this frame: changing a timer during build is a side
    // effect in the wrong place, and Riverpod will rebuild us again anyway.
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (!mounted) return;
      _schedule(busy ? widget.liveInterval : widget.idleInterval);
    });

    ref.listen(authProvider, (previous, next) {
      if (previous?.status != next.status || previous?.user?.id != next.user?.id) {
        _refresh();
      }
    });

    return widget.child;
  }
}
