import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:image_picker/image_picker.dart';

import '../../../../core/errors/api_exception.dart';
import '../../../../shared/design_system.dart';
import '../../data/models/attendance_models.dart';
import '../providers/attendance_provider.dart';

/// Module 7.3 / SRS 3.15 — the live face check at the gate.
///
/// Opens straight after an allowed entry has reached the server, because the
/// comparison needs an event to attach to. The id is client-generated
/// ([AttendanceRepository.newEventId]) and travels with the decision, so the
/// same id addresses the event here without a round trip to discover it.
///
/// -----------------------------------------------------------------------
/// THIS SHEET CANNOT REFUSE ANYBODY
/// -----------------------------------------------------------------------
/// Three outcomes, and two of them mean "a human decides":
///
///   * **matched** — above threshold. Nothing further to do.
///   * **below threshold** — a real comparison ran and scored low. The server
///     has already moved the event to review. Entry was NOT withdrawn.
///   * **unavailable** — no engine installed, no reference photo, or the
///     upload failed. Nothing was measured, so nothing is concluded.
///
/// The wording of the last two is deliberately different, and that difference
/// is the whole point of the screen. Face recognition is measurably less
/// accurate for darker skin tones, older cameras and poor lighting — which
/// describes this gate and this workforce. A low score is therefore presented
/// as "look at them yourself", never as a verdict. The backend carries the
/// same note in `apps/attendance/face.py`; if you change the wording here,
/// read that first.
///
/// Skipping is always available. The check is best-effort by design: it does
/// not run offline, it does not run without a registered photo, and a guard
/// with someone waiting in front of them must never be blocked by it.
Future<void> showFaceCheckSheet(
  BuildContext context, {
  required String eventId,
  required String workerName,
  String? referencePhotoUrl,
}) {
  return showModalBottomSheet<void>(
    context: context,
    isScrollControlled: true,
    // Dismissing by accident would skip the check silently. Skipping is fine,
    // but it should be a decision the guard made, not a stray tap.
    isDismissible: false,
    enableDrag: false,
    builder: (_) => _FaceCheckSheet(
      eventId: eventId,
      workerName: workerName,
      referencePhotoUrl: referencePhotoUrl,
    ),
  );
}

class _FaceCheckSheet extends ConsumerStatefulWidget {
  const _FaceCheckSheet({
    required this.eventId,
    required this.workerName,
    this.referencePhotoUrl,
  });

  final String eventId;
  final String workerName;
  final String? referencePhotoUrl;

  @override
  ConsumerState<_FaceCheckSheet> createState() => _FaceCheckSheetState();
}

class _FaceCheckSheetState extends ConsumerState<_FaceCheckSheet> {
  /// The captured photo, held so the guard can see what was sent alongside the
  /// registered one — the comparison they are being asked to make themselves
  /// when the result is anything other than a clean match.
  XFile? _shot;
  bool _isChecking = false;
  FaceCheckResult? _result;

  /// Set when the request itself failed rather than the comparison. Kept
  /// separate from [_result] so the copy can say "could not check" instead of
  /// implying a measurement happened.
  String? _transportError;

  bool get _isDone => _result != null || _transportError != null;

