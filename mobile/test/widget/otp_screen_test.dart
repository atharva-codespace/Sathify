import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:sathify/core/errors/api_exception.dart';
import 'package:sathify/core/theme/app_theme.dart';
import 'package:sathify/features/auth/data/models/user_model.dart';
import 'package:sathify/features/auth/data/repositories/auth_repository.dart';
import 'package:sathify/features/auth/presentation/providers/auth_provider.dart';
import 'package:sathify/features/auth/presentation/screens/otp_screen.dart';
import 'package:sathify/features/auth/presentation/screens/reset_password_screen.dart';

/// Module 1.4 — the two screens that redeem a code.
///
/// Rendered with `AppTheme.light` rather than a bare `MaterialApp`: the app's
/// own theme drives input decoration and button sizing, and a test against
/// Material's defaults can pass while the real screen is unusable on device.

/// Records what a screen asked the repository to do.
class _SpyRepository implements AuthRepository {
  _SpyRepository({
    this.user,
    this.verifyError,
    this.requestError,
    this.resetError,
  });

  final UserModel? user;
  final ApiException? verifyError;
  final ApiException? requestError;
  final ApiException? resetError;

  int requests = 0;
  int verifications = 0;
  int resets = 0;
  String? lastCode;
  String? lastNewPassword;
  OtpPurpose? lastPurpose;

  @override
  Future<void> requestOtp({
    required String phoneNumber,
    OtpPurpose purpose = OtpPurpose.registration,
  }) async {
    requests += 1;
    lastPurpose = purpose;
    if (requestError != null) throw requestError!;
  }

  @override
  Future<UserModel> verifyOtp({
    required String phoneNumber,
    required String code,
  }) async {
    verifications += 1;
    lastCode = code;
    if (verifyError != null) throw verifyError!;
    return user!;
  }

  @override
  Future<UserModel> resetPassword({
    required String phoneNumber,
    required String code,
    required String newPassword,
  }) async {
    resets += 1;
    lastCode = code;
    lastNewPassword = newPassword;
    if (resetError != null) throw resetError!;
    return user!;
  }

  @override
  Future<bool> hasSession() async => false;

  @override
  dynamic noSuchMethod(Invocation invocation) =>
      throw UnimplementedError('${invocation.memberName} is not used here');
}

const _user = UserModel(
  id: 1,
  phoneNumber: '9876543210',
  firstName: 'Anita',
  lastName: 'Desai',
  role: UserRole.resident,
  isApproved: true,
);

Future<void> _pump(WidgetTester tester, _SpyRepository spy, Widget screen) async {
  await tester.pumpWidget(
    ProviderScope(
      overrides: [authRepositoryProvider.overrideWithValue(spy)],
      child: MaterialApp(theme: AppTheme.light, home: screen),
    ),
  );
  await tester.pump();
}

