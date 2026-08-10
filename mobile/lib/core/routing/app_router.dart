import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../theme/app_theme.dart';
import 'nav_shell.dart';
import '../../features/auth/data/models/user_model.dart';
import '../../features/auth/presentation/providers/auth_provider.dart';
import '../../features/auth/presentation/screens/account_screen.dart';
import '../../features/auth/presentation/screens/login_screen.dart';
import '../../features/auth/presentation/screens/otp_screen.dart';
import '../../features/auth/presentation/screens/pending_approval_screen.dart';
import '../../features/auth/presentation/screens/reset_password_screen.dart';
import '../../features/administration/presentation/screens/admin_dashboard_screen.dart';
import '../../features/administration/presentation/screens/admin_home_screen.dart';
import '../../features/administration/presentation/screens/complaint_detail_screen.dart';
import '../../features/administration/presentation/screens/complaints_screen.dart';
import '../../features/administration/presentation/screens/directory_screen.dart';
import '../../features/administration/presentation/screens/raise_complaint_screen.dart';
import '../../features/administration/presentation/screens/reports_screen.dart';
import '../../features/administration/presentation/screens/unmet_demand_screen.dart';
import '../../features/ai/presentation/screens/assistant_screen.dart';
import '../../features/attendance/presentation/screens/gate_log_screen.dart';
import '../../features/attendance/presentation/screens/gate_scanner_screen.dart';
import '../../features/attendance/presentation/screens/my_gate_pass_screen.dart';
import '../../features/attendance/presentation/screens/self_checkin_screen.dart';
import '../../features/auth/presentation/screens/register_screen.dart';
import '../../features/payments/presentation/screens/earnings_screen.dart';
import '../../features/payments/presentation/screens/payments_screen.dart';
import '../../features/payments/presentation/screens/receipt_screen.dart';
import '../../features/ratings/presentation/screens/rate_job_screen.dart';
import '../../features/ratings/presentation/screens/review_flags_screen.dart';
import '../../features/ratings/presentation/screens/trust_score_screen.dart';
import '../../features/ratings/presentation/screens/worker_reviews_screen.dart';
import '../../features/bookings/presentation/screens/book_slot_screen.dart';
import '../../features/bookings/presentation/screens/my_bookings_screen.dart';
import '../../features/bookings/presentation/screens/raise_emergency_screen.dart';
import '../../features/bookings/presentation/screens/service_catalogue_screen.dart';
import '../../features/bookings/presentation/screens/worker_availability_screen.dart';
import '../../features/hiring/presentation/screens/engagements_screen.dart';
import '../../features/hiring/presentation/screens/hire_requests_screen.dart';
import '../../features/hiring/presentation/screens/worker_detail_screen.dart';
import '../../features/hiring/presentation/screens/worker_search_screen.dart';
import '../../features/notifications/presentation/screens/notification_center_screen.dart';
import '../../features/notifications/presentation/screens/notification_preferences_screen.dart';
import '../../features/scheduling/presentation/screens/apply_leave_screen.dart';
import '../../features/scheduling/presentation/screens/leave_response_screen.dart';
import '../../features/scheduling/presentation/screens/my_schedule_screen.dart';
import '../../features/societies/presentation/screens/claim_flat_screen.dart';
import '../../features/societies/presentation/screens/resident_approval_queue_screen.dart';
import '../../features/workers/presentation/screens/kyc_upload_screen.dart';
import '../../features/workers/presentation/screens/worker_approval_queue_screen.dart';
import '../../features/workers/presentation/screens/worker_onboarding_screen.dart';
import '../../features/workers/presentation/screens/worker_profile_screen.dart';

/// Route names, referenced by constant rather than by raw string so a rename is
/// a compile error instead of a runtime 404.
class Routes {
  const Routes._();

  static const String splash = '/';
  static const String login = '/login';
  static const String registerResident = '/register/resident';
  static const String registerWorker = '/register/worker';

  /// Module 1.4 — phone verification, the last step of sign-up.
  ///
  /// Takes `phone` and `sent` as query parameters rather than constructor
  /// arguments so a user who backgrounds the app mid-flow returns to the same
  /// prompt instead of the login screen.
  static const String otp = '/otp';

