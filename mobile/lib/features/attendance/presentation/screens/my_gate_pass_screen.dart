import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import 'package:qr_flutter/qr_flutter.dart';

import '../../../../core/errors/api_exception.dart';
import '../../../../core/routing/app_router.dart';
import '../../../../shared/design_system.dart';
import '../providers/attendance_provider.dart';

/// Module 7.1 — the worker's QR code.
///
/// Rendered large and at maximum brightness-friendly contrast: it gets scanned
/// through a cracked screen, in sunlight, by a guard holding an older phone.
/// A pretty small QR that will not scan is worse than no feature at all.
///
/// The same code is what gets printed on a laminated card for a worker without
/// a smartphone, which is why rotating it is presented as "lost my card" rather
/// than as a security control.
class MyGatePassScreen extends ConsumerWidget {
  const MyGatePassScreen({super.key});

  Future<void> _rotate(BuildContext context, WidgetRef ref) async {
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (_) => AlertDialog(
        title: const Text('Get a new code?'),
        content: const Text(
          'Your old card or code will stop working immediately. Only do this '
          'if you have lost it.',
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(context).pop(false),
            child: const Text('Cancel'),
          ),
          FilledButton(
            onPressed: () => Navigator.of(context).pop(true),
            child: const Text('Get a new code'),
          ),
        ],
      ),
    );
    if (confirmed != true) return;

    try {
      await ref.read(attendanceRepositoryProvider).rotateMyGatePass();
      if (!context.mounted) return;
      ref.invalidate(myGatePassProvider);
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('New code issued.')),
      );
    } on ApiException catch (error) {
      if (!context.mounted) return;
      ScaffoldMessenger.of(context)
          .showSnackBar(SnackBar(content: Text(error.message)));
    }
  }

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final pass = ref.watch(myGatePassProvider);

    return Scaffold(
      appBar: AppBar(title: const Text('My gate pass')),
      body: pass.when(
        loading: () => const AppSkeletonList(),
        error: (error, _) => AppErrorState(
          message: error is ApiException
              ? error.message
              : 'Could not load your pass.',
          onRetry: () => ref.invalidate(myGatePassProvider),
        ),
        data: (gatePass) => ListView(
          padding: const EdgeInsets.all(24),
          children: [
            if (!gatePass.isUsable)
              Container(
                padding: const EdgeInsets.all(14),
                margin: const EdgeInsets.only(bottom: 20),
                decoration: BoxDecoration(
                  color: AppColors.danger.withValues(alpha: 0.10),
                  borderRadius: BorderRadius.circular(12),
                ),
                child: Row(
                  children: [
                    const Icon(Icons.block, color: AppColors.danger),
                    const SizedBox(width: 12),
                    Expanded(
                      child: Text(
                        gatePass.revokedReason.isNotEmpty
                            ? gatePass.revokedReason
                            : 'This pass is not active. Speak to your society '
                                'office before you go to the gate.',
                      ),
                    ),
                  ],
                ),
              ),
            Center(
              child: Container(
                padding: const EdgeInsets.all(20),
                decoration: BoxDecoration(
                  // Always on white regardless of theme — scanners read the
                  // contrast, not the design.
                  color: Colors.white,
                  borderRadius: BorderRadius.circular(16),
                  boxShadow: [
                    BoxShadow(
                      color: Colors.black.withValues(alpha: 0.08),
                      blurRadius: 12,
                    ),
                  ],
                ),
                child: QrImageView(
                  data: gatePass.code,
                  version: QrVersions.auto,
                  size: 260,
                  backgroundColor: Colors.white,
                  errorCorrectionLevel: QrErrorCorrectLevel.H,
                ),
              ),
            ),
            const SizedBox(height: 28),
            const Text(
              'Show this to the guard at the gate.',
              textAlign: TextAlign.center,
              style: TextStyle(fontSize: 17, fontWeight: FontWeight.w600),
            ),
            const SizedBox(height: 8),
            const Text(
              'Turn your screen brightness up if it does not scan.',
              textAlign: TextAlign.center,
              style: TextStyle(color: AppColors.textSecondary),
            ),
            const SizedBox(height: 36),
            // Module 13.3's second tier, offered from the first tier's screen.
            // This is where a worker is standing when they discover there is
            // nobody to show the code to.
            OutlinedButton.icon(
              onPressed: () => context.push(Routes.selfCheckIn),
              icon: const Icon(Icons.where_to_vote_outlined),
              label: const Text('No guard at the gate?'),
              style: OutlinedButton.styleFrom(
                minimumSize: const Size.fromHeight(52),
              ),
            ),
            const SizedBox(height: 12),
            OutlinedButton.icon(
              onPressed: () => _rotate(context, ref),
              icon: const Icon(Icons.refresh),
              label: const Text('I lost my card'),
              style: OutlinedButton.styleFrom(
                minimumSize: const Size.fromHeight(52),
              ),
            ),
          ],
        ),
      ),
    );
  }
}
