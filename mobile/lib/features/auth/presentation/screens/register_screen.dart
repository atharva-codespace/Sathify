import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../../../core/routing/app_router.dart';
import '../../../societies/data/models/society_models.dart';
import '../../../societies/presentation/providers/society_provider.dart';
import '../../data/models/user_model.dart';
import '../../data/repositories/auth_repository.dart';
import '../providers/auth_provider.dart';

/// Registration for residents and domestic workers (Module 1.1 + 2.3).
///
/// Guards and society administrators are absent on purpose: guards are created
/// by an administrator, and administrator sign-up goes through a separate flow
/// that registers a society rather than joining one.
class RegisterScreen extends ConsumerStatefulWidget {
  const RegisterScreen({required this.role, super.key});

  /// Either [UserRole.resident] or [UserRole.worker].
  final UserRole role;

  @override
  ConsumerState<RegisterScreen> createState() => _RegisterScreenState();
}

class _RegisterScreenState extends ConsumerState<RegisterScreen> {
  final _formKey = GlobalKey<FormState>();
  final _firstNameController = TextEditingController();
  final _lastNameController = TextEditingController();
  final _phoneController = TextEditingController();
  final _passwordController = TextEditingController();
  final _societySearchController = TextEditingController();

  SocietySummary? _selectedSociety;
  String _societySearch = '';
  bool _obscurePassword = true;

  bool get _isWorker => widget.role == UserRole.worker;

  @override
  void dispose() {
    _firstNameController.dispose();
    _lastNameController.dispose();
    _phoneController.dispose();
    _passwordController.dispose();
    _societySearchController.dispose();
    super.dispose();
  }

