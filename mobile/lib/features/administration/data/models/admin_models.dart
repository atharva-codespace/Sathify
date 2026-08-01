/// Data models for Module 11 — Admin, Reporting & Complaints.
///
/// -----------------------------------------------------------------------
/// THE SLA IS SHOWN TO BOTH SIDES
/// -----------------------------------------------------------------------
/// [Complaint] carries its deadline and how long is left on it, and the server
/// sends those fields to everybody rather than only to administrators. A
/// response time nobody outside the committee can see is an internal metric;
/// one the person who raised the complaint can read is a commitment.
///
/// [Complaint.hoursRemaining] goes negative once the deadline has passed. That
/// is deliberate on the server side too — "12 hours over" is what somebody
/// triaging a queue needs to sort by.
library;

double _toDouble(dynamic value) {
  if (value is num) return value.toDouble();
  if (value is String) return double.tryParse(value) ?? 0;
  return 0;
}

/// What a complaint is about.
///
/// The wire values mirror the server's `ComplaintCategory`, which Module 12.5
/// will classify free text into. Getting one wrong here would silently file
/// every complaint of that kind under [other].
enum ComplaintCategory {
  lateArrival('late_arrival', 'Late or missed visit'),
  behaviour('behaviour', 'Behaviour or conduct'),
  payment('payment', 'Payment'),
  quality('quality', 'Quality of work'),
  safety('safety', 'Safety or security'),
  other('other', 'Something else');

  const ComplaintCategory(this.wireValue, this.label);

  final String wireValue;
  final String label;

  static ComplaintCategory fromWire(String? value) =>
      ComplaintCategory.values.firstWhere(
        (c) => c.wireValue == value,
        orElse: () => ComplaintCategory.other,
      );
}

enum ComplaintStatus {
  open('open', 'Open'),
  inProgress('in_progress', 'Being looked into'),
  resolved('resolved', 'Resolved'),
  rejected('rejected', 'Rejected'),
  withdrawn('withdrawn', 'Withdrawn');

  const ComplaintStatus(this.wireValue, this.label);

  final String wireValue;
  final String label;

  /// Matches the server's `CLOSED_STATUSES`. Rejection and withdrawal count as
  /// closed: the administrator answered, even though the answer was no.
  bool get isClosed =>
      this == resolved || this == rejected || this == withdrawn;

  static ComplaintStatus fromWire(String? value) =>
      ComplaintStatus.values.firstWhere(
        (s) => s.wireValue == value,
        orElse: () => ComplaintStatus.open,
      );
}

enum ComplaintPriority {
  urgent('urgent', 'Urgent'),
  high('high', 'High'),
  normal('normal', 'Normal');

  const ComplaintPriority(this.wireValue, this.label);

  final String wireValue;
  final String label;

  static ComplaintPriority fromWire(String? value) =>
      ComplaintPriority.values.firstWhere(
        (p) => p.wireValue == value,
        orElse: () => ComplaintPriority.normal,
      );
}

/// Module 11.3 — one complaint.
class Complaint {
  const Complaint({
    required this.id,
    required this.reference,
    required this.category,
    required this.subject,
    required this.description,
    required this.status,
    required this.priority,
    this.photoUrl,
    this.raisedById,
    this.raisedByName = '',
    this.about = '',
    this.againstWorker,
    this.againstResident,
    this.slaDueAt,
    this.escalatedAt,
    this.firstResponseAt,
    this.resolution = '',
    this.resolvedAt,
    this.isOpen = true,
    this.isOverdue = false,
    this.hoursRemaining = 0,
    this.ageActiveHours = 0,
    this.paymentDisputeId,
    this.createdAt,
    this.updates = const [],
  });

  final int id;

  /// The handle a corridor conversation uses. `CMP-202603-A1B2C3`.
  final String reference;

  final ComplaintCategory category;
  final String subject;
  final String description;
  final String? photoUrl;

  final ComplaintStatus status;
  final ComplaintPriority priority;

  /// The raiser's user id. Compared against the signed-in user to decide
  /// whether to offer "withdraw" — matching on display name would offer it to
  /// the wrong person the first time two residents share a name.
  final int? raisedById;

  final String raisedByName;

