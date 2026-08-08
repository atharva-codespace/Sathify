/// Every backend route the app calls, in one place.
///
/// Paths are relative to [AppConfig.apiBaseUrl] and mirror the module prefixes
/// registered in the Django `config/urls.py`, so a route added on the server has
/// exactly one obvious home here.
///
/// Endpoints are commented out until their module is built on both sides.
class ApiEndpoints {
  const ApiEndpoints._();

  // --- Module 1: Identity & Access ------------------------------------------
  static const String login = '/auth/login/';
  static const String refresh = '/auth/refresh/';
  static const String logout = '/auth/logout/';
  static const String registerResident = '/auth/register/resident/';
  static const String registerWorker = '/auth/register/worker/';
  static const String me = '/auth/me/';
  static const String requestOtp = '/auth/otp/request/';
  static const String verifyOtp = '/auth/otp/verify/';

  // --- Module 2: Society & Resident Onboarding ------------------------------
  /// Unauthenticated — powers the society picker during registration.
  static const String publicSocieties = '/societies/public/';
  static const String registerSociety = '/societies/register/';
  static const String mySociety = '/societies/me/';
  static const String societyConfig = '/societies/me/config/';
  static const String gates = '/societies/gates/';
  static const String towers = '/societies/towers/';
  static const String bulkFlats = '/societies/towers/bulk-flats/';
  static const String flats = '/societies/flats/';
  static const String claimFlat = '/societies/residents/';
  static const String myResidentProfile = '/societies/residents/me/';
  static const String residentDirectory = '/societies/residents/all/';
  static const String pendingResidents = '/societies/residents/pending/';
  static const String setPrimaryResident = '/societies/residents/set-primary/';

  /// Approve or reject a pending resident.
  static String residentDecision(int residentId) =>
      '/societies/residents/$residentId/decide/';

  // --- Module 3: Worker Onboarding & KYC ------------------------------------
  /// The catalogue a worker picks their services from.
  static const String serviceTypes = '/workers/service-types/';

  /// 3.1 — GET reads, POST creates, PATCH updates. Multipart for the photo.
  /// The caller's *own* profile. Distinct from [workerProfile], which is
  /// another worker's public profile under Module 4 — these collided until
  /// `flutter analyze` caught it.
  static const String myWorkerProfile = '/workers/profile/';

  /// 3.2/3.6 — Aadhaar upload. Consent travels in the same request, because the
  /// DPDP Act requires it at the point of collection.
  static const String uploadAadhaar = '/workers/kyc/';

  /// Every attempt, so a re-upload after a poor scan stays visible.
  static const String myKycAttempts = '/workers/kyc/mine/';

  /// 3.6 — consent records, one row per purpose.
  static const String consents = '/workers/consents/';

  /// 3.5 — the administrator's worker approval queue.
  static const String pendingWorkers = '/workers/review/pending/';

  /// Poll one attempt while OCR runs.
  static String kycAttempt(int kycId) => '/workers/kyc/$kycId/';

  /// 3.2/3.3 — confirm or correct the pre-filled fields, or enter them by hand
  /// when OCR could not read the document at all.
  static String confirmKyc(int kycId) => '/workers/kyc/$kycId/confirm/';

  static String withdrawConsent(int consentId) =>
      '/workers/consents/$consentId/withdraw/';

  static String workerReview(int workerId) => '/workers/review/$workerId/';

  /// 3.5 — approve or reject. Only an approved worker reaches Module 4's search.
  static String decideWorker(int workerId) =>
      '/workers/review/$workerId/decide/';

  // --- Module 4: Discovery & Hiring ------------------------------------------
  /// 4.1 — ranked worker search. Every row carries a `match_percentage`.
  static const String searchWorkers = '/hiring/workers/';
  static const String hireRequests = '/hiring/requests/';
  static const String engagements = '/hiring/engagements/';

  /// 4.2 — the full profile a resident reads before sending a request.
  static String workerProfile(int workerId) => '/hiring/workers/$workerId/';

  static String hireRequest(int requestId) => '/hiring/requests/$requestId/';

