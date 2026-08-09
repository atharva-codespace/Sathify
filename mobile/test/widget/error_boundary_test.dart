import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:sathify/core/errors/error_boundary.dart';

/// A widget-build crash must never render as a blank screen.
///
/// This is the mechanism behind the reported "full white screen": Flutter's
/// red error panel is debug-only, so in a release build the default
/// [ErrorWidget] is an unstyled grey box with no text at all.
class _Exploding extends StatelessWidget {
  const _Exploding();

  @override
  Widget build(BuildContext context) => throw StateError('boom');
}

/// Runs [body] with the boundary installed, restoring the globals before the
/// test ends.
///
/// The restore has to happen inside the test body: `flutter_test` asserts that
/// `ErrorWidget.builder` is back to its default by the time the body returns,
/// so a `tearDown` fires too late and fails the test that just passed.
Future<void> withBoundary(Future<void> Function() body) async {
  final builder = ErrorWidget.builder;
  final onError = FlutterError.onError;
  installErrorBoundary();
  try {
    await body();
  } finally {
    ErrorWidget.builder = builder;
    FlutterError.onError = onError;
  }
}

void main() {
  testWidgets('a crashing widget renders a readable panel, not nothing',
      (tester) async {
    await withBoundary(() async {
      await tester.pumpWidget(
        const MaterialApp(home: Scaffold(body: _Exploding())),
      );

      // The framework still records the error — the boundary wraps the default
      // handler rather than swallowing it.
      expect(tester.takeException(), isStateError);
      expect(find.text('This part could not be shown'), findsOneWidget);
    });
  });

  testWidgets('the surrounding screen survives one broken child',
      (tester) async {
    await withBoundary(() async {
      await tester.pumpWidget(
        const MaterialApp(
          home: Scaffold(
            body: Column(
              children: [
                Text('Still here'),
                Expanded(child: _Exploding()),
              ],
            ),
          ),
        ),
      );

      expect(tester.takeException(), isStateError);

      // One broken card must not cost the whole screen — the rest still
      // renders and the user can navigate away.
      expect(find.text('Still here'), findsOneWidget);
      expect(find.text('This part could not be shown'), findsOneWidget);
    });
  });

  testWidgets('the default builder really does render no text at all',
      (tester) async {
    // Pins the premise. If Flutter ever ships a readable default, the boundary
    // is still wanted for the logging, but this test should be revisited.
    await tester.pumpWidget(
      const MaterialApp(home: Scaffold(body: _Exploding())),
    );
    expect(tester.takeException(), isStateError);

    expect(find.text('This part could not be shown'), findsNothing);
  });

  test('the boundary keeps the framework handler rather than replacing it',
      () {
    final builder = ErrorWidget.builder;
    final original = FlutterError.onError;
    var delegated = false;
    FlutterError.onError = (_) => delegated = true;

    try {
      installErrorBoundary();
      FlutterError.onError!(
        FlutterErrorDetails(exception: StateError('boom')),
      );
      expect(
        delegated,
        isTrue,
        reason: 'swallowing the original handler would lose the diagnostic',
      );
    } finally {
      ErrorWidget.builder = builder;
      FlutterError.onError = original;
    }
  });
}
