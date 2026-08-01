/// Data models for Module 10 — Notifications.
///
/// -----------------------------------------------------------------------
/// DELIVERY STATE IS SHOWN TO THE USER, NOT HIDDEN
/// -----------------------------------------------------------------------
/// [AppNotification] carries [pushState] and [smsState] so the app can tell
/// "we could not reach you" apart from "you missed it". That distinction
/// matters when somebody says they were never told about a gate refusal — and
/// it means support can answer the question without database access.
library;

/// What a notification is about. Drives the icon, and whether it can be muted.
enum NotificationCategory {
  account('account', 'Account and verification'),
  hire('hire', 'Hire requests'),
  booking('booking', 'One-day bookings'),
  schedule('schedule', 'Upcoming visits'),
  attendance('attendance', 'Attendance'),
  gateEntry('gate_entry', 'Gate entry alerts'),
  urgentLeave('urgent_leave', 'Urgent leave and replacements'),
  payment('payment', 'Payments'),
  rating('rating', 'Ratings and reviews'),
  complaint('complaint', 'Complaints');

  const NotificationCategory(this.wireValue, this.label);

  final String wireValue;
  final String label;

  static NotificationCategory fromWire(String? value) =>
      NotificationCategory.values.firstWhere(
        (c) => c.wireValue == value,
        orElse: () => NotificationCategory.account,
      );
}

/// What happened on one channel.
enum DeliveryState {
  pending('pending'),
  sent('sent'),
  failed('failed'),
  skipped('skipped');

  const DeliveryState(this.wireValue);

  final String wireValue;

  static DeliveryState fromWire(String? value) =>
      DeliveryState.values.firstWhere(
        (s) => s.wireValue == value,
        orElse: () => DeliveryState.pending,
      );
}

/// One entry in the notification centre (Module 10.3).
class AppNotification {
  const AppNotification({
    required this.id,
    required this.category,
    required this.title,
    required this.body,
    required this.isRead,
    this.data = const {},
    this.isSafetyCritical = false,
    this.wasDelivered = false,
    this.pushState = DeliveryState.pending,
    this.smsState = DeliveryState.pending,
    this.createdAt,
  });

  final int id;
  final NotificationCategory category;
  final String title;
  final String body;

  /// Where tapping should go, as data rather than a URL — the app decides its
  /// own navigation.
  final Map<String, dynamic> data;

  final bool isRead;

  /// Cannot be muted: gate entry, urgent leave, account status.
  final bool isSafetyCritical;
  final bool wasDelivered;
  final DeliveryState pushState;
  final DeliveryState smsState;
  final DateTime? createdAt;

  /// The in-app route to open, if the server suggested one.
  String? get route {
    final value = data['route'];
    return value is String && value.isNotEmpty ? value : null;
  }

  /// True when neither channel reached the person. Worth surfacing quietly:
  /// they are reading it now, but it explains why it felt late.
  bool get arrivedOnlyInApp => !wasDelivered;

  factory AppNotification.fromJson(Map<String, dynamic> json) =>
      AppNotification(
        id: json['id'] as int,
        category: NotificationCategory.fromWire(json['category'] as String?),
        title: json['title'] as String? ?? '',
        body: json['body'] as String? ?? '',
        data: Map<String, dynamic>.from((json['data'] as Map?) ?? const {}),
        isRead: json['is_read'] as bool? ?? false,
        isSafetyCritical: json['is_safety_critical'] as bool? ?? false,
        wasDelivered: json['was_delivered'] as bool? ?? false,
        pushState: DeliveryState.fromWire(json['push_state'] as String?),
        smsState: DeliveryState.fromWire(json['sms_state'] as String?),
        createdAt: DateTime.tryParse(json['created_at'] as String? ?? ''),
      );
}

/// Module 10.4 — one category's mute setting.
class NotificationPreference {
  const NotificationPreference({
    required this.category,
    required this.label,
    required this.muted,
    required this.canMute,
  });

  final String category;
  final String label;
  final bool muted;

  /// False for safety-critical categories. The app renders these as locked with
  /// a reason, rather than offering a switch that silently refuses.
  final bool canMute;

  factory NotificationPreference.fromJson(Map<String, dynamic> json) =>
      NotificationPreference(
        category: json['category'] as String? ?? '',
        label: json['label'] as String? ?? '',
        muted: json['muted'] as bool? ?? false,
        canMute: json['can_mute'] as bool? ?? true,
      );
}