  /// Who it is about, already resolved to a readable phrase by the server —
  /// "The society" when a complaint names no individual.
  final String about;

  final int? againstWorker;
  final int? againstResident;

  final DateTime? slaDueAt;
  final DateTime? escalatedAt;
  final DateTime? firstResponseAt;

  final String resolution;
  final DateTime? resolvedAt;

  final bool isOpen;
  final bool isOverdue;

  /// Active hours to the deadline. Negative once past it.
  final double hoursRemaining;

  /// How long this has been open, counted in SLA hours rather than wall clock.
  final double ageActiveHours;

  /// Set when the complaint was opened from a Module 8.6 payment dispute.
  final int? paymentDisputeId;

  final DateTime? createdAt;

  /// Only populated by the detail endpoint. Internal notes are already filtered
  /// out server-side for anyone who is not an administrator.
  final List<ComplaintUpdate> updates;

  bool get wasEscalated => escalatedAt != null;

  bool get cameFromPaymentDispute => paymentDisputeId != null;

  /// Nobody has looked at it yet. The distinction an administrator's queue
  /// needs most: unanswered is worse than unresolved.
  bool get awaitingFirstResponse => isOpen && firstResponseAt == null;

  factory Complaint.fromJson(Map<String, dynamic> json) => Complaint(
        id: json['id'] as int,
        reference: json['reference'] as String? ?? '',
        category: ComplaintCategory.fromWire(json['category'] as String?),
        subject: json['subject'] as String? ?? '',
        description: json['description'] as String? ?? '',
        photoUrl: json['photo_url'] as String?,
        status: ComplaintStatus.fromWire(json['status'] as String?),
        priority: ComplaintPriority.fromWire(json['priority'] as String?),
        raisedById: json['raised_by'] as int?,
        raisedByName: json['raised_by_name'] as String? ?? '',
        about: json['about'] as String? ?? '',
        againstWorker: json['against_worker'] as int?,
        againstResident: json['against_resident'] as int?,
        slaDueAt: DateTime.tryParse(json['sla_due_at'] as String? ?? ''),
        escalatedAt: DateTime.tryParse(json['escalated_at'] as String? ?? ''),
        firstResponseAt:
            DateTime.tryParse(json['first_response_at'] as String? ?? ''),
        resolution: json['resolution'] as String? ?? '',
        resolvedAt: DateTime.tryParse(json['resolved_at'] as String? ?? ''),
        isOpen: json['is_open'] as bool? ?? true,
        isOverdue: json['is_overdue'] as bool? ?? false,
        hoursRemaining: _toDouble(json['hours_remaining']),
        ageActiveHours: _toDouble(json['age_active_hours']),
        paymentDisputeId: json['payment_dispute'] as int?,
        createdAt: DateTime.tryParse(json['created_at'] as String? ?? ''),
        updates: ((json['updates'] as List?) ?? const [])
            .map((row) => ComplaintUpdate.fromJson(row as Map<String, dynamic>))
            .toList(),
      );
}

/// One entry in a complaint's history (SRS 5.5).
class ComplaintUpdate {
  const ComplaintUpdate({
    required this.id,
    required this.note,
    this.authorName = '',
    this.oldStatus = '',
    this.newStatus = '',
    this.isSystem = false,
    this.isInternal = false,
    this.createdAt,
  });

  final int id;
  final String note;
  final String authorName;
  final String oldStatus;
  final String newStatus;

  /// Written by the system — an escalation firing, not a person's judgement.
  final bool isSystem;

  /// An administrator's note to themselves. Never reaches the other party;
  /// the server strips these before they are serialised.
  final bool isInternal;

  final DateTime? createdAt;

  bool get isTransition => newStatus.isNotEmpty;

  factory ComplaintUpdate.fromJson(Map<String, dynamic> json) =>
      ComplaintUpdate(
        id: json['id'] as int,
        note: json['note'] as String? ?? '',
        authorName: json['author_name'] as String? ?? '',
        oldStatus: json['old_status'] as String? ?? '',
        newStatus: json['new_status'] as String? ?? '',
        isSystem: json['is_system'] as bool? ?? false,
        isInternal: json['is_internal'] as bool? ?? false,
        createdAt: DateTime.tryParse(json['created_at'] as String? ?? ''),
      );
}

