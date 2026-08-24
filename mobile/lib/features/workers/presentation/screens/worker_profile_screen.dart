import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:image_picker/image_picker.dart';

import '../../../../core/errors/api_exception.dart';
import '../../../../shared/design_system.dart';
import '../../data/models/worker_models.dart';
import '../providers/worker_provider.dart';

/// Module 3.1 — the worker enters their own details.
///
/// Serves both creation and editing: the same fields either way, and which verb
/// to use is decided from whether a profile already exists rather than by the
/// caller passing a flag.
class WorkerProfileScreen extends ConsumerStatefulWidget {
  const WorkerProfileScreen({super.key});

  @override
  ConsumerState<WorkerProfileScreen> createState() =>
      _WorkerProfileScreenState();
}

class _WorkerProfileScreenState extends ConsumerState<WorkerProfileScreen> {
  final _formKey = GlobalKey<FormState>();
  final _experienceController = TextEditingController();
  final _bioController = TextEditingController();
  final _languagesController = TextEditingController();

  /// What the platform pays for this work, as the server reported it. Shown,
  /// never edited — see the pay section in build().
  int? _platformRate;

  final Set<int> _selectedServices = {};
  TimeOfDay? _availableFrom;
  TimeOfDay? _availableUntil;
  bool _isAvailable = true;

  XFile? _newPhoto;
  String? _existingPhotoUrl;

  bool _seeded = false;
  bool _isSaving = false;
  String? _error;

  @override
  void dispose() {
    _experienceController.dispose();
    _bioController.dispose();
    _languagesController.dispose();
    super.dispose();
  }

  void _seed(WorkerProfile? profile) {
    if (_seeded) return;
    _seeded = true;
    if (profile == null) return;

    _selectedServices.addAll(profile.serviceTypes.map((s) => s.id));
    _experienceController.text = '${profile.yearsOfExperience}';
    _bioController.text = profile.bio;
    _languagesController.text = profile.languagesSpoken;
    _platformRate = profile.expectedMonthlyRate;
    _isAvailable = profile.isAvailable;
    _availableFrom = _parseTime(profile.availableFrom);
    _availableUntil = _parseTime(profile.availableUntil);
    _existingPhotoUrl = profile.photoUrl;
  }

  TimeOfDay? _parseTime(String? value) {
    if (value == null || value.isEmpty) return null;
    final parts = value.split(':');
    if (parts.length < 2) return null;
    final hour = int.tryParse(parts[0]);
    final minute = int.tryParse(parts[1]);
    if (hour == null || minute == null) return null;
    return TimeOfDay(hour: hour, minute: minute);
  }

  String _wire(TimeOfDay time) => '${time.hour.toString().padLeft(2, '0')}:'
      '${time.minute.toString().padLeft(2, '0')}';

  Future<void> _pickPhoto(ImageSource source) async {
    final picked = await ImagePicker().pickImage(
      source: source,
      imageQuality: 80, // keeps uploads small on a metered connection
      maxWidth: 1200,
    );
    if (picked != null) setState(() => _newPhoto = picked);
  }

