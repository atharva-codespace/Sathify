import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import 'package:mobile_scanner/mobile_scanner.dart';

import '../../../../core/config/app_config.dart';
import '../../../../core/errors/api_exception.dart';
import '../../../../core/routing/app_router.dart';
import '../../../../shared/design_system.dart';
import '../../../notifications/presentation/widgets/notification_bell.dart';
import '../../data/models/attendance_models.dart';
import '../providers/attendance_provider.dart';
import 'face_check_sheet.dart';

/// Module 7.2 — the guard's scanner.
///
/// -----------------------------------------------------------------------
/// THIS SCREEN MUST WORK WITH NO SIGNAL
/// -----------------------------------------------------------------------
/// QR recognition is on-device (ML Kit via mobile_scanner), the roster is
/// cached, and the decision is written to a local queue. Nothing on the path
/// from "worker holds up a card" to "guard taps Allow" requires the network.
/// The banner tells the guard when they are working from the cache, because
/// that is a fact they should know — not because it changes what they can do.
class GateScannerScreen extends ConsumerStatefulWidget {
  const GateScannerScreen({super.key});

  @override
  ConsumerState<GateScannerScreen> createState() => _GateScannerScreenState();
}

class _GateScannerScreenState extends ConsumerState<GateScannerScreen> {
  final MobileScannerController _controller = MobileScannerController(
    detectionSpeed: DetectionSpeed.noDuplicates,
    formats: const [BarcodeFormat.qrCode],
  );

  /// Guards against the detector firing repeatedly while a sheet is open.
  bool _handling = false;

  /// Retries the offline queue on a timer rather than waiting for the guard to
  /// notice the banner and tap "Send now".
  ///
  /// A plain connectivity listener is not enough here: this gate's problem is
  /// a *weak* signal, not being fully offline, so the device often still
  /// reports "connected" while individual requests keep timing out. Polling
  /// regardless of the reported connectivity state is what actually recovers
  /// once the signal is good enough for one request to get through — the guard
  /// never has to do anything for the day's log to catch up.
  Timer? _retryTimer;

  @override
  void initState() {
    super.initState();
    _retryTimer = Timer.periodic(
      const Duration(seconds: 25),
      (_) => _retrySilently(),
    );
    // Also worth a try the moment the screen opens — the guard may have just
    // walked back into signal after their queue built up elsewhere.
    _retrySilently();
  }

  Future<void> _retrySilently() async {
    final repository = ref.read(attendanceRepositoryProvider);
    if (await repository.pendingCount() == 0) return;
    try {
      await repository.syncPending();
      if (mounted) invalidateAttendance(ref);
    } on ApiException {
      // Still no usable signal. The timer tries again shortly; the banner and
      // "Send now" remain for the guard to force it sooner.
    }
  }

  @override
  void dispose() {
    _retryTimer?.cancel();
    _controller.dispose();
    super.dispose();
  }

  Future<void> _onDetect(BarcodeCapture capture) async {
    if (_handling) return;

    // Explicit rather than `firstOrNull`, which comes from package:collection
    // and is not a direct dependency here.
    final barcodes = capture.barcodes;
    if (barcodes.isEmpty) return;
    final raw = barcodes.first.rawValue;
    if (raw == null || raw.isEmpty) return;

    setState(() => _handling = true);
    await _controller.stop();

    try {
      final result =
          await ref.read(attendanceRepositoryProvider).resolveScan(raw.trim());

      if (!mounted) return;

      if (result == null) {
        // Offline and not in the cached roster. Genuinely unknown to this
        // device — say so rather than guessing.
        await _showUnknown();
      } else {
        final outcome = await showModalBottomSheet<_DecisionOutcome>(
          context: context,
          isScrollControlled: true,
          isDismissible: false,
          builder: (_) => _DecisionSheet(scan: result),
        );

        // Driven from here, not from inside the decision sheet, because this
        // screen owns the camera. Opening it from the sheet would restart the
        // QR preview the moment the sheet popped — putting mobile_scanner and
        // image_picker on the same camera at once, and leaving the detector
        // live behind the face sheet.
        if (outcome != null && outcome.offerFaceCheck && mounted) {
          await showFaceCheckSheet(
            context,
            eventId: outcome.eventId,
            workerName: result.workerName,
            referencePhotoUrl: result.workerPhoto,
          );
        }
      }
    } on ApiException catch (error) {
      if (mounted) {
        ScaffoldMessenger.of(context)
            .showSnackBar(SnackBar(content: Text(error.message)));
      }
    } finally {
      if (mounted) {
        setState(() => _handling = false);
        await _controller.start();
      }
    }
  }