  /// 4.4 — the worker accepts or declines. Accepting creates the engagement.
  static String respondToHireRequest(int requestId) =>
      '/hiring/requests/$requestId/respond/';

  static String withdrawHireRequest(int requestId) =>
      '/hiring/requests/$requestId/withdraw/';

  static String engagement(int engagementId) =>
      '/hiring/engagements/$engagementId/';

  /// 4.5 — pause, resume or terminate. An action endpoint rather than a PATCH
  /// on `status`, because these are transitions with rules.
  static String engagementTransition(int engagementId) =>
      '/hiring/engagements/$engagementId/transition/';

  /// 4.6 — give notice. Distinct from `transition`'s `terminate` action on
  /// purpose: notice is the ordinary way an arrangement ends, and terminate is
  /// the exceptional one. Sharing an endpoint would make the exceptional path
  /// as easy to reach as the ordinary one.
  static String giveNotice(int engagementId) =>
      '/hiring/engagements/$engagementId/notice/';

  static String withdrawNotice(int engagementId) =>
      '/hiring/engagements/$engagementId/notice/withdraw/';

  // --- Module 5: One-Day Service Booking -------------------------------------
  /// 5.1 — the bookable catalogue, with duration and price guidance.
  static const String serviceCategories = '/bookings/categories/';

  /// 5.3 — the caller's own per-date availability (worker). GET reads, PUT
  /// upserts one date.
  static const String myAvailability = '/bookings/availability/';

  /// 5.3 — workers free for a specific category, date and time.
  static const String bookingMatch = '/bookings/match/';

  /// 5.2 — create a booking, or list the caller's own.
  static const String bookings = '/bookings/';

  /// 5.3 — a specific worker's open dates, for the resident's date picker.
  static String workerAvailability(int workerId) =>
      '/bookings/availability/$workerId/';

  static String booking(int bookingId) => '/bookings/$bookingId/';

  /// 5.4 — the worker confirms or declines.
  static String respondToBooking(int bookingId) =>
      '/bookings/$bookingId/respond/';

  /// 5.4 — what cancelling right now would cost. Always asked before cancelling,
  /// so the fee is never a surprise after the fact.
  static String cancellationQuote(int bookingId) =>
      '/bookings/$bookingId/cancellation-quote/';

  static String cancelBooking(int bookingId) => '/bookings/$bookingId/cancel/';

  static String completeBooking(int bookingId) =>
      '/bookings/$bookingId/complete/';

  // --- Module 6: Scheduling & Task Management --------------------------------
  /// 6.1 — the caller's own day, engagements and bookings merged.
  static const String myToday = '/scheduling/me/today/';

  /// 6.1 — the same over a date range (`?from=&to=`).
  static const String myAgenda = '/scheduling/me/agenda/';

  /// 6.1 — every expected visit in the society (administrators).
  static const String societyAgenda = '/scheduling/society/agenda/';

  /// 6.3 — pre-flight check before committing to a slot.
  static const String conflictCheck = '/scheduling/conflicts/check/';

  /// 6.5 — urgent leave ("chutti"). GET lists what the caller can see; POST
  /// applies, and the server approves it in the same response.
  static const String leaveRequests = '/scheduling/leave/';

  /// The household's answer: do you need somebody else that day?
  static String leaveResponse(int leaveId) =>
      '/scheduling/leave/$leaveId/response/';

  /// Workers free to cover, ranked by the Module 4.3 scorer.
  static String replacementCandidates(int leaveId) =>
      '/scheduling/leave/$leaveId/candidates/';

  /// Confirms who is covering, and settles their pay server-side.
  static String assignReplacement(int leaveId) =>
      '/scheduling/leave/$leaveId/replacement/';

  static String withdrawLeave(int leaveId) =>
      '/scheduling/leave/$leaveId/withdraw/';

  /// 6.4 — reminders ready to deliver.
  static const String dueReminders = '/scheduling/reminders/due/';

  static String workerAgenda(int workerId) =>
      '/scheduling/workers/$workerId/agenda/';

