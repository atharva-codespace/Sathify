/// Data models for Module 4 — Discovery & Hiring.
///
/// -----------------------------------------------------------------------
/// WEEKDAY CONVENTION — READ BEFORE CHANGING
/// -----------------------------------------------------------------------
/// The server stores `days_of_week` with **Monday = 0 … Sunday = 6** (Python's
/// `date.weekday()`). Dart's `DateTime.weekday` uses **Monday = 1 … Sunday = 7**.
/// The two must never be compared directly. [Weekday] is the only place the
/// conversion happens — go through it rather than adding or subtracting one at
/// the call site.
library;

/// A category of domestic work, as offered by a worker.
class ServiceType {
  const ServiceType({
    required this.id,
    required this.name,
    this.slug = '',
    this.icon = '',
  });

  final int id;
  final String name;
  final String slug;
  final String icon;

  factory ServiceType.fromJson(Map<String, dynamic> json) => ServiceType(
        id: json['id'] as int,
        name: json['name'] as String? ?? '',
        slug: json['slug'] as String? ?? '',
        icon: json['icon'] as String? ?? '',
      );
}

/// Server-convention weekdays (Monday = 0), with the Dart bridge in one place.
enum Weekday {
  monday(0, 'Mon', 'Monday'),
  tuesday(1, 'Tue', 'Tuesday'),
  wednesday(2, 'Wed', 'Wednesday'),
  thursday(3, 'Thu', 'Thursday'),
  friday(4, 'Fri', 'Friday'),
  saturday(5, 'Sat', 'Saturday'),
  sunday(6, 'Sun', 'Sunday');

  const Weekday(this.wireValue, this.shortLabel, this.label);

  /// What the server stores. Monday is 0.
  final int wireValue;
  final String shortLabel;
  final String label;

  /// Converts a Dart [DateTime] (Monday = 1) to the server's convention.
  static Weekday fromDateTime(DateTime date) =>
      Weekday.values[date.weekday - 1];

  static Weekday fromWire(int value) => Weekday.values.firstWhere(
        (d) => d.wireValue == value,
        orElse: () => Weekday.monday,
      );

  /// Renders a stored day list as "Mon, Wed, Fri".
  static String labelFor(List<int> days) {
    final sorted = [...days]..sort();
    return sorted.map((d) => Weekday.fromWire(d).shortLabel).join(', ');
  }
}

/// Reads a numeric field that may arrive as a number or a numeric string.
///
/// Django REST Framework renders `DecimalField` as a **string** by default.
/// Module 4's serializers override that for `trust_score` and `average_rating`,
/// but parsing tolerantly costs nothing here and means a server-side regression
/// shows up as a wrong-looking value rather than silently collapsing every
/// worker to "unrated" with no error anywhere.
double toDoubleOrZero(Object? value) {
  if (value is num) return value.toDouble();
  if (value is String) return double.tryParse(value) ?? 0;
  return 0;
}

/// As [toDoubleOrZero], but preserves the difference between "absent" and
/// "zero" — which matters for response rate, where null means no history.
double? toDoubleOrNull(Object? value) {
  if (value is num) return value.toDouble();
  if (value is String) return double.tryParse(value);
  return null;
}

/// Formats an `HH:MM:SS` wire time as `HH:MM`, leaving anything unexpected be.
String formatWireTime(String? value) {
  if (value == null || value.isEmpty) return '';
  final parts = value.split(':');
  return parts.length >= 2 ? '${parts[0]}:${parts[1]}' : value;
}

/// One weighted signal behind a worker's match percentage (Module 4.3).
///
/// Shown to the resident so the headline number is accountable rather than a
/// black box — the same standard Module 9's trust score is held to.
class MatchComponent {
  const MatchComponent({
    required this.key,
    required this.label,
    required this.weight,
    required this.score,
    required this.contribution,
    this.raw,
  });

  final String key;
  final String label;
  final double weight;

  /// This component on 0–1, after normalisation and any smoothing.
  final double score;

  /// `weight * score` — how much of the final percentage this actually moved.
  final double contribution;

  /// The underlying observation, where one exists. Null means "no history yet",
  /// and must be rendered as such rather than as a zero.
  final double? raw;

  int get scorePercentage => (score * 100).round();

