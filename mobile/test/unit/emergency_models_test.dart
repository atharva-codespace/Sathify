import 'package:flutter_test/flutter_test.dart';
import 'package:sathify/features/bookings/data/models/booking_models.dart';
import 'package:sathify/features/scheduling/data/models/schedule_models.dart';

/// Module 5.5 — wire format for the emergency broadcast, plus the client half
/// of the two "Mark as Done" bugs.
///
/// The regression cases here are about one thing: the app no longer decides for
/// itself whether a visit can be closed out. It reads the server's answer. A
/// test that asserted the old local rule would be asserting the bug.
void main() {
  group('Booking — the completion flag comes from the server', () {
    Map<String, dynamic> payload({
      bool canMarkDone = false,
      bool isEmergency = false,
      String status = 'confirmed',
      bool isPaid = false,
    }) =>
        {
          'id': 7,
          'status': status,
          'scheduled_date': '2026-08-09',
          'start_time': '14:30:00',
          'quoted_price': 800,
          'can_mark_done': canMarkDone,
          'is_emergency': isEmergency,
          // Always 'app' now. Emergency work was briefly settled in cash.
          'settlement': 'app',
          'emergency_surcharge_paise': isEmergency ? 10000 : 0,
          'is_paid': isPaid,
        };

    test('a booking the server says is closeable reports so', () {
      expect(Booking.fromJson(payload(canMarkDone: true)).canMarkDone, isTrue);
    });

    test('an old server that omits the flag does not offer the button', () {
      final booking = Booking.fromJson({
        'id': 7,
        'status': 'confirmed',
        'scheduled_date': '2026-08-09',
        'start_time': '14:30:00',
        'quoted_price': 800,
      });

      // Absent means "do not offer it", never "work it out yourself". Guessing
      // is exactly what produced a button that failed every time it was tapped.
      expect(booking.canMarkDone, isFalse);
    });

    test('a completed emergency asks the household to pay, like any job', () {
      // This is the reversal: emergency work was briefly settled in cash and
      // deliberately excluded from in-app payment. Every booking now settles
      // the same way, so the Pay button must appear here too.
      final emergency = Booking.fromJson(
        payload(status: 'completed', isEmergency: true),
      );

      expect(emergency.isEmergency, isTrue);
      expect(emergency.needsPayment, isTrue);
    });

    test('an ordinary completed job does too', () {
      expect(Booking.fromJson(payload(status: 'completed')).needsPayment, isTrue);
    });

    test('a paid job is not asked for again', () {
      final paid = Booking.fromJson(payload(status: 'completed', isPaid: true));
      expect(paid.needsPayment, isFalse);
    });

    test('the broadcast statuses parse and read as still looking', () {
      expect(
        Booking.fromJson(payload(status: 'payment_pending')).isSeekingWorker,
        isTrue,
      );
      expect(
        Booking.fromJson(payload(status: 'broadcast')).isSeekingWorker,
        isTrue,
      );
      expect(Booking.fromJson(payload()).isSeekingWorker, isFalse);
    });

    test('an unknown status does not crash the list', () {
      // Statuses arrive as strings from a server that may be newer than this
      // build. Falling back beats throwing on a screen full of bookings.
      expect(
        Booking.fromJson(payload(status: 'something_new')).status,
        BookingStatus.pending,
      );
    });
  });

  group('ScheduleItem — the maid dashboard card', () {
    Map<String, dynamic> row({
      bool canMarkDone = false,
      String settlement = 'app',
      String visitStatus = 'pending',
      bool isConfirmed = true,
      bool canRespond = false,
    }) =>
        {
          'source': 'booking',
          'source_id': 7,
          'date': '2026-08-09',
          'start_time': '14:30:00',
          'duration_minutes': 60,
          'can_mark_done': canMarkDone,
          'settlement': settlement,
          'visit_status': visitStatus,
          'is_confirmed': isConfirmed,
          'can_respond': canRespond,
        };

    test('an answerable request offers the answer', () {
      final pending = ScheduleItem.fromJson(
        row(isConfirmed: false, canRespond: true),
      );

      expect(pending.needsResponse, isTrue);
      expect(pending.canRespond, isTrue);
      // Not lapsed: there is still something to tap, which is the whole
      // difference between this and the reported bug.
      expect(pending.responseLapsed, isFalse);
    });

    test('a request past its deadline is lapsed, not still "awaiting"', () {
      final lapsed = ScheduleItem.fromJson(
        row(isConfirmed: false, canRespond: false),
      );

      expect(lapsed.needsResponse, isTrue);
      // The state the card used to render as "Awaiting your confirmation" with
      // nothing to tap. It now says so plainly instead.
      expect(lapsed.responseLapsed, isTrue);
    });

    test('a confirmed visit is neither awaiting nor lapsed', () {
      final confirmed = ScheduleItem.fromJson(row());

      expect(confirmed.needsResponse, isFalse);
      expect(confirmed.responseLapsed, isFalse);
    });

    test('an old server that omits the flag offers no button', () {
      // Absent means "do not offer it". Guessing from is_confirmed is exactly
      // what would put an Accept button on a request the server would refuse.
      expect(
        ScheduleItem.fromJson({
          'source': 'booking',
          'source_id': 7,
          'date': '2026-08-09',
          'start_time': '14:30:00',
          'is_confirmed': false,
        }).canRespond,
        isFalse,
      );
    });

    test('carries the server\'s completion flag', () {
      expect(ScheduleItem.fromJson(row(canMarkDone: true)).canMarkDone, isTrue);
      expect(ScheduleItem.fromJson(row()).canMarkDone, isFalse);
    });

    test('a finished visit still parses, and is no longer closeable', () {
      // The row now survives completion instead of being filtered out of the
      // schedule — which is what made the card vanish and look like the button
      // had destroyed the job.
      final done = ScheduleItem.fromJson(row(visitStatus: 'complete'));

      expect(done.isComplete, isTrue);
      expect(done.canMarkDone, isFalse);
    });

    test('every visit settles through the app', () {
      // One settlement route across the whole app now — the card no longer has
      // to branch on how a job is paid.
      expect(ScheduleItem.fromJson(row()).settlement, 'app');
    });
  });

  group('EmergencyOffer', () {
    Map<String, dynamic> payload() => {
          'id': 3,
          'booking_id': 41,
          'state': 'offered',
          'rank': 0,
          'category_name': 'Emergency household assistance',
          'category_icon': 'emergency',
          'flat_label': 'A-301',
          'scheduled_date': '2026-08-09',
          'start_time': '21:40:00',
          'duration_minutes': 60,
          'quoted_price': 600,
          'notes': 'Blocked drain',
          'expires_at': '2026-08-09T21:50:00Z',
          'seconds_left': 480,
        };

    test('parses a dashboard card', () {
      final offer = EmergencyOffer.fromJson(payload());

      // The accept endpoint takes the booking id, not the offer id — the two
      // being different is exactly the kind of thing to pin down in a test.
      expect(offer.bookingId, 41);
      expect(offer.id, 3);
      expect(offer.state, OfferState.offered);
      expect(offer.isOpen, isTrue);
      expect(offer.startTimeLabel, '21:40');
      expect(offer.secondsLeft, 480);
    });

    test('losing the race is its own state, distinct from declining', () {
      final lost = EmergencyOffer.fromJson({...payload(), 'state': 'lost'});

      expect(lost.state, OfferState.lost);
      expect(lost.state, isNot(OfferState.declined));
      expect(lost.isOpen, isFalse);
    });
  });

  group('EmergencyLiveState', () {
    test('a worker with an open offer counts as live work', () {
      final state = EmergencyLiveState.fromJson({
        'role': 'worker',
        'offers': [
          {
            'id': 1,
            'booking_id': 2,
            'state': 'offered',
            'category_name': 'Emergency',
            'scheduled_date': '2026-08-09',
            'start_time': '10:00:00',
            'quoted_price': 500,
            'seconds_left': 120,
          }
        ],
        'version': 'a',
      });

      // Drives the poll interval: live work polls fast, nothing polls slowly.
      expect(state.hasLiveWork, isTrue);
      expect(state.offers.single.bookingId, 2);
    });

    test('a quiet dashboard does not, so polling stays idle', () {
      final quiet = EmergencyLiveState.fromJson({
        'role': 'worker',
        'offers': const [],
        'version': '',
      });

      expect(quiet.hasLiveWork, isFalse);
    });

    test('a resident whose request has been claimed is no longer live work', () {
      final claimed = EmergencyLiveState.fromJson({
        'role': 'resident',
        'requests': [
          {
            'id': 9,
            'status': 'confirmed',
            'scheduled_date': '2026-08-09',
            'start_time': '10:00:00',
            'quoted_price': 500,
            'is_emergency': true,
            'settlement': 'app',
          }
        ],
        'version': 'b',
      });

      // Somebody is coming, so there is nothing left to watch second by second.
      expect(claimed.hasLiveWork, isFalse);
      expect(claimed.requests.single.isEmergency, isTrue);
    });
  });

  group('SurchargeQuote', () {
    test('carries the fee and the cash caveat together', () {
      final quote = SurchargeQuote.fromJson({
        'surcharge_paise': 10000,
        'surcharge_rupees': 100,
        'lead_days': 0,
        'rationale': 'Raised for today.',
        'worker_fee_settlement': 'app',
        'worker_fee_note':
            'Their charge is separate and asked for once the job is done.',
      });

      expect(quote.rupees, 100);
      expect(quote.leadDays, 0);
      // The screen that collects payment A must be able to say what payment B
      // is, in the server's own words — a different amount, at a later moment.
      expect(quote.workerFeeNote, contains('separate'));
    });
  });
}
