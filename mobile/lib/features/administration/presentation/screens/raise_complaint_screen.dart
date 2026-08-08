import 'dart:async';
import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:image_picker/image_picker.dart';

import '../../../../core/errors/api_exception.dart';
import '../../../../shared/design_system.dart';
import '../../../ai/presentation/providers/ai_provider.dart';
import '../../data/models/admin_models.dart';
import '../providers/admin_provider.dart';

/// Module 11.3 — raising a complaint.
///
/// -----------------------------------------------------------------------
/// NO URGENCY FIELD
/// -----------------------------------------------------------------------
/// There is deliberately nothing here labelled "how urgent is this?". The
/// server derives priority from the category, because a self-service urgency
/// picker makes everything urgent within a week and the genuinely urgent ones
/// become indistinguishable. Safety is the one category that jumps the queue,
/// and the copy under it says so plainly rather than hiding the rule.
class RaiseComplaintScreen extends ConsumerStatefulWidget {
  const RaiseComplaintScreen({
    super.key,
    this.againstWorker,
    this.againstResident,
    this.aboutLabel = '',
  });

  /// Prefilled when arriving from a worker's profile or an engagement.
  final int? againstWorker;
  final int? againstResident;
  final String aboutLabel;

  @override
  ConsumerState<RaiseComplaintScreen> createState() =>
      _RaiseComplaintScreenState();
}

class _RaiseComplaintScreenState extends ConsumerState<RaiseComplaintScreen> {
  final _formKey = GlobalKey<FormState>();
  final _subjectController = TextEditingController();
  final _descriptionController = TextEditingController();

  ComplaintCategory _category = ComplaintCategory.lateArrival;
  XFile? _photo;
  bool _submitting = false;
  String? _error;

  /// The text the classifier has been asked about. Held rather than read live
  /// from the controller so a rebuild does not re-ask about the same words —
  /// each distinct question costs a call against a metered free tier.
  String _classifiedText = '';

  Timer? _classifyDebounce;

  @override
  void dispose() {
    _classifyDebounce?.cancel();
    _subjectController.dispose();
    _descriptionController.dispose();
    super.dispose();
  }

  /// Ask Module 12.5 what this looks like, once the person has stopped typing.
  ///
  /// Only after enough words to be worth classifying. Sending three characters
  /// would spend a provider call to be told "other".
  void _onDescriptionChanged(String value) {
    _classifyDebounce?.cancel();

    if (value.trim().length < 25) {
      if (_classifiedText.isNotEmpty) setState(() => _classifiedText = '');
      return;
    }

    _classifyDebounce = Timer(const Duration(milliseconds: 900), () {
      if (mounted) setState(() => _classifiedText = value.trim());
    });
  }

  Future<void> _pickPhoto() async {
    final source = await showModalBottomSheet<ImageSource>(
      context: context,
      builder: (sheetContext) => SafeArea(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            ListTile(
              leading: const Icon(Icons.camera_alt),
              title: const Text('Take a photo'),
              onTap: () => Navigator.of(sheetContext).pop(ImageSource.camera),
            ),
            ListTile(
              leading: const Icon(Icons.photo_library),
              title: const Text('Choose from gallery'),
              onTap: () => Navigator.of(sheetContext).pop(ImageSource.gallery),
            ),
          ],
        ),
      ),
    );
    if (source == null) return;

    // Compressed harder than a KYC scan: nothing here is read by OCR, and this
    // uploads over mobile data from someone who is already annoyed.
    final picked = await ImagePicker().pickImage(
      source: source,
      imageQuality: 75,
      maxWidth: 1400,
    );
    if (picked != null) setState(() => _photo = picked);
  }

  Future<void> _submit() async {
    if (!(_formKey.currentState?.validate() ?? false)) return;

    setState(() {
      _submitting = true;
      _error = null;
    });

    try {
      await ref.read(adminRepositoryProvider).raiseComplaint(
            category: _category,
            subject: _subjectController.text,
            description: _descriptionController.text,
            againstWorker: widget.againstWorker,
            againstResident: widget.againstResident,
            photoPath: _photo?.path,
          );

      invalidateComplaints(ref);
      if (mounted) Navigator.of(context).pop(true);
    } on ApiException catch (error) {
      setState(() => _error = error.message);
    } finally {
      if (mounted) setState(() => _submitting = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Raise a complaint')),
      body: Form(
        key: _formKey,
        child: ListView(
          padding: const EdgeInsets.all(16),
          children: [
            if (widget.aboutLabel.isNotEmpty) ...[
              Card(
                margin: EdgeInsets.zero,
                child: ListTile(
                  leading: const Icon(Icons.person_outline),
                  title: Text('About ${widget.aboutLabel}'),
                ),
              ),
              const SizedBox(height: 16),
            ],
            const Text('What is this about?'),
            const SizedBox(height: 8),
            DropdownButtonFormField<ComplaintCategory>(
              // `initialValue`, not `value` — deprecated after Flutter 3.33.
              initialValue: _category,
              items: [
                for (final category in ComplaintCategory.values)
                  DropdownMenuItem(
                    value: category,
                    child: Text(category.label),
                  ),
              ],
              onChanged: (value) => setState(
                () => _category = value ?? ComplaintCategory.other,
              ),
            ),
            if (_category == ComplaintCategory.safety)
              const Padding(
                padding: EdgeInsets.only(top: 8),
                child: Row(
                  children: [
                    Icon(Icons.bolt, size: 16, color: AppColors.danger),
                    SizedBox(width: 6),
                    Expanded(
                      child: Text(
                        'Safety complaints are treated as urgent and go to the '
                        'front of the queue.',
                        style: TextStyle(fontSize: 12, color: AppColors.danger),
                      ),
                    ),
                  ],
                ),
              ),
            const SizedBox(height: 20),
            TextFormField(
              controller: _subjectController,
              maxLength: 150,
              decoration: const InputDecoration(
                labelText: 'In one line',
                hintText: 'e.g. Did not arrive on Tuesday',
              ),
              validator: (value) => (value ?? '').trim().isEmpty
                  ? 'Give it a short title.'
                  : null,
            ),
            const SizedBox(height: 12),
            TextFormField(
              controller: _descriptionController,
              maxLines: 6,
              maxLength: 2000,
              onChanged: _onDescriptionChanged,
              decoration: const InputDecoration(
                labelText: 'What happened?',
                alignLabelWithHint: true,
              ),
              validator: (value) => (value ?? '').trim().length < 10
                  ? 'A few more words will help your administrator act on this.'
                  : null,
            ),
            if (_classifiedText.isNotEmpty)
              _CategorySuggestion(
                text: _classifiedText,
                chosen: _category,
                onApply: (category) => setState(() => _category = category),
              ),
            const SizedBox(height: 8),
            _PhotoField(
              photo: _photo,
              onPick: _pickPhoto,
              onClear: () => setState(() => _photo = null),
            ),
            if (_error != null) ...[
              const SizedBox(height: 16),
              Text(_error!, style: const TextStyle(color: AppColors.danger)),
            ],
            const SizedBox(height: 24),
            ElevatedButton.icon(
              onPressed: _submitting ? null : _submit,
              icon: _submitting
                  ? const SizedBox(
                      width: 20,
                      height: 20,
                      child: CircularProgressIndicator(strokeWidth: 2),
                    )
                  : const Icon(Icons.send),
              label:
                  Text(_submitting ? 'Sending…' : 'Send to my administrator'),
            ),
            const SizedBox(height: 12),
            const Text(
              'Your administrator is notified straight away, and you can follow '
              'what happens to this from your complaints list.',
              style: TextStyle(fontSize: 12, color: AppColors.textSecondary),
            ),
          ],
        ),
      ),
    );
  }
}

