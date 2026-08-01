import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:image_picker/image_picker.dart';

import '../../../../core/errors/api_exception.dart';
import '../../../../shared/design_system.dart';
import '../../data/models/worker_models.dart';
import '../providers/worker_provider.dart';

/// Modules 3.2, 3.3, 3.4 and 3.6 — the Aadhaar step.
///
/// Three things this screen must get right:
///
/// * **Consent is not a formality.** It is captured in the same request as the
///   upload because the DPDP Act wants it at the point of collection, and the
///   server stores nothing without it. So the tick is a gate, not a checkbox
///   the user can skip past.
/// * **OCR is a convenience, never an authority.** Whatever is read is offered
///   for the worker to confirm or correct. A document that could not be read at
///   all is not a dead end — the same form is how they type it in.
/// * **The age gate is final.** If the card shows an age under 18 the server
///   rejects the registration outright, and there is no path forward from that
///   screen. Pretending otherwise would only waste the person's time.
class KycUploadScreen extends ConsumerStatefulWidget {
  const KycUploadScreen({super.key});

  @override
  ConsumerState<KycUploadScreen> createState() => _KycUploadScreenState();
}

class _KycUploadScreenState extends ConsumerState<KycUploadScreen> {
  XFile? _document;
  bool _consented = false;
  bool _isUploading = false;
  String? _error;

  KycUploadResult? _result;

  Future<void> _pickDocument(ImageSource source) async {
    final picked = await ImagePicker().pickImage(
      source: source,
      // Higher quality than a profile photo: OCR accuracy depends on it, and a
      // re-upload costs the worker far more than the extra kilobytes.
      imageQuality: 92,
      maxWidth: 2000,
    );
    if (picked != null) setState(() => _document = picked);
  }

