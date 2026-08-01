import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:geolocator/geolocator.dart';

import '../../../../core/device/device_identity.dart';
import '../../../../shared/design_system.dart';
import '../../data/models/self_checkin_models.dart';
import '../providers/attendance_provider.dart';

/// Module 13.3 tier 2 — the worker records their own arrival.
///
/// -----------------------------------------------------------------------
/// FOR THE GATE WITH NOBODY ON IT
/// -----------------------------------------------------------------------
/// An unstaffed service entrance, a night shift, a guard whose phone died.
/// Without this a worker who turned up and did the job has no record of having
/// done so, and Module 8 bills from that record — so what this prevents is
/// somebody not being paid.
///
/// -----------------------------------------------------------------------
/// IT CANNOT REFUSE ANYBODY, AND SAYS SO
/// -----------------------------------------------------------------------
/// The worst outcome is "an administrator will confirm this", never "denied".
/// The copy is deliberate: a worker who reads "location not verified" as a
/// rejection may go home. What they need to know is that the record exists and
/// somebody will look at it.
///
/// -----------------------------------------------------------------------
/// THE TAP IS SAVED BEFORE THE NETWORK IS TRIED
/// -----------------------------------------------------------------------
/// A worker in a stairwell has no signal. The check-in is written to the local
/// queue first (13.1), carrying an id this device generated, so pushing it
/// later cannot produce a second record.
class SelfCheckInScreen extends ConsumerStatefulWidget {
  const SelfCheckInScreen({super.key});

  @override
  ConsumerState<SelfCheckInScreen> createState() => _SelfCheckInScreenState();
}

class _SelfCheckInScreenState extends ConsumerState<SelfCheckInScreen> {
  bool _busy = false;
  SelfCheckInResult? _result;
  bool _queuedOffline = false;
  String? _locationNote;

  /// Reads the device's position, or gives up quietly.
  ///
  /// Never throws and never blocks the check-in. A refused permission, a
  /// switched-off GPS or a fix that will not arrive all produce the same
  /// outcome: the check-in goes ahead without a position and is reviewed by a
  /// person. Making location mandatory would let a settings toggle cost
  /// somebody a day's wages.
  Future<DevicePosition?> _readPosition() async {
    try {
      if (!await Geolocator.isLocationServiceEnabled()) {
        _locationNote = 'Location is switched off on this phone.';
        return null;
      }

      var permission = await Geolocator.checkPermission();
      if (permission == LocationPermission.denied) {
        permission = await Geolocator.requestPermission();
      }
      if (permission == LocationPermission.denied ||
          permission == LocationPermission.deniedForever) {
        _locationNote =
            'This app does not have permission to read your location.';
        return null;
      }

      final position = await Geolocator.getCurrentPosition(
        locationSettings: const LocationSettings(
          accuracy: LocationAccuracy.medium,
          // Bounded: a worker is standing at a gate, not waiting for a survey-
          // grade fix. Past this the check-in proceeds without one.
          timeLimit: Duration(seconds: 12),
        ),
      );

      return DevicePosition(
        latitude: position.latitude,
        longitude: position.longitude,
        accuracyMetres: position.accuracy,
      );
    } catch (_) {
      _locationNote = 'Your location could not be read just now.';
      return null;
    }
  }

  Future<void> _checkIn(String direction) async {
    setState(() {
      _busy = true;
      _result = null;
      _locationNote = null;
      _queuedOffline = false;
    });

    final repository = ref.read(attendanceRepositoryProvider);
    final position = await _readPosition();
    final identity = await DeviceIdentity.current();

    final draft = SelfCheckInDraft(
      id: repository.newEventId(),
      occurredAt: DateTime.now(),
      direction: direction,
      position: position,
      deviceId: identity.deviceId,
      wasOffline: true,
    );

    final result = await repository.selfCheckIn(draft);
    ref.invalidate(pendingCheckInCountProvider);

    if (!mounted) return;
    setState(() {
      _busy = false;
      _result = result;
      _queuedOffline = result == null;
    });
  }

