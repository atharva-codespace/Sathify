import 'package:flutter_test/flutter_test.dart';
import 'package:sathify/features/administration/data/models/admin_models.dart';

/// Wire-format tests for Module 11 — Admin, Reporting & Complaints.
///
/// NOT YET EXECUTED — the Flutter SDK is not installed on this machine. Run
/// `flutter test` once it is.
///
/// The [Complaint] group carries the most weight. `hours_remaining` goes
/// negative once a deadline has passed, and `raised_by` decides whether the
/// withdraw button is offered at all — parsing either wrongly would show a
/// complaint as comfortably on time when it is a day late, or hand somebody
/// else's complaint to the wrong person to withdraw.
void main() {
  group('Complaint', () {
    Map<String, dynamic> payload({
      String status = 'open',
      double hoursRemaining = 6.5,
      bool isOpen = true,
    }) =>
        {
          'id': 12,
          'reference': 'CMP-202603-A1B2C3',
          'category': 'late_arrival',
          'category_display': 'Late or missed visit',
          'subject': 'Did not arrive on Tuesday',
          'description': 'No message, no replacement.',
          'photo_url': null,
          'priority': 'normal',
          'priority_display': 'Normal',
          'status': status,
          'status_display': 'Open',
          'raised_by': 7,
          'raised_by_name': 'Anita Desai',
          'about': 'Rahul Sharma',
          'against_worker': 3,
          'against_resident': null,
          'sla_due_at': '2026-03-14T13:00:00Z',
          'escalated_at': null,
          'first_response_at': null,
          'resolution': '',
          'resolved_at': null,
          'is_open': isOpen,
          'is_overdue': hoursRemaining < 0,
          'hours_remaining': hoursRemaining,
          'age_active_hours': 2.5,
          'payment_dispute': null,
          'created_at': '2026-03-14T09:00:00Z',
        };

    test('parses a complaint', () {
      final complaint = Complaint.fromJson(payload());

      expect(complaint.id, 12);
      expect(complaint.reference, 'CMP-202603-A1B2C3');
      expect(complaint.category, ComplaintCategory.lateArrival);
      expect(complaint.status, ComplaintStatus.open);
      expect(complaint.priority, ComplaintPriority.normal);
      expect(complaint.about, 'Rahul Sharma');
      expect(complaint.slaDueAt, isNotNull);
    });

    test('an overdue complaint reports negative hours remaining', () {
      // "12 hours over" is what somebody triaging a queue sorts by, so the sign
      // has to survive parsing rather than being clamped at zero.
      final complaint = Complaint.fromJson(payload(hoursRemaining: -12.0));

      expect(complaint.hoursRemaining, -12.0);
      expect(complaint.isOverdue, isTrue);
    });

    test('hours arrive as numbers even when the server sends strings', () {
      // DecimalField has bitten this codebase before: a float serialised as a
      // JSON string parses to null under `as num?` and renders as "0 h left" on
      // a complaint that is a day late.
      final json = payload()
        ..['hours_remaining'] = '-4.25'
        ..['age_active_hours'] = '9.5';
      final complaint = Complaint.fromJson(json);

      expect(complaint.hoursRemaining, -4.25);
      expect(complaint.ageActiveHours, 9.5);
    });

    test('carries the raiser id, not just their name', () {
      // Only the raiser may withdraw. Matching on display name would offer the
      // button to the wrong person the first time two residents share a name.
      expect(Complaint.fromJson(payload()).raisedById, 7);
    });

    test('an open complaint with no response is flagged as unanswered', () {
      // Unanswered is worse than unresolved, and it is the distinction an
      // administrator's queue needs most.
      expect(Complaint.fromJson(payload()).awaitingFirstResponse, isTrue);

      final answered = payload()..['first_response_at'] = '2026-03-14T10:00:00Z';
      expect(Complaint.fromJson(answered).awaitingFirstResponse, isFalse);
    });

    test('a closed complaint is never awaiting a response', () {
      final closed = payload(status: 'resolved', isOpen: false);
      expect(Complaint.fromJson(closed).awaitingFirstResponse, isFalse);
    });

    test('an escalation is visible on the model', () {
      final json = payload()..['escalated_at'] = '2026-03-15T09:00:00Z';
      expect(Complaint.fromJson(json).wasEscalated, isTrue);
    });

    test('a complaint opened from a payment dispute says so', () {
      // Module 8.6 routes disputes into this queue rather than a second one,
      // and the badge is what tells an administrator where it came from.
      final json = payload()..['payment_dispute'] = 4;
      final complaint = Complaint.fromJson(json);

      expect(complaint.cameFromPaymentDispute, isTrue);
      expect(complaint.paymentDisputeId, 4);
    });

    test('an unknown category parses rather than throwing', () {
      // Module 12.5 may add a category before the app ships. Losing the whole
      // queue over one unrecognised row is far worse than showing it as "other".
      final json = payload()..['category'] = 'something_new';
      expect(Complaint.fromJson(json).category, ComplaintCategory.other);
    });

    test('history parses when the detail endpoint supplies it', () {
      final json = payload()
        ..['updates'] = [
          {
            'id': 1,
            'note': 'Complaint raised: Did not arrive on Tuesday',
            'author_name': 'Anita Desai',
            'old_status': '',
            'new_status': 'open',
            'is_system': false,
            'is_internal': false,
            'created_at': '2026-03-14T09:00:00Z',
          },
          {
            'id': 2,
            'note': 'Escalated automatically.',
            'author_name': 'Sathify',
            'old_status': '',
            'new_status': '',
            'is_system': true,
            'is_internal': false,
            'created_at': '2026-03-15T09:00:00Z',
          },
        ];

      final complaint = Complaint.fromJson(json);

      expect(complaint.updates.length, 2);
      expect(complaint.updates.first.isTransition, isTrue);
      expect(complaint.updates.last.isSystem, isTrue);
      expect(complaint.updates.last.isTransition, isFalse);
    });
  });

  group('ComplaintStatus', () {
    test('every wire value round-trips', () {
      for (final status in ComplaintStatus.values) {
        expect(ComplaintStatus.fromWire(status.wireValue), status);
      }
    });

    test('matches the server on which statuses are closed', () {
      // Rejection and withdrawal count as closed: the administrator answered,
      // even though the answer was no. Disagreeing with the server here would
      // leave rejected complaints sitting in the "open" filter forever.
      expect(ComplaintStatus.open.isClosed, isFalse);
      expect(ComplaintStatus.inProgress.isClosed, isFalse);
      expect(ComplaintStatus.resolved.isClosed, isTrue);
      expect(ComplaintStatus.rejected.isClosed, isTrue);
      expect(ComplaintStatus.withdrawn.isClosed, isTrue);
    });
  });

  group('ComplaintCategory', () {
    test('every wire value round-trips', () {
      // These mirror the server's choices, which Module 12.5 classifies free
      // text into. A typo would silently file that whole category as "other".
      for (final category in ComplaintCategory.values) {
        expect(ComplaintCategory.fromWire(category.wireValue), category);
      }
    });
  });

  group('DirectoryWorker', () {
    Map<String, dynamic> payload({int ratingCount = 12}) => {
          'id': 3,
          'full_name': 'Rahul Sharma',
          'phone_number': '9800000002',
          'is_approved': true,
          'is_available': true,
          'services': ['Maid', 'Cook'],
          'years_of_experience': 4,
          'trust_score': 78.5,
          'average_rating': 4.4,
          'rating_count': ratingCount,
          'completed_engagements': 6,
          'open_complaints': 1,
          'joined_at': '2025-11-02T08:00:00Z',
        };

    test('parses a directory row', () {
      final worker = DirectoryWorker.fromJson(payload());

      expect(worker.fullName, 'Rahul Sharma');
      expect(worker.services, ['Maid', 'Cook']);
      expect(worker.trustScore, 78.5);
      expect(worker.openComplaints, 1);
    });

    test('an unrated worker is not treated as a low-scoring one', () {
      // Their score is zero because nothing has happened, not because they did
      // badly. Rendering that as "0" beside a genuinely poor score would cost a
      // new worker their first job.
      final unrated = DirectoryWorker.fromJson(payload(ratingCount: 0));

      expect(unrated.isRated, isFalse);
      expect(DirectoryWorker.fromJson(payload()).isRated, isTrue);
    });

    test('decimal scores survive arriving as strings', () {
      final json = payload()
        ..['trust_score'] = '78.50'
        ..['average_rating'] = '4.40';
      final worker = DirectoryWorker.fromJson(json);

      expect(worker.trustScore, 78.5);
      expect(worker.averageRating, 4.4);
    });
  });

  group('AdminReport', () {
    Map<String, dynamic> payload() => {
          'title': 'Complaint report',
          'society_name': 'Green Meadows',
          'period_start': '2026-03-01',
          'period_end': '2026-03-31',
          'period_label': '01 Mar 2026 – 31 Mar 2026',
          'columns': ['Raised', 'Reference', 'Status'],
          'rows': [
            ['14 Mar 2026', 'CMP-202603-A1B2C3', 'Open'],
          ],
          'summary': [
            {'label': 'Complaints raised', 'value': '1'},
            {'label': 'Still open', 'value': '1'},
          ],
          'row_count': 1,
        };

    test('parses the generic table', () {
      final report = AdminReport.fromJson(payload());

      expect(report.title, 'Complaint report');
      expect(report.columns.length, 3);
      expect(report.rows.first.last, 'Open');
      expect(report.summary.first.label, 'Complaints raised');
      expect(report.isEmpty, isFalse);
    });

    test('an empty report is empty rather than absent', () {
      final json = payload()
        ..['rows'] = []
        ..['row_count'] = 0;

      expect(AdminReport.fromJson(json).isEmpty, isTrue);
    });

    test('non-string cells are stringified rather than dropped', () {
      // The server sends counts as numbers in some columns. A cast would throw
      // and lose the whole report over one integer.
      final json = payload()
        ..['rows'] = [
          [1, null, true],
        ];
      final report = AdminReport.fromJson(json);

      expect(report.rows.first, ['1', '', 'true']);
    });
  });

  group('AdminDashboard', () {
    test('a brand-new society reports no data in every panel', () {
      // Rather than a chart of zeros somebody will read a shape into.
      final dashboard = AdminDashboard.fromJson({
        'period_start': '2026-03-01',
        'period_end': '2026-03-31',
        'sentiment': {'has_data': false},
        'trust': {'has_data': false},
        'complaints': {'has_data': false},
        'unmet_demand': {'has_data': false},
        'availability': {'has_data': false},
      });

      expect(dashboard.sentiment.hasData, isFalse);
      expect(dashboard.trust.hasData, isFalse);
      expect(dashboard.unmetDemand.hasData, isFalse);
      expect(dashboard.periodStart, isNotNull);
    });

    test('a missing panel parses as an empty one', () {
      // An older server that has not shipped a panel yet must not crash the
      // whole dashboard.
      final dashboard = AdminDashboard.fromJson({});

      expect(dashboard.availability.hasData, isFalse);
      expect(dashboard.trust.workers.total, 0);
    });

    test('trust buckets keep unrated subjects out of the bands', () {
      final panel = TrustPanel.fromJson({
        'has_data': true,
        'workers': {
          'total': 10,
          'rated': 4,
          'unrated': 6,
          'average': 72.0,
          'buckets': [
            {'label': '0–20', 'count': 0},
            {'label': '60–80', 'count': 4},
          ],
        },
        'residents': {'total': 0},
      });

      expect(panel.workers.unrated, 6);
      expect(
        panel.workers.buckets.fold<int>(0, (sum, b) => sum + b.count),
        4,
      );
    });

    test('SLA compliance is null rather than perfect over an empty set', () {
      // 100% of nothing is the most misleading figure a dashboard can print.
      final nothingClosed = ComplaintPanel.fromJson({
        'has_data': true,
        'raised': 3,
        'resolved_within_sla': 0,
        'resolved_late': 0,
      });
      expect(nothingClosed.slaComplianceRate, isNull);

      final someClosed = ComplaintPanel.fromJson({
        'has_data': true,
        'resolved_within_sla': 3,
        'resolved_late': 1,
      });
      expect(someClosed.slaComplianceRate, 0.75);
    });

    test('counted rows read either the category or the kind key', () {
      // by_category and by_kind have the same shape but different key names,
      // and both feed the same widget.
      expect(
        CountedCategory.fromJson({
          'category': 'safety',
          'label': 'Safety',
          'count': 2,
        }).key,
        'safety',
      );
      expect(
        CountedCategory.fromJson({
          'kind': 'no_match',
          'label': 'No worker was free',
          'count': 5,
        }).key,
        'no_match',
      );
    });
  });

  group('UnmetDemandEntry', () {
    test('parses a logged request', () {
      final entry = UnmetDemandEntry.fromJson({
        'id': 9,
        'kind': 'no_match',
        'kind_display': 'No worker was free for the requested slot',
        'service_label': 'Deep cleaning',
        'requested_date': '2026-03-20',
        'requested_time': '10:00:00',
        'detail': 'No worker was free for this slot.',
        'created_at': '2026-03-19T18:00:00Z',
      });

      expect(entry.serviceLabel, 'Deep cleaning');
      expect(entry.kind, 'no_match');
      expect(entry.requestedDate, isNotNull);
    });
  });
}
