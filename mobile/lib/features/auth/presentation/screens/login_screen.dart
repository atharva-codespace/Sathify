import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../../../core/routing/app_router.dart';
import '../../../../shared/design_system.dart';
import '../../data/models/saved_account.dart';
import '../../data/repositories/auth_repository.dart';
import '../providers/auth_provider.dart';

/// Phone + password sign-in (Module 1.1), with the saved-accounts list as the
/// primary path.
///
/// The screen has two modes. When this device has signed in before it opens on
/// the account list, because the overwhelmingly common case is somebody
/// returning to an account they already use — the same reasoning behind Gmail's
/// and Netflix's switchers. Typing a phone number is the fallback, reached by
/// "Use another account" or by a lapsed quick sign-in.
///
/// "Forgot password" is answered by a code to the phone rather than a link to
/// an email, which many users here do not have.
///
/// Still deliberately plain in its mechanics: large touch targets, one field per
/// row, errors under the field they belong to. Users span a wide range of
/// digital literacy (SRS 5.4), so nothing here is clever.
class LoginScreen extends ConsumerStatefulWidget {
  const LoginScreen({super.key});

  @override
  ConsumerState<LoginScreen> createState() => _LoginScreenState();
}

enum _Mode { accounts, form }

class _LoginScreenState extends ConsumerState<LoginScreen> {
  final _formKey = GlobalKey<FormState>();
  final _phoneController = TextEditingController();
  final _passwordController = TextEditingController();
  final _passwordFocus = FocusNode();

  bool _obscurePassword = true;

  /// Null until the saved-account list has loaded, so the screen does not flash
  /// the password form for a moment before revealing the accounts behind it.
  _Mode? _mode;

  /// The account whose quick sign-in is in flight, so the spinner appears on
  /// that row rather than over the whole screen.
  int? _resumingId;

  /// Set when a quick sign-in lapsed. Explains why the password is being asked
  /// for, in plain language and without a red error treatment — the user did
  /// nothing wrong.
  String? _notice;

  @override
  void dispose() {
    _phoneController.dispose();
    _passwordController.dispose();
    _passwordFocus.dispose();
    super.dispose();
  }

  Future<void> _submit() async {
    if (!_formKey.currentState!.validate()) return;
    FocusScope.of(context).unfocus();

    await ref.read(authProvider.notifier).login(
          phoneNumber: _phoneController.text.trim(),
          password: _passwordController.text,
        );
    // Navigation is handled by the router's redirect, which watches auth state.
  }

  Future<void> _useAccount(SavedAccount account) async {
    if (!account.canQuickSignIn) {
      _fallBackToPassword(account, 'Enter your password to continue.');
      return;
    }

    setState(() => _resumingId = account.userId);
    final signedIn =
        await ref.read(authProvider.notifier).signInWithSavedAccount(account);
    if (!mounted) return;
    setState(() => _resumingId = null);

    if (!signedIn) {
      // The parked token expired or was revoked. That is expected after long
      // gaps, so it is framed as a routine prompt rather than a failure.
      _fallBackToPassword(
        account,
        'For security, please enter your password again.',
      );
    }
  }

  void _fallBackToPassword(SavedAccount account, String notice) {
    setState(() {
      _mode = _Mode.form;
      _phoneController.text = account.phoneNumber;
      _passwordController.clear();
      _notice = '$notice Signing in as ${account.displayName}.';
    });
    _passwordFocus.requestFocus();
  }

  /// "Forgot password" — a code to the phone rather than a link to an email.
  ///
  /// Only the phone number is validated before sending, so an empty password
  /// box does not block the one action taken by users who have no password to
  /// type. Confirmed first because succeeding signs the user out everywhere
  /// else, which is right after a compromise and a surprise otherwise.
  Future<void> _startPasswordReset() async {
    final phone = _phoneController.text.trim();
    if (_validatePhone(phone) != null) {
      showAppSnackBar(context, 'Enter your phone number first.');
      return;
    }
    FocusScope.of(context).unfocus();

    final confirmed = await showDialog<bool>(
      context: context,
      builder: (dialogContext) => AlertDialog(
        title: const Text('Reset your password?'),
        content: Text(
          'We will text a 6-digit code to $phone. You can then choose a new '
          'password. This signs you out on every other device.',
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(dialogContext).pop(false),
            child: const Text('Cancel'),
          ),
          TextButton(
            onPressed: () => Navigator.of(dialogContext).pop(true),
            child: const Text('Send code'),
          ),
        ],
      ),
    );

    if (confirmed != true || !mounted) return;