/// Module 11.1 — one row of the worker directory.
class DirectoryWorker {
  const DirectoryWorker({
    required this.id,
    required this.fullName,
    this.phoneNumber = '',
    this.isApproved = false,
    this.isAvailable = false,
    this.services = const [],
    this.yearsOfExperience = 0,
    this.trustScore = 0,
    this.averageRating = 0,
    this.ratingCount = 0,
    this.completedEngagements = 0,
    this.openComplaints = 0,
    this.joinedAt,
  });

  final int id;
  final String fullName;
  final String phoneNumber;
  final bool isApproved;
  final bool isAvailable;
  final List<String> services;
  final int yearsOfExperience;
  final double trustScore;
  final double averageRating;
  final int ratingCount;
  final int completedEngagements;
  final int openComplaints;
  final DateTime? joinedAt;

  /// A worker with no ratings yet scores zero because nothing has happened, not
  /// because they did badly. Shown as "not rated yet" rather than as 0/100 —
  /// the same distinction the server's analytics panel makes.
  bool get isRated => ratingCount > 0;

  factory DirectoryWorker.fromJson(Map<String, dynamic> json) =>
      DirectoryWorker(
        id: json['id'] as int,
        fullName: json['full_name'] as String? ?? '',
        phoneNumber: json['phone_number'] as String? ?? '',
        isApproved: json['is_approved'] as bool? ?? false,
        isAvailable: json['is_available'] as bool? ?? false,
        services: ((json['services'] as List?) ?? const [])
            .map((value) => value.toString())
            .toList(),
        yearsOfExperience: json['years_of_experience'] as int? ?? 0,
        trustScore: _toDouble(json['trust_score']),
        averageRating: _toDouble(json['average_rating']),
        ratingCount: json['rating_count'] as int? ?? 0,
        completedEngagements: json['completed_engagements'] as int? ?? 0,
        openComplaints: json['open_complaints'] as int? ?? 0,
        joinedAt: DateTime.tryParse(json['joined_at'] as String? ?? ''),
      );
}

/// Module 11.1 — one row of the resident directory.
class DirectoryResident {
  const DirectoryResident({
    required this.id,
    required this.fullName,
    this.phoneNumber = '',
    this.isApproved = false,
    this.flat = '',
    this.relationship = '',
    this.isPrimary = false,
    this.trustScore = 0,
    this.averageRating = 0,
    this.ratingCount = 0,
    this.openComplaints = 0,
    this.joinedAt,
  });

  final int id;
  final String fullName;
  final String phoneNumber;
  final bool isApproved;
  final String flat;
  final String relationship;
  final bool isPrimary;
  final double trustScore;
  final double averageRating;
  final int ratingCount;
  final int openComplaints;
  final DateTime? joinedAt;

  bool get isRated => ratingCount > 0;

  factory DirectoryResident.fromJson(Map<String, dynamic> json) =>
      DirectoryResident(
        id: json['id'] as int,
        fullName: json['full_name'] as String? ?? '',
        phoneNumber: json['phone_number'] as String? ?? '',
        isApproved: json['is_approved'] as bool? ?? false,
        flat: json['flat'] as String? ?? '',
        relationship: json['relationship'] as String? ?? '',
        isPrimary: json['is_primary'] as bool? ?? false,
        trustScore: _toDouble(json['trust_score']),
        averageRating: _toDouble(json['average_rating']),
        ratingCount: json['rating_count'] as int? ?? 0,
        openComplaints: json['open_complaints'] as int? ?? 0,
        joinedAt: DateTime.tryParse(json['joined_at'] as String? ?? ''),
      );
}

/// Module 11.2 — a report, in the same shape the CSV and PDF render from.
///
/// Kept generic on purpose: the server assembles arbitrary columns and rows so
/// that one screen renders all three reports, and a figure on screen cannot
/// disagree with a figure in an export.
class AdminReport {
  const AdminReport({
    required this.title,
    this.societyName = '',
    this.periodLabel = '',
    this.columns = const [],
    this.rows = const [],
    this.summary = const [],
    this.rowCount = 0,
  });

  final String title;
  final String societyName;
  final String periodLabel;
  final List<String> columns;
  final List<List<String>> rows;