  Future<void> _chooseSource() async {
    final source = await showModalBottomSheet<ImageSource>(
      context: context,
      builder: (_) => SafeArea(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            ListTile(
              leading: const Icon(Icons.camera_alt),
              title: const Text('Photograph your card'),
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
    if (source != null) await _pickDocument(source);
  }

  Future<void> _upload() async {
    if (_document == null) {
      setState(() => _error = 'Take a photo of your Aadhaar card first.');
      return;
    }
    if (!_consented) {
      setState(() => _error = 'We need your permission before we can use it.');
      return;
    }

    setState(() {
      _isUploading = true;
      _error = null;
    });

    try {
      final result = await ref.read(workerRepositoryProvider).uploadAadhaar(
            documentPath: _document!.path,
            consent: true,
          );
      if (!mounted) return;
      invalidateOnboarding(ref);
      setState(() {
        _isUploading = false;
        _result = result;
      });
    } on ApiException catch (error) {
      if (!mounted) return;
      setState(() {
        _isUploading = false;
        _error = error.fieldError('document') ??
            error.fieldError('consent') ??
            error.message;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final result = _result;

    return Scaffold(
      appBar: AppBar(title: const Text('Your Aadhaar card')),
      body: result == null
          ? _uploadForm(context)
          : result.autoRejected
              ? _RejectedPanel(message: result.message)
              : _ConfirmPanel(
                  document: result.document,
                  message: result.message,
                  onDone: () => Navigator.of(context).pop(true),
                ),
    );
  }

  Widget _uploadForm(BuildContext context) {
    return ListView(
      padding: const EdgeInsets.all(16),
      children: [
        _DocumentPreview(document: _document, onTap: _chooseSource),
        const SizedBox(height: 20),
        const _Guidance(),
        const SizedBox(height: 20),
        _ConsentBox(
          value: _consented,
          onChanged: (value) => setState(() {
            _consented = value;
            _error = null;
          }),
        ),
        if (_error != null) ...[
          const SizedBox(height: 12),
          Text(_error!, style: const TextStyle(color: AppColors.danger)),
        ],
        const SizedBox(height: 20),
        ElevatedButton.icon(
          onPressed: _isUploading ? null : _upload,
          icon: _isUploading
              ? const SizedBox(
                  width: 20,
                  height: 20,
                  child: CircularProgressIndicator(strokeWidth: 2),
                )
              : const Icon(Icons.upload_file),
          label: Text(_isUploading ? 'Reading your card…' : 'Upload'),
        ),
        if (_isUploading) ...[
          const SizedBox(height: 10),
          const Text(
            'This can take a few moments. Please keep the app open.',
            textAlign: TextAlign.center,
            style: TextStyle(fontSize: 13, color: AppColors.textSecondary),
          ),
        ],
        const SizedBox(height: 24),
      ],
    );
  }
}

class _DocumentPreview extends StatelessWidget {
  const _DocumentPreview({required this.document, required this.onTap});

  final XFile? document;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    return GestureDetector(
      onTap: onTap,
      child: Container(
        height: 200,
        decoration: BoxDecoration(
          color: AppColors.surfaceMuted,
          borderRadius: BorderRadius.circular(14),
          border: Border.all(color: AppColors.textTertiary),
          image: document == null
              ? null
              : DecorationImage(
                  image: FileImage(File(document!.path)),
                  fit: BoxFit.cover,
                ),
        ),
        child: document != null
            ? null
            : const Center(
                child: Column(
                  mainAxisAlignment: MainAxisAlignment.center,
                  children: [
                    Icon(
                      Icons.add_a_photo_outlined,
                      size: 40,
                      color: Colors.black38,
                    ),
                    SizedBox(height: 10),
                    Text('Tap to photograph the front of your card'),
                  ],
                ),
              ),
      ),
    );
  }
}

class _Guidance extends StatelessWidget {
  const _Guidance();

  @override
  Widget build(BuildContext context) {
    return const Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(
          'For the best reading',
          style: TextStyle(fontWeight: FontWeight.w600, fontSize: 15),
        ),
        SizedBox(height: 8),
        _Tip('Lay the card flat on a plain surface'),
        _Tip('Fill most of the frame with the card'),
        _Tip('Avoid glare and shadows'),
      ],
    );
  }
}

class _Tip extends StatelessWidget {
  const _Tip(this.text);

  final String text;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 4),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const Icon(Icons.check, size: 16, color: AppColors.success),
          const SizedBox(width: 8),
          Expanded(child: Text(text, style: const TextStyle(fontSize: 14))),
        ],
      ),
    );
  }
}

/// Module 3.6 — consent, in the same step as the upload.
class _ConsentBox extends StatelessWidget {
  const _ConsentBox({required this.value, required this.onChanged});

  final bool value;
  final ValueChanged<bool> onChanged;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.all(4),
      decoration: BoxDecoration(
        color: AppColors.info.withValues(alpha: 0.06),
        borderRadius: BorderRadius.circular(12),
        border: Border.all(color: AppColors.info.withValues(alpha: 0.3)),
      ),
      child: CheckboxListTile(
        value: value,
        onChanged: (checked) => onChanged(checked ?? false),
        controlAffinity: ListTileControlAffinity.leading,
        title: const Text(
          'I agree to Sathify checking my identity with this document',
          style: TextStyle(fontWeight: FontWeight.w600),
        ),
        subtitle: const Text(
          'Your full Aadhaar number is never stored — only the last four digits '
          'are kept, and only to show you which card this was. You can withdraw '
          'this permission later.',
          style: TextStyle(fontSize: 13),
        ),
        isThreeLine: true,
      ),
    );
  }
}

/// Module 3.4 — the age gate fired. There is no way forward from here, and the
/// screen says so plainly rather than leaving a retry button that cannot work.
class _RejectedPanel extends StatelessWidget {
  const _RejectedPanel({required this.message});

  final String message;

