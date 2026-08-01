import 'package:flutter_test/flutter_test.dart';
import 'package:sathify/features/hiring/data/models/hiring_models.dart';

/// Wire-format tests for Module 4 — Discovery & Hiring.
///
/// NOT YET EXECUTED — the Flutter SDK is not installed on this machine. Run
/// `flutter test` once it is.
///
/// The weekday group carries the most weight here: the server counts Monday as
/// 0 and Dart counts it as 1, so an off-by-one would silently schedule every
/// engagement on the wrong day rather than failing outright.
void main() {
  group('Weekday', () {
    test('converts a Dart DateTime to the server convention', () {
      // 3 August 2026 is a Monday.
      expect(Weekday.fromDateTime(DateTime(2026, 8, 3)), Weekday.monday);
      expect(Weekday.fromDateTime(DateTime(2026, 8, 3)).wireValue, 0);
    });

    test('maps Sunday to 6, not 0 or 7', () {
      expect(Weekday.fromDateTime(DateTime(2026, 8, 9)), Weekday.sunday);
      expect(Weekday.fromDateTime(DateTime(2026, 8, 9)).wireValue, 6);
    });

    test('every Dart weekday maps to a distinct wire value', () {
      final wireValues = List.generate(7, (offset) {
        final day = DateTime(2026, 8, 3).add(Duration(days: offset));
        return Weekday.fromDateTime(day).wireValue;
      });

      expect(wireValues, [0, 1, 2, 3, 4, 5, 6]);
    });

    test('fromWire round-trips', () {
      for (final day in Weekday.values) {
        expect(Weekday.fromWire(day.wireValue), day);
      }
    });

    test('labels a day list in week order regardless of input order', () {
      expect(Weekday.labelFor([4, 0, 2]), 'Mon, Wed, Fri');
    });

    test('falls back rather than throwing on an unknown wire value', () {
      expect(Weekday.fromWire(99), Weekday.monday);
    });
  });

  group('numeric parsing', () {
    test('accepts a plain number', () {
      expect(toDoubleOrZero(4.5), 4.5);
      expect(toDoubleOrZero(70), 70.0);
    });

    test('accepts a numeric string, as DRF renders DecimalField by default', () {
      // The server is configured to send these as numbers, but a regression
      // there must degrade the value visibly rather than zero it silently.
      expect(toDoubleOrZero('4.50'), 4.5);
      expect(toDoubleOrZero('70.00'), 70.0);
    });

    test('falls back to zero on rubbish rather than throwing', () {
      expect(toDoubleOrZero(null), 0);
      expect(toDoubleOrZero('not a number'), 0);
    });

    test('toDoubleOrNull keeps absent distinct from zero', () {
      expect(toDoubleOrNull(null), isNull);
      expect(toDoubleOrNull(0), 0.0);
      expect(toDoubleOrNull('0.75'), 0.75);
    });
  });

  group('formatWireTime', () {
    test('trims seconds off the wire format', () {
      expect(formatWireTime('09:30:00'), '09:30');
    });

    test('leaves an empty or null value empty', () {
      expect(formatWireTime(null), '');
      expect(formatWireTime(''), '');
    });
  });

  group('WorkerSearchResult', () {
    Map<String, dynamic> payload() => {
          'id': 7,
          'full_name': 'Rahul Sharma',
          'photo': 'https://example.test/p.jpg',
          'service_types': [
            {'id': 1, 'name': 'Maid', 'slug': 'maid', 'icon': 'cleaning'},
          ],
          'years_of_experience': 4,
          'languages_spoken': 'Hindi, Marathi',
          'expected_monthly_rate': 4000,
          'available_from': '08:00:00',
          'available_until': '18:00:00',
          'average_rating': 4.5,
          'trust_score': 72.5,
          'completed_engagements': 12,
          'engagement_count': 3,
          'match_percentage': 88,
        };

    test('parses a search row', () {
      final worker = WorkerSearchResult.fromJson(payload());

      expect(worker.id, 7);
      expect(worker.fullName, 'Rahul Sharma');
      expect(worker.serviceTypes.single.name, 'Maid');
      expect(worker.averageRating, 4.5);
      expect(worker.trustScore, 72.5);
      expect(worker.matchPercentage, 88);
      expect(worker.availabilityLabel, '08:00 – 18:00');
    });

    test('treats an unrated worker as new rather than zero-rated', () {
      final worker =
          WorkerSearchResult.fromJson({...payload(), 'average_rating': 0});
      expect(worker.hasRating, isFalse);
    });

    test('keeps a null rate null instead of defaulting it to zero', () {
      final worker = WorkerSearchResult.fromJson(
          {...payload(), 'expected_monthly_rate': null},);
      expect(worker.expectedMonthlyRate, isNull);
    });

    test('parses scores sent as strings without collapsing them to zero', () {
      final worker = WorkerSearchResult.fromJson(
          {...payload(), 'average_rating': '4.50', 'trust_score': '72.50'},);

      expect(worker.averageRating, 4.5);
      expect(worker.trustScore, 72.5);
      expect(worker.hasRating, isTrue);
    });

    test('availability label is empty when the worker declared no hours', () {
      final worker = WorkerSearchResult.fromJson(
          {...payload(), 'available_from': null, 'available_until': null},);
      expect(worker.availabilityLabel, '');
    });
  });

  group('MatchComponent', () {
    test('parses a breakdown row', () {
      final component = MatchComponent.fromJson({
        'key': 'trust',
        'label': 'Trust score',
        'weight': 0.3,
        'score': 0.725,
        'contribution': 0.2175,
        'raw': 72.5,
      });

      expect(component.key, 'trust');
      expect(component.scorePercentage, 73);
      expect(component.raw, 72.5);
    });

    test('keeps a null raw null — no history is not the same as zero', () {
      final component = MatchComponent.fromJson({
        'key': 'response_rate',
        'label': 'Responds to requests',
        'weight': 0.15,
        'score': 0.8,
        'contribution': 0.12,
        'raw': null,
      });

      expect(component.raw, isNull);
    });
  });

  group('WorkerDetail', () {
    test('parses the profile payload including nested verification', () {
      final detail = WorkerDetail.fromJson({
        'id': 7,
        'full_name': 'Rahul Sharma',
        'bio': 'Ten years in this area.',
        'service_types': const [],
        'average_rating': 4.2,
        'response_rate': 0.75,
        'verification': {
          'is_approved': true,
          'id_verified': true,
          'id_masked': 'XXXX XXXX 9012',
        },
        'match_percentage': 91,
        'match_breakdown': [
          {
            'key': 'trust',
            'label': 'Trust score',
            'weight': 0.3,
            'score': 0.7,
            'contribution': 0.21,
            'raw': 70,
          },
        ],
      });

      expect(detail.id, 7);
      expect(detail.bio, 'Ten years in this area.');
      expect(detail.verification.isApproved, isTrue);
      expect(detail.verification.idMasked, 'XXXX XXXX 9012');
      expect(detail.responseRate, 0.75);
      expect(detail.matchBreakdown.single.key, 'trust');
      expect(detail.summary.matchPercentage, 91);
    });

    test('a worker with no request history has a null response rate', () {
      final detail = WorkerDetail.fromJson({
        'id': 1,
        'full_name': 'New Worker',
        'service_types': const [],
        'response_rate': null,
        'verification': const {},
      });

      expect(detail.responseRate, isNull);
      expect(detail.verification.isApproved, isFalse);
    });
  });

  group('RecurringTerms', () {
    test('round-trips through the wire format', () {
      const terms = RecurringTerms(
        daysOfWeek: [0, 2, 4],
        startTime: '09:00',
        expectedDurationMinutes: 90,
        monthlyRate: 4500,
      );

      final parsed = RecurringTerms.fromJson(terms.toJson());

      expect(parsed.daysOfWeek, [0, 2, 4]);
      expect(parsed.startTime, '09:00');
      expect(parsed.expectedDurationMinutes, 90);
      expect(parsed.monthlyRate, 4500);
    });

    test('renders a readable schedule', () {
      const terms = RecurringTerms(
        daysOfWeek: [0, 2, 4],
        startTime: '09:00:00',
        monthlyRate: 4000,
      );

      expect(terms.scheduleLabel, 'Mon, Wed, Fri at 09:00');
    });
  });

  group('HireRequest', () {
    Map<String, dynamic> payload() => {
          'id': 11,
          'worker': 7,
          'worker_name': 'Rahul Sharma',
          'resident_name': 'Anita Desai',
          'resident_flat': 'A-301',
          'service_type': {'id': 1, 'name': 'Maid', 'slug': 'maid'},
          'days_of_week': [0, 1, 2, 3, 4],
          'start_time': '09:00:00',
          'expected_duration_minutes': 60,
          'monthly_rate': 4000,
          'message': 'Weekday mornings please.',
          'status': 'pending',
          'is_actionable': true,
          'expires_at':
              DateTime.now().add(const Duration(hours: 10)).toIso8601String(),
          'created_at': DateTime.now().toIso8601String(),
        };

    test('parses a request', () {
      final request = HireRequest.fromJson(payload());

      expect(request.id, 11);
      expect(request.workerName, 'Rahul Sharma');
      expect(request.status, HireRequestStatus.pending);
      expect(request.isPending, isTrue);
      expect(request.serviceType?.name, 'Maid');
      expect(request.terms.daysLabel, 'Mon, Tue, Wed, Thu, Fri');
    });

    test('reports the hours left to respond', () {
      final request = HireRequest.fromJson(payload());
      expect(request.hoursRemaining, inInclusiveRange(8, 10));
    });

    test('an unanswerable request reports no time remaining', () {
      final request =
          HireRequest.fromJson({...payload(), 'is_actionable': false});
      expect(request.hoursRemaining, isNull);
    });

    test('never reports negative hours for a lapsed request', () {
      final request = HireRequest.fromJson({
        ...payload(),
        'expires_at':
            DateTime.now().subtract(const Duration(hours: 5)).toIso8601String(),
      });

      expect(request.hoursRemaining, 0);
    });

    test('carries the engagement id once accepted', () {
      final request = HireRequest.fromJson({
        ...payload(),
        'status': 'accepted',
        'is_actionable': false,
        'engagement_id': 42,
      });

      expect(request.status, HireRequestStatus.accepted);
      expect(request.engagementId, 42);
    });

    test('falls back rather than throwing on an unknown status', () {
      final request =
          HireRequest.fromJson({...payload(), 'status': 'something_new'});
      expect(request.status, HireRequestStatus.pending);
    });
  });

  group('Engagement', () {
    Map<String, dynamic> payload() => {
          'id': 5,
          'worker': 7,
          'worker_name': 'Rahul Sharma',
          'worker_phone': '9800000002',
          'resident_name': 'Anita Desai',
          'resident_flat': 'A-301',
          'service_type': {'id': 1, 'name': 'Maid', 'slug': 'maid'},
          'days_of_week': [0, 2, 4],
          'start_time': '09:00:00',
          'expected_duration_minutes': 60,
          'monthly_rate': 4000,
          'status': 'active',
          'started_on': '2026-08-01',
        };

    test('parses an engagement', () {
      final engagement = Engagement.fromJson(payload());

      expect(engagement.id, 5);
      expect(engagement.status, EngagementStatus.active);
      expect(engagement.isActive, isTrue);
      expect(engagement.isLive, isTrue);
      expect(engagement.workerPhone, '9800000002');
    });

    test('expects a visit on a scheduled weekday', () {
      final engagement = Engagement.fromJson(payload());
      // Monday 3 August 2026 is in [0, 2, 4].
      expect(engagement.occursOn(DateTime(2026, 8, 3)), isTrue);
      // Tuesday is not.
      expect(engagement.occursOn(DateTime(2026, 8, 4)), isFalse);
    });

    test('a paused engagement expects no visits at all', () {
      final engagement =
          Engagement.fromJson({...payload(), 'status': 'paused'});

      expect(engagement.isPaused, isTrue);
      expect(engagement.isLive, isTrue, reason: 'the worker is still expected back');
      expect(engagement.occursOn(DateTime(2026, 8, 3)), isFalse);
    });

    test('a terminated engagement is neither active nor live', () {
      final engagement =
          Engagement.fromJson({...payload(), 'status': 'terminated'});

      expect(engagement.isActive, isFalse);
      expect(engagement.isLive, isFalse);
    });
  });

  group('WorkerSearchFilters', () {
    test('omits defaults from the query so the URL stays clean', () {
      expect(const WorkerSearchFilters().toQuery(), isEmpty);
    });

    test('serialises the filters that are set', () {
      const filters = WorkerSearchFilters(
        query: 'Rahul',
        serviceTypeId: 2,
        maxRate: 5000,
        minRating: 4,
        availableFrom: '08:00',
        availableUntil: '12:00',
        strictAvailability: true,
        sort: 'rating',
      );

      expect(filters.toQuery(), {
        'q': 'Rahul',
        'service_type': 2,
        'max_rate': 5000,
        'min_rating': 4.0,
        'available_from': '08:00',
        'available_until': '12:00',
        'strict_availability': 'true',
        'sort': 'rating',
      });
    });

    test('compares by value so the provider refetches only on real changes', () {
      const a = WorkerSearchFilters(query: 'x', serviceTypeId: 1);
      const b = WorkerSearchFilters(query: 'x', serviceTypeId: 1);
      const c = WorkerSearchFilters(query: 'y', serviceTypeId: 1);

      expect(a, equals(b));
      expect(a.hashCode, b.hashCode);
      expect(a, isNot(equals(c)));
    });

    test('clearServiceType removes the filter rather than keeping the old id', () {
      const filters = WorkerSearchFilters(serviceTypeId: 3);
      expect(filters.copyWith(clearServiceType: true).serviceTypeId, isNull);
    });
  });
}