  /// The headline figures, printed above the table. Most readers look only at
  /// these, which is why the server puts them first.
  final List<ReportSummaryLine> summary;

  final int rowCount;

  bool get isEmpty => rows.isEmpty;

  factory AdminReport.fromJson(Map<String, dynamic> json) => AdminReport(
        title: json['title'] as String? ?? 'Report',
        societyName: json['society_name'] as String? ?? '',
        periodLabel: json['period_label'] as String? ?? '',
        columns: ((json['columns'] as List?) ?? const [])
            .map((value) => value.toString())
            .toList(),
        rows: ((json['rows'] as List?) ?? const [])
            .map(
              (row) => ((row as List?) ?? const [])
                  .map((cell) => cell?.toString() ?? '')
                  .toList(),
            )
            .toList(),
        summary: ((json['summary'] as List?) ?? const [])
            .map(
              (row) => ReportSummaryLine.fromJson(row as Map<String, dynamic>),
            )
            .toList(),
        rowCount: json['row_count'] as int? ?? 0,
      );
}

class ReportSummaryLine {
  const ReportSummaryLine({required this.label, required this.value});

  final String label;
  final String value;

  factory ReportSummaryLine.fromJson(Map<String, dynamic> json) =>
      ReportSummaryLine(
        label: json['label'] as String? ?? '',
        value: json['value'] as String? ?? '',
      );
}

/// Module 11.4 — every dashboard panel.
///
/// Each panel carries its own `has_data`. A brand-new society genuinely has no
/// sentiment and no trust distribution, and rendering that as a chart of zeros
/// invites people to read a shape into noise.
class AdminDashboard {
  const AdminDashboard({
    required this.sentiment,
    required this.trust,
    required this.complaints,
    required this.unmetDemand,
    required this.availability,
    this.periodStart,
    this.periodEnd,
  });

  final SentimentPanel sentiment;
  final TrustPanel trust;
  final ComplaintPanel complaints;
  final UnmetDemandPanel unmetDemand;
  final AvailabilityPanel availability;
  final DateTime? periodStart;
  final DateTime? periodEnd;

  factory AdminDashboard.fromJson(Map<String, dynamic> json) => AdminDashboard(
        periodStart: DateTime.tryParse(json['period_start'] as String? ?? ''),
        periodEnd: DateTime.tryParse(json['period_end'] as String? ?? ''),
        sentiment: SentimentPanel.fromJson(_panel(json, 'sentiment')),
        trust: TrustPanel.fromJson(_panel(json, 'trust')),
        complaints: ComplaintPanel.fromJson(_panel(json, 'complaints')),
        unmetDemand: UnmetDemandPanel.fromJson(_panel(json, 'unmet_demand')),
        availability: AvailabilityPanel.fromJson(_panel(json, 'availability')),
      );

  static Map<String, dynamic> _panel(Map<String, dynamic> json, String key) =>
      Map<String, dynamic>.from((json[key] as Map?) ?? const {});
}

class SentimentPanel {
  const SentimentPanel({
    this.hasData = false,
    this.analysed = 0,
    this.notConfident = 0,
    this.positive = 0,
    this.neutral = 0,
    this.negative = 0,
    this.averagePolarity = 0,
    this.themes = const [],
    this.note = '',
  });

  final bool hasData;
  final int analysed;

  /// Reviews the analyser could not read confidently. Shown rather than folded
  /// into "neutral": counting an admission of ignorance as a neutral verdict
  /// manufactures a reassuring flat line.
  final int notConfident;

  final int positive;
  final int neutral;
  final int negative;
  final double averagePolarity;
  final List<SentimentTheme> themes;
  final String note;

  factory SentimentPanel.fromJson(Map<String, dynamic> json) => SentimentPanel(
        hasData: json['has_data'] as bool? ?? false,
        analysed: json['analysed'] as int? ?? 0,
        notConfident: json['not_confident'] as int? ?? 0,
        positive: json['positive'] as int? ?? 0,
        neutral: json['neutral'] as int? ?? 0,
        negative: json['negative'] as int? ?? 0,
        averagePolarity: _toDouble(json['average_polarity']),
        themes: ((json['themes'] as List?) ?? const [])
            .map((row) => SentimentTheme.fromJson(row as Map<String, dynamic>))
            .toList(),
        note: json['note'] as String? ?? '',
      );
}