  factory MatchComponent.fromJson(Map<String, dynamic> json) => MatchComponent(
        key: json['key'] as String? ?? '',
        label: json['label'] as String? ?? '',
        weight: toDoubleOrZero(json['weight']),
        score: toDoubleOrZero(json['score']),
        contribution: toDoubleOrZero(json['contribution']),
        raw: toDoubleOrNull(json['raw']),
      );
}

/// One row of the Module 4.1 search results.
class WorkerSearchResult {
  const WorkerSearchResult({
    required this.id,
    required this.fullName,
    this.photoUrl,
    this.serviceTypes = const [],
    this.yearsOfExperience = 0,
    this.languagesSpoken = '',
    this.expectedMonthlyRate,
    this.availableFrom = '',
    this.availableUntil = '',
    this.averageRating = 0,
    this.trustScore = 0,
    this.completedEngagements = 0,
    this.engagementCount = 0,
    this.matchPercentage,
  });

  final int id;
  final String fullName;
  final String? photoUrl;
  final List<ServiceType> serviceTypes;
  final int yearsOfExperience;
  final String languagesSpoken;

  /// Null means the worker has not stated a rate — negotiable, not zero.
  final int? expectedMonthlyRate;
  final String availableFrom;
  final String availableUntil;
  final double averageRating;
  final double trustScore;
  final int completedEngagements;
  final int engagementCount;

  /// The Module 4.3 score, 0–100. Null if the server did not rank this list.
  final int? matchPercentage;

  bool get hasRating => averageRating > 0;

  /// "08:00 – 18:00", or empty when the worker declared no hours.
  String get availabilityLabel {
    final from = formatWireTime(availableFrom);
    final until = formatWireTime(availableUntil);
    return (from.isEmpty || until.isEmpty) ? '' : '$from – $until';
  }

  factory WorkerSearchResult.fromJson(Map<String, dynamic> json) =>
      WorkerSearchResult(
        id: json['id'] as int,
        fullName: json['full_name'] as String? ?? '',
        photoUrl: json['photo'] as String?,
        serviceTypes: ((json['service_types'] as List?) ?? const [])
            .map((row) => ServiceType.fromJson(row as Map<String, dynamic>))
            .toList(),
        yearsOfExperience: json['years_of_experience'] as int? ?? 0,
        languagesSpoken: json['languages_spoken'] as String? ?? '',
        expectedMonthlyRate: json['expected_monthly_rate'] as int?,
        availableFrom: json['available_from'] as String? ?? '',
        availableUntil: json['available_until'] as String? ?? '',
        averageRating: toDoubleOrZero(json['average_rating']),
        trustScore: toDoubleOrZero(json['trust_score']),
        completedEngagements: json['completed_engagements'] as int? ?? 0,
        engagementCount: json['engagement_count'] as int? ?? 0,
        matchPercentage: json['match_percentage'] as int?,
      );
}

/// The verification badge on a worker's profile (Module 4.2).
class WorkerVerification {
  const WorkerVerification({
    this.isApproved = false,
    this.idVerified = false,
    this.idMasked,
  });

  /// An administrator reviewed the KYC evidence and admitted this worker.
  final bool isApproved;

  /// The Aadhaar checksum passed — a narrower claim than [isApproved].
  final bool idVerified;
  final String? idMasked;

  factory WorkerVerification.fromJson(Map<String, dynamic> json) =>
      WorkerVerification(
        isApproved: json['is_approved'] as bool? ?? false,
        idVerified: json['id_verified'] as bool? ?? false,
        idMasked: json['id_masked'] as String?,
      );
}

/// The full worker profile behind a search result (Module 4.2).
class WorkerDetail {
  const WorkerDetail({
    required this.summary,
    required this.verification,
    this.bio = '',
    this.responseRate,
    this.matchBreakdown = const [],
  });

  final WorkerSearchResult summary;
  final WorkerVerification verification;
  final String bio;

  /// Observed share of past requests answered, 0–1. Null means no history —
  /// render that as "No requests yet", never as 0%.
  final double? responseRate;
  final List<MatchComponent> matchBreakdown;

  int get id => summary.id;
  String get fullName => summary.fullName;

