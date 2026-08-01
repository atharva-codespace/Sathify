import 'package:flutter_test/flutter_test.dart';
import 'package:sathify/features/notifications/data/models/notification_models.dart';

/// Wire-format tests for Module 10 — Notifications.
///
/// NOT YET EXECUTED — the Flutter SDK is not installed on this machine. Run
/// `flutter test` once it is.
///
/// The [NotificationPreference] group carries the most weight. `can_mute` is the
/// only thing standing between a locked control with a reason and a switch that
/// flips back with an error, so it has to survive parsing and it has to default
/// the safe way round when a server omits it.
void main() {
  group('AppNotification', () {
    Map<String, dynamic> payload({
      String category = 'gate_entry',
      bool isRead = false,
      bool wasDelivered = true,
    }) =>
        {
          'id': 41,
          'category': category,
          'category_display': 'Gate entry alerts',
          'title': 'Entry refused',
          'body': 'Sunita Devi was refused entry at the main gate.',
          'data': {'route': '/gate/log', 'event_id': 'abc-123'},
          'is_read': isRead,
          'read_at': null,
          'is_safety_critical': true,
          'was_delivered': wasDelivered,
          'created_at': '2026-03-14T09:05:00Z',
          'push_state': 'sent',
          'sms_state': 'pending',
        };

    test('parses a notification', () {
      final notification = AppNotification.fromJson(payload());

      expect(notification.id, 41);
      expect(notification.category, NotificationCategory.gateEntry);
      expect(notification.title, 'Entry refused');
      expect(notification.isRead, isFalse);
      expect(notification.isSafetyCritical, isTrue);
      expect(notification.pushState, DeliveryState.sent);
      expect(notification.smsState, DeliveryState.pending);
      expect(notification.createdAt, isNotNull);
    });

    test('exposes the route the server suggested', () {
      // Navigation targets travel as data, not as a URL the server dictates —
      // the app decides what `/gate/log` means.
      expect(AppNotification.fromJson(payload()).route, '/gate/log');
    });

    test('a notification with no route has none rather than an empty string', () {
      final json = payload()..['data'] = <String, dynamic>{};
      expect(AppNotification.fromJson(json).route, isNull);
    });

    test('an undelivered notification is flagged as in-app only', () {
      // This is what lets support answer "I was never told" without database
      // access: the row is here, and it says it never left the server.
      final delivered = AppNotification.fromJson(payload());
      final undelivered =
          AppNotification.fromJson(payload(wasDelivered: false));

      expect(delivered.arrivedOnlyInApp, isFalse);
      expect(undelivered.arrivedOnlyInApp, isTrue);
    });

    test('an unknown category parses rather than throwing', () {
      // A server that adds a category before the app ships must not crash the
      // notification centre — the message still has a title and a body worth
      // reading, and losing the whole list over one unknown row is far worse
      // than showing it with a generic icon.
      final json = payload()..['category'] = 'something_new';
      expect(
        AppNotification.fromJson(json).category,
        NotificationCategory.account,
      );
    });

    test('a missing body parses as empty rather than null', () {
      final json = payload()..remove('body');
      expect(AppNotification.fromJson(json).body, '');
    });
  });

  group('NotificationCategory', () {
    test('every wire value round-trips', () {
      // The wire values are the server's `NotificationCategory.choices`. A typo
      // here would silently file that category under `account` for every user.
      for (final category in NotificationCategory.values) {
        expect(NotificationCategory.fromWire(category.wireValue), category);
      }
    });

    test('carries a human label for the settings screen', () {
      for (final category in NotificationCategory.values) {
        expect(category.label, isNotEmpty);
      }
    });
  });

  group('DeliveryState', () {
    test('every wire value round-trips', () {
      for (final state in DeliveryState.values) {
        expect(DeliveryState.fromWire(state.wireValue), state);
      }
    });

    test('an unknown state falls back to pending', () {
      expect(DeliveryState.fromWire('exploded'), DeliveryState.pending);
      expect(DeliveryState.fromWire(null), DeliveryState.pending);
    });
  });

  group('NotificationPreference', () {
    test('parses a mutable category', () {
      final preference = NotificationPreference.fromJson({
        'category': 'payment',
        'label': 'Payments',
        'muted': true,
        'can_mute': true,
      });

      expect(preference.category, 'payment');
      expect(preference.muted, isTrue);
      expect(preference.canMute, isTrue);
    });

    test('a safety-critical category comes back locked', () {
      // The server refuses to mute these, and the model refuses underneath the
      // serializer. `can_mute` exists so the app never offers the switch in the
      // first place — a control that flips back is a worse explanation than a
      // lock with a reason next to it.
      for (final category in const ['gate_entry', 'urgent_leave', 'account']) {
        final preference = NotificationPreference.fromJson({
          'category': category,
          'label': 'Whatever',
          'muted': false,
          'can_mute': false,
        });

        expect(preference.canMute, isFalse, reason: category);
        expect(preference.muted, isFalse, reason: category);
      }
    });

    test('defaults to mutable when the server omits can_mute', () {
      // Defaulting the other way would lock every control on an older server
      // and leave the user unable to change anything at all. An extra switch
      // the server then refuses is the smaller failure, and it is visible.
      final preference = NotificationPreference.fromJson({
        'category': 'booking',
        'label': 'One-day bookings',
        'muted': false,
      });

      expect(preference.canMute, isTrue);
    });
  });
}
