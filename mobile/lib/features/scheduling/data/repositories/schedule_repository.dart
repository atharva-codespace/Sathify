import '../../../../core/config/api_endpoints.dart';
import '../../../../core/network/api_client.dart';
import '../../../bookings/data/models/booking_models.dart' show formatWireDate;
import '../models/schedule_models.dart';

/// All Module 6 endpoints — calendar, task timing, conflicts, reminders.
class ScheduleRepository {
  ScheduleRepository({ApiClient? client}) : _client = client ?? ApiClient();

  final ApiClient _client;

  // --- 6.1 Calendar ----------------------------------------------------------

  /// The caller's own day. The server resolves by role, so a worker gets their
  /// commitments and a resident gets their household's — one call either way.
  Future<List<ScheduleItem>> fetchToday() async {
    final response =
        await _client.get(ApiEndpoints.myToday) as Map<String, dynamic>;
    return _items(response);
  }

  Future<List<ScheduleItem>> fetchAgenda(AgendaRange range) async {
    final response = await _client.get(
      ApiEndpoints.myAgenda,
      query: range.toQuery(),
    ) as Map<String, dynamic>;
    return _items(response);
  }

  /// Every expected visit in the society (administrators).
  Future<List<ScheduleItem>> fetchSocietyAgenda(AgendaRange range) async {
    final response = await _client.get(
      ApiEndpoints.societyAgenda,
      query: range.toQuery(),
    ) as Map<String, dynamic>;
    return _items(response);
  }

  /// One worker's schedule, for an administrator checking their load.
  Future<List<ScheduleItem>> fetchWorkerAgenda(
    int workerId,
    AgendaRange range,
  ) async {
    final response = await _client.get(
      ApiEndpoints.workerAgenda(workerId),
      query: range.toQuery(),
    ) as Map<String, dynamic>;
    return _items(response);
  }

  List<ScheduleItem> _items(Map<String, dynamic> response) =>
      ((response['results'] as List?) ?? const [])
          .map((row) => ScheduleItem.fromJson(row as Map<String, dynamic>))
          .toList();

  // --- 6.2 Task timing -------------------------------------------------------

  Future<TaskTiming> fetchTaskTiming(int engagementId) async {
    final response = await _client.get(ApiEndpoints.taskTiming(engagementId))
        as Map<String, dynamic>;
    return TaskTiming.fromJson(response);
  }

  /// Upserts the timing. Only the flat's primary account holder may do this;
  /// the server answers 403 for anyone else, including the worker.
  Future<TaskTiming> setTaskTiming(int engagementId, TaskTiming timing) async {
    final response = await _client.put(
      ApiEndpoints.taskTiming(engagementId),
      data: timing.toJson(),
    ) as Map<String, dynamic>;

    return TaskTiming.fromJson(response['timing'] as Map<String, dynamic>);
  }

  // --- 6.3 Conflicts ---------------------------------------------------------

  /// Pre-flight check, so the app can warn before a whole form is filled in.
  ///
  /// Advisory only — the authoritative check runs inside booking creation under
  /// a row lock. A clear result here is not a promise the slot will still be
  /// free by the time the booking is sent.
  Future<ConflictReport> checkConflict({
    required int workerId,
    required DateTime date,
    required String startTime,
    required int durationMinutes,
    int? excludeBookingId,
  }) async {
    final response = await _client.get(
      ApiEndpoints.conflictCheck,
      query: {
        'worker': workerId,
        'date': formatWireDate(date),
        'start_time': startTime,
        'duration_minutes': durationMinutes,
        if (excludeBookingId != null) 'exclude_booking': excludeBookingId,
      },
    ) as Map<String, dynamic>;

    return ConflictReport.fromJson(response);
  }

  // --- 6.4 Reminders ---------------------------------------------------------

  Future<List<Reminder>> fetchDueReminders() async {
    final response = await _client.get(ApiEndpoints.dueReminders) as List;
    return response
        .map((row) => Reminder.fromJson(row as Map<String, dynamic>))
        .toList();
  }

  /// Reports back after attempting delivery. Idempotent on the server.
  Future<void> markReminderDelivered(
    int reminderId, {
    bool delivered = true,
    String failureReason = '',
  }) async {
    await _client.post(
      ApiEndpoints.reminderDelivered(reminderId),
      data: {
        'delivered': delivered,
        if (failureReason.isNotEmpty) 'failure_reason': failureReason,
      },
    );
  }
}