  factory WorkerDetail.fromJson(Map<String, dynamic> json) => WorkerDetail(
        summary: WorkerSearchResult.fromJson(json),
        // Copied rather than cast: a nested map does not reliably arrive as
        // Map<String, dynamic>, and a hard cast throws on the whole profile
        // because one sub-object had a looser generic type.
        verification: WorkerVerification.fromJson(
          json['verification'] is Map
              ? Map<String, dynamic>.from(json['verification'] as Map)
              : const {},
        ),
        bio: json['bio'] as String? ?? '',
        responseRate: toDoubleOrNull(json['response_rate']),
        matchBreakdown: ((json['match_breakdown'] as List?) ?? const [])
            .map((row) => MatchComponent.fromJson(row as Map<String, dynamic>))
            .toList(),
      );
}

enum HireRequestStatus {
  pending('pending', 'Awaiting response'),
  accepted('accepted', 'Accepted'),
  declined('declined', 'Declined'),
  withdrawn('withdrawn', 'Withdrawn'),
  expired('expired', 'No response');

  const HireRequestStatus(this.wireValue, this.label);

  final String wireValue;
  final String label;

  static HireRequestStatus fromWire(String? value) =>
      HireRequestStatus.values.firstWhere(
        (s) => s.wireValue == value,
        orElse: () => HireRequestStatus.pending,
      );
}

/// The terms of a recurring arrangement — proposed on a request, agreed on an
/// engagement. Mirrors the server's shared `RecurringTerms` base.
class RecurringTerms {
  const RecurringTerms({
    required this.daysOfWeek,
    required this.startTime,
    required this.monthlyRate,
    this.expectedDurationMinutes = 60,
  });

  /// Server convention: Monday = 0. See [Weekday].
  final List<int> daysOfWeek;
  final String startTime;
  final int expectedDurationMinutes;
  final int monthlyRate;

  String get daysLabel => Weekday.labelFor(daysOfWeek);
  String get startTimeLabel => formatWireTime(startTime);

  /// "Mon, Wed, Fri at 09:00".
  String get scheduleLabel => '$daysLabel at $startTimeLabel';

  Map<String, dynamic> toJson() => {
        'days_of_week': daysOfWeek,
        'start_time': startTime,
        'expected_duration_minutes': expectedDurationMinutes,
        'monthly_rate': monthlyRate,
      };

  factory RecurringTerms.fromJson(Map<String, dynamic> json) => RecurringTerms(
        daysOfWeek: ((json['days_of_week'] as List?) ?? const [])
            .map((d) => d as int)
            .toList(),
        startTime: json['start_time'] as String? ?? '',
        expectedDurationMinutes:
            json['expected_duration_minutes'] as int? ?? 60,
        monthlyRate: json['monthly_rate'] as int? ?? 0,
      );
}

/// A resident's proposal to a worker (Module 4.4).
class HireRequest {
  const HireRequest({
    required this.id,
    required this.terms,
    required this.status,
    this.workerId = 0,
    this.workerName = '',
    this.workerPhotoUrl,
    this.residentName = '',
    this.residentFlat = '',
    this.serviceType,
    this.message = '',
    this.responseNote = '',
    this.isActionable = false,
    this.expiresAt,
    this.respondedAt,
    this.engagementId,
    this.createdAt,
  });

  final int id;
  final RecurringTerms terms;
  final HireRequestStatus status;
  final int workerId;
  final String workerName;
  final String? workerPhotoUrl;
  final String residentName;
  final String residentFlat;
  final ServiceType? serviceType;
  final String message;
  final String responseNote;

  /// Whether the worker may still answer — false once answered or lapsed.
  final bool isActionable;
  final DateTime? expiresAt;
  final DateTime? respondedAt;

  /// Set once accepted: the engagement this request became.
  final int? engagementId;
  final DateTime? createdAt;

  bool get isPending => status == HireRequestStatus.pending;

  /// Whole hours left to answer, floored at zero. Null when not applicable.
  int? get hoursRemaining {
    if (!isActionable || expiresAt == null) return null;
    final remaining = expiresAt!.difference(DateTime.now()).inHours;
    return remaining < 0 ? 0 : remaining;
  }

