import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:sathify/core/theme/app_theme.dart';
import 'package:sathify/features/attendance/data/models/work_session_models.dart';
import 'package:sathify/features/attendance/presentation/providers/work_session_provider.dart';
import 'package:sathify/features/attendance/presentation/screens/my_day_screen.dart';

/// Module 7.7 — the worker's Today screen.
///
/// Every test here pumps the screen **with `AppTheme.light`**, not a bare
/// `MaterialApp`. A default theme silently supplies its own text styles and
/// densities, so a screen that depends on the app's real typography can pass a
/// test and then render blank, clipped or overflowing on a device. The theme is
/// part of what is under test.
void main() {
  Map<String, dynamic> sessionJson({
    required String id,
    required String status,
    String source = 'self',
    int timePaise = 36000,
    int visitFeePaise = 6000,
    int billableMinutes = 180,
    bool needsReview = false,
  }) =>
      {
        'id': id,
        'engagement': 1,
        'visit_date': '2026-08-13',
        'started_at': '2026-08-13T03:30:00Z',
        'ended_at': status == 'open' ? null : '2026-08-13T06:30:00Z',
        'source': source,
        'status': status,
        'needs_review': needsReview,
        'review_note': '',
        'approved_ot_minutes': 0,
        'billable_minutes': billableMinutes,
        'overtime_minutes': 0,
        'unbilled_extra_minutes': 0,
        'time_paise': timePaise,
        'overtime_paise': 0,
        'visit_fee_paise': visitFeePaise,
        'total_paise': timePaise + visitFeePaise,
        'priced_at': status == 'open' ? null : '2026-08-13T06:31:00Z',
        'scheduled_start': '09:00:00',
        'scheduled_end': '12:00:00',
        'flat': 'A-102',
        'resident_name': 'Anita Desai',
        'worker_name': 'Sunita Devi',
        'can_request_overtime': status == 'open',
      };

  Map<String, dynamic> card({
    required int engagementId,
    required String flat,
    Map<String, dynamic>? session,
    String start = '09:00:00',
    String end = '12:00:00',
  }) =>
      {
        'engagement': engagementId,
        'flat': flat,
        'resident_name': 'Anita Desai',
        'scheduled_start': start,
        'scheduled_end': end,
        'is_hourly': true,
        'hourly_rate': 120,
        'visit_fee': 60,
        'session': session,
      };

  TodayBoard board({
    required List<Map<String, dynamic>> cards,
    int earnedPaise = 0,
    int billedMinutes = 0,
    int flatsDone = 0,
  }) =>
      TodayBoard.fromJson({
        'date': '2026-08-13',
        'earned_paise': earnedPaise,
        'billed_minutes': billedMinutes,
        'flats_total': cards.length,
        'flats_done': flatsDone,
        'cards': cards,
      });

  Future<void> pump(WidgetTester tester, TodayBoard data) async {
    await tester.pumpWidget(
      ProviderScope(
        overrides: [
          todayBoardProvider.overrideWith((ref) async => data),
        ],
        child: MaterialApp(
          theme: AppTheme.light,
          home: const MyDayScreen(),
        ),
      ),
    );
    await tester.pumpAndSettle();
  }

  group('the day is a stack of flats', () {
    testWidgets('one card per home scheduled today', (tester) async {
      await pump(
        tester,
        board(cards: [
          card(engagementId: 1, flat: 'A-102'),
          card(engagementId: 2, flat: 'B-704', start: '13:00:00'),
        ],),
      );

      expect(find.text('A-102'), findsOneWidget);
      expect(find.text('B-704'), findsOneWidget);
      expect(find.text('Start work'), findsNWidgets(2));
    });

    testWidgets('only one visit can be started at a time', (tester) async {
      // A tall viewport on purpose: the disabled-state hint is the last widget
      // on the screen, and a lazy ListView simply never builds what is below
      // the fold — the assertion would fail for a reason that has nothing to do
      // with the behaviour under test.
      tester.view.physicalSize = const Size(400, 1400);
      tester.view.devicePixelRatio = 1.0;
      addTearDown(tester.view.resetPhysicalSize);
      addTearDown(tester.view.resetDevicePixelRatio);

      // Two overlapping open sessions would produce two spans for the same
      // hour and a bill neither household could be shown the working for.
      await pump(
        tester,
        board(cards: [
          card(
            engagementId: 1,
            flat: 'A-102',
            session: sessionJson(id: 'a', status: 'open'),
          ),
          card(engagementId: 2, flat: 'B-704', start: '13:00:00'),
        ],),
      );

      expect(find.text('Stop work'), findsOneWidget);
      expect(find.text('Finish the home you are in first.'), findsOneWidget);

      final startButton = tester.widget<ElevatedButton>(
        find.ancestor(
          of: find.text('Start work'),
          matching: find.byType(ElevatedButton),
        ),
      );
      expect(startButton.onPressed, isNull);
    });

    testWidgets('an empty day says so rather than showing a blank list',
        (tester) async {
      await pump(tester, board(cards: []));
      expect(find.text('Nothing scheduled today'), findsOneWidget);
    });
  });

  group('earnings are visible', () {
    testWidgets('the running total includes the visit fee', (tester) async {
      await pump(
        tester,
        board(
          cards: [
            card(
              engagementId: 1,
              flat: 'A-102',
              session: sessionJson(id: 'a', status: 'closed'),
            ),
          ],
          // 3h at ₹120 = ₹360, plus the ₹60 visit fee.
          earnedPaise: 42000,
          billedMinutes: 180,
          flatsDone: 1,
        ),
      );

      expect(find.text('₹420.00'), findsOneWidget);
      expect(find.text('1 of 1 homes done'), findsOneWidget);
    });

    testWidgets('a finished card names the fee separately from the work',
        (tester) async {
      // Folding the fee into one total would leave her unable to check it
      // against the number she was told it would be.
      await pump(
        tester,
        board(cards: [
          card(
            engagementId: 1,
            flat: 'A-102',
            session: sessionJson(id: 'a', status: 'closed'),
          ),
        ],),
      );

      expect(
        find.text('₹360.00 work  +  ₹60.00 visit  =  ₹420.00'),
        findsOneWidget,
      );
    });
  });

  group('an auto-closed day is a question, not a correction', () {
    testWidgets('she is asked yes/no about her own day', (tester) async {
      await pump(
        tester,
        board(cards: [
          card(
            engagementId: 1,
            flat: 'A-102',
            session: sessionJson(
              id: 'a',
              status: 'auto_closed',
              needsReview: true,
            ),
          ),
        ],),
      );

      expect(find.textContaining('you did not tap Stop'), findsOneWidget);
      expect(find.text('Yes, correct'), findsOneWidget);
      expect(find.text('No, check it'), findsOneWidget);
      // She is never asked to compute what she is owed — only to say whether
      // the record matches her day.
      expect(find.textContaining('₹420.00'), findsWidgets);
    });

    testWidgets('a normal closed day is not flagged', (tester) async {
      await pump(
        tester,
        board(cards: [
          card(
            engagementId: 1,
            flat: 'A-102',
            session: sessionJson(id: 'a', status: 'closed'),
          ),
        ],),
      );
      expect(find.text('Yes, correct'), findsNothing);
    });
  });

  group('nothing renders off-screen at a small size', () {
    testWidgets('a full day fits a 360x640 phone without overflow',
        (tester) async {
      // The smallest screen the app targets. Overflow here is the failure the
      // theme-less test would have missed.
      tester.view.physicalSize = const Size(360, 640);
      tester.view.devicePixelRatio = 1.0;
      addTearDown(tester.view.resetPhysicalSize);
      addTearDown(tester.view.resetDevicePixelRatio);

      await pump(
        tester,
        board(
          cards: [
            card(
              engagementId: 1,
              flat: 'A-102',
              session: sessionJson(id: 'a', status: 'closed'),
            ),
            card(
              engagementId: 2,
              flat: 'B-704',
              start: '13:00:00',
              session: sessionJson(id: 'b', status: 'open'),
            ),
            card(engagementId: 3, flat: 'C-201', start: '15:00:00'),
          ],
          earnedPaise: 42000,
          billedMinutes: 180,
          flatsDone: 1,
        ),
      );

      expect(tester.takeException(), isNull);
    });
  });
}