  /// 6.2 — arrival and departure expectations for one engagement.
  static String taskTiming(int engagementId) =>
      '/scheduling/timing/$engagementId/';

  static String reminderDelivered(int reminderId) =>
      '/scheduling/reminders/$reminderId/delivered/';

  // --- Module 7: Attendance & Gate Verification -----------------------------
  /// 7.1 — the worker's own QR payload.
  static const String myGatePass = '/attendance/my-pass/';
  static const String rotateGatePass = '/attendance/my-pass/rotate/';

  /// 7.2/7.4 — the day's expected visits and pass codes. Cached on the guard's
  /// device so scanning keeps working with no connectivity.
  static const String gateRoster = '/attendance/roster/';

  /// 7.2 — resolve a scanned code. Creates nothing.
  static const String scanPass = '/attendance/scan/';

  /// 7.2/7.5/7.6 — log a decision, or read the audit trail.
  static const String attendanceEvents = '/attendance/events/';

  /// 7.4 — replay the offline queue. Idempotent on the client-generated UUIDs.
  static const String attendanceSync = '/attendance/sync/';

  /// 13.3 tier 2 — the worker records their own arrival when no guard is on
  /// duty. Idempotent on the same client-generated UUID scheme.
  static const String selfCheckIn = '/attendance/self-checkin/';

  /// 7.5 — photographed paper register, the last-resort path.
  static const String registerScans = '/attendance/registers/';

  /// 7.3 — submit a live gate photo for comparison.
  static String verifyFace(String eventId) =>
      '/attendance/events/$eventId/face/';

  /// 7.3 — the guard resolves a below-threshold match.
  static String resolveEvent(String eventId) =>
      '/attendance/events/$eventId/resolve/';

  // --- Module 8: Payments & Payouts -------------------------------------------
  /// 8.2 — the ledger, scoped to whoever is asking.
  static const String payments = '/payments/';

  /// 8.1 — the attendance arithmetic behind a suggested salary amount.
  static const String salaryBasis = '/payments/salary-basis/';

  /// 8.1/8.4 — open a payment for a month's salary, tip included.
  static const String payEngagement = '/payments/engagement/';

  /// 8.1/8.4 — open a payment for a one-day booking.
  static const String payBooking = '/payments/booking/';

  /// 8.3 — monthly salary summary. `/csv/` and `/pdf/` return files.
  static const String paymentSummary = '/payments/summary/';
  static const String paymentSummaryCsv = '/payments/summary/csv/';
  static const String paymentSummaryPdf = '/payments/summary/pdf/';

  /// 8.6 — raised disputes.
  static const String paymentDisputes = '/payments/disputes/';

  static String payment(String paymentId) => '/payments/$paymentId/';

  /// 8.3 — a single transaction's receipt, for either party.
  static String paymentReceipt(String paymentId) =>
      '/payments/$paymentId/receipt/';

  /// 8.1 — opens the Razorpay order server-side and returns the checkout payload.
  static String paymentCheckout(String paymentId) =>
      '/payments/$paymentId/checkout/';

  /// 8.1 — hands back the signed response Razorpay Checkout produced. This is
  /// what actually settles the payment; the app's own word does not.
  static String paymentConfirm(String paymentId) =>
      '/payments/$paymentId/confirm/';

  /// 8.6 — raise a dispute on a payment.
  static String paymentDispute(String paymentId) =>
      '/payments/$paymentId/dispute/';

  /// 8.5 — the replacement-worker pay rule for an engagement.
  static String replacementSplit(int engagementId) =>
      '/payments/split/$engagementId/';

  // --- Module 9: Ratings, Reviews & Trust Score -------------------------------
  /// 9.1 — submit a rating, or list the ones the caller can see.
  static const String ratings = '/ratings/';

  /// 9.1 — completed jobs the caller has not rated yet.
  static const String pendingRatings = '/ratings/pending/';

  /// 9.3 — the caller's own score. Always arrives with its breakdown.
  static const String myTrustScore = '/ratings/trust/me/';