  factory HireRequest.fromJson(Map<String, dynamic> json) => HireRequest(
        id: json['id'] as int,
        terms: RecurringTerms.fromJson(json),
        status: HireRequestStatus.fromWire(json['status'] as String?),
        workerId: json['worker'] as int? ?? 0,
        workerName: json['worker_name'] as String? ?? '',
        workerPhotoUrl: json['worker_photo'] as String?,
        residentName: json['resident_name'] as String? ?? '',
        residentFlat: json['resident_flat'] as String? ?? '',
        serviceType: json['service_type'] is Map<String, dynamic>
            ? ServiceType.fromJson(json['service_type'] as Map<String, dynamic>)
            : null,
        message: json['message'] as String? ?? '',
        responseNote: json['response_note'] as String? ?? '',
        isActionable: json['is_actionable'] as bool? ?? false,
        expiresAt: DateTime.tryParse(json['expires_at'] as String? ?? ''),
        respondedAt: DateTime.tryParse(json['responded_at'] as String? ?? ''),
        engagementId: json['engagement_id'] as int?,
        createdAt: DateTime.tryParse(json['created_at'] as String? ?? ''),
      );
}

enum EngagementStatus {
  active('active', 'Active'),
  paused('paused', 'Paused'),
  terminated('terminated', 'Ended');

  const EngagementStatus(this.wireValue, this.label);

  final String wireValue;
  final String label;

  static EngagementStatus fromWire(String? value) =>
      EngagementStatus.values.firstWhere(
        (s) => s.wireValue == value,
        orElse: () => EngagementStatus.active,
      );
}

/// Why a recurring engagement ended (Module 4.5).
enum EngagementEndReason {
  residentEnded('resident_ended', 'We no longer need this help'),
  workerEnded('worker_ended', 'The worker stopped'),
  residentMovedOut('resident_moved_out', 'Resident moved out'),
  workerLeftSociety('worker_left_society', 'Worker left this society'),
  adminEnded('admin_ended', 'Ended by an administrator');

  const EngagementEndReason(this.wireValue, this.label);

  final String wireValue;
  final String label;
}

/// A standing resident–worker relationship (Module 4.4 / 4.5).
class Engagement {
  const Engagement({
    required this.id,
    required this.terms,
    required this.status,
    this.workerId = 0,
    this.workerName = '',
    this.workerPhotoUrl,
    this.workerPhone = '',
    this.residentName = '',
    this.residentFlat = '',
    this.serviceType,
    this.startedOn,
    this.pauseReason = '',
    this.endReason = '',
    this.endNote = '',
    this.endedAt,
    this.lastWorkingDay,
    this.isServingNotice = false,
    this.noticeDaysRemaining = 0,
    this.visitsRemaining = 0,
  });

  final int id;
  final RecurringTerms terms;
  final EngagementStatus status;
  final int workerId;
  final String workerName;
  final String? workerPhotoUrl;
  final String workerPhone;
  final String residentName;
  final String residentFlat;
  final ServiceType? serviceType;
  final DateTime? startedOn;
  final String pauseReason;
  final String endReason;
  final String endNote;
  final DateTime? endedAt;

  // --- 4.6 notice period ---------------------------------------------------
  //
  // An engagement serving notice is still ACTIVE and still produces visits.
  // Anything that reads [isActive] to decide whether work is happening stays
  // correct without knowing about notice at all — which is the point.

  /// The last day this arrangement calls for a visit, once notice is given.
  final DateTime? lastWorkingDay;
  final bool isServingNotice;

  /// Calendar days until [lastWorkingDay].
  final int noticeDaysRemaining;

  /// Visits left before then — a different and more useful number. Ten days'
  /// notice on a Tuesday-only engagement is one more visit, not ten.
  final int visitsRemaining;

  bool get isActive => status == EngagementStatus.active;
  bool get isPaused => status == EngagementStatus.paused;

  /// Active or paused — the worker is still expected back.
  bool get isLive => isActive || isPaused;

  /// Whether a visit is due on [day]. A paused engagement expects none,
  /// matching the server's `Engagement.occurs_on`.
  bool occursOn(DateTime day) =>
      isActive &&
      terms.daysOfWeek.contains(Weekday.fromDateTime(day).wireValue);

