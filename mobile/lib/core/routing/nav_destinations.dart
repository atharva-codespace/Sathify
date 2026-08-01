import 'package:flutter/material.dart';

import '../../features/auth/data/models/user_model.dart';
import 'app_router.dart';

/// One tab in the persistent bottom navigation bar.
class NavDestination {
  const NavDestination({
    required this.route,
    required this.icon,
    required this.label,
    IconData? activeIcon,
  }) : activeIcon = activeIcon ?? icon;

  final String route;
  final IconData icon;
  final IconData activeIcon;
  final String label;
}

/// The tab set for a role.
///
/// -----------------------------------------------------------------------
/// WHY THIS IS PER-ROLE RATHER THAN ONE SHARED BAR
/// -----------------------------------------------------------------------
/// The four roles barely overlap. A guard scans a gate and does nothing else;
/// a worker looks at a schedule and gets paid; a resident hires people; an
/// administrator approves them. A single compromise bar would be four-fifths
/// irrelevant to everyone looking at it, which is worse than the overflow menu
/// it replaces.
///
/// Every route here already exists — this maps roles onto a subset of them and
/// changes no path. Destinations that are *reached from* a tab (a worker's
/// profile, a booking form, a complaint) stay off the bar and push over it.
///
/// Kept to 3–5 tabs: below three a bar is not worth its height, and above five
/// the labels stop fitting on a 360dp phone, which is most of this fleet.
List<NavDestination> destinationsForRole(UserRole role) {
  switch (role) {
    case UserRole.resident:
      return const [
        NavDestination(
          route: Routes.residentHome,
          icon: Icons.search_outlined,
          activeIcon: Icons.search_rounded,
          label: 'Find help',
        ),
        NavDestination(
          route: Routes.serviceCatalogue,
          icon: Icons.grid_view_outlined,
          activeIcon: Icons.grid_view_rounded,
          label: 'Book',
        ),
        // "Visits", not "Who's coming": five tabs on a 320dp phone give each
        // label about 64dp, which truncates anything past roughly ten
        // characters. The screen it opens still says "Who's coming".
        NavDestination(
          route: Routes.mySchedule,
          icon: Icons.calendar_today_outlined,
          activeIcon: Icons.calendar_month_rounded,
          label: 'Visits',
        ),
        NavDestination(
          route: Routes.payments,
          icon: Icons.payments_outlined,
          activeIcon: Icons.payments_rounded,
          label: 'Payments',
        ),
        NavDestination(
          route: Routes.account,
          icon: Icons.person_outline_rounded,
          activeIcon: Icons.person_rounded,
          label: 'Account',
        ),
      ];

    case UserRole.worker:
      return const [
        NavDestination(
          route: Routes.workerHome,
          icon: Icons.calendar_today_outlined,
          activeIcon: Icons.calendar_month_rounded,
          label: 'Schedule',
        ),
        NavDestination(
          route: Routes.hireRequests,
          icon: Icons.mail_outline_rounded,
          activeIcon: Icons.mail_rounded,
          label: 'Requests',
        ),
        // A worker opens this at a gate, often with somebody waiting. It earns
        // a permanent tab far more than it earned a slot in an overflow menu.
        NavDestination(
          route: Routes.myGatePass,
          icon: Icons.qr_code_2_outlined,
          activeIcon: Icons.qr_code_2_rounded,
          label: 'My pass',
        ),
        NavDestination(
          route: Routes.earnings,
          icon: Icons.payments_outlined,
          activeIcon: Icons.payments_rounded,
          label: 'Earnings',
        ),
        NavDestination(
          route: Routes.account,
          icon: Icons.person_outline_rounded,
          activeIcon: Icons.person_rounded,
          label: 'Account',
        ),
      ];

    case UserRole.guard:
      // Three, deliberately. There is nothing else a guard does at a gate, and
      // every extra tap happens with someone standing in front of them.
      return const [
        NavDestination(
          route: Routes.guardHome,
          icon: Icons.qr_code_scanner_rounded,
          label: 'Scan',
        ),
        NavDestination(
          route: Routes.gateLog,
          icon: Icons.list_alt_outlined,
          activeIcon: Icons.list_alt_rounded,
          label: "Today's log",
        ),
        NavDestination(
          route: Routes.account,
          icon: Icons.person_outline_rounded,
          activeIcon: Icons.person_rounded,
          label: 'Account',
        ),
      ];

    case UserRole.societyAdmin:
      return const [
        NavDestination(
          route: Routes.adminHome,
          icon: Icons.dashboard_outlined,
          activeIcon: Icons.dashboard_rounded,
          label: 'Home',
        ),
        NavDestination(
          route: Routes.complaints,
          icon: Icons.report_gmailerrorred_outlined,
          activeIcon: Icons.report_rounded,
          label: 'Complaints',
        ),
        NavDestination(
          route: Routes.directory,
          icon: Icons.people_outline_rounded,
          activeIcon: Icons.people_rounded,
          label: 'Directory',
        ),
        NavDestination(
          route: Routes.adminDashboard,
          icon: Icons.insights_outlined,
          activeIcon: Icons.insights_rounded,
          label: 'Insights',
        ),
        NavDestination(
          route: Routes.account,
          icon: Icons.person_outline_rounded,
          activeIcon: Icons.person_rounded,
          label: 'Account',
        ),
      ];

    case UserRole.unknown:
      return const [];
  }
}
