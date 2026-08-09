import 'package:flutter_test/flutter_test.dart';
import 'package:sathify/features/bookings/data/models/booking_models.dart';
import 'package:sathify/features/hiring/data/models/hiring_models.dart';
import 'package:sathify/features/hiring/presentation/providers/hiring_provider.dart'
    show RequestEntry;
import 'package:sathify/features/payments/data/models/payment_models.dart';

/// Modules 8.9 and 4.6 — the UPI/QR payload and the notice settlement.
void main() {
  group('UpiQr', () {
    Map<String, dynamic> payload() => {
          'qr_code_id': 'qr_HMsVL8HOpbMcjU',
          'image_url': 'https://rzp.io/i/BWcUVrLp',
          'amount_paise': 10000,
          'amount_display': '₹100.00',
          'reference': 'abc-123',
          'expires_at': '2026-08-09T21:30:00Z',
          'settles': 'webhook',
          'apps': [
            {'key': 'gpay', 'label': 'Google Pay'},
            {'key': 'famapp', 'label': 'FamApp (FamPay)'},
          ],
        };

    test('parses the hosted code', () {
      final qr = UpiQr.fromJson(payload());

      // Razorpay hosts the image now; the app no longer draws the code, so
      // there is no upi:// string on this model at all.
      expect(qr.imageUrl, startsWith('https://'));
      expect(qr.qrCodeId, 'qr_HMsVL8HOpbMcjU');
      expect(qr.reference, 'abc-123');
      expect(qr.apps.length, 2);
    });

    test('names FamApp among the UPI apps', () {
      // The brief asked for FamPay. It has no merchant API, so scanning a
      // standard UPI code was always the only way to pay from it — and that is
      // still true now the code is Razorpay's rather than ours.
      final qr = UpiQr.fromJson(payload());
      expect(qr.apps.any((app) => app.label.contains('FamPay')), isTrue);
    });

    test('knows when it has expired', () {
      // Single-use codes close themselves, so a stale one must not be shown as
      // though it will still collect.
      final stale = UpiQr.fromJson({
        ...payload(),
        'expires_at': '2020-01-01T00:00:00Z',
      });
      expect(stale.hasExpired, isTrue);
    });

    test('a missing expiry is not treated as expired', () {
      final qr = UpiQr.fromJson({...payload(), 'expires_at': null});
      expect(qr.expiresAt, isNull);
      expect(qr.hasExpired, isFalse);
    });

    test('an empty app list is survivable', () {
      final qr = UpiQr.fromJson({...payload(), 'apps': const []});
      expect(qr.apps, isEmpty);
      expect(qr.imageUrl, isNotEmpty);
    });
  });

  group('PaymentKind', () {
    test('knows the two kinds added since the ledger shipped', () {
      expect(
        PaymentKind.fromWire('emergency_surcharge'),
        PaymentKind.emergencySurcharge,
      );
      expect(
        PaymentKind.fromWire('notice_settlement'),
        PaymentKind.noticeSettlement,
      );
    });

    test('an unknown kind falls back rather than throwing', () {
      // Kinds arrive as strings from a server that may be newer than this
      // build. A ledger screen that throws is worse than one mislabelling a row.
      expect(PaymentKind.fromWire('something_new'), PaymentKind.booking);
    });
  });

  group('NoticeSettlement', () {
    Map<String, dynamic> payload() => {
          'days_worked': 12,
          'scheduled_days': 22,
          'attended_days': 10,
          'completed_days': 12,
          'days_in_month': 31,
          'monthly_rate_paise': 800000,
          'monthly_rate_display': '₹8,000.00',
          'amount_paise': 436363,
          'amount_display': '₹4,363.63',
          'explanation': '12 of 22 scheduled visits were worked this month.',
          'is_outstanding': true,
          'blocks_notice': true,
        };

    test('parses every term of the breakdown', () {
      final settlement = NoticeSettlement.fromJson(payload());

      expect(settlement.daysWorked, 12);
      expect(settlement.scheduledDays, 22);
      expect(settlement.monthlyRatePaise, 800000);
      expect(settlement.amountDisplay, '₹4,363.63');
      expect(settlement.blocksNotice, isTrue);
    });

    test('days in the month is the denominator, and is shown', () {
      // `days_worked / days_in_month * monthly_rate`. Scheduled days is
      // reported alongside it for context, so the two must stay distinguishable
      // in the UI — showing one where the other belongs would make the total
      // look wrong to anybody checking the sum.
      final settlement = NoticeSettlement.fromJson(payload());

      expect(settlement.daysInMonth, 31);
      expect(settlement.scheduledDays, isNot(settlement.daysInMonth));
    });

    test('an amount already covered by a salary is not outstanding', () {
      // A household that pays on the 1st and gives notice on the 20th has
      // already handed over the money.
      final settled = NoticeSettlement.fromJson({
        ...payload(),
        'is_outstanding': false,
        'blocks_notice': false,
      });

      expect(settled.amountPaise, greaterThan(0));
      expect(settled.isOutstanding, isFalse);
      expect(settled.blocksNotice, isFalse);
    });

    test('a month with nothing worked owes nothing', () {
      final nothing = NoticeSettlement.fromJson({
        ...payload(),
        'days_worked': 0,
        'amount_paise': 0,
        'is_outstanding': false,
        'blocks_notice': false,
      });

      expect(nothing.amountPaise, 0);
      expect(nothing.isOutstanding, isFalse);
    });
  });

  group('RequestEntry — the unified "My requests" list', () {
    test('a booking entry carries the booking and no hire request', () {
      final entry = RequestEntry.job(
        Booking.fromJson({
          'id': 4,
          'status': 'broadcast',
          'scheduled_date': '2026-08-09',
          'start_time': '15:00:00',
          'quoted_price': 600,
          'is_emergency': true,
        }),
      );

      // The screen switches on exactly this to choose a card.
      expect(entry.booking, isNotNull);
      expect(entry.hireRequest, isNull);
      expect(entry.booking!.isEmergency, isTrue);
    });

    test('entries sort newest first across both sources', () {
      final older = RequestEntry.job(
        Booking.fromJson({
          'id': 1,
          'status': 'completed',
          'scheduled_date': '2026-08-01',
          'start_time': '10:00:00',
          'quoted_price': 500,
        }),
      );
      final newer = RequestEntry.job(
        Booking.fromJson({
          'id': 2,
          'status': 'broadcast',
          'scheduled_date': '2026-08-09',
          'start_time': '10:00:00',
          'quoted_price': 500,
        }),
      );

      final list = <RequestEntry>[older, newer]
        ..sort((a, b) => b.sortedOn.compareTo(a.sortedOn));

      expect(list.first.booking!.id, 2);
    });
  });
}