  @override
  Widget build(BuildContext context) {
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(32),
        child: Column(
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            const Icon(Icons.block, size: 72, color: AppColors.danger),
            const SizedBox(height: 20),
            Text(
              'We cannot register you',
              style: Theme.of(context)
                  .textTheme
                  .titleLarge
                  ?.copyWith(fontWeight: FontWeight.w700),
            ),
            const SizedBox(height: 12),
            // The server's own wording, verbatim — the app must not soften or
            // reinterpret a decision that has already been recorded.
            Text(message, textAlign: TextAlign.center),
            const SizedBox(height: 24),
            const Text(
              'If you believe this is a mistake, speak to your society office.',
              textAlign: TextAlign.center,
              style: TextStyle(fontSize: 13, color: AppColors.textSecondary),
            ),
          ],
        ),
      ),
    );
  }
}

/// Modules 3.2/3.3 — confirm or correct what was read.
class _ConfirmPanel extends ConsumerStatefulWidget {
  const _ConfirmPanel({
    required this.document,
    required this.message,
    required this.onDone,
  });

  final KycDocument document;
  final String message;
  final VoidCallback onDone;

  @override
  ConsumerState<_ConfirmPanel> createState() => _ConfirmPanelState();
}

class _ConfirmPanelState extends ConsumerState<_ConfirmPanel> {
  late final _nameController =
      TextEditingController(text: widget.document.extractedName);
  late final _dobController =
      TextEditingController(text: widget.document.extractedDob);
  late final _genderController =
      TextEditingController(text: widget.document.extractedGender);
  final _aadhaarController = TextEditingController();

  bool _isSaving = false;
  String? _error;
  bool _saved = false;

  @override
  void dispose() {
    _nameController.dispose();
    _dobController.dispose();
    _genderController.dispose();
    _aadhaarController.dispose();
    super.dispose();
  }

  Future<void> _confirm() async {
    setState(() {
      _isSaving = true;
      _error = null;
    });

    try {
      await ref.read(workerRepositoryProvider).confirmKyc(
            widget.document.id,
            name: _nameController.text.trim(),
            dob: _dobController.text.trim(),
            gender: _genderController.text.trim(),
            aadhaarNumber: _aadhaarController.text.trim(),
          );
      if (!mounted) return;
      invalidateOnboarding(ref);
      setState(() {
        _isSaving = false;
        _saved = true;
      });
    } on ApiException catch (error) {
      if (!mounted) return;
      setState(() {
        _isSaving = false;
        _error = error.fieldError('aadhaar_number') ?? error.message;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final document = widget.document;

    if (_saved) {
      return _SubmittedPanel(onDone: widget.onDone);
    }

    // A failed read means nothing was pre-filled, so the same form becomes the
    // manual-entry path rather than a dead end.
    final needsTyping = document.failed;

    return ListView(
      padding: const EdgeInsets.all(16),
      children: [
        Container(
          padding: const EdgeInsets.all(14),
          decoration: BoxDecoration(
            color: (needsTyping ? AppColors.warning : AppColors.info)
                .withValues(alpha: 0.10),
            borderRadius: BorderRadius.circular(12),
          ),
          child: Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Icon(
                needsTyping ? Icons.edit_note : Icons.fact_check_outlined,
                color: needsTyping ? AppColors.warning : AppColors.info,
              ),
              const SizedBox(width: 12),
              Expanded(child: Text(widget.message)),
            ],
          ),
        ),
        const SizedBox(height: 20),
        if (!needsTyping) ...[
          _ReadRow(
            label: 'Aadhaar',
            value: document.maskedAadhaar,
            ok: document.aadhaarChecksumValid,
            hint: document.aadhaarChecksumValid
                ? null
                : 'The number did not check out — please type it below.',
          ),
          if (document.extractedAge != null)
            _ReadRow(label: 'Age', value: '${document.extractedAge}', ok: true),
          const SizedBox(height: 16),
        ],
        _Field(
          label: 'Your name',
          controller: _nameController,
          flagged: document.isLowConfidence('name'),
        ),
        _Field(
          label: 'Date of birth',
          controller: _dobController,
          hint: 'DD/MM/YYYY',
          flagged: document.isLowConfidence('dob'),
        ),
        _Field(
          label: 'Gender',
          controller: _genderController,
          flagged: document.isLowConfidence('gender'),
        ),
        _Field(
          label: document.aadhaarChecksumValid
              ? 'Aadhaar number (only if the one above is wrong)'
              : 'Aadhaar number',
          controller: _aadhaarController,
          hint: '12 digits',
          keyboardType: TextInputType.number,
          flagged: !document.aadhaarChecksumValid ||
              document.isLowConfidence('aadhaar'),
        ),
        if (_error != null) ...[
          const SizedBox(height: 8),
          Text(_error!, style: const TextStyle(color: AppColors.danger)),
        ],
        const SizedBox(height: 20),
        ElevatedButton.icon(
          onPressed: _isSaving ? null : _confirm,
          icon: _isSaving
              ? const SizedBox(
                  width: 20,
                  height: 20,
                  child: CircularProgressIndicator(strokeWidth: 2),
                )
              : const Icon(Icons.check),
          label: Text(_isSaving ? 'Saving…' : 'Confirm my details'),
        ),
        const SizedBox(height: 24),
      ],
    );
  }
}

class _SubmittedPanel extends StatelessWidget {
  const _SubmittedPanel({required this.onDone});