  /// Module 1.4 — "forgot password": a code plus the new password.
  static const String resetPassword = '/reset-password';

  static const String pendingApproval = '/pending-approval';

  /// The Account tab. Home for the profile, the account switcher and sign-out.
  static const String account = '/account';

  /// Module 2.3 — resident picks their flat. Reachable while unapproved.
  static const String claimFlat = '/claim-flat';

  /// Module 2.3 — administrator's approval queue.
  static const String residentApprovals = '/admin/resident-approvals';

  /// Module 3 — worker onboarding. All three are reachable while unapproved,
  /// because completing them is exactly what an administrator reviews.
  static const String workerOnboarding = '/onboarding/worker';
  static const String workerProfileEdit = '/onboarding/worker/profile';
  static const String kycUpload = '/onboarding/worker/kyc';

  /// Module 3.5 — administrator's worker approval queue.
  static const String workerApprovals = '/admin/worker-approvals';

  /// Module 4.2 — one worker's profile. Use [workerDetailPath] to build it.
  static const String workerDetail = '/workers/:workerId';

  /// Module 4.4 — the hire-request inbox, for whichever side is signed in.
  static const String hireRequests = '/hire-requests';

  /// Module 4.5 — standing engagements.
  static const String engagements = '/engagements';

  static String workerDetailPath(int workerId) => '/workers/$workerId';

  /// Module 5.1 — the bookable service catalogue.
  static const String serviceCatalogue = '/book';

  /// Module 5.2/5.3 — pick a slot, then a worker. Use [bookSlotPath].
  static const String bookSlot = '/book/:categoryId';

  /// Module 5.2 — the caller's one-day bookings, whichever side they are on.
  static const String myBookings = '/bookings';

  /// Module 5.3 — the worker marks which days they can take one-off jobs.
  static const String myAvailability = '/availability';

  /// Module 5.5 — the resident raises an emergency, broadcast to whoever is
  /// free. Also the route the server puts on an offer notification, so a worker
  /// tapping one lands on her dashboard where the card is.
  ///
  /// Reached from the service catalogue: tapping an emergency category comes
  /// here rather than to [bookSlot], because an emergency is not a slot-and-a-
  /// worker to be chosen. Use [emergencyPath] to carry which category was
  /// tapped.
  static const String emergency = '/emergency';

  static String emergencyPath(int categoryId) =>
      Uri(path: emergency, queryParameters: {'category': '$categoryId'})
          .toString();

  static String bookSlotPath(int categoryId) => '/book/$categoryId';

  /// Module 6.1 — recurring visits and one-day jobs in one schedule.
  static const String mySchedule = '/schedule';

  /// Module 6.5 — urgent leave ("chutti"). The worker takes a day off at
  /// `/schedule/leave/new`; the household answers at `/schedule/leave/<id>`.
  /// Both sit under `/schedule` so the leave screens inherit its place in the
  /// navigation rather than becoming a fifth top-level destination.
  static const String applyLeave = '/schedule/leave/new';
  static const String leaveDetail = '/schedule/leave/:leaveId';

  static String leaveDetailPath(int leaveId) => '/schedule/leave/$leaveId';

  /// Module 7 — the gate. The scanner is the guard's home.
  static const String gateScanner = '/gate';
  static const String gateLog = '/gate/log';

  /// Module 7.1 — the worker's own QR code.
  static const String myGatePass = '/my-pass';

  /// Module 13.3 tier 2 — the worker records their own arrival when there is
  /// no guard at the gate.
  static const String selfCheckIn = '/my-pass/check-in';

  /// Module 8 — the ledger, a receipt, and a worker's monthly statement.
  static const String payments = '/payments';
  static const String earnings = '/earnings';
  static const String receipt = '/payments/:paymentId';

  static String receiptPath(String paymentId) => '/payments/$paymentId';

  /// Module 9 — rating, trust scores, reviews, and the flag queue.
  static const String rateJobs = '/rate';
  static const String myTrustScore = '/trust';
  static const String reviewFlags = '/admin/review-flags';
  static const String workerTrust = '/trust/workers/:workerId';
  static const String workerReviews = '/reviews/workers/:workerId';

  static String workerTrustPath(int workerId) => '/trust/workers/$workerId';
  static String workerReviewsPath(int workerId) => '/reviews/workers/$workerId';