    final sent = await ref
        .read(authProvider.notifier)
        .requestOtp(phoneNumber: phone, purpose: OtpPurpose.passwordReset);
    if (!mounted || !sent) return;

    // Not awaited: this completes only when the reset screen is popped, and
    // there is nothing for this method to do at that point.
    unawaited(
      context.push(
        Uri(
          path: Routes.resetPassword,
          queryParameters: {'phone': phone},
        ).toString(),
      ),
    );
  }

  Future<void> _confirmForget(SavedAccount account) async {
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (dialogContext) => AlertDialog(
        title: const Text('Forget this account?'),
        content: Text(
          '${account.displayName} will be removed from this device. '
          'You can sign in again any time with your password.',
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(dialogContext).pop(false),
            child: const Text('Cancel'),
          ),
          TextButton(
            onPressed: () => Navigator.of(dialogContext).pop(true),
            style: TextButton.styleFrom(foregroundColor: AppColors.danger),
            child: const Text('Forget'),
          ),
        ],
      ),
    );

    if (confirmed != true || !mounted) return;
    await ref.read(authProvider.notifier).forgetAccount(account);
    if (!mounted) return;
    showAppSnackBar(context, 'Account removed from this device.');
  }

  String? _validatePhone(String? value) {
    final text = (value ?? '').trim();
    if (text.isEmpty) return 'Enter your phone number';
    // Matches the server-side validator in apps/accounts/models.py.
    if (!RegExp(r'^(\+91)?[6-9]\d{9}$').hasMatch(text)) {
      return 'Enter a valid 10-digit mobile number';
    }
    return null;
  }

  @override
  Widget build(BuildContext context) {
    final state = ref.watch(authProvider);
    final saved = ref.watch(savedAccountsProvider);
    final accounts = saved.valueOrNull ?? const <SavedAccount>[];

    // Decide the opening mode once, as soon as the list is known.
    if (_mode == null && saved.hasValue) {
      _mode = accounts.isEmpty ? _Mode.form : _Mode.accounts;
    }
    final mode = _mode;

    return Scaffold(
      body: SafeArea(
        child: Center(
          child: SingleChildScrollView(
            padding: const EdgeInsets.symmetric(
              horizontal: AppSpacing.xl,
              vertical: AppSpacing.xxl,
            ),
            child: ConstrainedBox(
              constraints: const BoxConstraints(maxWidth: 460),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  const AppFadeIn(child: _Brandmark()),
                  const SizedBox(height: AppSpacing.xxl),
                  AppFadeIn(
                    index: 1,
                    child: _Heading(
                      returning: mode == _Mode.accounts,
                      accountCount: accounts.length,
                    ),
                  ),
                  const SizedBox(height: AppSpacing.xl),
                  if (mode == null)
                    const _AccountsLoading()
                  else if (mode == _Mode.accounts)
                    AppFadeIn(
                      index: 2,
                      child: _SavedAccounts(
                        accounts: accounts,
                        resumingId: _resumingId,
                        busy: state.isSubmitting,
                        onUse: _useAccount,
                        onForget: _confirmForget,
                        onUseAnother: () => setState(() {
                          _mode = _Mode.form;
                          _notice = null;
                          _phoneController.clear();
                          _passwordController.clear();
                        }),
                      ),
                    )
                  else
                    AppFadeIn(index: 2, child: _buildForm(state)),
                  const SizedBox(height: AppSpacing.xxl),
                  AppFadeIn(
                    index: 3,
                    child: _RegisterFooter(disabled: state.isSubmitting),
                  ),
                  const SizedBox(height: AppSpacing.xl),
                  const AppFadeIn(index: 4, child: _TrustRow()),
                ],
              ),
            ),
          ),
        ),
      ),
    );
  }

  Widget _buildForm(AuthState state) {
    final hasSaved =
        (ref.read(savedAccountsProvider).valueOrNull ?? []).isNotEmpty;

    return Form(
      key: _formKey,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          if (_notice != null) ...[
            _Notice(message: _notice!),
            const SizedBox(height: AppSpacing.md),
          ],

          TextFormField(
            controller: _phoneController,
            keyboardType: TextInputType.phone,
            textInputAction: TextInputAction.next,
            autofillHints: const [AutofillHints.telephoneNumber],
            inputFormatters: [
              FilteringTextInputFormatter.allow(RegExp(r'[0-9+]')),
              LengthLimitingTextInputFormatter(13),
            ],
            decoration: InputDecoration(
              labelText: 'Phone number',
              prefixIcon: const Icon(Icons.phone_outlined),
              errorText: state.fieldErrors['phone_number'],
            ),
            validator: _validatePhone,
          ),
          const SizedBox(height: AppSpacing.sm),

          TextFormField(
            controller: _passwordController,
            focusNode: _passwordFocus,
            obscureText: _obscurePassword,
            textInputAction: TextInputAction.done,
            autofillHints: const [AutofillHints.password],
            onFieldSubmitted: (_) => _submit(),
            decoration: InputDecoration(
              labelText: 'Password',
              prefixIcon: const Icon(Icons.lock_outline),
              errorText: state.fieldErrors['password'],
              suffixIcon: IconButton(
                icon: Icon(
                  _obscurePassword
                      ? Icons.visibility_outlined
                      : Icons.visibility_off_outlined,
                ),
                tooltip: _obscurePassword ? 'Show password' : 'Hide password',
                onPressed: () =>
                    setState(() => _obscurePassword = !_obscurePassword),
              ),
            ),
            validator: (value) =>
                (value ?? '').isEmpty ? 'Enter your password' : null,
          ),

          if (state.errorMessage != null) ...[
            const SizedBox(height: AppSpacing.md),
            AppErrorBanner(
              message: state.errorMessage!,
              onDismiss: () => ref.read(authProvider.notifier).clearError(),
            ),
          ],

          const SizedBox(height: AppSpacing.lg),
          AppButton(
            label: 'Sign in',
            isLoading: state.isSubmitting,
            onPressed: _submit,
          ),

          const SizedBox(height: AppSpacing.xs),
          // Answered by SMS rather than email: Module 1.4's premise is that
          // many users here have no reliable email address, so a reset link
          // would exclude exactly the people it most needs to reach.
          AppButton.text(
            label: 'Forgot password?',
            icon: Icons.lock_reset_outlined,
            expand: true,
            onPressed: state.isSubmitting ? null : _startPasswordReset,
          ),

          // Only offered when there is something to go back to.
          if (hasSaved) ...[
            const SizedBox(height: AppSpacing.xxs),
            AppButton.text(
              label: 'Back to saved accounts',
              icon: Icons.arrow_back_rounded,
              expand: true,
              onPressed: state.isSubmitting
                  ? null
                  : () => setState(() {
                        _mode = _Mode.accounts;
                        _notice = null;
                        ref.read(authProvider.notifier).clearError();
                      }),
            ),
          ],
        ],
      ),
    );
  }
}