  Future<void> _choosePhotoSource() async {
    final source = await showModalBottomSheet<ImageSource>(
      context: context,
      builder: (_) => SafeArea(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            ListTile(
              leading: const Icon(Icons.camera_alt),
              title: const Text('Take a photo'),
              onTap: () => Navigator.of(context).pop(ImageSource.camera),
            ),
            ListTile(
              leading: const Icon(Icons.photo_library),
              title: const Text('Choose from gallery'),
              onTap: () => Navigator.of(context).pop(ImageSource.gallery),
            ),
          ],
        ),
      ),
    );
    if (source != null) await _pickPhoto(source);
  }

  Future<void> _save({required bool creating}) async {
    setState(() => _error = null);

    if (!_formKey.currentState!.validate()) return;
    if (_selectedServices.isEmpty) {
      setState(() => _error = 'Choose at least one kind of work you do.');
      return;
    }
    // Both or neither — the server rejects half a window, and catching it here
    // saves a round trip on a poor connection.
    if ((_availableFrom == null) != (_availableUntil == null)) {
      setState(() => _error = 'Set both a start and an end time, or neither.');
      return;
    }

    final draft = WorkerProfileDraft(
      serviceTypeIds: _selectedServices.toList()..sort(),
      yearsOfExperience: int.tryParse(_experienceController.text.trim()) ?? 0,
      bio: _bioController.text.trim(),
      languagesSpoken: _languagesController.text.trim(),
      // No rate: the server sets pay, and sending one would be silently
      // dropped. Omitted rather than passed through so this screen never
      // implies the worker had a say in the figure.
      isAvailable: _isAvailable,
      availableFrom: _availableFrom == null ? null : _wire(_availableFrom!),
      availableUntil: _availableUntil == null ? null : _wire(_availableUntil!),
    );

    setState(() => _isSaving = true);
    try {
      final repository = ref.read(workerRepositoryProvider);
      if (creating) {
        await repository.createProfile(draft, photoPath: _newPhoto?.path);
      } else {
        await repository.updateProfile(draft, photoPath: _newPhoto?.path);
      }

      if (!mounted) return;
      invalidateOnboarding(ref);
      Navigator.of(context).pop(true);
    } on ApiException catch (error) {
      if (!mounted) return;
      setState(() {
        _isSaving = false;
        _error = error.fieldError('service_types') ??
            error.fieldError('available_from') ??
            error.fieldError('available_until') ??
            error.message;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final profile = ref.watch(myWorkerProfileProvider);
    final services = ref.watch(serviceTypesProvider);

    return Scaffold(
      appBar: AppBar(title: const Text('Your details')),
      body: profile.when(
        loading: () => const AppSkeletonList(count: 4),
        error: (error, _) => AppErrorState(
          message: error is ApiException
              ? error.message
              : 'Could not load your profile.',
          onRetry: () => ref.invalidate(myWorkerProfileProvider),
        ),
        data: (existing) {
          _seed(existing);
          return _form(context, existing == null, services);
        },
      ),
    );
  }

  Widget _form(
    BuildContext context,
    bool creating,
    AsyncValue<List<ServiceType>> services,
  ) {
    return Form(
      key: _formKey,
      child: ListView(
        padding: const EdgeInsets.fromLTRB(
          AppSpacing.gutter,
          AppSpacing.md,
          AppSpacing.gutter,
          AppSpacing.xxl,
        ),
        children: [
          _PhotoPicker(
            newPhoto: _newPhoto,
            existingUrl: _existingPhotoUrl,
            onTap: _choosePhotoSource,
          ),
          const SizedBox(height: 24),
          const _Label('What work do you do?'),
          const SizedBox(height: 8),
          services.when(
            loading: () => const Padding(
              padding: EdgeInsets.symmetric(vertical: AppSpacing.xs),
              child: Row(
                children: [
                  AppSkeleton(width: 90, height: 34),
                  SizedBox(width: AppSpacing.xs),
                  AppSkeleton(width: 74, height: 34),
                  SizedBox(width: AppSpacing.xs),
                  AppSkeleton(width: 96, height: 34),
                ],
              ),
            ),
            error: (_, __) =>
                const Text('Could not load the list of services.'),
            data: (options) {
              if (options.isEmpty) {
                return const Text(
                  'Your society has not set up any services yet. Ask your '
                  'administrator to add them.',
                );
              }
              return Wrap(
                spacing: 8,
                runSpacing: 4,
                children: options
                    .map(
                      (service) => FilterChip(
                        label: Text(service.name),
                        selected: _selectedServices.contains(service.id),
                        onSelected: (selected) => setState(() {
                          if (selected) {
                            _selectedServices.add(service.id);
                          } else {
                            _selectedServices.remove(service.id);
                          }
                        }),
                      ),
                    )
                    .toList(),
              );
            },
          ),
          const SizedBox(height: 24),
          const _Label('Years of experience'),
          const SizedBox(height: 8),
          TextFormField(
            controller: _experienceController,
            keyboardType: TextInputType.number,
            decoration: const InputDecoration(hintText: 'e.g. 5'),
            validator: (value) {
              final years = int.tryParse((value ?? '').trim());
              if (value != null && value.trim().isNotEmpty && years == null) {
                return 'Enter a number.';
              }
              if (years != null && years > 60) return 'That seems too high.';
              return null;
            },
          ),
          const SizedBox(height: 20),
          const _Label('Languages you speak'),
          const SizedBox(height: 8),
          TextFormField(
            controller: _languagesController,
            decoration: const InputDecoration(hintText: 'e.g. Hindi, Marathi'),
          ),
          const SizedBox(height: 20),
          const _Label('Your monthly pay'),
          const SizedBox(height: 8),
          // Read-only on purpose. Pay is set by Sathify at one rate for
          // everybody, which is what stops a resident bargaining an individual
          // helper down. Showing the figure here — rather than hiding the
          // section — means a worker can see what they are owed.
          Container(
            width: double.infinity,
            padding: const EdgeInsets.symmetric(
              horizontal: AppSpacing.sm,
              vertical: AppSpacing.sm,
            ),
            decoration: BoxDecoration(
              color: AppColors.surfaceMuted,
              borderRadius: BorderRadius.circular(AppRadius.md),
            ),
            child: Row(
              children: [
                const Icon(Icons.currency_rupee, size: AppIconSize.md),
                const SizedBox(width: AppSpacing.xs),
                Expanded(
                  child: Text(
                    _platformRate == null
                        ? 'Shown once your profile is approved'
                        : '$_platformRate per month',
                    style: Theme.of(context).textTheme.titleMedium,
                  ),
                ),
                Text(
                  'Set by Sathify',
                  style: Theme.of(context).textTheme.bodySmall,
                ),
              ],
            ),
          ),
          const SizedBox(height: 20),
          const _Label('About you (optional)'),
          const SizedBox(height: 8),
          TextFormField(
            controller: _bioController,
            maxLines: 3,
            maxLength: 500,
            decoration: const InputDecoration(
              hintText: 'Anything a resident should know',
            ),
          ),
          const _Label('Your usual hours'),
          const SizedBox(height: 4),
          const Text(
            'Optional. Leaving this blank means you are open to any hours.',
            style: TextStyle(fontSize: 13, color: AppColors.textSecondary),
          ),
          const SizedBox(height: 8),
          Row(
            children: [
              Expanded(
                child: OutlinedButton(
                  onPressed: () async {
                    final picked = await showTimePicker(
                      context: context,
                      initialTime:
                          _availableFrom ?? const TimeOfDay(hour: 8, minute: 0),
                    );
                    if (picked != null) setState(() => _availableFrom = picked);
                  },
                  style: OutlinedButton.styleFrom(
                    minimumSize: const Size.fromHeight(AppTheme.minTouchTarget),
                  ),
                  child: Text(
                    _availableFrom == null
                        ? 'From'
                        : 'From ${_availableFrom!.format(context)}',
                  ),
                ),
              ),
              const SizedBox(width: 12),
              Expanded(
                child: OutlinedButton(
                  onPressed: () async {
                    final picked = await showTimePicker(
                      context: context,
                      initialTime: _availableUntil ??
                          const TimeOfDay(hour: 18, minute: 0),
                    );
                    if (picked != null) {
                      setState(() => _availableUntil = picked);
                    }
                  },
                  style: OutlinedButton.styleFrom(
                    minimumSize: const Size.fromHeight(AppTheme.minTouchTarget),
                  ),
                  child: Text(
                    _availableUntil == null
                        ? 'To'
                        : 'To ${_availableUntil!.format(context)}',
                  ),
                ),
              ),
            ],
          ),
          SwitchListTile(
            contentPadding: EdgeInsets.zero,
            title: const Text('Available for work'),
            subtitle: const Text(
              'Turn this off to hide yourself from search without leaving',
            ),
            value: _isAvailable,
            onChanged: (value) => setState(() => _isAvailable = value),
          ),
          if (_error != null) ...[
            const SizedBox(height: AppSpacing.xs),
            AppErrorBanner(message: _error!),
          ],
          const SizedBox(height: AppSpacing.md),
          AppButton(
            label: 'Save',
            icon: Icons.check_rounded,
            isLoading: _isSaving,
            onPressed: () => _save(creating: creating),
          ),
        ],
      ),
    );
  }
}

class _PhotoPicker extends StatelessWidget {
  const _PhotoPicker({
    required this.newPhoto,
    required this.existingUrl,
    required this.onTap,
  });

  final XFile? newPhoto;
  final String? existingUrl;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final hasAny = newPhoto != null || (existingUrl?.isNotEmpty ?? false);

    return Center(
      child: Column(
        children: [
          GestureDetector(
            onTap: onTap,
            child: CircleAvatar(
              radius: 56,
              backgroundColor: theme.colorScheme.primaryContainer,
              // A newly picked file wins over the stored one, so the worker
              // sees what they are about to upload rather than what is saved.
              backgroundImage: newPhoto != null
                  ? FileImage(File(newPhoto!.path)) as ImageProvider
                  : (existingUrl?.isNotEmpty ?? false)
                      ? NetworkImage(existingUrl!)
                      : null,
              child: hasAny
                  ? null
                  : const Icon(Icons.add_a_photo_outlined, size: 34),
            ),
          ),
          const SizedBox(height: 10),
          TextButton.icon(
            onPressed: onTap,
            icon: const Icon(Icons.camera_alt_outlined, size: 18),
            label: Text(hasAny ? 'Change photo' : 'Add your photo'),
          ),
          const Padding(
            padding: EdgeInsets.symmetric(horizontal: 24),
            child: Text(
              'The guard at the gate checks this photo against your face, so a '
              'clear picture of just you works best.',
              textAlign: TextAlign.center,
              style: TextStyle(fontSize: 13, color: AppColors.textSecondary),
            ),
          ),
        ],
      ),
    );
  }
}

class _Label extends StatelessWidget {
  const _Label(this.text);

  final String text;

  @override
  Widget build(BuildContext context) => Text(
        text,
        style: const TextStyle(fontWeight: FontWeight.w600, fontSize: 15),
      );
}
