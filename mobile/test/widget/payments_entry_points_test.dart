import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:sathify/core/theme/app_theme.dart';
import 'package:sathify/features/auth/data/models/user_model.dart';
import 'package:sathify/features/auth/presentation/providers/auth_provider.dart';
import 'package:sathify/features/payments/data/models/payment_models.dart';
import 'package:sathify/features/payments/presentation/providers/payment_provider.dart';
import 'package:sathify/features/payments/presentation/screens/payments_screen.dart';

/// The way *into* the hourly-billing screens.
///
/// Module 8.10's screens shipped registered in the router but reachable from
/// nowhere: no widget pushed [Routes.myBills], so a resident could not open
/// their own invoices on a real device even though every unit test passed.
/// Analysis cannot catch that — an unused route constant is still a used
/// symbol — so it is pinned here instead.
void main() {
  UserModel user(UserRole role) => UserModel(
        id: 1,
        phoneNumber: '9800000002',
        role: role,
        isApproved: true,
        firstName: 'Rohit',
      );

  Future<void> pumpPayments(WidgetTester tester, UserRole role) async {
    await tester.pumpWidget(
      ProviderScope(
        overrides: [
          paymentsProvider.overrideWith((ref) async => <Payment>[]),
          authProvider.overrideWith(() => _StubAuth(user(role))),
        ],
        // AppTheme.light rather than a bare MaterialApp: the app's own theme is
        // what decides whether an AppBar action survives beside a long title.
        child: MaterialApp(theme: AppTheme.light, home: const PaymentsScreen()),
      ),
    );
    await tester.pumpAndSettle();
  }

  testWidgets('a resident can reach their hourly bills', (tester) async {
    await pumpPayments(tester, UserRole.resident);

    expect(find.byTooltip('My bills'), findsOneWidget);
    expect(find.byTooltip('Monthly statement'), findsNothing);
  });

  testWidgets('a worker still gets the statement, not the bill list',
      (tester) async {
    await pumpPayments(tester, UserRole.worker);

    expect(find.byTooltip('Monthly statement'), findsOneWidget);
    expect(find.byTooltip('My bills'), findsNothing);
  });
}

/// A signed-in user, without touching secure storage or the network.
class _StubAuth extends AuthNotifier {
  _StubAuth(this._user);

  final UserModel _user;

  @override
  AuthState build() => AuthState(status: AuthStatus.authenticated, user: _user);
}