  /// Module 10.3/10.4 — the notification centre and its settings.
  static const String notifications = '/notifications';
  static const String notificationPreferences = '/notifications/settings';

  /// Module 11.3 — complaints. Reachable by every role: an administrator sees
  /// the society queue, everyone else sees their own and any raised about them.
  static const String complaints = '/complaints';
  static const String raiseComplaint = '/complaints/new';
  static const String complaintDetail = '/complaints/:complaintId';

  /// Module 11.1/11.2/11.4 — administrator-only.
  static const String directory = '/admin/directory';
  static const String adminDashboard = '/admin/insights';
  static const String adminReports = '/admin/reports';
  static const String unmetDemand = '/admin/unmet-demand';

  /// Module 12.2 — the assistant. Every role, because it only ever reads the
  /// caller's own records and the server decides what those are.
  static const String assistant = '/assistant';

  static String complaintPath(int complaintId) => '/complaints/$complaintId';

  /// Prefills the complaint form with who it is about. The name travels too, so
  /// the form can say "About Rahul Sharma" without a second round trip.
  static String raiseComplaintAboutWorker(int workerId, {String name = ''}) =>
      Uri(
        path: raiseComplaint,
        queryParameters: {
          'worker': '$workerId',
          if (name.isNotEmpty) 'about': name,
        },
      ).toString();

  // One home per role. The redirect below picks between these using the role
  // carried in the authenticated user's profile.
  static const String residentHome = '/resident';
  static const String workerHome = '/worker';
  static const String guardHome = '/guard';
  static const String adminHome = '/admin';
}

/// Maps a role onto that role's home route.
String homeRouteForRole(UserRole role) {
  switch (role) {
    case UserRole.resident:
      return Routes.residentHome;
    case UserRole.worker:
      return Routes.workerHome;
    case UserRole.guard:
      return Routes.guardHome;
    case UserRole.societyAdmin:
      return Routes.adminHome;
    case UserRole.unknown:
      return Routes.login;
  }
}