  factory Engagement.fromJson(Map<String, dynamic> json) => Engagement(
        id: json['id'] as int,
        terms: RecurringTerms.fromJson(json),
        status: EngagementStatus.fromWire(json['status'] as String?),
        workerId: json['worker'] as int? ?? 0,
        workerName: json['worker_name'] as String? ?? '',
        workerPhotoUrl: json['worker_photo'] as String?,
        workerPhone: json['worker_phone'] as String? ?? '',
        residentName: json['resident_name'] as String? ?? '',
        residentFlat: json['resident_flat'] as String? ?? '',
        serviceType: json['service_type'] is Map<String, dynamic>
            ? ServiceType.fromJson(json['service_type'] as Map<String, dynamic>)
            : null,
        startedOn: DateTime.tryParse(json['started_on'] as String? ?? ''),
        pauseReason: json['pause_reason'] as String? ?? '',
        endReason: json['end_reason'] as String? ?? '',
        endNote: json['end_note'] as String? ?? '',
        endedAt: DateTime.tryParse(json['ended_at'] as String? ?? ''),
        lastWorkingDay:
            DateTime.tryParse(json['last_working_day'] as String? ?? ''),
        isServingNotice: json['is_serving_notice'] as bool? ?? false,
        noticeDaysRemaining: json['notice_days_remaining'] as int? ?? 0,
        visitsRemaining: json['visits_remaining'] as int? ?? 0,
      );
}

/// Module 4.6 — the pro-rata a household owes before it can give notice.
///
/// -----------------------------------------------------------------------
/// EVERY TERM IS SHOWN, ON PURPOSE
/// -----------------------------------------------------------------------
/// This is the last money to change hands in a relationship that is ending, and
/// a figure nobody can account for at that moment is a figure that becomes a
/// complaint. So the screen shows the division rather than just the answer:
/// days worked, days scheduled, the monthly rate, and what falls out of it.
///
/// [daysWorked] counts days the helper *actually* worked — a gate entry or a
/// completed-visit mark, whichever exists. It is not the number of calendar
/// days that have passed.
class NoticeSettlement {
  const NoticeSettlement({
    required this.daysWorked,
    required this.scheduledDays,
    required this.monthlyRatePaise,
    required this.amountPaise,
    this.attendedDays = 0,
    this.completedDays = 0,
    this.presumedDays = 0,
    this.daysInMonth = 0,
    this.amountDisplay = '',
    this.monthlyRateDisplay = '',
    this.explanation = '',
    this.isOutstanding = false,
    this.blocksNotice = false,
  });

  /// Distinct days worked so far this calendar month.
  final int daysWorked;

  /// Visits this month's terms called for. **Not** the denominator — shown
  /// alongside it so the resident can see what a full month would have been.
  final int scheduledDays;

  /// Where the worked days came from. Not shown directly — [explanation]
  /// already says it in the server's own words — but carried for disputes.
  final int attendedDays;
  final int completedDays;

  /// Days counted from the roster alone: her terms called for a visit, the day
  /// has passed, and no leave was recorded against it. A gate log is only
  /// attached to an engagement when the scan lands inside the visit window, so
  /// without this a helper who came every day can settle at zero.
  final int presumedDays;

  /// Calendar days in the month. **The denominator**:
  /// `days_worked / days_in_month * monthly_rate`.
  final int daysInMonth;

  final int monthlyRatePaise;
  final int amountPaise;
  final String amountDisplay;
  final String monthlyRateDisplay;
  final String explanation;

  /// Whether anything is actually still owed. Distinct from a non-zero
  /// [amountPaise]: a salary already paid this month can cover the pro-rata,
  /// and a household must never be asked twice for the same work.
  final bool isOutstanding;

  /// Whether notice is blocked until this is paid.
  final bool blocksNotice;