  Future<void> _submit() async {
    if (!_formKey.currentState!.validate()) return;
    if (_selectedSociety == null) {
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('Please choose your society')),
      );
      return;
    }
    FocusScope.of(context).unfocus();

    final notifier = ref.read(authProvider.notifier);
    final register =
        _isWorker ? notifier.registerWorker : notifier.registerResident;

    final phone = _phoneController.text.trim();
    final result = await register(
      phoneNumber: phone,
      password: _passwordController.text,
      firstName: _firstNameController.text.trim(),
      lastName: _lastNameController.text.trim(),
      societyId: _selectedSociety!.id,
    );

    if (!mounted || result == null) return;

    // Awaited, so that *this* screen owns what happens next.
    //
    // The dialog used to navigate for itself with two bare
    // `Navigator.of(context).pop()` calls — one for itself and one meant for
    // this screen. Under go_router the second pop is not this screen's: the
    // routes live in the ShellRoute's nested navigator, so it hits the root
    // navigator's last route and takes the app down with it. The dialog now
    // only closes itself, and the route change happens here.
    await showDialog<void>(
      context: context,
      barrierDismissible: false,
      builder: (_) => _RegistrationSuccessDialog(
        isWorker: _isWorker,
        phoneNumber: phone,
      ),
    );

    if (!mounted) return;
    // Straight to the code prompt, not back to sign-in: the server has already
    // texted a code, and sending the user to a login screen would waste it and
    // make them ask for another.
    //
    // `go`, not `push`: registration is a finished chapter, and this works
    // identically whether the screen was pushed from sign-in or opened cold
    // from a deep link — where there is nothing to pop at all.
    context.go(
      Uri(
        path: Routes.otp,
        queryParameters: {
          'phone': phone,
          'purpose': OtpPurpose.registration.wireValue,
          // False when the server's throttle bit; the code screen then opens
          // with resend live instead of waiting on a code that never went out.
          'sent': result.otpSent.toString(),
        },
      ).toString(),
    );
  }

  String? _validatePhone(String? value) {
    final text = (value ?? '').trim();
    if (text.isEmpty) return 'Enter your phone number';
    if (!RegExp(r'^(\+91)?[6-9]\d{9}$').hasMatch(text)) {
      return 'Enter a valid 10-digit mobile number';
    }
    return null;
  }

  /// Fields whose server-side errors are already shown under their input.
  static const _boundFields = {
    'first_name',
    'last_name',
    'phone_number',
    'password',
  };

  /// The banner text, with any error the form cannot attribute appended.
  ///
  /// Without this, a server complaint about a field this screen does not render
  /// vanishes: `fieldErrors` holds it, no `errorText` is bound to it, and the
  /// user is left with a bare "One or more fields failed validation" and no way
  /// to know what failed. That is exactly what a client/server version skew
  /// looks like — an app that has stopped sending a field the deployed server
  /// still demands — and it is the case where a legible message matters most,
  /// because nothing the user does to the form can fix it.
  String _errorText(AuthState state) {
    final unattributed = state.fieldErrors.entries
        .where((entry) => !_boundFields.contains(entry.key))
        .map((entry) => '${entry.key}: ${entry.value}')
        .toList();

    if (unattributed.isEmpty) return state.errorMessage!;
    return '${state.errorMessage!}\n${unattributed.join('\n')}';
  }

  @override
  Widget build(BuildContext context) {
    final state = ref.watch(authProvider);

    return Scaffold(
      appBar: AppBar(
        title:
            Text(_isWorker ? 'Register as a worker' : 'Register as a resident'),
      ),
      body: SafeArea(
        child: Form(
          key: _formKey,
          child: ListView(
            padding: const EdgeInsets.all(20),
            children: [
              TextFormField(
                controller: _firstNameController,
                textCapitalization: TextCapitalization.words,
                decoration: InputDecoration(
                  labelText: 'First name',
                  prefixIcon: const Icon(Icons.person_outline),
                  errorText: state.fieldErrors['first_name'],
                ),
                validator: (v) =>
                    (v ?? '').trim().isEmpty ? 'Enter your first name' : null,
              ),
              const SizedBox(height: 14),
              TextFormField(
                controller: _lastNameController,
                textCapitalization: TextCapitalization.words,
                decoration: const InputDecoration(
                  labelText: 'Last name',
                  prefixIcon: Icon(Icons.person_outline),
                ),
              ),
              const SizedBox(height: 14),
              TextFormField(
                controller: _phoneController,
                keyboardType: TextInputType.phone,
                inputFormatters: [
                  FilteringTextInputFormatter.allow(RegExp(r'[0-9+]')),
                  LengthLimitingTextInputFormatter(13),
                ],
                decoration: InputDecoration(
                  labelText: 'Phone number',
                  helperText: 'We will text a code here to verify it',
                  prefixIcon: const Icon(Icons.phone_outlined),
                  errorText: state.fieldErrors['phone_number'],
                ),
                validator: _validatePhone,
              ),
              const SizedBox(height: 14),
              TextFormField(
                controller: _passwordController,
                obscureText: _obscurePassword,
                decoration: InputDecoration(
                  labelText: 'Password',
                  helperText: 'At least 8 characters. Used every time you sign in.',
                  prefixIcon: const Icon(Icons.lock_outline),
                  errorText: state.fieldErrors['password'],
                  suffixIcon: IconButton(
                    icon: Icon(
                      _obscurePassword
                          ? Icons.visibility_outlined
                          : Icons.visibility_off_outlined,
                    ),
                    onPressed: () =>
                        setState(() => _obscurePassword = !_obscurePassword),
                  ),
                ),
                validator: (v) =>
                    (v ?? '').length < 8 ? 'Use at least 8 characters' : null,
              ),
              const SizedBox(height: 28),
              Text(
                'Your society',
                style: Theme.of(context).textTheme.titleMedium,
              ),
              const SizedBox(height: 8),
              _SocietyPicker(
                search: _societySearch,
                selected: _selectedSociety,
                controller: _societySearchController,
                onSearchChanged: (value) =>
                    setState(() => _societySearch = value),
                onSelected: (society) =>
                    setState(() => _selectedSociety = society),
              ),
              if (state.errorMessage != null) ...[
                const SizedBox(height: 16),
                Text(
                  _errorText(state),
                  style: TextStyle(color: Theme.of(context).colorScheme.error),
                ),
              ],
              const SizedBox(height: 28),
              ElevatedButton(
                onPressed: state.isSubmitting ? null : _submit,
                child: state.isSubmitting
                    ? const SizedBox(
                        height: 22,
                        width: 22,
                        child: CircularProgressIndicator(strokeWidth: 2.5),
                      )
                    : const Text('Create account'),
              ),
              const SizedBox(height: 12),
              Text(
                'Your registration is reviewed by your society administrator '
                'before your account becomes active.',
                textAlign: TextAlign.center,
                style: Theme.of(context).textTheme.bodySmall,
              ),
            ],
          ),
        ),
      ),
    );
  }
}

