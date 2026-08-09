import 'package:flutter/material.dart';
import 'package:flutter/services.dart' show PlatformException;
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

  /// Opens the gallery. Never lets the plugin's failure be silence.
  ///
  /// `pickImage` throws a `PlatformException` for the ordinary cases — gallery
  /// permission refused, no picker on the device, the plugin failing to return
  /// a file. Unguarded, every one of those made the button do *nothing*
  /// visible, which is indistinguishable from a dead button and is how "can't
  /// attach the proof image" was reported.
  Future<void> _pickProof() async {
    try {
      final picked = await ImagePicker().pickImage(
        source: ImageSource.gallery,
        imageQuality: 80, // keeps uploads small on a metered connection
      );
      // A null result is the user backing out of the picker, not a failure.
      if (picked == null || !mounted) return;
      setState(() {
        _proofDocument = picked;
        _error = null;
      });
    } on PlatformException catch (error) {
      if (!mounted) return;
      setState(() {
        _error = error.code == 'photo_access_denied'
            ? 'Sathify needs permission to open your photos. You can grant it '
                'in your phone settings.'
            : 'Could not open your photos. Please try again.';
      });
    } catch (_) {
      if (!mounted) return;
      setState(() => _error = 'Could not open your photos. Please try again.');
    }
  }

  Future<void> _submit() async {
    if (_selectedFlat == null) {
      setState(() => _error = 'Choose your flat to continue.');
      return;
    }
    // The administrator approves against the document; a claim submitted
    // without one is a queue item they can only reject, so it is refused here
    // rather than accepted and left to stall.
    if (_proofDocument == null) {
      setState(
        () => _error = 'Attach your proof of residence to continue.',
      );
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
            proofDocumentPath: _proofDocument!.path,
          );
      if (!mounted) return;
      ref.invalidate(myResidentProfileProvider);
      Navigator.of(context).pop(true);
    } on ApiException catch (error) {
      if (!mounted) return;
      setState(() => _error = error.fieldError('flat') ?? error.message);
    } catch (error, stackTrace) {
      // Anything that is not an ApiException: the file vanished between
      // picking and uploading, a multipart encoding failure, a plugin throwing
      // on a path it cannot read. These used to escape the handler entirely,
      // which left the button spinning for ever with nothing said.
      if (!mounted) return;
      debugPrint('Flat claim failed: $error\n$stackTrace');
      setState(
        () => _error = 'Could not submit your claim. Please try again.',
      );
    } finally {
      // On every branch, including the success path if the pop is skipped.
      if (mounted) setState(() => _isSubmitting = false);
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
              onPressed: _isSubmitting ? null : _pickProof,
              icon: Icon(
                _proofDocument == null
                    ? Icons.upload_file_outlined
                    : Icons.check_circle_outline,
              ),
              label: Text(
                _proofDocument == null
                    ? 'Attach proof of residence'
                    : 'Attached: ${_proofDocument!.name}',
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
              ),
              style: OutlinedButton.styleFrom(
                foregroundColor:
                    _proofDocument == null ? null : AppColors.success,
                minimumSize: const Size.fromHeight(52),
              ),
            ),
            const SizedBox(height: 4),
            Text(
              // Was "Optional, but it speeds up approval" while the form also
              // accepted an empty submission. It is required now, and the copy
              // has to say so before the button does.
              'Required: rent agreement, sale deed or a utility bill.',
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