void main() {
  group('OtpScreen (phone verification)', () {
    Future<void> pump(
      WidgetTester tester,
      _SpyRepository spy, {
      bool codeAlreadySent = true,
    }) =>
        _pump(
          tester,
          spy,
          OtpScreen(
            phoneNumber: '9876543210',
            codeAlreadySent: codeAlreadySent,
          ),
        );

    testWidgets('a complete code submits without needing the button',
        (tester) async {
      final spy = _SpyRepository(user: _user);
      await pump(tester, spy);

      await tester.enterText(find.byType(TextField), '123456');
      await tester.pump();

      expect(spy.verifications, 1);
      expect(spy.lastCode, '123456');
    });

    testWidgets('an incomplete code is not sent to the server', (tester) async {
      final spy = _SpyRepository(user: _user);
      await pump(tester, spy);

      await tester.enterText(find.byType(TextField), '123');
      await tester.pump();

      expect(spy.verifications, 0);
    });

    testWidgets('non-digits cannot be typed into the code field', (tester) async {
      final spy = _SpyRepository(user: _user);
      await pump(tester, spy);

      await tester.enterText(find.byType(TextField), 'abc12x3456');
      await tester.pump();

      // The formatter strips letters, so what remains is the six digits.
      expect(spy.lastCode, '123456');
    });

    testWidgets('a rejected code shows the remedy and stays on the screen',
        (tester) async {
      final spy = _SpyRepository(
        verifyError: const ApiException(
          code: 'invalid_otp',
          message: 'That code is incorrect or has expired.',
          statusCode: 400,
        ),
      );
      await pump(tester, spy);

      await tester.enterText(find.byType(TextField), '000000');
      await tester.pumpAndSettle();

      expect(find.textContaining('Tap resend'), findsOneWidget);
      expect(find.byType(OtpScreen), findsOneWidget);
    });

    testWidgets('resend is blocked until the cooldown elapses', (tester) async {
      // The server allows one code per 60 seconds; letting the user tap
      // straight into a 429 would read as a broken button.
      final spy = _SpyRepository(user: _user);
      await pump(tester, spy);

      expect(find.textContaining('Resend code in'), findsOneWidget);

      await tester.pump(const Duration(seconds: 61));
      expect(find.text('Resend code'), findsOneWidget);

      await tester.tap(find.text('Resend code'));
      await tester.pump();
      expect(spy.requests, 1);
      expect(spy.lastPurpose, OtpPurpose.registration);
    });

    testWidgets('a throttled resend explains the wait instead of failing silently',
        (tester) async {
      final spy = _SpyRepository(
        user: _user,
        requestError: const ApiException(
          code: 'throttled',
          message: 'Please wait 20 seconds before requesting another code.',
          details: {'retry_after_seconds': 20},
          statusCode: 429,
        ),
      );
      await pump(tester, spy);

      await tester.pump(const Duration(seconds: 61));
      await tester.tap(find.text('Resend code'));
      await tester.pumpAndSettle();

      expect(find.textContaining('20 seconds'), findsOneWidget);
    });

    testWidgets('opening without a code sent asks for one immediately',
        (tester) async {
      // The throttled-registration path: the account exists but no code went
      // out, so waiting for one the user will never receive is wrong.
      final spy = _SpyRepository(user: _user);
      await pump(tester, spy, codeAlreadySent: false);
      await tester.pump();

      expect(spy.requests, 1);
    });
  });

  group('ResetPasswordScreen', () {
    Future<void> pump(WidgetTester tester, _SpyRepository spy) => _pump(
          tester,
          spy,
          const ResetPasswordScreen(phoneNumber: '9876543210'),
        );

    Finder codeField() => find.byType(TextField).first;
    Finder passwordField() => find.byType(TextField).last;

    testWidgets('sends the code and the new password together', (tester) async {
      final spy = _SpyRepository(user: _user);
      await pump(tester, spy);

      await tester.enterText(codeField(), '123456');
      await tester.enterText(passwordField(), 'brand-new-pass-99');
      await tester.tap(find.text('Set new password'));
      await tester.pumpAndSettle();

      expect(spy.resets, 1);
      expect(spy.lastCode, '123456');
      expect(spy.lastNewPassword, 'brand-new-pass-99');
    });

    testWidgets('a complete code does not submit on its own', (tester) async {
      // Unlike phone verification, the code is only half the form here.
      // Auto-submitting on the sixth digit would send an empty password.
      final spy = _SpyRepository(user: _user);
      await pump(tester, spy);

      await tester.enterText(codeField(), '123456');
      await tester.pump();

      expect(spy.resets, 0);
    });

    testWidgets('a short password is refused before reaching the server',
        (tester) async {
      final spy = _SpyRepository(user: _user);
      await pump(tester, spy);

      await tester.enterText(codeField(), '123456');
      await tester.enterText(passwordField(), 'short');
      await tester.tap(find.text('Set new password'));
      await tester.pumpAndSettle();

      expect(spy.resets, 0);
      expect(find.text('Use at least 8 characters'), findsOneWidget);
    });

    testWidgets('a rejected reset code keeps the typed password on screen',
        (tester) async {
      // Clearing the form on a bad code would make the user retype a password
      // they had already chosen, over a mistake in a different field.
      final spy = _SpyRepository(
        resetError: const ApiException(
          code: 'invalid_otp',
          message: 'That code is incorrect or has expired.',
          statusCode: 400,
        ),
      );
      await pump(tester, spy);

      await tester.enterText(codeField(), '000000');
      await tester.enterText(passwordField(), 'brand-new-pass-99');
      await tester.tap(find.text('Set new password'));
      await tester.pumpAndSettle();

      expect(find.textContaining('Tap resend'), findsOneWidget);
      expect(
        tester.widget<TextField>(passwordField()).controller?.text,
        'brand-new-pass-99',
      );
    });

    testWidgets('warns that other devices will be signed out', (tester) async {
      // Not a free action, and the consequence must be visible before the user
      // commits rather than discovered afterwards.
      await pump(tester, _SpyRepository(user: _user));

      expect(
        find.textContaining('signs you out on every other device'),
        findsOneWidget,
      );
    });

    testWidgets('resend asks for a reset code, not a registration one',
        (tester) async {
      // Codes are scoped server-side: the wrong purpose here would hand the
      // user a code that is refused on submit, with nothing explaining why.
      final spy = _SpyRepository(user: _user);
      await pump(tester, spy);

      await tester.pump(const Duration(seconds: 61));
      await tester.tap(find.text('Resend code'));
      await tester.pump();

      expect(spy.lastPurpose, OtpPurpose.passwordReset);
    });
  });
}
