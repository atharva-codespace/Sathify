import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../../shared/design_system.dart';
import '../../data/repositories/auth_repository.dart';
import '../providers/auth_provider.dart';
import '../widgets/otp_code_field.dart';

/// The last step of sign-up: verify the phone number (Module 1.4).
///
/// Reached once per account, straight after registration. A correct code marks
/// the number verified and signs the new user in with the password they chose a
/// moment ago, so sign-up runs through to the dashboard without asking for that
/// password again. Every later sign-in goes through the login screen.
///
/// The resend countdown is always visible. A code that expires in two minutes is
/// unforgiving if the user cannot tell whether theirs is still good, and "did my
/// SMS arrive" is the commonest failure in this flow.
class OtpScreen extends ConsumerStatefulWidget {
  const OtpScreen({
    required this.phoneNumber,
    this.codeAlreadySent = true,
    super.key,
  });

  final String phoneNumber;

  /// False when registration was throttled and no code went out. The screen
  /// then sends one on open rather than waiting for a code that is not coming.
  final bool codeAlreadySent;

  @override
  ConsumerState<OtpScreen> createState() => _OtpScreenState();
}

class _OtpScreenState extends ConsumerState<OtpScreen> {
  final _formKey = GlobalKey<FormState>();
  final _codeController = TextEditingController();

  late final OtpResendTimer _resend;

  @override
  void initState() {
    super.initState();
    _resend = OtpResendTimer(onTick: () => setState(() {}));

    if (widget.codeAlreadySent) {
      _resend.start();
    } else {
      // Deliberately after the first frame: this touches the provider, and
      // notifier writes during initState are not safe to make.
      WidgetsBinding.instance.addPostFrameCallback((_) => _resendCode());
    }
  }

  @override
  void dispose() {
    _resend.dispose();
    _codeController.dispose();
    super.dispose();
  }

  Future<void> _resendCode() async {
    final sent = await ref.read(authProvider.notifier).requestOtp(
          phoneNumber: widget.phoneNumber,
          purpose: OtpPurpose.registration,
        );
    if (!mounted) return;

    if (sent) {
      _codeController.clear();
      setState(_resend.start);
      showAppSnackBar(context, 'A new code is on its way to ${widget.phoneNumber}.');
    }
    // A failure is already on `state.errorMessage` and rendered in the banner.
  }

  Future<void> _submit() async {
    if (!_formKey.currentState!.validate()) return;
    FocusScope.of(context).unfocus();

    await ref.read(authProvider.notifier).verifyOtp(
          phoneNumber: widget.phoneNumber,
          code: _codeController.text.trim(),
        );
    // On success the router's redirect takes over — it watches auth state, so
    // navigating from here as well would race it. On failure the banner shows.
  }

  @override
  Widget build(BuildContext context) {
    final state = ref.watch(authProvider);
    final theme = Theme.of(context);

    return Scaffold(
      appBar: AppBar(title: const Text('Verify your number')),
      body: SafeArea(
        child: Center(
          child: SingleChildScrollView(
            padding: const EdgeInsets.symmetric(
              horizontal: AppSpacing.xl,
              vertical: AppSpacing.xxl,
            ),
            child: ConstrainedBox(
              constraints: const BoxConstraints(maxWidth: 460),
              child: Form(
                key: _formKey,
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: [
                    Icon(
                      Icons.sms_outlined,
                      size: 48,
                      color: theme.colorScheme.primary,
                    ),
                    const SizedBox(height: AppSpacing.md),
                    Text(
                      'Your account is created. Enter the code to verify your '
                      'number and finish signing in.',
                      textAlign: TextAlign.center,
                      style: theme.textTheme.bodyMedium,
                    ),
                    const SizedBox(height: AppSpacing.xs),
                    Text(
                      'Sent to ${widget.phoneNumber}',
                      textAlign: TextAlign.center,
                      style: theme.textTheme.titleSmall,
                    ),
                    const SizedBox(height: AppSpacing.xl),

                    OtpCodeField(
                      controller: _codeController,
                      errorText: state.fieldErrors['code'],
                      // Submit as soon as the code is complete. Saves a tap on a
                      // screen where the next action is never in doubt.
                      onCompleted: state.isSubmitting ? null : _submit,
                    ),

                    if (state.errorMessage != null) ...[
                      const SizedBox(height: AppSpacing.md),
                      AppErrorBanner(
                        message: state.errorMessage!,
                        onDismiss: () =>
                            ref.read(authProvider.notifier).clearError(),
                      ),
                    ],

                    const SizedBox(height: AppSpacing.lg),
                    AppButton(
                      label: 'Verify and continue',
                      isLoading: state.isSubmitting,
                      onPressed: _submit,
                    ),
                    const SizedBox(height: AppSpacing.xs),
                    AppButton.text(
                      label: _resend.label,
                      icon: Icons.refresh_rounded,
                      expand: true,
                      onPressed: _resend.isWaiting || state.isSubmitting
                          ? null
                          : _resendCode,
                    ),
                    const SizedBox(height: AppSpacing.md),
                    Text(
                      'Codes expire after 2 minutes. Never share this code with '
                      'anyone, including someone claiming to be from Sathify.',
                      textAlign: TextAlign.center,
                      style: theme.textTheme.bodySmall,
                    ),
                  ],
                ),
              ),
            ),
          ),
        ),
      ),
    );
  }
}