class _Brandmark extends StatelessWidget {
  const _Brandmark();

  @override
  Widget build(BuildContext context) {
    return Column(
      children: [
        Container(
          width: 64,
          height: 64,
          decoration: BoxDecoration(
            color: AppColors.primary,
            borderRadius: BorderRadius.circular(AppRadius.lg),
            boxShadow: AppShadow.md,
          ),
          child: const Icon(
            Icons.verified_user_rounded,
            size: 34,
            color: AppColors.textOnPrimary,
          ),
        ),
        const SizedBox(height: AppSpacing.sm),
        Text(
          'Sathify',
          textAlign: TextAlign.center,
          style: Theme.of(context).textTheme.titleLarge,
        ),
      ],
    );
  }
}

class _Heading extends StatelessWidget {
  const _Heading({required this.returning, required this.accountCount});

  final bool returning;
  final int accountCount;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(
          returning ? 'Welcome back' : 'Sign in to continue',
          style: theme.textTheme.headlineMedium,
        ),
        const SizedBox(height: AppSpacing.xxs),
        Text(
          returning
              ? (accountCount == 1
                  ? 'Tap your account to continue.'
                  : 'Choose an account to continue.')
              : 'Your society, your workers, in one place.',
          style: theme.textTheme.bodyMedium,
        ),
      ],
    );
  }
}

class _SavedAccounts extends StatelessWidget {
  const _SavedAccounts({
    required this.accounts,
    required this.resumingId,
    required this.busy,
    required this.onUse,
    required this.onForget,
    required this.onUseAnother,
  });

  final List<SavedAccount> accounts;
  final int? resumingId;
  final bool busy;
  final ValueChanged<SavedAccount> onUse;
  final ValueChanged<SavedAccount> onForget;
  final VoidCallback onUseAnother;

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        AppCardGroup(
          children: [
            for (final account in accounts)
              AccountTile(
                name: account.displayName,
                subtitle: account.subtitle,
                seed: account.userId,
                onTap: busy ? () {} : () => onUse(account),
                onForget: busy ? null : () => onForget(account),
                trailing: resumingId == account.userId
                    ? const SizedBox(
                        width: 44,
                        height: 44,
                        child: Center(
                          child: SizedBox(
                            width: 20,
                            height: 20,
                            child: CircularProgressIndicator(strokeWidth: 2.4),
                          ),
                        ),
                      )
                    : null,
              ),
          ],
        ),
        const SizedBox(height: AppSpacing.md),
        AppButton.secondary(
          label: 'Use another account',
          icon: Icons.person_add_alt_1_outlined,
          onPressed: busy ? null : onUseAnother,
        ),
      ],
    );
  }
}

