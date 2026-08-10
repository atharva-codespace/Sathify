import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../../shared/design_system.dart';
import '../../data/repositories/auth_repository.dart';
import '../providers/auth_provider.dart';
import '../widgets/otp_code_field.dart';

/// "Forgot password", answered by SMS rather than an emailed link (Module 1.4).
///
/// The premise is that many domestic workers have no reliable email address, so
/// a reset link would exclude a large share of the people this platform exists
/// for. The phone number is the account's anchor, so a code sent to it is both
/// the strongest proof available and the one every user can actually complete.
///
/// The code and the new password are collected on one screen and submitted
/// together. Splitting them would mean holding a redeemed code in client state
/// between two screens, and a user who backgrounded the app in between would
/// come back to a code already spent.
class ResetPasswordScreen extends ConsumerStatefulWidget {
  const ResetPasswordScreen({required this.phoneNumber, super.key});

  final String phoneNumber;

  @override
  ConsumerState<ResetPasswordScreen> createState() =>
      _ResetPasswordScreenState();
}

class _ResetPasswordScreenState extends ConsumerState<ResetPasswordScreen> {
  final _formKey = GlobalKey<FormState>();
  final _codeController = TextEditingController();
  final _passwordController = TextEditingController();

  bool _obscurePassword = true;
  late final OtpResendTimer _resend;

  @override
  void initState() {
    super.initState();
    // The login screen sends the code before pushing this screen, so the
    // cooldown is already running when we arrive.
    _resend = OtpResendTimer(onTick: () => setState(() {}))..start();
  }

  @override
  void dispose() {
    _resend.dispose();
    _codeController.dispose();
    _passwordController.dispose();
    super.dispose();
  }

  Future<void> _resendCode() async {
    final sent = await ref.read(authProvider.notifier).requestOtp(
          phoneNumber: widget.phoneNumber,
          purpose: OtpPurpose.passwordReset,
        );
    if (!mounted) return;

    if (sent) {
      _codeController.clear();
      setState(_resend.start);
      showAppSnackBar(context, 'A new code is on its way to ${widget.phoneNumber}.');
    }
  }

  Future<void> _submit() async {
    if (!_formKey.currentState!.validate()) return;
    FocusScope.of(context).unfocus();

    await ref.read(authProvider.notifier).resetPassword(
          phoneNumber: widget.phoneNumber,
          code: _codeController.text.trim(),
          newPassword: _passwordController.text,
        );
    // Success signs the user in, and the router's redirect takes them onward.
  }

  @override
  Widget build(BuildContext context) {
    final state = ref.watch(authProvider);
    final theme = Theme.of(context);

    return Scaffold(
      appBar: AppBar(title: const Text('Reset password')),
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
                      Icons.lock_reset_outlined,
                      size: 48,
                      color: theme.colorScheme.primary,
                    ),
                    const SizedBox(height: AppSpacing.md),
                    Text(
                      'Enter the code we texted you, then choose a new password.',
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
                      // No auto-submit here: unlike phone verification, the code
                      // is only half the form. Firing on the sixth digit would
                      // submit an empty password.
                    ),
                    const SizedBox(height: AppSpacing.sm),

                    TextFormField(
                      controller: _passwordController,
                      obscureText: _obscurePassword,
                      textInputAction: TextInputAction.done,
                      autofillHints: const [AutofillHints.newPassword],
                      onFieldSubmitted: (_) => _submit(),
                      decoration: InputDecoration(
                        labelText: 'New password',
                        helperText: 'At least 8 characters',
                        prefixIcon: const Icon(Icons.lock_outline),
                        errorText: state.fieldErrors['new_password'],
                        suffixIcon: IconButton(
                          icon: Icon(
                            _obscurePassword
                                ? Icons.visibility_outlined
                                : Icons.visibility_off_outlined,
                          ),
                          tooltip: _obscurePassword
                              ? 'Show password'
                              : 'Hide password',
                          onPressed: () => setState(
                            () => _obscurePassword = !_obscurePassword,
                          ),
                        ),
                      ),
                      validator: (value) => (value ?? '').length < 8
                          ? 'Use at least 8 characters'
                          : null,
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
                      label: 'Set new password',
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
                      'Codes expire after 2 minutes. Setting a new password '
                      'signs you out on every other device.',
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