  Future<void> _showUnknown() => showDialog<void>(
        context: context,
        builder: (_) => AlertDialog(
          title: const Text('Code not recognised'),
          content: const Text(
            'This code is not on today’s list on this device. If you are '
            'offline, refresh the list when you have signal — or log the entry '
            'by hand.',
          ),
          actions: [
            TextButton(
              onPressed: () => Navigator.of(context).pop(),
              child: const Text('OK'),
            ),
          ],
        ),
      );

  @override
  Widget build(BuildContext context) {
    final pending = ref.watch(pendingSyncCountProvider);

    return Scaffold(
      appBar: AppBar(
        title: const Text('Scan gate pass'),
        actions: [
          IconButton(
            tooltip: 'Torch',
            icon: const Icon(Icons.flashlight_on_outlined),
            onPressed: () => _controller.toggleTorch(),
          ),
          IconButton(
            tooltip: "Today's list",
            icon: const Icon(Icons.list_alt),
            onPressed: () => context.push(Routes.gateLog),
          ),
          const NotificationBell(),
        ],
      ),
      body: Column(
        children: [
          _SyncBanner(pendingCount: pending.valueOrNull ?? 0),
          Expanded(
            child: ClipRRect(
              borderRadius: AppRadius.hero,
              child: Stack(
                alignment: Alignment.center,
                children: [
                  MobileScanner(controller: _controller, onDetect: _onDetect),

                  // Corner brackets rather than a full rectangle. Same
                  // reasoning as the frame they replace — this runs on cheap
                  // devices in bright sun, so nothing animates and everything
                  // is maximum contrast — but brackets leave the middle of the
                  // viewfinder clear, which matters when the pass being
                  // scanned is a phone screen someone is holding up.
                  const _AimingBrackets(),

                  const Positioned(
                    bottom: AppSpacing.lg,
                    left: AppSpacing.lg,
                    right: AppSpacing.lg,
                    child: _HintPill(),
                  ),

                  if (_handling)
                    ColoredBox(
                      color: Colors.black.withValues(alpha: 0.55),
                      child: const Center(
                        child: CircularProgressIndicator(color: Colors.white),
                      ),
                    ),
                ],
              ),
            ),
          ),
          SafeArea(
            child: Padding(
              padding: const EdgeInsets.all(AppSpacing.gutter),
              child: AppButton.secondary(
                label: 'Scanning not working? Log by hand',
                icon: Icons.edit_note_rounded,
                onPressed: () => context.push(Routes.gateLog),
              ),
            ),
          ),
        ],
      ),
    );
  }
}

/// Module 7.4 — the only visible sign that a day's attendance is not landing.
class _SyncBanner extends ConsumerWidget {
  const _SyncBanner({required this.pendingCount});

  final int pendingCount;

  Future<void> _sync(BuildContext context, WidgetRef ref) async {
    final messenger = ScaffoldMessenger.of(context);
    try {
      final result = await ref.read(attendanceRepositoryProvider).syncPending();
      invalidateAttendance(ref);
      showAppSnackBarOn(
        messenger,
        result == null
            ? 'Nothing waiting to send.'
            : '${result.acceptedCount} entry(ies) sent.',
        tone: result == null ? AppTone.neutral : AppTone.success,
      );
    } on ApiException catch (error) {
      showAppSnackBarOn(
        messenger,
        'Still offline. ${error.message}',
        tone: AppTone.warning,
      );
    }
  }

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    if (pendingCount == 0) return const SizedBox.shrink();

