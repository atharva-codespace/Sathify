import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:image_picker/image_picker.dart';

import '../../../../core/errors/api_exception.dart';
import '../../../../shared/design_system.dart';
import '../../data/models/society_models.dart';
import '../providers/society_provider.dart';

/// Module 2.3 — a registered resident claims their flat and submits proof.
///
/// Reachable while the account is still unapproved: this submission is exactly
/// what the administrator reviews in order to approve.
class ClaimFlatScreen extends ConsumerStatefulWidget {
  const ClaimFlatScreen({super.key});

  @override
  ConsumerState<ClaimFlatScreen> createState() => _ClaimFlatScreenState();
}

class _ClaimFlatScreenState extends ConsumerState<ClaimFlatScreen> {
  Tower? _selectedTower;
  Flat? _selectedFlat;
  ResidentRelationship _relationship = ResidentRelationship.owner;
  XFile? _proofDocument;
  bool _isSubmitting = false;
  String? _error;

  Future<void> _pickProof() async {
    final picked = await ImagePicker().pickImage(
      source: ImageSource.gallery,
      imageQuality: 80, // keeps uploads small on a metered connection
    );
    if (picked != null) setState(() => _proofDocument = picked);
  }

  Future<void> _submit() async {
    if (_selectedFlat == null) {
      setState(() => _error = 'Choose your flat to continue.');
      return;
    }

    setState(() {
      _isSubmitting = true;
      _error = null;
    });

    try {
      await ref.read(societyRepositoryProvider).claimFlat(
            flatId: _selectedFlat!.id,
            relationship: _relationship,
            proofDocumentPath: _proofDocument?.path,
          );
      if (!mounted) return;
      ref.invalidate(myResidentProfileProvider);
      Navigator.of(context).pop(true);
    } on ApiException catch (error) {
      setState(() {
        _error = error.fieldError('flat') ?? error.message;
        _isSubmitting = false;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final towers = ref.watch(towersProvider);

    return Scaffold(
      appBar: AppBar(title: const Text('Choose your flat')),
      body: SafeArea(
        child: ListView(
          padding: const EdgeInsets.all(20),
          children: [
            Text(
              'Tell us where you live so your administrator can verify you.',
              style: Theme.of(context).textTheme.bodyLarge,
            ),
            const SizedBox(height: 24),
            towers.when(
              // A field-shaped placeholder, not a page-shaped one: this `when`
              // wraps a single dropdown, so a full skeleton list would be
              // wildly out of scale.
              loading: () => const AppSkeleton(height: 56, width: double.infinity),
              error: (_, __) =>
                  const Text('Could not load towers. Pull to retry.'),
              data: (list) => DropdownButtonFormField<Tower>(
                initialValue: _selectedTower,
                decoration: const InputDecoration(
                  labelText: 'Tower / Wing',
                  prefixIcon: Icon(Icons.apartment_outlined),
                ),
                items: list
                    .map((t) => DropdownMenuItem(value: t, child: Text(t.name)))
                    .toList(),
                onChanged: (tower) => setState(() {
                  _selectedTower = tower;
                  // Clear the flat: it belonged to the previous tower.
                  _selectedFlat = null;
                }),
              ),
            ),
            const SizedBox(height: 16),
            if (_selectedTower != null)
              Consumer(
                builder: (context, ref, _) {
                  final flats = ref.watch(flatsProvider(_selectedTower!.id));
                  return flats.when(
                    loading: () =>
                        const AppSkeleton(height: 56, width: double.infinity),
                    error: (_, __) => const Text('Could not load flats.'),
                    data: (list) => DropdownButtonFormField<Flat>(
                      initialValue: _selectedFlat,
                      decoration: const InputDecoration(
                        labelText: 'Flat number',
                        prefixIcon: Icon(Icons.home_outlined),
                      ),
                      items: list
                          .map(
                            (f) => DropdownMenuItem(
                              value: f,
                              child: Text(
                                f.isOccupied
                                    ? '${f.number}  (shared)'
                                    : f.number,
                              ),
                            ),
                          )
                          .toList(),
                      onChanged: (flat) => setState(() => _selectedFlat = flat),
                    ),
                  );
                },
              ),
            const SizedBox(height: 16),
            DropdownButtonFormField<ResidentRelationship>(
              initialValue: _relationship,
              decoration: const InputDecoration(
                labelText: 'You are the',
                prefixIcon: Icon(Icons.badge_outlined),
              ),
              items: ResidentRelationship.values
                  .map((r) => DropdownMenuItem(value: r, child: Text(r.label)))
                  .toList(),
              onChanged: (value) => setState(
                () => _relationship = value ?? ResidentRelationship.owner,
              ),
            ),
            const SizedBox(height: 24),
            OutlinedButton.icon(
              onPressed: _pickProof,
              icon: const Icon(Icons.upload_file_outlined),
              label: Text(
                _proofDocument == null
                    ? 'Attach proof of residence'
                    : 'Attached: ${_proofDocument!.name}',
              ),
            ),
            const SizedBox(height: 4),
            Text(
              'Rent agreement, sale deed or a utility bill. Optional, but it '
              'speeds up approval.',
              style: Theme.of(context).textTheme.bodySmall,
            ),
            if (_error != null) ...[
              const SizedBox(height: 16),
              Text(
                _error!,
                style: TextStyle(color: Theme.of(context).colorScheme.error),
              ),
            ],
            const SizedBox(height: 28),
            ElevatedButton(
              onPressed: _isSubmitting ? null : _submit,
              child: _isSubmitting
                  ? const SizedBox(
                      height: 22,
                      width: 22,
                      child: CircularProgressIndicator(strokeWidth: 2.5),
                    )
                  : const Text('Submit for approval'),
            ),
          ],
        ),
      ),
    );
  }
}
