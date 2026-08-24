import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:sathify/core/theme/app_theme.dart';
import 'package:sathify/features/attendance/data/models/work_session_models.dart';
import 'package:sathify/features/attendance/presentation/providers/work_session_provider.dart';
import 'package:sathify/features/attendance/presentation/screens/session_detail_screen.dart';
import 'package:sathify/features/payments/presentation/screens/invoice_screen.dart';

/// The resident's side: one visit, and the month's bill.
///
/// The assertions here are mostly about *sentences*, which is unusual for
/// widget tests and deliberate. Two specific paragraphs do more to prevent
/// disputes than any layout on these screens:
///
///  * "There is no late fee and no penalty on top" — both parties assume a
///    short day was fined. It never was.
///  * "the rest of the bill is unaffected" — a worker who thinks a query
///    freezes her month will make sure no query is ever raised.
///
/// If someone deletes either while tidying copy, these fail.
void main() {
  Map<String, dynamic> sessionJson({
    int billableMinutes = 90,
    int timePaise = 18000,
    int visitFeePaise = 6000,
    int unbilledExtra = 0,
    String source = 'self',
  }) =>
      {
        'id': 'session-1',
        'engagement': 1,
        'visit_date': '2026-08-08',
        'started_at': '2026-08-08T06:22:00Z',
        'ended_at': '2026-08-08T08:00:00Z',
        'source': source,
        'status': 'closed',
        'needs_review': false,
        'review_note': '',
        'approved_ot_minutes': 0,
        'billable_minutes': billableMinutes,
        'overtime_minutes': 0,
        'unbilled_extra_minutes': unbilledExtra,
        'time_paise': timePaise,
        'overtime_paise': 0,
        'visit_fee_paise': visitFeePaise,
        'total_paise': timePaise + visitFeePaise,
        'priced_at': '2026-08-08T08:01:00Z',
        // Scheduled 11:30-13:30 = 120 minutes; she was billed 90.
        'scheduled_start': '11:30:00',
        'scheduled_end': '13:30:00',
        'flat': 'A-102',
        'resident_name': 'Anita Desai',
        'worker_name': 'Sunita Devi',
        'can_request_overtime': false,
      };

  Future<void> pumpSession(
    WidgetTester tester,
    Map<String, dynamic> json,
  ) async {
    // Tall on purpose. The provenance block and the unbilled-time note are the
    // last things on this screen, and a lazy ListView never builds what is
    // below the fold — an assertion about them would otherwise fail for
    // reasons that have nothing to do with the screen being wrong.
    tester.view.physicalSize = const Size(400, 1600);
    tester.view.devicePixelRatio = 1.0;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);

    final session = WorkSession.fromJson(json);
    await tester.pumpWidget(
      ProviderScope(
        overrides: [
          sessionHistoryProvider.overrideWith((ref, arg) async => [session]),
        ],
        child: MaterialApp(
          theme: AppTheme.light,
          home: const SessionDetailScreen(sessionId: 'session-1'),
        ),
      ),
    );
    await tester.pumpAndSettle();
  }

  Map<String, dynamic> invoiceJson({
    int heldPaise = 0,
    int unbilledExtra = 0,
  }) =>
      {
        'id': 7,
        'number': 'INV-1-2608',
        'status': 'review',
        'in_review': true,
        'period_start': '2026-07-16',
        'period_end': '2026-08-15',
        'review_closes_at': null,
        'issued_at': null,
        'settled_at': null,
        'worker_name': 'Sunita Devi',
        'flat': 'A-102',
        'payment': null,
        'time_paise': 495000,
        'overtime_paise': 12000,
        'visit_fee_paise': 156000,
        'adjustment_paise': 0,
        'held_paise': heldPaise,
        'total_paise': 663000,
        'payable_paise': 663000 - heldPaise,
        'total_display': '₹6,630.00',
        'payable_display': '₹${((663000 - heldPaise) / 100).toStringAsFixed(2)}',
        'unbilled_extra_minutes': unbilledExtra,
        'days': {'billed': 26},
        'lines': [
          {
            'id': 1,
            'kind': 'time',
            'description': '08 Aug — time worked',
            'minutes': 90,
            'amount_paise': 18000,
            'amount_display': '₹180.00',
            'is_held': heldPaise > 0,
            'session': 'session-1',
            'query': null,
          },
          {
            'id': 2,
            'kind': 'visit_fee',
            'description': '08 Aug — visit fee',
            'minutes': 0,
            'amount_paise': 6000,
            'amount_display': '₹60.00',
            'is_held': heldPaise > 0,
            'session': 'session-1',
            'query': null,
          },
        ],
      };

  Future<void> pumpInvoice(
    WidgetTester tester,
    Map<String, dynamic> json,
  ) async {
    tester.view.physicalSize = const Size(400, 1600);
    tester.view.devicePixelRatio = 1.0;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);

    await tester.pumpWidget(
      ProviderScope(
        overrides: [
          invoiceProvider.overrideWith((ref, arg) async => Invoice.fromJson(json)),
        ],
        child: MaterialApp(
          theme: AppTheme.light,
          home: const InvoiceScreen(invoiceId: 7),
        ),
      ),
    );
    await tester.pumpAndSettle();
  }

  group('a short day is explained, not just displayed', () {
    testWidgets('it says there is no penalty, in words', (tester) async {
      await pumpSession(tester, sessionJson());

      expect(find.text('Short day'), findsOneWidget);
      expect(
        find.textContaining('no late fee and no penalty on top'),
        findsOneWidget,
      );
    });

    testWidgets('the difference is named as time not worked', (tester) async {
      // 120 scheduled minus 90 billed = 30 minutes; ₹120/hr x 0.5h = ₹60.
      await pumpSession(tester, sessionJson());
      expect(find.textContaining('30 minutes that were not worked'),
          findsOneWidget,);
    });

    testWidgets('a full day carries no penalty note at all', (tester) async {
      await pumpSession(
        tester,
        sessionJson(billableMinutes: 120, timePaise: 24000),
      );
      expect(find.text('Full day'), findsOneWidget);
      expect(find.textContaining('no late fee'), findsNothing);
    });
  });

  group('the visit fee is always its own line', () {
    testWidgets('on a visit', (tester) async {
      await pumpSession(tester, sessionJson());
      expect(find.text('Visit fee'), findsOneWidget);
      expect(find.text('₹60.00'), findsWidgets);
    });

    testWidgets('on the bill', (tester) async {
      await pumpInvoice(tester, invoiceJson());
      expect(find.text('08 Aug — visit fee'), findsOneWidget);
      expect(
        find.textContaining('covers her travel and the time your slot commits'),
        findsOneWidget,
      );
    });
  });

  group('a query holds one line, not the month', () {
    testWidgets('the bill says the rest is unaffected', (tester) async {
      await pumpInvoice(tester, invoiceJson(heldPaise: 24000));

      expect(find.textContaining('is being checked'), findsWidgets);
      expect(
        find.textContaining('is unaffected and Sunita is paid it on time'),
        findsOneWidget,
      );
    });

    testWidgets('with nothing queried there is no hold notice', (tester) async {
      await pumpInvoice(tester, invoiceJson());
      expect(find.textContaining('is unaffected and'), findsNothing);
    });
  });

  group('goodwill is shown rather than quietly absorbed', () {
    testWidgets('unbilled extra time appears on the visit', (tester) async {
      await pumpSession(tester, sessionJson(unbilledExtra: 11));
      expect(
        find.textContaining('11 minutes past the scheduled finish'),
        findsOneWidget,
      );
    });

    testWidgets('and on the bill', (tester) async {
      await pumpInvoice(tester, invoiceJson(unbilledExtra: 38));
      expect(
        find.textContaining('38 minutes of extra time that was not approved'),
        findsOneWidget,
      );
    });
  });

  group('the platform takes nothing from wages, and says so', () {
    testWidgets('the bill states it', (tester) async {
      await pumpInvoice(tester, invoiceJson());
      expect(
        find.textContaining('Sathify takes no fee from wages'),
        findsOneWidget,
      );
    });
  });

  group('provenance is visible', () {
    testWidgets('a derived visit warns that it was inferred', (tester) async {
      await pumpSession(tester, sessionJson(source: 'derived'));
      expect(find.text('Tier 4'), findsOneWidget);
      expect(
        find.textContaining('worked out rather than observed'),
        findsOneWidget,
      );
    });

    testWidgets('an observed visit does not', (tester) async {
      await pumpSession(tester, sessionJson());
      expect(find.text('Tier 1'), findsOneWidget);
      expect(find.textContaining('worked out rather than observed'),
          findsNothing,);
    });
  });
}
