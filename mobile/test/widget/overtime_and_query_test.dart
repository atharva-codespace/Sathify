import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:sathify/core/theme/app_theme.dart';
import 'package:sathify/features/attendance/data/models/work_session_models.dart';
import 'package:sathify/features/attendance/presentation/widgets/overtime_approval_sheet.dart';
import 'package:sathify/features/attendance/presentation/widgets/raise_query_sheet.dart';

/// The two flows that had a repository method and no way to reach it.
///
/// Both are pumped with `AppTheme.light` rather than a bare `MaterialApp`, for
/// the reason the other widget suites state: a default theme supplies its own
/// typography, so a screen can pass a test and then clip on a device.
void main() {
  WorkSession session({int approvedOt = 0}) => WorkSession.fromJson({
        'id': 'session-1',
        'engagement': 1,
        'visit_date': '2026-08-14',
        'started_at': '2026-08-14T03:30:00Z',
        'ended_at': null,
        'source': 'self',
        'status': 'open',
        'needs_review': false,
        'review_note': '',
        'approved_ot_minutes': approvedOt,
        'billable_minutes': 0,
        'overtime_minutes': 0,
        'unbilled_extra_minutes': 0,
        'time_paise': 0,
        'overtime_paise': 0,
        'visit_fee_paise': 0,
        'total_paise': 0,
        'priced_at': null,
        'scheduled_start': '09:00:00',
        'scheduled_end': '12:00:00',
        'flat': 'A-102',
        'resident_name': 'Anita Desai',
        'worker_name': 'Sunita Devi',
        'can_request_overtime': true,
      });

  Future<void> pump(WidgetTester tester, Widget child) async {
    tester.view.physicalSize = const Size(400, 1400);
    tester.view.devicePixelRatio = 1.0;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);

    await tester.pumpWidget(
      ProviderScope(
        child: MaterialApp(
          theme: AppTheme.light,
          home: Scaffold(body: child),
        ),
      ),
    );
    await tester.pumpAndSettle();
  }

  group('the resident can answer a request for extra time', () {
    testWidgets('the approval offers a decision, not just an acknowledgement',
        (tester) async {
      await pump(tester, OvertimeApprovalSheet(session: session()));

      expect(find.textContaining('wants to stay longer'), findsOneWidget);
      expect(find.text('Approve 30 minutes'), findsOneWidget);
      // Declining has to be as easy as approving, or "no" becomes silence and
      // she works unpaid time believing it was allowed.
      expect(find.text('No, thank you'), findsOneWidget);
    });

    testWidgets('the total is shown before the tap', (tester) async {
      await pump(
        tester,
        OvertimeApprovalSheet(session: session(), hourlyRate: 120),
      );
      // 30 min at ₹120/hr.
      expect(find.text('+ ₹60.00'), findsOneWidget);
    });

    testWidgets('and it says no second visit fee applies', (tester) async {
      await pump(
        tester,
        OvertimeApprovalSheet(session: session(), hourlyRate: 120),
      );
      expect(
        find.textContaining('No second visit fee'),
        findsOneWidget,
      );
    });

    testWidgets('doing nothing is stated as costing her the time',
        (tester) async {
      await pump(tester, OvertimeApprovalSheet(session: session()));
      expect(
        find.textContaining('If you do nothing, she is not charged'),
        findsOneWidget,
      );
    });

    testWidgets('an existing approval is the starting point', (tester) async {
      await pump(
        tester,
        OvertimeApprovalSheet(session: session(approvedOt: 60)),
      );
      expect(find.text('Approve 60 minutes'), findsOneWidget);
    });

    testWidgets('the money preview is hidden rather than guessed at',
        (tester) async {
      await pump(tester, OvertimeApprovalSheet(session: session()));
      expect(find.textContaining('+ ₹'), findsNothing);
    });
  });

  group('the query sheet publishes the ladder before the button', () {
    const sheet = RaiseQuerySheet(
      invoiceId: 7,
      sessionId: 'session-1',
      workerName: 'Sunita Devi',
      amountPaise: 24000,
    );

    testWidgets('all three stages are on the screen', (tester) async {
      await pump(tester, sheet);
      expect(find.textContaining('You both see the same record'), findsOneWidget);
      expect(find.textContaining('can agree in one tap'), findsOneWidget);
      expect(find.textContaining('society admin decides'), findsOneWidget);
    });

    testWidgets('it keeps the platform out of the facts', (tester) async {
      await pump(tester, sheet);
      expect(find.textContaining('Sathify does not'), findsOneWidget);
    });

    testWidgets('it says only this amount waits', (tester) async {
      // The sentence that makes raising a query safe to do at all.
      await pump(tester, sheet);
      expect(
        find.textContaining('She is paid the rest of the month now'),
        findsOneWidget,
      );
    });

    testWidgets('the reasons are the server\'s own vocabulary', (tester) async {
      await pump(tester, sheet);
      expect(find.text('The times are wrong'), findsOneWidget);
      expect(find.text('She did not come'), findsOneWidget);
      expect(find.text('The amount is wrong'), findsOneWidget);
    });

    testWidgets('a description is optional, not demanded', (tester) async {
      await pump(tester, sheet);
      expect(find.textContaining('(optional)'), findsOneWidget);
      expect(find.text('Send query'), findsOneWidget);
    });
  });

  group('nothing overflows on a small phone', () {
    testWidgets('the approval sheet fits 360x640', (tester) async {
      tester.view.physicalSize = const Size(360, 640);
      tester.view.devicePixelRatio = 1.0;
      addTearDown(tester.view.resetPhysicalSize);
      addTearDown(tester.view.resetDevicePixelRatio);

      await tester.pumpWidget(
        ProviderScope(
          child: MaterialApp(
            theme: AppTheme.light,
            home: Scaffold(
              body: OvertimeApprovalSheet(session: session(), hourlyRate: 120),
            ),
          ),
        ),
      );
      await tester.pumpAndSettle();
      expect(tester.takeException(), isNull);
    });
  });
}
