/// Data models for Module 5 — One-Day Service Booking.
///
/// -----------------------------------------------------------------------
/// TWO "SERVICE" VOCABULARIES
/// -----------------------------------------------------------------------
/// [ServiceCategory] is what a *resident books* — deep cleaning, event
/// preparation, emergency assistance — each with a duration and a price band.
/// `ServiceType` (in the hiring models) is what a *worker does* — maid, cook,
/// cleaner. A category names the service type qualified for it, which is how
/// matching narrows the pool. Do not use one where the other is meant.
///
/// -----------------------------------------------------------------------
/// MATCHED WORKERS REUSE MODULE 4's MODEL
/// -----------------------------------------------------------------------
/// The match endpoint returns exactly the Module 4 search row (the server's
/// serializer subclasses Module 4's), so this module parses it with
/// `WorkerSearchResult` rather than defining a near-identical twin that would
/// drift. Booking flows and hiring flows therefore render the same worker card.
library;

import '../../../hiring/data/models/hiring_models.dart'
    show ServiceType, toDoubleOrZero;

/// Formats an `HH:MM:SS` wire time as `HH:MM`.
String formatBookingTime(String? value) {
  if (value == null || value.isEmpty) return '';
  final parts = value.split(':');
  return parts.length >= 2 ? '${parts[0]}:${parts[1]}' : value;
}

/// Formats a `YYYY-MM-DD` wire date for sending back to the server.
String formatWireDate(DateTime date) =>
    '${date.year.toString().padLeft(4, '0')}-'
    '${date.month.toString().padLeft(2, '0')}-'
    '${date.day.toString().padLeft(2, '0')}';

/// Module 5.1 — a bookable one-off job.
class ServiceCategory {
  const ServiceCategory({
    required this.id,
    required this.name,
    required this.expectedDurationMinutes,
    required this.priceMin,
    required this.priceMax,
    this.slug = '',
    this.description = '',
    this.icon = '',
    this.serviceType,
    this.priceGuidance = '',
    this.bypassesNoticePeriod = false,
  });

  final int id;
  final String name;
  final String slug;
  final String description;
  final String icon;

  /// The kind of worker qualified for this job. Null means any approved
  /// worker — the server's default until an administrator links the two.
  final ServiceType? serviceType;

  final int expectedDurationMinutes;
  final int priceMin;
  final int priceMax;

  /// Server-rendered band, e.g. "₹1200–₹3000", so the client never re-derives it.
  final String priceGuidance;

  /// Exempt from the society's minimum booking notice. Set for emergencies.
  final bool bypassesNoticePeriod;

  /// "4 hr" / "90 min", for the catalogue card.
  String get durationLabel {
    if (expectedDurationMinutes < 60) return '$expectedDurationMinutes min';
    final hours = expectedDurationMinutes / 60;
    return hours == hours.roundToDouble()
        ? '${hours.round()} hr'
        : '${hours.toStringAsFixed(1)} hr';
  }

  factory ServiceCategory.fromJson(Map<String, dynamic> json) =>
      ServiceCategory(
        id: json['id'] as int,
        name: json['name'] as String? ?? '',
        slug: json['slug'] as String? ?? '',
        description: json['description'] as String? ?? '',
        icon: json['icon'] as String? ?? '',
        serviceType: json['service_type'] is Map<String, dynamic>
            ? ServiceType.fromJson(json['service_type'] as Map<String, dynamic>)
            : null,
        expectedDurationMinutes:
            json['expected_duration_minutes'] as int? ?? 60,
        priceMin: json['price_min'] as int? ?? 0,
        priceMax: json['price_max'] as int? ?? 0,
        priceGuidance: json['price_guidance'] as String? ?? '',
        bypassesNoticePeriod: json['bypasses_notice_period'] as bool? ?? false,
      );
}

