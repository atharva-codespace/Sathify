import '../../../../core/config/api_endpoints.dart';
import '../../../../core/network/api_client.dart';
import '../../../hiring/data/models/hiring_models.dart' show WorkerSearchResult;
import '../models/booking_models.dart';

/// All Module 5 endpoints — catalogue, availability, matching, and bookings.
class BookingRepository {
  BookingRepository({ApiClient? client}) : _client = client ?? ApiClient();

  final ApiClient _client;

  // --- 5.1 Catalogue ---------------------------------------------------------

  /// The bookable categories. Unpaginated server-side — the catalogue is short
  /// and fixed, so paging it would only add a round trip.
  Future<List<ServiceCategory>> fetchCategories() async {
    final response = await _client.get(ApiEndpoints.serviceCategories) as List;
    return response
        .map((row) => ServiceCategory.fromJson(row as Map<String, dynamic>))
        .toList();
  }

  // --- 5.3 Availability ------------------------------------------------------

  /// The signed-in worker's own upcoming availability.
  Future<List<DayAvailability>> fetchMyAvailability() async {
    final response = await _client.get(ApiEndpoints.myAvailability) as List;
    return response
        .map((row) => DayAvailability.fromJson(row as Map<String, dynamic>))
        .toList();
  }

  /// Upserts one date. Idempotent by (worker, date) on the server, so a double
  /// tap on a flaky connection cannot leave two contradictory answers.
  Future<DayAvailability> setMyAvailability(
    DayAvailability availability,
  ) async {
    final response = await _client.put(
      ApiEndpoints.myAvailability,
      data: availability.toJson(),
    ) as Map<String, dynamic>;

    return DayAvailability.fromJson(response);
  }

  /// A worker's open dates, for the resident's date picker.
  Future<List<DayAvailability>> fetchWorkerAvailability(int workerId) async {
    final response =
        await _client.get(ApiEndpoints.workerAvailability(workerId)) as List;
    return response
        .map((row) => DayAvailability.fromJson(row as Map<String, dynamic>))
        .toList();
  }

  // --- 5.3 Matching ----------------------------------------------------------

  /// Workers free for a slot, ranked by Module 4.3's score.
  ///
  /// Returns Module 4's search row: the server's matched-worker serializer
  /// subclasses the search one, so the two flows share a model and a card.
  Future<List<WorkerSearchResult>> matchWorkers(BookingSlot slot) async {
    final response = await _client.get(
      ApiEndpoints.bookingMatch,
      query: slot.toQuery(),
    ) as Map<String, dynamic>;

    return (response['results'] as List)
        .map((row) => WorkerSearchResult.fromJson(row as Map<String, dynamic>))
        .toList();
  }

  // --- 5.2 Bookings ----------------------------------------------------------

  /// Creates a booking. Only the flat's primary account holder may do this
  /// (Module 2.4); the server answers 403 for anyone else in the household.
  Future<Booking> createBooking({
    required int workerId,
    required int categoryId,
    required BookingSlot slot,
    int? quotedPrice,
    String notes = '',
  }) async {
    final response = await _client.post(
      ApiEndpoints.bookings,
      data: {
        'worker': workerId,
        'category': categoryId,
        'scheduled_date': formatWireDate(slot.date),
        'start_time': slot.startTime,
        if (slot.durationMinutes != null)
          'expected_duration_minutes': slot.durationMinutes,
        // Omitted values fall back to the category's guidance server-side, so
        // the client never has to echo a number the resident did not change.
        if (quotedPrice != null) 'quoted_price': quotedPrice,
        if (notes.isNotEmpty) 'notes': notes,
      },
    ) as Map<String, dynamic>;

    return Booking.fromJson(response['booking'] as Map<String, dynamic>);
  }

  /// Bookings the caller is party to. The server decides whether that means
  /// "booked by me" or "assigned to me" from the caller's role.
  Future<List<Booking>> fetchBookings({
    String? status,
    bool upcomingOnly = false,
  }) async {
    final response = await _client.get(
      ApiEndpoints.bookings,
      query: {
        if (status != null) 'status': status,
        if (upcomingOnly) 'upcoming': 'true',
        'page_size': 100,
      },
    ) as Map<String, dynamic>;

    return (response['results'] as List)
        .map((row) => Booking.fromJson(row as Map<String, dynamic>))
        .toList();
  }

  // --- 5.4 Confirmation & cancellation --------------------------------------

  Future<Booking> confirmBooking(int bookingId, {String note = ''}) =>
      _respond(bookingId, confirm: true, note: note);

  Future<Booking> declineBooking(int bookingId, {String note = ''}) =>
      _respond(bookingId, confirm: false, note: note);

  Future<Booking> _respond(
    int bookingId, {
    required bool confirm,
    String note = '',
  }) async {
    final response = await _client.post(
      ApiEndpoints.respondToBooking(bookingId),
      data: {'confirm': confirm, if (note.isNotEmpty) 'note': note},
    ) as Map<String, dynamic>;

    return Booking.fromJson(response['booking'] as Map<String, dynamic>);
  }

  /// What cancelling right now would cost. Always call this before
  /// [cancelBooking] and show the result — see [CancellationQuote].
  Future<CancellationQuote> fetchCancellationQuote(int bookingId) async {
    final response = await _client
        .get(ApiEndpoints.cancellationQuote(bookingId)) as Map<String, dynamic>;
    return CancellationQuote.fromJson(response);
  }

  /// Cancels a booking.
  ///
  /// [acknowledgedFee] is the fee the user was actually shown. The server
  /// refuses with `fee_changed` if a threshold was crossed while the dialog was
  /// open, so nobody is ever charged more than the number they agreed to.
  Future<Booking> cancelBooking(
    int bookingId, {
    required int acknowledgedFee,
    String reason = '',
  }) async {
    final response = await _client.post(
      ApiEndpoints.cancelBooking(bookingId),
      data: {
        'acknowledged_fee': acknowledgedFee,
        if (reason.isNotEmpty) 'reason': reason,
      },
    ) as Map<String, dynamic>;

    return Booking.fromJson(response['booking'] as Map<String, dynamic>);
  }

  Future<Booking> completeBooking(int bookingId) async {
    final response = await _client.post(ApiEndpoints.completeBooking(bookingId))
        as Map<String, dynamic>;
    return Booking.fromJson(response['booking'] as Map<String, dynamic>);
  }
}