    return Material(
      color: AppColors.warningSoft,
      child: Padding(
        padding: const EdgeInsets.fromLTRB(
          AppSpacing.gutter,
          AppSpacing.xs,
          AppSpacing.xs,
          AppSpacing.xs,
        ),
        child: Row(
          children: [
            const Icon(
              Icons.cloud_upload_outlined,
              color: AppColors.warning,
              size: AppIconSize.md,
            ),
            const SizedBox(width: AppSpacing.sm),
            Expanded(
              child: Text(
                '$pendingCount entry(ies) saved on this device, not sent yet.',
                style: const TextStyle(
                  fontWeight: FontWeight.w600,
                  fontSize: 13.5,
                  height: 1.35,
                  color: AppColors.warning,
                ),
              ),
            ),
            TextButton(
              onPressed: () => _sync(context, ref),
              style: TextButton.styleFrom(foregroundColor: AppColors.warning),
              child: const Text('Send now'),
            ),
          ],
        ),
      ),
    );
  }
}

/// Four corner brackets marking the scan target.
class _AimingBrackets extends StatelessWidget {
  const _AimingBrackets();

  @override
  Widget build(BuildContext context) {
    return const SizedBox(
      width: 250,
      height: 250,
      child: Stack(
        children: [
          Positioned(top: 0, left: 0, child: _Corner(top: true, left: true)),
          Positioned(top: 0, right: 0, child: _Corner(top: true, left: false)),
          Positioned(
            bottom: 0,
            left: 0,
            child: _Corner(top: false, left: true),
          ),
          Positioned(
            bottom: 0,
            right: 0,
            child: _Corner(top: false, left: false),
          ),
        ],
      ),
    );
  }
}

class _Corner extends StatelessWidget {
  const _Corner({required this.top, required this.left});

  final bool top;
  final bool left;

  @override
  Widget build(BuildContext context) {
    const side = BorderSide(color: Colors.white, width: 4);
    return Container(
      width: 40,
      height: 40,
      decoration: BoxDecoration(
        border: Border(
          top: top ? side : BorderSide.none,
          bottom: top ? BorderSide.none : side,
          left: left ? side : BorderSide.none,
          right: left ? BorderSide.none : side,
        ),
        borderRadius: BorderRadius.only(
          topLeft: top && left ? const Radius.circular(10) : Radius.zero,
          topRight: top && !left ? const Radius.circular(10) : Radius.zero,
          bottomLeft: !top && left ? const Radius.circular(10) : Radius.zero,
          bottomRight: !top && !left ? const Radius.circular(10) : Radius.zero,
        ),
      ),
    );
  }
}

/// Tells the guard what to point at, on a dark scrim so it stays readable
/// against whatever the camera happens to be showing.
class _HintPill extends StatelessWidget {
  const _HintPill();

  @override
  Widget build(BuildContext context) {
    return Center(
      child: Container(
        padding: const EdgeInsets.symmetric(
          horizontal: AppSpacing.md,
          vertical: AppSpacing.xs,
        ),
        decoration: BoxDecoration(
          color: Colors.black.withValues(alpha: 0.6),
          borderRadius: BorderRadius.circular(AppRadius.pill),
        ),
        child: const Text(
          "Point at the worker's gate pass",
          style: TextStyle(
            color: Colors.white,
            fontSize: 14,
            fontWeight: FontWeight.w600,
          ),
        ),
      ),
    );
  }
}

/// What the decision sheet hands back to the scanner screen.
///
/// The event id is the client-generated one that went to the server with the
/// decision, so the face check can address the event without a round trip to
/// discover it.
class _DecisionOutcome {
  const _DecisionOutcome({
    required this.eventId,
    required this.offerFaceCheck,
  });

  final String eventId;
  final bool offerFaceCheck;
}