/// Searchable society selector backed by the public society endpoint.
class _SocietyPicker extends ConsumerWidget {
  const _SocietyPicker({
    required this.search,
    required this.selected,
    required this.controller,
    required this.onSearchChanged,
    required this.onSelected,
  });

  final String search;
  final SocietySummary? selected;
  final TextEditingController controller;
  final ValueChanged<String> onSearchChanged;
  final ValueChanged<SocietySummary> onSelected;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final societies = ref.watch(publicSocietiesProvider(search));

    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        TextField(
          controller: controller,
          decoration: const InputDecoration(
            labelText: 'Search by name, city or pincode',
            prefixIcon: Icon(Icons.search),
          ),
          onChanged: onSearchChanged,
        ),
        const SizedBox(height: 12),
        societies.when(
          loading: () => const Padding(
            padding: EdgeInsets.all(16),
            child: Center(child: CircularProgressIndicator()),
          ),
          error: (error, _) => Padding(
            padding: const EdgeInsets.all(12),
            child: Text(
              'Could not load societies. Check your connection and try again.',
              style: TextStyle(color: Theme.of(context).colorScheme.error),
            ),
          ),
          data: (list) {
            if (list.isEmpty) {
              return const Padding(
                padding: EdgeInsets.all(12),
                child: Text(
                  'No societies found. Ask your society administrator to '
                  'register your society on Sathify first.',
                ),
              );
            }
            return ConstrainedBox(
              constraints: const BoxConstraints(maxHeight: 260),
              // RadioGroup replaces the per-tile groupValue/onChanged pair,
              // deprecated after Flutter 3.32. The selection now lives on the
              // ancestor, which is also why each tile below carries only its
              // own value.
              child: RadioGroup<int>(
                groupValue: selected?.id,
                onChanged: (id) {
                  if (id == null) return;
                  onSelected(list.firstWhere((s) => s.id == id));
                },
                child: ListView.builder(
                  shrinkWrap: true,
                  itemCount: list.length,
                  itemBuilder: (context, index) {
                    final society = list[index];
                    return RadioListTile<int>(
                      value: society.id,
                      title: Text(society.name),
                      subtitle: Text(society.subtitle),
                    );
                  },
                ),
              ),
            );
          },
        ),
      ],
    );
  }
}

class _RegistrationSuccessDialog extends StatelessWidget {
  const _RegistrationSuccessDialog({
    required this.isWorker,
    required this.phoneNumber,
  });

  final bool isWorker;
  final String phoneNumber;

  @override
  Widget build(BuildContext context) {
    return AlertDialog(
      icon: const Icon(Icons.check_circle_outline, size: 48),
      title: const Text('Account created'),
      content: Text(
        'We texted a 6-digit code to $phoneNumber. Enter it to verify your '
        'number and finish signing in.\n\n'
        '${isWorker ? 'After that, upload your Aadhaar card and photo to complete verification.' : 'After that, choose your flat so your administrator can approve you.'}',
      ),
      actions: [
        TextButton(
          // Closes the dialog and nothing else. The screen underneath awaits
          // this and handles the route change; popping twice from here reaches
          // past go_router's navigator and crashes.
          onPressed: () => Navigator.of(context).pop(),
          child: const Text('Enter code'),
        ),
      ],
    );
  }
}