final routerProvider = Provider<GoRouter>((ref) {
  return GoRouter(
    initialLocation: Routes.splash,
    // Re-evaluates `redirect` whenever auth state changes, so signing in or out
    // navigates without any screen calling go() itself.
    refreshListenable: _AuthChangeNotifier(ref),
    redirect: (context, goState) {
      final auth = ref.read(authProvider);
      final location = goState.matchedLocation;

      // Still restoring a cached session: hold on the splash screen.
      if (auth.status == AuthStatus.checking) {
        return location == Routes.splash ? null : Routes.splash;
      }

      // The session is resolved, so the splash screen has done its job.
      //
      // It is a waiting room, never a destination, and every resolved status
      // must therefore send the user somewhere concrete. Falling through to the
      // switch below would hand '/' to the unauthenticated branch, which finds
      // it in _publicRoutes and returns null — "stay put" — leaving a fresh
      // install spinning on the splash screen forever, because a device with no
      // stored session resolves to exactly that state on every cold start.
      if (location == Routes.splash) {
        switch (auth.status) {
          case AuthStatus.unauthenticated:
            return Routes.login;
          case AuthStatus.pendingApproval:
            return Routes.pendingApproval;
          case AuthStatus.authenticated:
            return homeRouteForRole(auth.user?.role ?? UserRole.unknown);
          case AuthStatus.checking:
            return null; // Unreachable: handled above.
        }
      }

      switch (auth.status) {
        case AuthStatus.unauthenticated:
          // Sign-in and the two registration flows are the only reachable
          // screens without a session.
          return _publicRoutes.contains(location) ? null : Routes.login;

        case AuthStatus.pendingApproval:
          // An unapproved account cannot transact. It may still reach the
          // pending screen and — for residents — the flat-claim screen, since
          // that submission is exactly what the administrator reviews.
          // Funnelling everything to the pending screen would deadlock onboarding.
          return _pendingAllowedRoutes.contains(location)
              ? null
              : Routes.pendingApproval;

        case AuthStatus.authenticated:
          final home = homeRouteForRole(auth.user?.role ?? UserRole.unknown);
          // Bounce away from auth-only screens...
          if (_publicRoutes.contains(location) ||
              location == Routes.pendingApproval) {
            return home;
          }
          // ...and stop one role opening another role's dashboard by typing
          // its route. The server enforces this too; this is just UX.
          if (_isRoleHome(location) && location != home) return home;
          return null;

        case AuthStatus.checking:
          return Routes.splash;
      }
    },
    routes: [
      // ---------------------------------------------------------------------
      // One shell wraps everything, rather than re-parenting the fifteen tab
      // destinations into their own subtree.
      //
      // [NavShell] decides per location whether the bar belongs there: it draws
      // one for a route that is a tab of the signed-in user's role, and returns
      // the screen untouched otherwise. So a worker profile, a booking form or
      // the login screen renders exactly as it did before, while the fifteen
      // tab destinations gain a bar.
      //
      // The point of doing it this way is that **not one `path` string moved**.
      // Deep links and the routes carried in push notifications are unaffected,
      // which is what the "do not change navigation routes" constraint asks for.
      // ---------------------------------------------------------------------
      ShellRoute(
        builder: (context, goState, child) => NavShell(
          location: goState.matchedLocation,
          child: child,
        ),
        routes: [
          GoRoute(
            path: Routes.splash,
            builder: (_, __) => const _SplashScreen(),
          ),
          GoRoute(path: Routes.login, builder: (_, __) => const LoginScreen()),
          GoRoute(
            path: Routes.account,
            builder: (_, __) => const AccountScreen(),
          ),
          GoRoute(
            path: Routes.registerResident,
            builder: (_, __) => const RegisterScreen(role: UserRole.resident),
          ),
          GoRoute(
            path: Routes.registerWorker,
            builder: (_, __) => const RegisterScreen(role: UserRole.worker),
          ),
          GoRoute(
            path: Routes.otp,
            builder: (_, goState) {
              final params = goState.uri.queryParameters;
              return OtpScreen(
                phoneNumber: params['phone'] ?? '',
                // Absent means "no code has gone out yet", so the screen sends
                // one on open. Only the flows that already triggered a send
                // pass sent=true.
                codeAlreadySent: params['sent'] == 'true',
              );
            },
          ),
          GoRoute(
            path: Routes.resetPassword,
            builder: (_, goState) => ResetPasswordScreen(
              phoneNumber: goState.uri.queryParameters['phone'] ?? '',
            ),
          ),
          GoRoute(
            path: Routes.pendingApproval,
            builder: (_, __) => const PendingApprovalScreen(),
          ),
          GoRoute(
            path: Routes.claimFlat,
            builder: (_, __) => const ClaimFlatScreen(),
          ),
          GoRoute(
            path: Routes.residentApprovals,
            builder: (_, __) => const ResidentApprovalQueueScreen(),
          ),

          // --- Module 3: Worker Onboarding & KYC ----------------------------
          GoRoute(
            path: Routes.workerOnboarding,
            builder: (_, __) => const WorkerOnboardingScreen(),
          ),
          GoRoute(
            path: Routes.workerProfileEdit,
            builder: (_, __) => const WorkerProfileScreen(),
          ),
          GoRoute(
            path: Routes.kycUpload,
            builder: (_, __) => const KycUploadScreen(),
          ),
          GoRoute(
            path: Routes.workerApprovals,
            builder: (_, __) => const WorkerApprovalQueueScreen(),
          ),
          // --- Module 4: Discovery & Hiring ---------------------------------
          GoRoute(
            path: Routes.workerDetail,
            builder: (_, goState) => WorkerDetailScreen(
              workerId: int.parse(goState.pathParameters['workerId']!),
            ),
          ),
          GoRoute(
            path: Routes.hireRequests,
            builder: (_, __) => const HireRequestsScreen(),
          ),
          GoRoute(
            path: Routes.engagements,
            builder: (_, __) => const EngagementsScreen(),
          ),

          // --- Module 5: One-Day Service Booking ----------------------------
          GoRoute(
            path: Routes.serviceCatalogue,
            builder: (_, __) => const ServiceCatalogueScreen(),
          ),
          GoRoute(
            path: Routes.bookSlot,
            builder: (_, goState) => BookSlotScreen(
              categoryId: int.parse(goState.pathParameters['categoryId']!),
            ),
          ),
          GoRoute(
            path: Routes.myBookings,
            builder: (_, __) => const MyBookingsScreen(),
          ),
          GoRoute(
            path: Routes.myAvailability,
            builder: (_, __) => const WorkerAvailabilityScreen(),
          ),
          // Module 5.5. Role-split at the route rather than inside one screen:
          // a resident is raising a request, a worker is answering one, and the
          // two share a notification route but nothing else. A worker arriving
          // here from a push lands on her schedule, which is where the offer
          // cards are.
          GoRoute(
            path: Routes.emergency,
            builder: (_, goState) {
              final role = ref.read(authProvider).user?.role;
              if (role == UserRole.worker) return const MyScheduleScreen();
              return RaiseEmergencyScreen(
                // Which category was tapped in the catalogue. Absent when the
                // route arrives from a notification, which the screen handles
                // by asking.
                categoryId: int.tryParse(
                  goState.uri.queryParameters['category'] ?? '',
                ),
              );
            },
          ),

          // A resident's home is worker discovery — the first thing they came to
          // the app to do (Module 4.1).
          GoRoute(
            path: Routes.residentHome,
            builder: (_, __) => const WorkerSearchScreen(),
          ),
          // --- Module 6: Scheduling -----------------------------------------
          GoRoute(
            path: Routes.mySchedule,
            builder: (_, __) => const MyScheduleScreen(),
          ),
          // Declared before `/schedule/leave/:leaveId` so "new" is matched as a
          // literal rather than swallowed as an id, exactly as `/complaints/new`
          // is above.
          GoRoute(
            path: Routes.applyLeave,
            builder: (_, __) => const ApplyLeaveScreen(),
          ),
          GoRoute(
            path: Routes.leaveDetail,
            builder: (_, goState) => LeaveResponseScreen(
              leaveId:
                  int.tryParse(goState.pathParameters['leaveId'] ?? '') ?? 0,
            ),
          ),

          // A worker's home is their schedule. Module 6.1 exists precisely so they
          // see one merged day rather than checking recurring work and one-day
          // jobs separately, which makes it the right landing screen; the request
          // inbox moved into its menu.
          //
          // Module 3's KYC onboarding screen is still not built. When it is, it
          // belongs ahead of this in the flow: an unverified worker is never
          // discoverable, so nothing will ever appear on this schedule until they
          // have been through it.
          GoRoute(
            path: Routes.workerHome,
            builder: (_, __) => const MyScheduleScreen(),
          ),
          // --- Module 9: Ratings, Reviews & Trust Score ---------------------
          GoRoute(
            path: Routes.rateJobs,
            builder: (_, __) => const RateJobScreen(),
          ),
          GoRoute(
            path: Routes.myTrustScore,
            builder: (_, __) => const TrustScoreScreen(),
          ),
          GoRoute(
            path: Routes.workerTrust,
            builder: (_, goState) => TrustScoreScreen(
              workerId: int.parse(goState.pathParameters['workerId']!),
            ),
          ),
          GoRoute(
            path: Routes.workerReviews,
            builder: (_, goState) => WorkerReviewsScreen(
              workerId: int.parse(goState.pathParameters['workerId']!),
            ),
          ),
          GoRoute(
            path: Routes.reviewFlags,
            builder: (_, __) => const ReviewFlagsScreen(),
          ),

          // --- Module 10: Notifications -------------------------------------
          GoRoute(
            path: Routes.notifications,
            builder: (_, __) => const NotificationCenterScreen(),
          ),
          GoRoute(
            path: Routes.notificationPreferences,
            builder: (_, __) => const NotificationPreferencesScreen(),
          ),

          // --- Module 11: Admin, Reporting & Complaints ---------------------
          // `/complaints/new` is declared before `/complaints/:complaintId` so
          // "new" is matched as a literal rather than parsed as an id — go_router
          // tries routes in order, and int.parse('new') would throw.
          GoRoute(
            path: Routes.raiseComplaint,
            builder: (_, goState) {
              final query = goState.uri.queryParameters;
              return RaiseComplaintScreen(
                againstWorker: int.tryParse(query['worker'] ?? ''),
                againstResident: int.tryParse(query['resident'] ?? ''),
                aboutLabel: query['about'] ?? '',
              );
            },
          ),
          GoRoute(
            path: Routes.complaintDetail,
            builder: (_, goState) => ComplaintDetailScreen(
              complaintId: int.parse(goState.pathParameters['complaintId']!),
            ),
          ),
          GoRoute(
            path: Routes.complaints,
            builder: (_, __) => const ComplaintsScreen(),
          ),
          GoRoute(
            path: Routes.directory,
            builder: (_, __) => const DirectoryScreen(),
          ),
          GoRoute(
            path: Routes.adminDashboard,
            builder: (_, __) => const AdminDashboardScreen(),
          ),
          GoRoute(
            path: Routes.adminReports,
            builder: (_, __) => const ReportsScreen(),
          ),
          GoRoute(
            path: Routes.unmetDemand,
            builder: (_, __) => const UnmetDemandScreen(),
          ),

          // --- Module 12: AI Layer ------------------------------------------
          GoRoute(
            path: Routes.assistant,
            builder: (_, __) => const AssistantScreen(),
          ),

          // --- Module 8: Payments & Payouts ---------------------------------
          GoRoute(
            path: Routes.payments,
            builder: (_, __) => const PaymentsScreen(),
          ),
          GoRoute(
            path: Routes.earnings,
            builder: (_, __) => const EarningsScreen(),
          ),
          GoRoute(
            path: Routes.receipt,
            builder: (_, goState) => ReceiptScreen(
              paymentId: goState.pathParameters['paymentId']!,
            ),
          ),

          // --- Module 7: Attendance & Gate Verification ---------------------
          GoRoute(
            path: Routes.gateLog,
            builder: (_, __) => const GateLogScreen(),
          ),
          GoRoute(
            path: Routes.myGatePass,
            builder: (_, __) => const MyGatePassScreen(),
          ),
          // Module 13.3 tier 2. Declared after the pass itself, which is the
          // primary path — this is the fallback for a gate with nobody on it.
          GoRoute(
            path: Routes.selfCheckIn,
            builder: (_, __) => const SelfCheckInScreen(),
          ),

          // A guard's home is the scanner. There is nothing else they do at a
          // gate, and every extra tap happens with someone waiting in front of
          // them.
          GoRoute(
            path: Routes.guardHome,
            builder: (_, __) => const GateScannerScreen(),
          ),
          GoRoute(
            path: Routes.gateScanner,
            builder: (_, __) => const GateScannerScreen(),
          ),
          GoRoute(
            path: Routes.adminHome,
            // A minimal hub, not Module 11. It exists because the approval queues
            // from Modules 2.3 and 3.5 had routes but nothing linking to them, so
            // the two screens that gate every user on the platform could not be
            // reached. Module 11 should replace this rather than build around it.
            builder: (_, __) => const AdminHomeScreen(),
          ),
        ],
      ),
    ],
    // A location that matches nothing must never be a crash.
    //
    // Most navigation in this app is typed — `Routes.something` — and cannot be
    // wrong. Notification routes are not: they are strings chosen by the server
    // and stored on the notification row, so a backend that emits a path this
    // build does not know (or an old row written before a route was renamed)
    // arrives here. Tapping it used to throw GoException and take the screen
    // down, which is a spectacular way to fail at "you have a new complaint".
    //
    // `errorBuilder` rather than `onException`, for two reasons. go_router
    // asserts that only *one* of them may be set, and this one pushes a page
    // instead of navigating: an `onException` that called `go()` would reset the
    // stack, so a bad notification tapped mid-task would also throw away
    // whatever the person was in the middle of doing.
    errorBuilder: (context, goState) =>
        RouteNotFoundScreen(location: goState.uri.toString()),
  );
});