class SentimentTheme {
  const SentimentTheme({
    required this.theme,
    required this.positive,
    required this.negative,
  });

  final String theme;
  final int positive;
  final int negative;

  int get total => positive + negative;

  factory SentimentTheme.fromJson(Map<String, dynamic> json) => SentimentTheme(
        theme: json['theme'] as String? ?? '',
        positive: json['positive'] as int? ?? 0,
        negative: json['negative'] as int? ?? 0,
      );
}

class TrustPanel {
  const TrustPanel({
    required this.workers,
    required this.residents,
    this.hasData = false,
  });

  final bool hasData;
  final TrustGroup workers;
  final TrustGroup residents;

  factory TrustPanel.fromJson(Map<String, dynamic> json) => TrustPanel(
        hasData: json['has_data'] as bool? ?? false,
        workers: TrustGroup.fromJson(
          Map<String, dynamic>.from((json['workers'] as Map?) ?? const {}),
        ),
        residents: TrustGroup.fromJson(
          Map<String, dynamic>.from((json['residents'] as Map?) ?? const {}),
        ),
      );
}

class TrustGroup {
  const TrustGroup({
    this.total = 0,
    this.rated = 0,
    this.unrated = 0,
    this.average = 0,
    this.buckets = const [],
  });

  final int total;
  final int rated;

  /// Held apart from the lowest band on purpose — see [DirectoryWorker.isRated].
  final int unrated;

  final double average;
  final List<TrustBucket> buckets;

  factory TrustGroup.fromJson(Map<String, dynamic> json) => TrustGroup(
        total: json['total'] as int? ?? 0,
        rated: json['rated'] as int? ?? 0,
        unrated: json['unrated'] as int? ?? 0,
        average: _toDouble(json['average']),
        buckets: ((json['buckets'] as List?) ?? const [])
            .map((row) => TrustBucket.fromJson(row as Map<String, dynamic>))
            .toList(),
      );
}

class TrustBucket {
  const TrustBucket({required this.label, required this.count});

  final String label;
  final int count;

  factory TrustBucket.fromJson(Map<String, dynamic> json) => TrustBucket(
        label: json['label'] as String? ?? '',
        count: json['count'] as int? ?? 0,
      );
}

class ComplaintPanel {
  const ComplaintPanel({
    this.hasData = false,
    this.raised = 0,
    this.resolved = 0,
    this.openNow = 0,
    this.overdueNow = 0,
    this.resolvedWithinSla = 0,
    this.resolvedLate = 0,
    this.byCategory = const [],
  });

  final bool hasData;
  final int raised;
  final int resolved;
  final int openNow;
  final int overdueNow;
  final int resolvedWithinSla;
  final int resolvedLate;
  final List<CountedCategory> byCategory;

  /// Null rather than 100% when nothing has been resolved yet — a perfect score
  /// over an empty set is the most misleading figure a dashboard can show.
  double? get slaComplianceRate {
    final total = resolvedWithinSla + resolvedLate;
    return total == 0 ? null : resolvedWithinSla / total;
  }

  factory ComplaintPanel.fromJson(Map<String, dynamic> json) => ComplaintPanel(
        hasData: json['has_data'] as bool? ?? false,
        raised: json['raised'] as int? ?? 0,
        resolved: json['resolved'] as int? ?? 0,
        openNow: json['open_now'] as int? ?? 0,
        overdueNow: json['overdue_now'] as int? ?? 0,
        resolvedWithinSla: json['resolved_within_sla'] as int? ?? 0,
        resolvedLate: json['resolved_late'] as int? ?? 0,
        byCategory: ((json['by_category'] as List?) ?? const [])
            .map((row) => CountedCategory.fromJson(row as Map<String, dynamic>))
            .toList(),
      );
}

class CountedCategory {
  const CountedCategory({
    required this.key,
    required this.label,
    required this.count,
  });

  final String key;
  final String label;
  final int count;

  factory CountedCategory.fromJson(Map<String, dynamic> json) =>
      CountedCategory(
        key: (json['category'] ?? json['kind'] ?? '').toString(),
        label: json['label'] as String? ?? '',
        count: json['count'] as int? ?? 0,
      );
}

