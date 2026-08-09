import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:sathify/features/workers/data/models/worker_models.dart';
import 'package:sathify/features/workers/data/repositories/worker_repository.dart';
import 'package:sathify/features/workers/presentation/providers/worker_provider.dart';
import 'package:sathify/features/workers/presentation/screens/worker_approval_queue_screen.dart';

/// Module 3.5 — rejecting a helper must not leave the card spinning.
///
/// The bug: `_isBusy` was cleared only in the error branch, so a *successful*
/// rejection showed its confirmation snackbar and then replaced the buttons
/// with a CircularProgressIndicator for ever. Invalidating the list is not a
/// substitute — Riverpod keeps serving the previous value while the refetch is
/// in flight, so the card stays mounted across the gap.

class _FakeRepository implements WorkerRepository {
  _FakeRepository({this.fail = false});

  final bool fail;
  int decisions = 0;

  /// Held open so the test can observe the in-flight state deliberately.
  final Completer<void> gate = Completer<void>();

  @override
  Future<WorkerReview> decideWorker({
    required int workerId,
    required bool approve,
    String rejectionReason = '',
  }) async {
    decisions += 1;
    await gate.future;
    if (fail) throw StateError('server exploded');
    return const WorkerReview(id: 1, fullName: 'Sunita D');
  }

  /// The list keeps returning the worker, which is the case that used to stick:
  /// the refetch does not remove the card, so nothing else clears the flag.
  @override
  Future<List<WorkerReview>> fetchPendingWorkers() async =>
      const [WorkerReview(id: 1, fullName: 'Sunita D')];

  @override
  dynamic noSuchMethod(Invocation invocation) =>
      throw UnimplementedError('${invocation.memberName} is not used here');
}

void main() {
  Future<_FakeRepository> pump(WidgetTester tester, {bool fail = false}) async {
    final repository = _FakeRepository(fail: fail);

    await tester.pumpWidget(
      ProviderScope(
        overrides: [
          workerRepositoryProvider.overrideWithValue(repository),
        ],
        child: const MaterialApp(home: WorkerApprovalQueueScreen()),
      ),
    );
    await tester.pumpAndSettle();
    return repository;
  }

  /// Taps Reject and fills in the reason the server requires.
  Future<void> reject(WidgetTester tester) async {
    await tester.tap(find.text('Reject'));
    await tester.pumpAndSettle();

    await tester.enterText(find.byType(TextField).first, 'Blurred Aadhaar');
    await tester.pumpAndSettle();

    // The dialog's own confirm button, not the card's.
    //
    // `pump`, never `pumpAndSettle`: the request is deliberately held open
    // after this, so the spinner animates and settling would never return.
    await tester.tap(find.widgetWithText(FilledButton, 'Reject').last);
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 300));
  }

  testWidgets('the spinner clears after a successful rejection',
      (tester) async {
    final repository = await pump(tester);

    await reject(tester);
    expect(repository.decisions, 1);

    // In flight: the spinner has replaced the buttons, which is correct.
    expect(find.byType(CircularProgressIndicator), findsOneWidget);

    repository.gate.complete();
    await tester.pumpAndSettle();

    // The regression. Before the fix this stayed on screen for ever.
    expect(
      find.byType(CircularProgressIndicator),
      findsNothing,
      reason: 'the loading state must be reset on the success branch too',
    );
    expect(find.text('Reject'), findsOneWidget);
  });

  testWidgets('the confirmation is still shown', (tester) async {
    final repository = await pump(tester);

    await reject(tester);
    repository.gate.complete();
    await tester.pump();
    await tester.pump();

    expect(find.text('Sunita D rejected'), findsOneWidget);
  });

  testWidgets('a non-ApiException failure also clears the spinner',
      (tester) async {
    // This used to escape the `on ApiException` handler entirely and leave the
    // card stuck with no message at all.
    final repository = await pump(tester, fail: true);

    await reject(tester);
    repository.gate.complete();
    await tester.pumpAndSettle();

    expect(find.byType(CircularProgressIndicator), findsNothing);
    expect(find.textContaining('Could not save that'), findsOneWidget);
  });
}
