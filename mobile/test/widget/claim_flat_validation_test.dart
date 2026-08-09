import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:sathify/features/societies/data/models/society_models.dart';
import 'package:sathify/features/societies/data/repositories/society_repository.dart';
import 'package:sathify/features/societies/presentation/providers/society_provider.dart';
import 'package:sathify/features/societies/presentation/screens/claim_flat_screen.dart';

/// Module 2.3 — the flat-approval form must not submit without proof.
///
/// The reported bug: the form accepted a claim with no document attached, so
/// the administrator's queue filled with items they could only reject.

/// Records whether the claim ever reached the repository.
class _SpyRepository implements SocietyRepository {
  int claims = 0;
  String? lastProofPath;

  @override
  Future<ResidentProfile> claimFlat({
    required int flatId,
    required ResidentRelationship relationship,
    String? proofDocumentPath,
    DateTime? moveInDate,
  }) async {
    claims += 1;
    lastProofPath = proofDocumentPath;
    return ResidentProfile.fromJson(const {'id': 1});
  }

  @override
  dynamic noSuchMethod(Invocation invocation) =>
      throw UnimplementedError('${invocation.memberName} is not used here');
}

void main() {
  const tower = Tower(id: 1, name: 'A', floors: 10);
  const flat = Flat(id: 5, number: '301', label: 'A-301', towerId: 1);

  Future<_SpyRepository> pump(WidgetTester tester) async {
    final spy = _SpyRepository();

    await tester.pumpWidget(
      ProviderScope(
        overrides: [
          societyRepositoryProvider.overrideWithValue(spy),
          towersProvider.overrideWith((ref) async => [tower]),
          flatsProvider.overrideWith((ref, towerId) async => [flat]),
        ],
        child: const MaterialApp(home: ClaimFlatScreen()),
      ),
    );
    await tester.pumpAndSettle();
    return spy;
  }

  /// Picks the tower, then the flat, leaving only the proof outstanding.
  Future<void> chooseFlat(WidgetTester tester) async {
    await tester.tap(find.byType(DropdownButtonFormField<Tower>));
    await tester.pumpAndSettle();
    await tester.tap(find.text('A').last);
    await tester.pumpAndSettle();

    await tester.tap(find.byType(DropdownButtonFormField<Flat>));
    await tester.pumpAndSettle();
    await tester.tap(find.text('301').last);
    await tester.pumpAndSettle();
  }

  testWidgets('the form says the proof is required', (tester) async {
    await pump(tester);

    expect(find.textContaining('Required'), findsOneWidget);
    expect(find.textContaining('Optional'), findsNothing);
  });

  testWidgets('submitting without a flat asks for the flat', (tester) async {
    final spy = await pump(tester);

    await tester.tap(find.text('Submit for approval'));
    await tester.pumpAndSettle();

    expect(find.text('Choose your flat to continue.'), findsOneWidget);
    expect(spy.claims, 0);
  });

  testWidgets('submitting without a proof image is refused', (tester) async {
    // The regression under test.
    final spy = await pump(tester);
    await chooseFlat(tester);

    await tester.tap(find.text('Submit for approval'));
    await tester.pumpAndSettle();

    expect(
      find.text('Attach your proof of residence to continue.'),
      findsOneWidget,
    );
    expect(spy.claims, 0, reason: 'nothing should have been sent');
  });

  testWidgets('the submit button is not left spinning after a refusal',
      (tester) async {
    await pump(tester);
    await chooseFlat(tester);

    await tester.tap(find.text('Submit for approval'));
    await tester.pumpAndSettle();

    // The label is back, so the button is usable again rather than stuck.
    expect(find.text('Submit for approval'), findsOneWidget);
    expect(find.byType(CircularProgressIndicator), findsNothing);
  });
}