class UnmetDemandPanel {
  const UnmetDemandPanel({
    this.hasData = false,
    this.total = 0,
    this.byKind = const [],
    this.byService = const [],
  });

  final bool hasData;
  final int total;
  final List<CountedCategory> byKind;

  /// The recruiting brief: what people asked for and how often nobody could
  /// supply it. The one panel a society committee can act on directly.
  final List<CountedService> byService;

  factory UnmetDemandPanel.fromJson(Map<String, dynamic> json) =>
      UnmetDemandPanel(
        hasData: json['has_data'] as bool? ?? false,
        total: json['total'] as int? ?? 0,
        byKind: ((json['by_kind'] as List?) ?? const [])
            .map((row) => CountedCategory.fromJson(row as Map<String, dynamic>))
            .toList(),
        byService: ((json['by_service'] as List?) ?? const [])
            .map((row) => CountedService.fromJson(row as Map<String, dynamic>))
            .toList(),
      );
}

class CountedService {
  const CountedService({required this.service, required this.count});

  final String service;
  final int count;

  factory CountedService.fromJson(Map<String, dynamic> json) => CountedService(
        service: json['service'] as String? ?? '',
        count: json['count'] as int? ?? 0,
      );
}

class AvailabilityPanel {
  const AvailabilityPanel({
    this.hasData = false,
    this.workersTotal = 0,
    this.workersAvailableNow = 0,
    this.workersUnavailableNow = 0,
    this.horizonDays = 0,
    this.blockedWorkerDays = 0,
    this.days = const [],
  });

  final bool hasData;
  final int workersTotal;
  final int workersAvailableNow;
  final int workersUnavailableNow;
  final int horizonDays;

  /// The churn signal. A jump here two weeks before a festival is the thing an
  /// administrator needs to see *before* half the workers travel home.
  final int blockedWorkerDays;

  final List<AvailabilityDay> days;

  factory AvailabilityPanel.fromJson(Map<String, dynamic> json) =>
      AvailabilityPanel(
        hasData: json['has_data'] as bool? ?? false,
        workersTotal: json['workers_total'] as int? ?? 0,
        workersAvailableNow: json['workers_available_now'] as int? ?? 0,
        workersUnavailableNow: json['workers_unavailable_now'] as int? ?? 0,
        horizonDays: json['horizon_days'] as int? ?? 0,
        blockedWorkerDays: json['blocked_worker_days'] as int? ?? 0,
        days: ((json['days'] as List?) ?? const [])
            .map((row) => AvailabilityDay.fromJson(row as Map<String, dynamic>))
            .toList(),
      );
}

class AvailabilityDay {
  const AvailabilityDay({
    required this.date,
    required this.workersOpen,
    required this.workersBlocked,
  });

  final DateTime? date;
  final int workersOpen;
  final int workersBlocked;

  factory AvailabilityDay.fromJson(Map<String, dynamic> json) =>
      AvailabilityDay(
        date: DateTime.tryParse(json['date'] as String? ?? ''),
        workersOpen: json['workers_open'] as int? ?? 0,
        workersBlocked: json['workers_blocked'] as int? ?? 0,
      );
}

/// One logged request nobody could fill.
class UnmetDemandEntry {
  const UnmetDemandEntry({
    required this.id,
    required this.kind,
    required this.kindLabel,
    this.serviceLabel = '',
    this.requestedDate,
    this.detail = '',
    this.createdAt,
  });

  final int id;
  final String kind;
  final String kindLabel;
  final String serviceLabel;
  final DateTime? requestedDate;
  final String detail;
  final DateTime? createdAt;

  factory UnmetDemandEntry.fromJson(Map<String, dynamic> json) =>
      UnmetDemandEntry(
        id: json['id'] as int,
        kind: json['kind'] as String? ?? '',
        kindLabel: json['kind_display'] as String? ?? '',
        serviceLabel: json['service_label'] as String? ?? '',
        requestedDate:
            DateTime.tryParse(json['requested_date'] as String? ?? ''),
        detail: json['detail'] as String? ?? '',
        createdAt: DateTime.tryParse(json['created_at'] as String? ?? ''),
      );
}