  factory NoticeSettlement.fromJson(Map<String, dynamic> json) =>
      NoticeSettlement(
        daysWorked: json['days_worked'] as int? ?? 0,
        scheduledDays: json['scheduled_days'] as int? ?? 0,
        attendedDays: json['attended_days'] as int? ?? 0,
        completedDays: json['completed_days'] as int? ?? 0,
        presumedDays: json['presumed_days'] as int? ?? 0,
        daysInMonth: json['days_in_month'] as int? ?? 0,
        monthlyRatePaise: json['monthly_rate_paise'] as int? ?? 0,
        amountPaise: json['amount_paise'] as int? ?? 0,
        amountDisplay: json['amount_display'] as String? ?? '',
        monthlyRateDisplay: json['monthly_rate_display'] as String? ?? '',
        explanation: json['explanation'] as String? ?? '',
        isOutstanding: json['is_outstanding'] as bool? ?? false,
        blocksNotice: json['blocks_notice'] as bool? ?? false,
      );
}

/// Module 4.6 — the ten-day notice rule, client side.
///
/// Mirrors `NOTICE_PERIOD_DAYS` in `apps/hiring/models.py`. The server is the
/// authority and refuses anything shorter with `notice_too_short`; this exists
/// so a date picker cannot offer a day the server is going to reject, which is
/// a worse experience than one that was never selectable.
class NoticePeriod {
  const NoticePeriod._();

  static const int days = 10;

  /// The earliest permitted last working day, counted from [today].
  ///
  /// Built with `DateTime(y, m, d + n)` rather than `add(Duration(days: n))`:
  /// a Duration is absolute, so a clock or timezone change can land the result
  /// on the wrong calendar day. India has no DST, but the phone's timezone is
  /// the user's to change and this costs nothing to get right.
  static DateTime earliestLastDay(DateTime today) =>
      DateTime(today.year, today.month, today.day + days);

  static bool isPermitted(DateTime candidate, {required DateTime today}) =>
      !candidate.isBefore(earliestLastDay(today));

  /// What somebody is told before they confirm — concrete, not a rule.
  static String summary({required int visitsRemaining}) =>
      visitsRemaining == 1
          ? 'One more visit before then, and it is paid.'
          : '$visitsRemaining more visits before then, and all of them are paid.';
}

/// Filters for the Module 4.1 search, converted to query parameters in one
/// place so the screen never hand-builds the wire format.
class WorkerSearchFilters {
  const WorkerSearchFilters({
    this.query = '',
    this.serviceTypeId,
    this.maxRate,
    this.minRating,
    this.availableFrom,
    this.availableUntil,
    this.strictAvailability = false,
    this.sort = 'recommended',
  });

  final String query;
  final int? serviceTypeId;
  final int? maxRate;
  final double? minRating;
  final String? availableFrom;
  final String? availableUntil;

  /// Drop workers who cannot cover the whole window, rather than merely
  /// ranking them lower.
  final bool strictAvailability;
  final String sort;

  Map<String, dynamic> toQuery() => {
        if (query.isNotEmpty) 'q': query,
        if (serviceTypeId != null) 'service_type': serviceTypeId,
        if (maxRate != null) 'max_rate': maxRate,
        if (minRating != null) 'min_rating': minRating,
        if (availableFrom != null) 'available_from': availableFrom,
        if (availableUntil != null) 'available_until': availableUntil,
        if (strictAvailability) 'strict_availability': 'true',
        if (sort != 'recommended') 'sort': sort,
      };

  WorkerSearchFilters copyWith({
    String? query,
    int? serviceTypeId,
    bool clearServiceType = false,
    int? maxRate,
    double? minRating,
    String? sort,
  }) =>
      WorkerSearchFilters(
        query: query ?? this.query,
        serviceTypeId:
            clearServiceType ? null : (serviceTypeId ?? this.serviceTypeId),
        maxRate: maxRate ?? this.maxRate,
        minRating: minRating ?? this.minRating,
        availableFrom: availableFrom,
        availableUntil: availableUntil,
        strictAvailability: strictAvailability,
        sort: sort ?? this.sort,
      );

  @override
  bool operator ==(Object other) =>
      other is WorkerSearchFilters &&
      other.query == query &&
      other.serviceTypeId == serviceTypeId &&
      other.maxRate == maxRate &&
      other.minRating == minRating &&
      other.availableFrom == availableFrom &&
      other.availableUntil == availableUntil &&
      other.strictAvailability == strictAvailability &&
      other.sort == sort;

  @override
  int get hashCode => Object.hash(
        query,
        serviceTypeId,
        maxRate,
        minRating,
        availableFrom,
        availableUntil,
        strictAvailability,
        sort,
      );
}