/// Reachable without a session.
const _publicRoutes = {
  Routes.splash,
  Routes.login,
  Routes.registerResident,
  Routes.registerWorker,
  // Necessarily public: both are reached by a user who is not signed in.
  Routes.otp,
  Routes.resetPassword,
};

/// Reachable while signed in but not yet approved.
///
/// Onboarding submissions live here: a resident claiming a flat and a worker
/// building their profile and uploading their Aadhaar are precisely what an
/// administrator reviews in order to approve them. Funnelling an unapproved
/// user to the pending screen and nowhere else would deadlock the very step
/// that gets them approved.
const _pendingAllowedRoutes = {
  Routes.pendingApproval,
  Routes.claimFlat,
  Routes.workerOnboarding,
  Routes.workerProfileEdit,
  Routes.kycUpload,
  // An unapproved user is precisely the person waiting on a notification: the
  // ACCOUNT message telling them they were approved, or why they were not.
  // Locking them out of the centre would hide the one thing they are here for.
  Routes.notifications,
  Routes.notificationPreferences,
};

bool _isRoleHome(String location) => const {
      Routes.residentHome,
      Routes.workerHome,
      Routes.guardHome,
      Routes.adminHome,
    }.contains(location);

/// Bridges Riverpod's [authProvider] to go_router's [Listenable] refresh hook.
class _AuthChangeNotifier extends ChangeNotifier {
  _AuthChangeNotifier(Ref ref) {
    ref.listen(authProvider, (previous, next) {
      // The identity check is load-bearing for account switching. Swapping to
      // another saved account moves from `authenticated` straight to
      // `authenticated`, so a status-only comparison would never re-run the
      // redirect — leaving, say, a resident sitting on `/resident` after they
      // switched into their administrator account.
      final statusChanged = previous?.status != next.status;
      final userChanged = previous?.user?.id != next.user?.id;
      if (statusChanged || userChanged) notifyListeners();
    });
  }
}