/// Module 12.5's suggestion, offered rather than applied.
///
/// -----------------------------------------------------------------------
/// THE PERSON RAISING THE COMPLAINT DECIDES
/// -----------------------------------------------------------------------
/// This never changes the category on its own. The server takes the same view:
/// it records a disagreement as an internal note and files the complaint under
/// whatever was chosen. Silently reclassifying somebody's safety report as
/// "quality" would move it out of the queue position they were promised, and
/// they would have no way to know it had happened.
///
/// It only appears when the classifier is confident *and* disagrees. A banner
/// agreeing with the obvious on every complaint is noise, and noise on a form
/// somebody is filling in while upset is worse than nothing.
class _CategorySuggestion extends ConsumerWidget {
  const _CategorySuggestion({
    required this.text,
    required this.chosen,
    required this.onApply,
  });

  final String text;
  final ComplaintCategory chosen;
  final ValueChanged<ComplaintCategory> onApply;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final suggestion = ref.watch(complaintSuggestionProvider(text));

    return suggestion.maybeWhen(
      orElse: () => const SizedBox.shrink(),
      data: (result) {
        final suggested = ComplaintCategory.fromWire(result.category);
        if (!result.isConfident || suggested == chosen) {
          return const SizedBox.shrink();
        }

        return Card(
          margin: const EdgeInsets.only(top: 8),
          color: AppColors.info.withValues(alpha: 0.08),
          elevation: 0,
          child: Padding(
            padding: const EdgeInsets.fromLTRB(12, 10, 12, 4),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(
                  children: [
                    const Icon(
                      Icons.lightbulb_outline,
                      size: 16,
                      color: AppColors.info,
                    ),
                    const SizedBox(width: 8),
                    Expanded(
                      child: Text(
                        'This reads more like "${suggested.label}".',
                        style: const TextStyle(fontSize: 13),
                      ),
                    ),
                  ],
                ),
                Row(
                  mainAxisAlignment: MainAxisAlignment.end,
                  children: [
                    TextButton(
                      onPressed: () => onApply(suggested),
                      child: Text('Use ${suggested.label}'),
                    ),
                  ],
                ),
              ],
            ),
          ),
        );
      },
    );
  }
}

class _PhotoField extends StatelessWidget {
  const _PhotoField({
    required this.photo,
    required this.onPick,
    required this.onClear,
  });

  final XFile? photo;
  final VoidCallback onPick;
  final VoidCallback onClear;

  @override
  Widget build(BuildContext context) {
    if (photo == null) {
      return OutlinedButton.icon(
        onPressed: onPick,
        icon: const Icon(Icons.add_a_photo_outlined),
        label: const Text('Add a photo (optional)'),
      );
    }

    return Card(
      margin: EdgeInsets.zero,
      child: Column(
        children: [
          ClipRRect(
            borderRadius: const BorderRadius.vertical(top: Radius.circular(14)),
            child: Image.file(
              File(photo!.path),
              height: 180,
              width: double.infinity,
              fit: BoxFit.cover,
            ),
          ),
          Row(
            mainAxisAlignment: MainAxisAlignment.end,
            children: [
              TextButton(onPressed: onPick, child: const Text('Replace')),
              TextButton(
                onPressed: onClear,
                style: TextButton.styleFrom(foregroundColor: AppColors.danger),
                child: const Text('Remove'),
              ),
            ],
          ),
        ],
      ),
    );
  }
}