class _AccountsLoading extends StatelessWidget {
  const _AccountsLoading();

  @override
  Widget build(BuildContext context) {
    // Matches the height of a single account row, so the layout does not jump
    // when the real list arrives a few milliseconds later.
    return Container(
      padding: const EdgeInsets.all(AppSpacing.md),
      decoration: BoxDecoration(
        color: AppColors.surface,
        borderRadius: AppRadius.card,
        border: Border.all(color: AppColors.border),
      ),
      child: const Row(
        children: [
          AppSkeleton.circle(size: 46),
          SizedBox(width: AppSpacing.sm),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                AppSkeleton(width: 130, height: 15),
                SizedBox(height: AppSpacing.xs),
                AppSkeleton(width: 180, height: 12),
              ],
            ),
          ),
        ],
      ),
    );
  }
}

/// An informational strip, distinct from [AppErrorBanner].
///
/// A lapsed quick sign-in is routine, not a fault, and dressing it in red
/// trains people to ignore red.
class _Notice extends StatelessWidget {
  const _Notice({required this.message});

  final String message;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.all(AppSpacing.sm),
      decoration: BoxDecoration(
        color: AppColors.infoSoft,
        borderRadius: AppRadius.button,
        border: Border.all(color: AppColors.info.withValues(alpha: 0.2)),
      ),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const Icon(
            Icons.info_outline_rounded,
            color: AppColors.info,
            size: AppIconSize.md,
          ),
          const SizedBox(width: AppSpacing.xs + 2),
          Expanded(
            child: Text(
              message,
              style: const TextStyle(
                color: AppColors.info,
                fontSize: 14,
                height: 1.4,
                fontWeight: FontWeight.w500,
              ),
            ),
          ),
        ],
      ),
    );
  }
}

class _RegisterFooter extends StatelessWidget {
  const _RegisterFooter({required this.disabled});

  final bool disabled;

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Row(
          children: [
            const Expanded(child: Divider()),
            Padding(
              padding: const EdgeInsets.symmetric(horizontal: AppSpacing.sm),
              child: Text(
                'New to Sathify?',
                style: Theme.of(context).textTheme.bodySmall,
              ),
            ),
            const Expanded(child: Divider()),
          ],
        ),
        const SizedBox(height: AppSpacing.md),
        // Two entry points rather than a role dropdown: the wording is plainer,
        // and it removes a step for users who may not recognise the word
        // "resident" or "worker" in English.
        AppButton.secondary(
          label: 'I live here',
          icon: Icons.home_outlined,
          onPressed:
              disabled ? null : () => context.push(Routes.registerResident),
        ),
        const SizedBox(height: AppSpacing.xs + 2),
        AppButton.secondary(
          label: 'I work here',
          icon: Icons.work_outline,
          onPressed:
              disabled ? null : () => context.push(Routes.registerWorker),
        ),
      ],
    );
  }
}

/// The reassurance row every reference app carries under its sign-in control.
///
/// Not decoration: this app asks for a phone number, an Aadhaar document and a
/// live photo before it is useful, so stating the safeguards at the point of
/// entry is doing real work.
class _TrustRow extends StatelessWidget {
  const _TrustRow();

  @override
  Widget build(BuildContext context) {
    return const Row(
      mainAxisAlignment: MainAxisAlignment.spaceEvenly,
      children: [
        _TrustItem(icon: Icons.verified_outlined, label: 'Verified\nworkers'),
        _TrustItem(icon: Icons.lock_outline, label: 'Secure\nsign-in'),
        _TrustItem(icon: Icons.apartment_outlined, label: 'Society\napproved'),
      ],
    );
  }
}

class _TrustItem extends StatelessWidget {
  const _TrustItem({required this.icon, required this.label});

  final IconData icon;
  final String label;

  @override
  Widget build(BuildContext context) {
    return Column(
      mainAxisSize: MainAxisSize.min,
      children: [
        Icon(icon, size: AppIconSize.md, color: AppColors.primary),
        const SizedBox(height: AppSpacing.xxs + 2),
        Text(
          label,
          textAlign: TextAlign.center,
          style: const TextStyle(
            fontSize: 12,
            height: 1.3,
            fontWeight: FontWeight.w600,
            color: AppColors.textSecondary,
          ),
        ),
      ],
    );
  }
}