  Future<void> _capture() async {
    // Rear camera: the guard is photographing the person in front of them, not
    // themselves. Quality matches the profile-photo path — Facenet resizes to
    // 160x160 internally, so a larger upload buys nothing but a slower gate.
    final picked = await ImagePicker().pickImage(
      source: ImageSource.camera,
      preferredCameraDevice: CameraDevice.rear,
      imageQuality: 80,
      maxWidth: 1200,
    );

    // Null means the guard backed out of the camera. Not an error — stay put
    // so they can try again or skip.
    if (picked == null || !mounted) return;

    setState(() {
      _shot = picked;
      _isChecking = true;
      _result = null;
      _transportError = null;
    });

    try {
      final result = await ref
          .read(attendanceRepositoryProvider)
          .verifyFace(widget.eventId, picked.path);
      if (!mounted) return;
      setState(() => _result = result);

      // The server may have moved the event to review, so the gate log and the
      // pending-review count are both stale now.
      invalidateAttendance(ref);
    } on ApiException catch (error) {
      if (!mounted) return;
      // Offline, a 404 on an event that never landed, or a server fault. None
      // of them measured anything, so none of them may read as a failed match.
      setState(() => _transportError = error.message);
    } finally {
      if (mounted) setState(() => _isChecking = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);

    return Padding(
      padding: EdgeInsets.only(
        left: AppSpacing.lg,
        right: AppSpacing.lg,
        top: AppSpacing.lg,
        bottom: MediaQuery.of(context).viewInsets.bottom + AppSpacing.lg,
      ),
      child: SingleChildScrollView(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              'Face check',
              style: theme.textTheme.titleLarge
                  ?.copyWith(fontWeight: FontWeight.w700),
            ),
            const SizedBox(height: AppSpacing.xxs),
            Text(
              widget.workerName.isEmpty ? 'Unknown worker' : widget.workerName,
              style: const TextStyle(color: AppColors.textSecondary),
            ),
            const SizedBox(height: AppSpacing.lg),

            _PhotoPair(
              referencePhotoUrl: widget.referencePhotoUrl,
              shot: _shot,
            ),
            const SizedBox(height: AppSpacing.lg),

            if (_isChecking)
              const _CheckingRow()
            else if (_result != null)
              _Banner(presentation: FaceCheckPresentation.of(_result!))
            else if (_transportError != null)
              _Banner(
                presentation: FaceCheckPresentation.notRun(
                  'The check could not run: $_transportError '
                  'Please verify visually.',
                ),
              )
            else
              const Text(
                'Take a photo of the person at the gate. It is compared '
                'against the photo on their profile.',
                style: TextStyle(color: AppColors.textSecondary, height: 1.35),
              ),

            const SizedBox(height: AppSpacing.lg),
            ..._actions(),
          ],
        ),
      ),
    );
  }

  List<Widget> _actions() {
    if (_isDone) {
      return [
        AppButton(
          label: 'Done',
          icon: Icons.check_rounded,
          onPressed: () => Navigator.of(context).pop(),
        ),
        // Retaking after a low or unavailable result is legitimate: bad light
        // and a bad angle are the two most common causes, and both are fixable
        // by taking another photo.
        AppButton.text(
          label: 'Take another photo',
          onPressed: _isChecking ? null : _capture,
        ),
      ];
    }

    return [
      AppButton(
        label: _shot == null ? 'Take photo' : 'Retake photo',
        icon: Icons.photo_camera_rounded,
        isLoading: _isChecking,
        onPressed: _isChecking ? null : _capture,
      ),
      AppButton.text(
        label: 'Skip — verify visually',
        onPressed: _isChecking ? null : () => Navigator.of(context).pop(),
      ),
    ];
  }
}

/// The registered photo and the live one, side by side.
///
/// Present even before a shot is taken: the guard's own eyes are the check
/// that always works, and this is the comparison they are actually making.
class _PhotoPair extends StatelessWidget {
  const _PhotoPair({required this.referencePhotoUrl, required this.shot});

  final String? referencePhotoUrl;
  final XFile? shot;

  @override
  Widget build(BuildContext context) {
    return Row(
      children: [
        Expanded(
          child: _PhotoTile(
            label: 'On file',
            image: referencePhotoUrl != null
                ? NetworkImage(referencePhotoUrl!)
                : null,
          ),
        ),
        const SizedBox(width: AppSpacing.sm),
        Expanded(
          child: _PhotoTile(
            label: 'At the gate',
            image: shot != null ? FileImage(File(shot!.path)) : null,
          ),
        ),
      ],
    );
  }
}

class _PhotoTile extends StatelessWidget {
  const _PhotoTile({required this.label, required this.image});

  final String label;
  final ImageProvider? image;

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(
          label,
          style: const TextStyle(
            fontSize: 12.5,
            fontWeight: FontWeight.w600,
            color: AppColors.textSecondary,
          ),
        ),
        const SizedBox(height: AppSpacing.xxs),
        AspectRatio(
          aspectRatio: 1,
          child: DecoratedBox(
            decoration: BoxDecoration(
              color: AppColors.surfaceMuted,
              borderRadius: BorderRadius.circular(AppRadius.md),
              border: Border.all(color: AppColors.border),
              image: image != null
                  ? DecorationImage(image: image!, fit: BoxFit.cover)
                  : null,
            ),
            child: image == null
                ? const Center(
                    child: Icon(
                      Icons.person_outline_rounded,
                      size: AppIconSize.xl,
                      color: AppColors.textTertiary,
                    ),
                  )
                : null,
          ),
        ),
      ],
    );
  }
}