  final VoidCallback onDone;

  @override
  Widget build(BuildContext context) {
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(32),
        child: Column(
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            const Icon(
              Icons.mark_email_read_outlined,
              size: 72,
              color: AppColors.success,
            ),
            const SizedBox(height: 20),
            Text(
              'Sent for review',
              style: Theme.of(context)
                  .textTheme
                  .titleLarge
                  ?.copyWith(fontWeight: FontWeight.w700),
            ),
            const SizedBox(height: 10),
            const Text(
              'Your society administrator will check your details and approve '
              'you. You will be able to take work as soon as they do.',
              textAlign: TextAlign.center,
            ),
            const SizedBox(height: 28),
            ElevatedButton(onPressed: onDone, child: const Text('Done')),
          ],
        ),
      ),
    );
  }
}

class _ReadRow extends StatelessWidget {
  const _ReadRow({
    required this.label,
    required this.value,
    required this.ok,
    this.hint,
  });

  final String label;
  final String value;
  final bool ok;
  final String? hint;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 8),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Icon(
                ok ? Icons.check_circle : Icons.error_outline,
                size: 18,
                color: ok ? AppColors.success : AppColors.danger,
              ),
              const SizedBox(width: 8),
              Text(
                '$label: ',
                style: const TextStyle(color: AppColors.textSecondary),
              ),
              Text(value, style: const TextStyle(fontWeight: FontWeight.w600)),
            ],
          ),
          if (hint != null)
            Padding(
              padding: const EdgeInsets.only(left: 26, top: 2),
              child: Text(
                hint!,
                style: const TextStyle(fontSize: 13, color: AppColors.danger),
              ),
            ),
        ],
      ),
    );
  }
}

class _Field extends StatelessWidget {
  const _Field({
    required this.label,
    required this.controller,
    this.hint,
    this.keyboardType,
    this.flagged = false,
  });

  final String label;
  final TextEditingController controller;
  final String? hint;
  final TextInputType? keyboardType;

  /// Read below the confidence threshold, so the worker is asked to look at it
  /// rather than it being quietly accepted.
  final bool flagged;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 16),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Text(label, style: const TextStyle(fontWeight: FontWeight.w600)),
              if (flagged) ...[
                const SizedBox(width: 8),
                const Icon(
                  Icons.priority_high,
                  size: 16,
                  color: AppColors.warning,
                ),
                const Text(
                  'please check',
                  style: TextStyle(fontSize: 12, color: AppColors.warning),
                ),
              ],
            ],
          ),
          const SizedBox(height: 6),
          TextField(
            controller: controller,
            keyboardType: keyboardType,
            decoration: InputDecoration(
              hintText: hint,
              fillColor: flagged ? const Color(0xFFFFF8E1) : Colors.white,
            ),
          ),
        ],
      ),
    );
  }
}
