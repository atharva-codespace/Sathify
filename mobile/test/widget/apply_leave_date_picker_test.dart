import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:sathify/features/hiring/data/models/hiring_models.dart';
import 'package:sathify/features/hiring/presentation/providers/hiring_provider.dart';
import 'package:sathify/features/scheduling/presentation/screens/apply_leave_screen.dart';

/// Module 6.5 — which dates the leave picker actually lets a worker choose.
///
/// The reported bug was a calendar where every cell but one was greyed out, so
/// these tests assert on the *enabled* state of specific day cells rather than
/// on anything the screen says about itself.
void main() {
  Engagement engagement({required List<int> daysOfWeek}) => Engagement.fromJson({
        'id': 1,
        'days_of_week': daysOfWeek,
        'start_time': '09:00:00',
        'expected_duration_minutes': 60,
        'monthly_rate': 4000,
        'status': 'active',
        'worker': 2,
        'worker_name': 'Sunita D',
        'resident': 1,
        'resident_name': 'Anita Desai',
        'resident_flat': 'A-301',
      });

  Future<void> pumpScreen(WidgetTester tester, List<Engagement> list) async {
    await tester.pumpWidget(
      ProviderScope(
        overrides: [
          engagementsProvider.overrideWith((ref) async => list),
        ],
        child: const MaterialApp(home: ApplyLeaveScreen()),
      ),
    );
    await tester.pumpAndSettle();
  }

  /// Opens the picker by tapping the "Choose a day" card.
  Future<void> openPicker(WidgetTester tester) async {
    await tester.tap(find.text('Choose a day'));
    await tester.pumpAndSettle();
  }

  /// Whether the calendar cell for [day] can be tapped.
  ///
  /// Disabled cells are still rendered — that is the whole point of the bug —
  /// so presence proves nothing and the enabled state has to be read off the
  /// widget itself.
  bool dayIsEnabled(WidgetTester tester, int day) {
    final cell = find.ancestor(
      of: find.text('$day'),
      matching: find.byType(InkResponse),
    );
    if (cell.evaluate().isEmpty) return false;
    return tester.widget<InkResponse>(cell.first).onTap != null;
  }

  testWidgets('every day is selectable for an engagement that runs daily',
      (tester) async {
    await pumpScreen(tester, [
      engagement(daysOfWeek: const [0, 1, 2, 3, 4, 5, 6]),
    ]);
    await openPicker(tester);

    // Tomorrow and the day after are both inside the 14-day window and both
    // working days, so both must be choosable.
    final tomorrow = DateTime.now().add(const Duration(days: 1));
    final dayAfter = DateTime.now().add(const Duration(days: 2));

    expect(dayIsEnabled(tester, tomorrow.day), isTrue);
    expect(dayIsEnabled(tester, dayAfter.day), isTrue);
  });

  testWidgets(
      'a missing days_of_week offers the dates instead of disabling them all',
      (tester) async {
    // The regression under test. An engagement whose working days did not
    // arrive used to make the predicate return false for every date, which
    // greys out the entire calendar and leaves the worker unable to pick
    // anything at all.
    await pumpScreen(tester, [engagement(daysOfWeek: const [])]);
    await openPicker(tester);

    final tomorrow = DateTime.now().add(const Duration(days: 1));
    expect(dayIsEnabled(tester, tomorrow.day), isTrue);
  });

  testWidgets('opening the picker does not throw when today is a rest day',
      (tester) async {
    // showDatePicker asserts that initialDate satisfies selectableDayPredicate
    // and throws otherwise, so an engagement that does not run today must
    // still open on a day it does run.
    final todayIndex = DateTime.now().weekday - 1;
    final worksOn = [for (var d = 0; d < 7; d++) d]..remove(todayIndex);

    await pumpScreen(tester, [engagement(daysOfWeek: worksOn)]);
    await openPicker(tester);

    expect(tester.takeException(), isNull);
    // The rest day itself stays unselectable — the server would refuse it.
    expect(dayIsEnabled(tester, DateTime.now().day), isFalse);
  });

  testWidgets('the screen says which days can be taken off', (tester) async {
    await pumpScreen(tester, [engagement(daysOfWeek: const [0, 2, 4])]);

    expect(find.textContaining('Mon, Wed, Fri'), findsOneWidget);
  });
}