  @override
  Widget build(BuildContext context) {
    final queued = ref.watch(pendingCheckInCountProvider).valueOrNull ?? 0;

    return Scaffold(
      appBar: AppBar(title: const Text('I have arrived')),
      body: ListView(
        padding: const EdgeInsets.all(24),
        children: [
          const SizedBox(height: 8),
          const Icon(
            Icons.where_to_vote_outlined,
            size: 72,
            color: AppColors.primary,
          ),
          const SizedBox(height: 16),
          Text(
            'Use this when there is no guard at the gate',
            textAlign: TextAlign.center,
            style: Theme.of(context)
                .textTheme
                .titleMedium
                ?.copyWith(fontWeight: FontWeight.w700),
          ),
          const SizedBox(height: 8),
          const Text(
            'Your society will see that you arrived. If a guard scanned your '
            'pass, you do not need this.',
            textAlign: TextAlign.center,
            style: TextStyle(color: AppColors.textSecondary),
          ),
          const SizedBox(height: 28),
          ElevatedButton.icon(
            onPressed: _busy ? null : () => _checkIn('entry'),
            icon: _busy
                ? const SizedBox(
                    width: 20,
                    height: 20,
                    child: CircularProgressIndicator(strokeWidth: 2),
                  )
                : const Icon(Icons.login),
            label: Text(_busy ? 'Recording…' : 'I have arrived'),
          ),
          const SizedBox(height: 12),
          OutlinedButton.icon(
            onPressed: _busy ? null : () => _checkIn('exit'),
            icon: const Icon(Icons.logout),
            label: const Text('I am leaving'),
            style: OutlinedButton.styleFrom(
              minimumSize: const Size.fromHeight(AppTheme.minTouchTarget),
            ),
          ),
          if (_result != null || _queuedOffline) ...[
            const SizedBox(height: 24),
            _Outcome(
              result: _result,
              queuedOffline: _queuedOffline,
              locationNote: _locationNote,
            ),
          ],
          if (queued > 0) ...[
            const SizedBox(height: 24),
            _QueuedNotice(count: queued),
          ],
        ],
      ),
    );
  }
}

class _Outcome extends StatelessWidget {
  const _Outcome({
    required this.result,
    required this.queuedOffline,
    required this.locationNote,
  });

  final SelfCheckInResult? result;
  final bool queuedOffline;
  final String? locationNote;

  @override
  Widget build(BuildContext context) {
    // Queued: durable locally, not yet at the server. Deliberately framed as
    // done rather than as pending — from the worker's side it *is* done, and
    // the sync is the app's problem, not theirs.
    if (queuedOffline) {
      return _Card(
        icon: Icons.cloud_off,
        colour: AppColors.info,
        title: 'Saved on your phone',
        body: 'You have no connection right now. This will be sent '
            'automatically when you are back online.',
        note: locationNote,
      );
    }

    final outcome = result!;
    if (outcome.isAllowed) {
      return _Card(
        icon: Icons.check_circle_outline,
        colour: AppColors.success,
        title: 'Arrival recorded',
        body: outcome.distanceMetres != null
            ? 'Confirmed at the society.'
            : 'Confirmed.',
        note: locationNote,
      );
    }

    return _Card(
      icon: Icons.hourglass_top_outlined,
      colour: AppColors.warning,
      title: 'Recorded — your society will confirm it',
      // Never "rejected". A worker who reads a location warning as a refusal
      // may go home, and the record is exactly as durable either way.
      body: outcome.decisionReason.isNotEmpty
          ? outcome.decisionReason
          : 'An administrator will check this against the schedule.',
      note: locationNote,
    );
  }
}

class _Card extends StatelessWidget {
  const _Card({
    required this.icon,
    required this.colour,
    required this.title,
    required this.body,
    this.note,
  });

  final IconData icon;
  final Color colour;
  final String title;
  final String body;
  final String? note;

  @override
  Widget build(BuildContext context) {
    return Card(
      margin: EdgeInsets.zero,
      color: colour.withValues(alpha: 0.08),
      elevation: 0,
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Icon(icon, color: colour),
            const SizedBox(width: 12),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    title,
                    style:
                        TextStyle(fontWeight: FontWeight.w700, color: colour),
                  ),
                  const SizedBox(height: 4),
                  Text(body),
                  if (note != null && note!.isNotEmpty) ...[
                    const SizedBox(height: 6),
                    Text(
                      note!,
                      style: const TextStyle(
                        fontSize: 12,
                        color: AppColors.textSecondary,
                      ),
                    ),
                  ],
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class _QueuedNotice extends ConsumerWidget {
  const _QueuedNotice({required this.count});

  final int count;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    return Card(
      margin: EdgeInsets.zero,
      child: ListTile(
        leading: const Icon(Icons.sync_problem, color: AppColors.warning),
        title: Text(
          '$count check-in${count == 1 ? '' : 's'} waiting to send',
        ),
        subtitle: const Text('They are safe on this phone until they go.'),
        trailing: TextButton(
          onPressed: () async {
            final messenger = ScaffoldMessenger.of(context);
            final sent = await ref
                .read(attendanceRepositoryProvider)
                .syncPendingCheckIns();
            ref.invalidate(pendingCheckInCountProvider);
            messenger.showSnackBar(
              SnackBar(
                content: Text(
                  sent == 0 ? 'Still no connection.' : '$sent sent.',
                ),
              ),
            );
          },
          child: const Text('Try now'),
        ),
      ),
    );
  }
}