  /// 9.3 — every change, with the breakdown frozen as it was at the time.
  static const String trustHistory = '/ratings/trust/history/';

  /// 9.4 — flagged ratings awaiting an administrator.
  static const String reviewFlags = '/ratings/flags/';

  /// A worker's public reviews. Withheld ones are excluded server-side.
  static String workerRatings(int workerId) => '/ratings/workers/$workerId/';

  /// 9.3 — a worker's score and the reasons behind it.
  static String workerTrustScore(int workerId) =>
      '/ratings/trust/workers/$workerId/';

  static String resolveReviewFlag(int flagId) =>
      '/ratings/flags/$flagId/resolve/';

  // --- Module 10: Notifications ----------------------------------------------
  /// 10.3 — the notification centre, so nothing is lost to a missed push.
  static const String notifications = '/notifications/';

  /// Badge count. Its own endpoint so polling does not pull a page of bodies.
  static const String unreadCount = '/notifications/unread-count/';
  static const String markAllRead = '/notifications/read-all/';

  /// 10.1 — register or clear this device's FCM token.
  static const String notificationDevice = '/notifications/device/';

  /// 10.4 — per-category mute settings.
  static const String notificationPreferences = '/notifications/preferences/';

  /// Drains Module 6.4's reminder queue. Administrators only.
  static const String deliverDueNotifications = '/notifications/deliver-due/';

  static String markNotificationRead(int notificationId) =>
      '/notifications/$notificationId/read/';

  // --- Module 11: Admin, Reporting & Complaints ------------------------------
  /// 11.1 — the society directory. Administrators only.
  ///
  /// Prefixed `admin` to keep them apart from Module 2's [residentDirectory],
  /// which is the resident-facing list and a different endpoint entirely.
  static const String adminWorkerDirectory = '/admin-tools/directory/workers/';
  static const String adminResidentDirectory =
      '/admin-tools/directory/residents/';

  /// 11.3 — the queue for an administrator, the caller's own for everyone else.
  static const String complaints = '/admin-tools/complaints/';

  /// 11.3 — the overdue sweep. The free tier has no scheduler, so this runs on
  /// demand as well as whenever an administrator loads the queue.
  static const String escalateComplaints = '/admin-tools/complaints/escalate/';

  /// 11.4 — every analytics panel in one response.
  static const String adminDashboard = '/admin-tools/dashboard/';
  static const String unmetDemand = '/admin-tools/unmet-demand/';

  static String complaint(int complaintId) =>
      '/admin-tools/complaints/$complaintId/';

  static String complaintUpdates(int complaintId) =>
      '/admin-tools/complaints/$complaintId/updates/';

  static String startComplaint(int complaintId) =>
      '/admin-tools/complaints/$complaintId/start/';

  static String closeComplaint(int complaintId) =>
      '/admin-tools/complaints/$complaintId/close/';

  static String withdrawComplaint(int complaintId) =>
      '/admin-tools/complaints/$complaintId/withdraw/';

  /// 11.2 — `kind` is one of `attendance`, `payments`, `complaints`.
  ///
  /// The JSON form is what the app renders. `/csv/` and `/pdf/` return files
  /// and are not called from here — see the note in `reports_screen.dart`.
  static String adminReport(String kind) => '/admin-tools/reports/$kind/';

  // --- Module 12: AI ---------------------------------------------------------
  /// What this deployment can actually do. Reports capability, never keys, so
  /// the app can hide a feature rather than offer one that always fails.
  static const String aiStatus = '/ai/status/';

  /// 12.2 — one question. Answers only about the caller's own records.
  static const String aiChat = '/ai/chat/';

  /// 12.5 — a suggested category for free text. A suggestion, not a verdict.
  static const String classifyComplaint = '/ai/complaints/classify/';

  /// 12.5 — a worker's reviews, condensed.
  static String reviewSummary(int workerId) => '/ai/reviews/$workerId/summary/';

  /// Liveness probe. Sits outside `/api/v1`, hence the leading `../`-style path
  /// handled by the client's absolute-URL branch.
  static const String health = '/health/';
}