class _CheckingRow extends StatelessWidget {
  const _CheckingRow();

  @override
  Widget build(BuildContext context) {
    return const Row(
      children: [
        SizedBox(
          width: AppIconSize.sm,
          height: AppIconSize.sm,
          child: CircularProgressIndicator(strokeWidth: 2),
        ),
        SizedBox(width: AppSpacing.sm),
        Expanded(
          child: Text(
            'Comparing…',
            style: TextStyle(color: AppColors.textSecondary),
          ),
        ),
      ],
    );
  }
}

/// Which of the three outcomes is being shown.
///
/// [review] and [notChecked] both mean "a human decides", but they must never
/// collapse into one another: one says the comparison ran and scored low, the
/// other says nothing was measured at all. Keeping them separate in the type
/// is what stops a later edit quietly merging the two messages.
enum FaceCheckTone { matched, review, notChecked }

/// The outcome as the guard should read it.
///
/// Split out from the widget so the wording — the part with actual
/// consequences for a worker — can be asserted in a unit test without pumping
/// a camera. See the header of this file, and `apps/attendance/face.py`.
class FaceCheckPresentation {
  const FaceCheckPresentation({
    required this.tone,
    required this.title,
    required this.body,
  });

  final FaceCheckTone tone;
  final String title;
  final String body;

  /// Nothing was measured — no engine, no reference photo, or the request
  /// never landed. Distinct from a low score by construction.
  factory FaceCheckPresentation.notRun(String message) => FaceCheckPresentation(
        tone: FaceCheckTone.notChecked,
        title: 'Not checked',
        body: message,
      );

  factory FaceCheckPresentation.of(FaceCheckResult result) {
    if (!result.available) {
      return FaceCheckPresentation.notRun(
        result.reason.isEmpty
            ? 'The face check could not run. Please verify visually.'
            : '${result.reason} Please verify visually.',
      );
    }

    final percent = result.score == null ? null : (result.score! * 100).round();

    if (result.verified) {
      return FaceCheckPresentation(
        tone: FaceCheckTone.matched,
        title: 'Face matched',
        body: percent == null
            ? 'This matches the photo on file.'
            : 'Scored $percent% against the photo on file.',
      );
    }

    // A real comparison, below threshold. The event is already in review on the
    // server; entry stands until a human says otherwise. The copy says so
    // explicitly because a guard reading a low number under time pressure will
    // otherwise assume the system has refused entry for them.
    return FaceCheckPresentation(
      tone: FaceCheckTone.review,
      title: 'Please check visually',
      body: percent == null
          ? 'The comparison did not match confidently, so this entry is '
              'flagged for review. Entry has not been refused.'
          : 'Scored $percent%, below the level accepted automatically. This '
              'entry is flagged for review — it has not been refused. Poor '
              'light and a side-on angle are the usual causes.',
    );
  }
}

class _Banner extends StatelessWidget {
  const _Banner({required this.presentation});

  final FaceCheckPresentation presentation;

  @override
  Widget build(BuildContext context) {
    final (colour, background, icon) = switch (presentation.tone) {
      FaceCheckTone.matched => (
          AppColors.success,
          AppColors.successSoft,
          Icons.verified_rounded,
        ),
      FaceCheckTone.review => (
          AppColors.warning,
          AppColors.warningSoft,
          Icons.remove_red_eye_outlined,
        ),
      FaceCheckTone.notChecked => (
          AppColors.info,
          AppColors.infoSoft,
          Icons.info_outline_rounded,
        ),
    };
    final title = presentation.title;
    final body = presentation.body;

    return Container(
      width: double.infinity,
      padding: const EdgeInsets.all(AppSpacing.sm),
      decoration: BoxDecoration(
        color: background,
        borderRadius: BorderRadius.circular(AppRadius.md),
      ),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Icon(icon, color: colour, size: AppIconSize.md),
          const SizedBox(width: AppSpacing.sm),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  title,
                  style: TextStyle(
                    color: colour,
                    fontWeight: FontWeight.w700,
                    height: 1.3,
                  ),
                ),
                const SizedBox(height: AppSpacing.xxs),
                Text(
                  body,
                  style: const TextStyle(
                    color: AppColors.textPrimary,
                    fontSize: 13.5,
                    height: 1.35,
                  ),
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}
