import 'package:flutter_test/flutter_test.dart';
import 'package:sathify/features/bookings/data/models/booking_models.dart';

/// Wire-format tests for Module 5 — One-Day Service Booking.
///
/// NOT YET EXECUTED — the Flutter SDK is not installed on this machine. Run
/// `flutter test` once it is.
void main() {
  group('formatWireDate', () {
    test('zero-pads month and day', () {
      expect(formatWireDate(DateTime(2026, 8, 3)), '2026-08-03');
    });

    test('handles a two-digit month and day', () {
      expect(formatWireDate(DateTime(2026, 12, 25)), '2026-12-25');
    });
  });

  group('formatBookingTime', () {
    test('trims seconds', () {
      expect(formatBookingTime('09:30:00'), '09:30');
    });

    test('leaves null and empty alone', () {
      expect(formatBookingTime(null), '');
      expect(formatBookingTime(''), '');
    });
  });

  group('ServiceCategory', () {
    Map<String, dynamic> payload() => {
          'id': 2,
          'name': 'Festival or deep cleaning',
          'slug': 'deep-cleaning',
          'description': 'A thorough top-to-bottom clean.',
          'icon': 'cleaning_services',
          'service_type': null,
          'expected_duration_minutes': 240,
          'price_min': 1200,
          'price_max': 3000,
          'price_guidance': '₹1200–₹3000',
          'bypasses_notice_period': false,
        };

    test('parses a catalogue row', () {
      final category = ServiceCategory.fromJson(payload());

      expect(category.id, 2);
      expect(category.slug, 'deep-cleaning');
      expect(category.priceMin, 1200);
      expect(category.priceGuidance, '₹1200–₹3000');
      expect(category.bypassesNoticePeriod, isFalse);
      expect(category.serviceType, isNull);
    });

    test('renders whole hours without a stray decimal', () {
      expect(ServiceCategory.fromJson(payload()).durationLabel, '4 hr');
    });

    test('renders a half hour as a decimal', () {
      final category =
          ServiceCategory.fromJson({...payload(), 'expected_duration_minutes': 90});
      expect(category.durationLabel, '1.5 hr');
    });

    test('renders under an hour in minutes', () {
      final category =
          ServiceCategory.fromJson({...payload(), 'expected_duration_minutes': 45});
      expect(category.durationLabel, '45 min');
    });

    test('parses a nested service type when the server links one', () {
      final category = ServiceCategory.fromJson({
        ...payload(),
        'service_type': {'id': 1, 'name': 'Maid', 'slug': 'maid'},
      });
      expect(category.serviceType?.name, 'Maid');
    });

    test('marks the emergency category as notice-exempt', () {
      final category = ServiceCategory.fromJson(
          {...payload(), 'bypasses_notice_period': true},);
      expect(category.bypassesNoticePeriod, isTrue);
    });
  });

  group('DayAvailability', () {
    test('round-trips an all-day opt-in', () {
      final day = DayAvailability(date: DateTime(2026, 8, 10), isAvailable: true);
      final json = day.toJson();

      expect(json['date'], '2026-08-10');
      expect(json['is_available'], isTrue);
      expect(json.containsKey('start_time'), isFalse);

      final parsed = DayAvailability.fromJson({...json, 'id': 4});
      expect(parsed.isAvailable, isTrue);
      expect(parsed.hasWindow, isFalse);
      expect(parsed.windowLabel, 'All day');
    });

    test('round-trips a narrowed window', () {
      final day = DayAvailability(
        date: DateTime(2026, 8, 10),
        isAvailable: true,
        startTime: '09:00',
        endTime: '13:00',
      );

      final parsed = DayAvailability.fromJson(day.toJson());
      expect(parsed.hasWindow, isTrue);
      expect(parsed.windowLabel, '09:00 – 13:00');
    });

    test('a blocked date round-trips as unavailable', () {
      final day = DayAvailability(date: DateTime(2026, 8, 11), isAvailable: false);
      expect(DayAvailability.fromJson(day.toJson()).isAvailable, isFalse);
    });
  });

  group('CancellationQuote', () {
    test('parses a free quote', () {
      final quote = CancellationQuote.fromJson({
        'fee': 0,
        'tier': 'free',
        'rationale': 'Cancelled 48 hours ahead.',
        'is_free': true,
      });

      expect(quote.fee, 0);
      expect(quote.isFree, isTrue);
      expect(quote.tier, 'free');
    });

    test('parses a chargeable quote', () {
      final quote = CancellationQuote.fromJson({
        'fee': 1000,
        'tier': 'partial',
        'rationale': '50% of the quoted price.',
        'is_free': false,
      });

      expect(quote.fee, 1000);
      expect(quote.isFree, isFalse);
    });

    test('derives is_free from the fee when the server omits it', () {
      final quote = CancellationQuote.fromJson({'fee': 0, 'tier': 'free'});
      expect(quote.isFree, isTrue);
    });
  });

  group('Booking', () {
    Map<String, dynamic> payload() => {
          'id': 9,
          'status': 'pending',
          'scheduled_date': '2026-08-15',
          'start_time': '10:00:00',
          'end_time': '14:00:00',
          'scheduled_start':
              DateTime.now().add(const Duration(hours: 30)).toIso8601String(),
          'expected_duration_minutes': 240,
          'quoted_price': 2000,
          'category': {
            'id': 2,
            'name': 'Festival or deep cleaning',
            'slug': 'deep-cleaning',
            'expected_duration_minutes': 240,
            'price_min': 1200,
            'price_max': 3000,
          },
          'worker': 7,
          'worker_name': 'Rahul Sharma',
          'worker_phone': '9800000002',
          'resident_name': 'Anita Desai',
          'resident_flat': 'A-301',
          'notes': 'Third floor.',
          'is_actionable': true,
          'can_be_cancelled': true,
          'cancellation_fee': 0,
        };

    test('parses a booking', () {
      final booking = Booking.fromJson(payload());

      expect(booking.id, 9);
      expect(booking.status, BookingStatus.pending);
      expect(booking.isPending, isTrue);
      expect(booking.isLive, isTrue);
      expect(booking.scheduledDate, DateTime(2026, 8, 15));
      expect(booking.category?.slug, 'deep-cleaning');
      expect(booking.quotedPrice, 2000);
    });

    test('renders the time range', () {
      expect(Booking.fromJson(payload()).timeRangeLabel, '10:00 – 14:00');
    });

    test('falls back to the start time when no end is given', () {
      final booking = Booking.fromJson({...payload(), 'end_time': ''});
      expect(booking.timeRangeLabel, '10:00');
    });

    test('reports hours until the job starts', () {
      expect(Booking.fromJson(payload()).hoursUntilStart, inInclusiveRange(28, 30));
    });

    test('a job already under way reports no countdown', () {
      final booking = Booking.fromJson({
        ...payload(),
        'scheduled_start':
            DateTime.now().subtract(const Duration(hours: 2)).toIso8601String(),
      });
      expect(booking.hoursUntilStart, isNull);
    });

    test('parses a price sent as a decimal string', () {
      // Guards the DecimalField-as-string trap that bit the hiring models.
      final booking = Booking.fromJson(
          {...payload(), 'quoted_price': '2000.00', 'cancellation_fee': '1000.00'},);

      expect(booking.quotedPrice, 2000);
      expect(booking.cancellationFee, 1000);
    });

    test('a cancelled booking is no longer live', () {
      final booking = Booking.fromJson({
        ...payload(),
        'status': 'cancelled',
        'is_actionable': false,
        'can_be_cancelled': false,
        'cancellation_fee': 1000,
      });

      expect(booking.status, BookingStatus.cancelled);
      expect(booking.isLive, isFalse);
      expect(booking.cancellationFee, 1000);
    });

    test('falls back rather than throwing on an unknown status', () {
      final booking = Booking.fromJson({...payload(), 'status': 'something_new'});
      expect(booking.status, BookingStatus.pending);
    });

    test('an expired booking parses as expired', () {
      final booking = Booking.fromJson({...payload(), 'status': 'expired'});
      expect(booking.status, BookingStatus.expired);
      expect(booking.isLive, isFalse);
    });
  });

  group('BookingSlot', () {
    test('serialises to the match query', () {
      final slot = BookingSlot(
        categoryId: 2,
        date: DateTime(2026, 8, 15),
        startTime: '10:00',
        durationMinutes: 240,
      );

      expect(slot.toQuery(), {
        'category': 2,
        'date': '2026-08-15',
        'start_time': '10:00',
        'duration_minutes': 240,
      });
    });

    test('omits the duration so the server uses the category default', () {
      final slot = BookingSlot(
        categoryId: 2,
        date: DateTime(2026, 8, 15),
        startTime: '10:00',
      );

      expect(slot.toQuery().containsKey('duration_minutes'), isFalse);
    });

    test('compares by value so the provider refetches only on real changes', () {
      final a = BookingSlot(
          categoryId: 2, date: DateTime(2026, 8, 15), startTime: '10:00',);
      final b = BookingSlot(
          categoryId: 2, date: DateTime(2026, 8, 15), startTime: '10:00',);
      final c = BookingSlot(
          categoryId: 2, date: DateTime(2026, 8, 15), startTime: '11:00',);

      expect(a, equals(b));
      expect(a.hashCode, b.hashCode);
      expect(a, isNot(equals(c)));
    });
  });
}