/// The launch hold, shown only while a cached session is being restored.
///
/// Most launches never render this for a perceptible moment: the session lives
/// in the Keystore and resolves in milliseconds, and the redirect above moves
/// on the instant it does. It stays deliberately quiet — a brand mark and a
/// slim progress line rather than the icon-plus-headline-plus-spinner stack it
/// replaced, which announced itself as a loading screen every single launch.
///
/// The one time it is genuinely visible is a cold start against a sleeping
/// free-tier backend, and even then the cached profile lands first.
class _SplashScreen extends StatelessWidget {
  const _SplashScreen();

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      body: Center(
        child: Column(
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            // The brand mark itself, not a stand-in glyph in a green tile.
            // Sized and centred to match `drawable/launch_background.xml`, so
            // the native launch screen hands over to this one without the logo
            // jumping — on a cold start the two are on screen back to back.
            Image.asset(
              'assets/images/sathify_logo.png',
              width: 96,
              height: 96,
              fit: BoxFit.contain,
              // A missing asset must not take down the launch screen, which is
              // the one screen with no way back.
              errorBuilder: (_, __, ___) => const Icon(
                Icons.verified_user_rounded,
                size: 56,
                color: AppColors.primary,
              ),
            ),
            const SizedBox(height: AppSpacing.md),
            Text('Sathify', style: Theme.of(context).textTheme.headlineSmall),
            const SizedBox(height: AppSpacing.xxl),
            const SizedBox(
              width: 96,
              child: LinearProgressIndicator(
                minHeight: 3,
                backgroundColor: AppColors.surfaceMuted,
              ),
            ),
          ],
        ),
      ),
    );
  }
}