/// Module 5.3 — a worker's answer for one date.
class DayAvailability {
  const DayAvailability({
    required this.date,
    required this.isAvailable,
    this.id,
    this.startTime,
    this.endTime,
    this.note = '',
  });

  final int? id;
  final DateTime date;

  /// True opts into the date; false blocks it out.
  final bool isAvailable;

  /// An optional narrower window than the worker's usual hours. Both are set
  /// or neither is — the server rejects half a window.
  final String? startTime;
  final String? endTime;
  final String note;

  bool get hasWindow => startTime != null && endTime != null;

  String get windowLabel => hasWindow
      ? '${formatBookingTime(startTime)} – ${formatBookingTime(endTime)}'
      : 'All day';

  Map<String, dynamic> toJson() => {
        'date': formatWireDate(date),
        'is_available': isAvailable,
        if (startTime != null) 'start_time': startTime,
        if (endTime != null) 'end_time': endTime,
        if (note.isNotEmpty) 'note': note,
      };

  factory DayAvailability.fromJson(Map<String, dynamic> json) =>
      DayAvailability(
        id: json['id'] as int?,
        date: DateTime.parse(json['date'] as String),
        isAvailable: json['is_available'] as bool? ?? true,
        startTime: json['start_time'] as String?,
        endTime: json['end_time'] as String?,
        note: json['note'] as String? ?? '',
      );
}

enum BookingStatus {
  pending('pending', 'Awaiting confirmation'),
  confirmed('confirmed', 'Confirmed'),
  completed('completed', 'Completed'),
  declined('declined', 'Declined'),
  cancelled('cancelled', 'Cancelled'),
  expired('expired', 'No response');

  const BookingStatus(this.wireValue, this.label);

  final String wireValue;
  final String label;

  static BookingStatus fromWire(String? value) =>
      BookingStatus.values.firstWhere(
        (s) => s.wireValue == value,
        orElse: () => BookingStatus.pending,
      );
}

/// Module 5.4 — what cancelling right now would cost, and why.
///
/// Always fetched and shown before the user commits. A fee that appears only
/// after the fact is the kind of surprise that costs an app its users' trust.
class CancellationQuote {
  const CancellationQuote({
    required this.fee,
    required this.tier,
    required this.rationale,
    required this.isFree,
  });

  final int fee;

  /// `free`, `partial`, or `full`.
  final String tier;

  /// The server's plain-language explanation, shown verbatim so the app and
  /// the server can never disagree about why a fee applies.
  final String rationale;
  final bool isFree;

  factory CancellationQuote.fromJson(Map<String, dynamic> json) =>
      CancellationQuote(
        fee: json['fee'] as int? ?? 0,
        tier: json['tier'] as String? ?? 'free',
        rationale: json['rationale'] as String? ?? '',
        isFree: json['is_free'] as bool? ?? (json['fee'] as int? ?? 0) == 0,
      );
}

/// Module 5.2 — one time-bound job.
class Booking {
  const Booking({
    required this.id,
    required this.status,
    required this.scheduledDate,
    required this.startTime,
    required this.quotedPrice,
    this.category,
    this.workerId = 0,
    this.workerName = '',
    this.workerPhotoUrl,
    this.workerPhone = '',
    this.residentName = '',
    this.residentFlat = '',
    this.endTime = '',
    this.scheduledStart,
    this.expectedDurationMinutes = 60,
    this.notes = '',
    this.responseNote = '',
    this.isActionable = false,
    this.canBeCancelled = false,
    this.cancellationFee = 0,
    this.cancelledBy = '',
    this.cancellationReason = '',
  });

  final int id;
  final BookingStatus status;
  final DateTime scheduledDate;
  final String startTime;
  final String endTime;

  /// Aware start instant, for countdowns. Prefer this over recombining the
  /// date and time locally — the server owns the society's timezone.
  final DateTime? scheduledStart;

  final int expectedDurationMinutes;
  final int quotedPrice;
  final ServiceCategory? category;

