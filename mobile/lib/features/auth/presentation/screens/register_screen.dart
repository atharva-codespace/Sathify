import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../societies/data/models/society_models.dart';
import '../../../societies/presentation/providers/society_provider.dart';
import '../../data/models/user_model.dart';
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

    final succeeded = await register(
      phoneNumber: _phoneController.text.trim(),
      password: _passwordController.text,
      firstName: _firstNameController.text.trim(),
      lastName: _lastNameController.text.trim(),
      societyId: _selectedSociety!.id,
    );

    if (!mounted) return;
    if (succeeded) {
      // Deliberately not awaited: registration is finished either way, and the
      // dialog's dismissal is the user's business, not this method's.
      unawaited(
        showDialog<void>(
          context: context,
          barrierDismissible: false,
          builder: (_) => _RegistrationSuccessDialog(isWorker: _isWorker),
        ),
      );
    }
  }

  String? _validatePhone(String? value) {
    final text = (value ?? '').trim();
    if (text.isEmpty) return 'Enter your phone number';
    if (!RegExp(r'^(\+91)?[6-9]\d{9}$').hasMatch(text)) {
      return 'Enter a valid 10-digit mobile number';
    }
    return null;
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
                  helperText: 'You will sign in with this number',
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
                  helperText: 'At least 8 characters',
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
                  state.errorMessage!,
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
              child: ListView.builder(
                shrinkWrap: true,
                itemCount: list.length,
                itemBuilder: (context, index) {
                  final society = list[index];
                  return RadioListTile<int>(
                    value: society.id,
                    groupValue: selected?.id,
                    title: Text(society.name),
                    subtitle: Text(society.subtitle),
                    onChanged: (_) => onSelected(society),
                  );
                },
              ),
            );
          },
        ),
      ],
    );
  }
}

class _RegistrationSuccessDialog extends StatelessWidget {
  const _RegistrationSuccessDialog({required this.isWorker});

  final bool isWorker;

  @override
  Widget build(BuildContext context) {
    return AlertDialog(
      icon: const Icon(Icons.check_circle_outline, size: 48),
      title: const Text('Account created'),
      content: Text(
        isWorker
            ? 'Next, sign in and upload your Aadhaar card and photo to complete '
                'verification.'
            : 'Next, sign in and choose your flat so your administrator can '
                'approve you.',
      ),
      actions: [
        TextButton(
          onPressed: () {
            Navigator.of(context).pop();
            Navigator.of(context).pop();
          },
          child: const Text('Go to sign in'),
        ),
      ],
    );
  }
}