/// The allow/refuse decision. The server recommends; the guard decides.
class _DecisionSheet extends ConsumerStatefulWidget {
  const _DecisionSheet({required this.scan});

  final ScanResult scan;

  @override
  ConsumerState<_DecisionSheet> createState() => _DecisionSheetState();
}

class _DecisionSheetState extends ConsumerState<_DecisionSheet> {
  final _reasonController = TextEditingController();
  GateDirection _direction = GateDirection.entry;
  bool _isSaving = false;

  @override
  void dispose() {
    _reasonController.dispose();
    super.dispose();
  }

  Future<void> _record(GateDecision decision) async {
    final reason = _reasonController.text.trim();
    if (decision == GateDecision.denied && reason.isEmpty) {
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('Say why entry was refused.')),
      );
      return;
    }

    setState(() => _isSaving = true);

    final repository = ref.read(attendanceRepositoryProvider);
    final eventId = repository.newEventId();
    final sent = await repository.recordDecision(
      AttendanceEventDraft(
        id: eventId,
        workerId: widget.scan.workerId,
        occurredAt: DateTime.now(),
        direction: _direction,
        method: VerificationMethod.qr,
        decision: decision,
        decisionReason: reason,
      ),
    );

    if (!mounted) return;
    invalidateAttendance(ref);

    final messenger = ScaffoldMessenger.of(context);

    // Hand the decision back rather than acting on it here: the scanner screen
    // has to run the face check *before* it restarts the camera preview.
    Navigator.of(context).pop(
      _DecisionOutcome(
        eventId: eventId,
        offerFaceCheck: _shouldOfferFaceCheck(decision: decision, sent: sent),
      ),
    );

    messenger.showSnackBar(
      SnackBar(
        content: Text(
          sent
              ? 'Logged.'
              : 'Saved on this device. It will send when you have signal.',
        ),
      ),
    );
  }

  /// Whether a live face check is worth asking the guard for.
  ///
  /// Every condition here exists to avoid wasting a guard's time at a gate with
  /// someone standing in front of them:
  ///
  ///   * **Allowed only.** A refusal has already been decided by a human, and
  ///     the server's check only ever downgrades an *allowed* event to review.
  ///   * **Online only.** The comparison addresses an event by id; if the
  ///     decision is still sitting in the offline queue there is nothing on the
  ///     server to attach a photo to.
  ///   * **A reference photo must exist.** Without one the backend can only
  ///     answer "unavailable", so asking for a photo would cost a gate
  ///     interaction to learn nothing.
  ///   * **The feature flag.** ENABLE_FACE_VERIFICATION in .env.
  bool _shouldOfferFaceCheck({
    required GateDecision decision,
    required bool sent,
  }) {
    if (!AppConfig.faceVerificationEnabled) return false;
    if (decision != GateDecision.allowed) return false;
    if (!sent) return false;
    final photo = widget.scan.workerPhoto;
    return photo != null && photo.isNotEmpty;
  }

  @override
  Widget build(BuildContext context) {
    final scan = widget.scan;
    final theme = Theme.of(context);

    return Padding(
      padding: EdgeInsets.only(
        left: 20,
        right: 20,
        top: 20,
        bottom: MediaQuery.of(context).viewInsets.bottom + 20,
      ),
      child: SingleChildScrollView(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                CircleAvatar(
                  radius: 32,
                  backgroundColor: theme.colorScheme.primaryContainer,
                  backgroundImage: scan.workerPhoto != null
                      ? NetworkImage(scan.workerPhoto!)
                      : null,
                  child: scan.workerPhoto == null
                      ? const Icon(Icons.person, size: 32)
                      : null,
                ),
                const SizedBox(width: 14),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                        scan.workerName.isEmpty
                            ? 'Unknown worker'
                            : scan.workerName,
                        style: theme.textTheme.titleLarge
                            ?.copyWith(fontWeight: FontWeight.w700),
                      ),
                      if (scan.fromCache)
                        const Text(
                          'From today’s saved list (offline)',
                          style: TextStyle(
                            fontSize: 12.5,
                            color: AppColors.textSecondary,
                          ),
                        ),
                    ],
                  ),
                ),
              ],
            ),
            const SizedBox(height: 16),
            if (!scan.isUsable)
              _Banner(
                colour: AppColors.danger,
                icon: Icons.block,
                text: scan.reason.isEmpty
                    ? 'This pass is not valid.'
                    : scan.reason,
              )
            else if (!scan.isExpected)
              const _Banner(
                colour: AppColors.warning,
                icon: Icons.help_outline,
                // Not scheduled is not the same as not permitted.
                text: 'Not on today’s list. Check with the resident before '
                    'allowing entry.',
              )
            else
              const _Banner(
                colour: AppColors.success,
                icon: Icons.check_circle,
                text: 'Expected today.',
              ),
            if (scan.expectedVisits.isNotEmpty) ...[
              const SizedBox(height: 14),
              ...scan.expectedVisits.map(
                (visit) => Padding(
                  padding: const EdgeInsets.only(bottom: 4),
                  child: Row(
                    children: [
                      const Icon(
                        Icons.schedule,
                        size: 16,
                        color: AppColors.textSecondary,
                      ),
                      const SizedBox(width: 8),
                      Expanded(
                        child: Text(
                          '${visit.timeLabel} · ${visit.flatLabel} · ${visit.title}',
                          style: const TextStyle(fontSize: 14),
                        ),
                      ),
                    ],
                  ),
                ),
              ),
            ],
            const SizedBox(height: 20),
            SegmentedButton<GateDirection>(
              segments: const [
                ButtonSegment(
                  value: GateDirection.entry,
                  label: Text('Coming in'),
                  icon: Icon(Icons.login),
                ),
                ButtonSegment(
                  value: GateDirection.exit,
                  label: Text('Going out'),
                  icon: Icon(Icons.logout),
                ),
              ],
              selected: {_direction},
              onSelectionChanged: (selection) =>
                  setState(() => _direction = selection.first),
            ),
            const SizedBox(height: 16),
            TextField(
              controller: _reasonController,
              maxLines: 2,
              decoration: const InputDecoration(
                labelText: 'Note',
                hintText: 'Required if you refuse entry',
              ),
            ),
            const SizedBox(height: 20),
            if (_isSaving)
              const Center(child: CircularProgressIndicator())
            else
              Row(
                children: [
                  Expanded(
                    child: OutlinedButton.icon(
                      onPressed: () => _record(GateDecision.denied),
                      icon: const Icon(Icons.close),
                      label: const Text('Refuse'),
                      style: OutlinedButton.styleFrom(
                        foregroundColor: AppColors.danger,
                        minimumSize: const Size.fromHeight(56),
                      ),
                    ),
                  ),
                  const SizedBox(width: 12),
                  Expanded(
                    child: ElevatedButton.icon(
                      onPressed: () => _record(GateDecision.allowed),
                      icon: const Icon(Icons.check),
                      label: const Text('Allow'),
                      style: ElevatedButton.styleFrom(
                        minimumSize: const Size.fromHeight(56),
                      ),
                    ),
                  ),
                ],
              ),
            TextButton(
              onPressed: _isSaving ? null : () => Navigator.of(context).pop(),
              child: const Text('Cancel'),
            ),
          ],
        ),
      ),
    );
  }
}

class _Banner extends StatelessWidget {
  const _Banner({required this.colour, required this.icon, required this.text});

  final Color colour;
  final IconData icon;
  final String text;

  @override
  Widget build(BuildContext context) {
    return Container(
      width: double.infinity,
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(
        color: colour.withValues(alpha: 0.12),
        borderRadius: BorderRadius.circular(10),
      ),
      child: Row(
        children: [
          Icon(icon, color: colour),
          const SizedBox(width: 10),
          Expanded(
            child: Text(
              text,
              style: TextStyle(color: colour, fontWeight: FontWeight.w600),
            ),
          ),
        ],
      ),
    );
  }
}