  final int workerId;
  final String workerName;
  final String? workerPhotoUrl;
  final String workerPhone;
  final String residentName;
  final String residentFlat;

  final String notes;
  final String responseNote;

  /// Whether the worker may still confirm or decline.
  final bool isActionable;
  final bool canBeCancelled;

  final int cancellationFee;
  final String cancelledBy;
  final String cancellationReason;

  bool get isPending => status == BookingStatus.pending;
  bool get isConfirmed => status == BookingStatus.confirmed;

  /// Pending or confirmed — still occupies the worker's day.
  bool get isLive =>
      status == BookingStatus.pending || status == BookingStatus.confirmed;

  String get startTimeLabel => formatBookingTime(startTime);

  String get timeRangeLabel {
    final end = formatBookingTime(endTime);
    return end.isEmpty ? startTimeLabel : '$startTimeLabel – $end';
  }

  /// Whole hours until the job starts, floored at zero. Null once it has begun.
  int? get hoursUntilStart {
    if (scheduledStart == null) return null;
    final remaining = scheduledStart!.difference(DateTime.now()).inHours;
    return remaining < 0 ? null : remaining;
  }

  factory Booking.fromJson(Map<String, dynamic> json) => Booking(
        id: json['id'] as int,
        status: BookingStatus.fromWire(json['status'] as String?),
        scheduledDate: DateTime.parse(json['scheduled_date'] as String),
        startTime: json['start_time'] as String? ?? '',
        endTime: json['end_time'] as String? ?? '',
        scheduledStart:
            DateTime.tryParse(json['scheduled_start'] as String? ?? ''),
        expectedDurationMinutes:
            json['expected_duration_minutes'] as int? ?? 60,
        // Guard against the server ever sending this as a decimal string, the
        // way DRF renders DecimalField — see the hiring models for the bug
        // this shape of parse prevented there.
        quotedPrice: toDoubleOrZero(json['quoted_price']).round(),
        category: json['category'] is Map<String, dynamic>
            ? ServiceCategory.fromJson(json['category'] as Map<String, dynamic>)
            : null,
        workerId: json['worker'] as int? ?? 0,
        workerName: json['worker_name'] as String? ?? '',
        workerPhotoUrl: json['worker_photo'] as String?,
        workerPhone: json['worker_phone'] as String? ?? '',
        residentName: json['resident_name'] as String? ?? '',
        residentFlat: json['resident_flat'] as String? ?? '',
        notes: json['notes'] as String? ?? '',
        responseNote: json['response_note'] as String? ?? '',
        isActionable: json['is_actionable'] as bool? ?? false,
        canBeCancelled: json['can_be_cancelled'] as bool? ?? false,
        cancellationFee: toDoubleOrZero(json['cancellation_fee']).round(),
        cancelledBy: json['cancelled_by'] as String? ?? '',
        cancellationReason: json['cancellation_reason'] as String? ?? '',
      );
}

/// The parameters of a Module 5.3 match query, kept in one place so the screen
/// never hand-builds the wire format.
class BookingSlot {
  const BookingSlot({
    required this.categoryId,
    required this.date,
    required this.startTime,
    this.durationMinutes,
  });

  final int categoryId;
  final DateTime date;

  /// `HH:MM`.
  final String startTime;

  /// Null falls back to the category's expected duration, server-side.
  final int? durationMinutes;

  Map<String, dynamic> toQuery() => {
        'category': categoryId,
        'date': formatWireDate(date),
        'start_time': startTime,
        if (durationMinutes != null) 'duration_minutes': durationMinutes,
      };

  @override
  bool operator ==(Object other) =>
      other is BookingSlot &&
      other.categoryId == categoryId &&
      other.date == date &&
      other.startTime == startTime &&
      other.durationMinutes == durationMinutes;

  @override
  int get hashCode => Object.hash(categoryId, date, startTime, durationMinutes);
}