/// Shown when a location matches no route.
///
/// Deliberately blameless. The only way an ordinary user reaches this is a
/// notification pointing somewhere this build of the app does not have, which
/// is the platform's mistake and not theirs, so it offers a way onwards rather
/// than an error code.
class RouteNotFoundScreen extends StatelessWidget {
  const RouteNotFoundScreen({super.key, required this.location});

  final String location;

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Not available')),
      body: Center(
        child: Padding(
          padding: const EdgeInsets.all(AppSpacing.xl),
          child: Column(
            mainAxisAlignment: MainAxisAlignment.center,
            children: [
              const Icon(Icons.explore_off_outlined, size: 48),
              const SizedBox(height: AppSpacing.md),
              Text(
                'We could not open that',
                style: Theme.of(context).textTheme.titleMedium,
                textAlign: TextAlign.center,
              ),
              const SizedBox(height: AppSpacing.sm),
              Text(
                'This link may need a newer version of the app.',
                style: Theme.of(context).textTheme.bodyMedium,
                textAlign: TextAlign.center,
              ),
              const SizedBox(height: AppSpacing.xl),
              FilledButton(
                onPressed: () => context.go(Routes.notifications),
                child: const Text('Go to notifications'),
              ),
            ],
          ),
        ),
      ),
    );
  }
}
