import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../../../core/errors/api_exception.dart';
import '../../../../core/routing/app_router.dart';
import '../../../../shared/design_system.dart';
import '../../data/models/worker_models.dart';
import '../providers/worker_provider.dart';

/// Module 3 — the worker's onboarding hub.
///
/// Reachable while unapproved, and deliberately so: building the profile and
/// uploading the document is exactly what an administrator reviews, so gating
/// it behind approval would deadlock onboarding. Mirrors how Module 2.3 lets a
/// resident claim their flat before they are approved.
///
/// The screen leads with what is still missing rather than with a status
/// message. A worker left on "pending approval" with no idea that their photo
/// is the thing holding it up will simply wait forever.
class WorkerOnboardingScreen extends ConsumerWidget {
  const WorkerOnboardingScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final profile = ref.watch(myWorkerProfileProvider);

    return Scaffold(
      appBar: AppBar(title: const Text('Get verified')),
      body: profile.when(
        loading: () => const AppSkeletonList(),
        error: (error, _) => Center(
          child: Padding(
            padding: const EdgeInsets.all(24),
            child: Column(
              mainAxisAlignment: MainAxisAlignment.center,
              children: [
                Text(
                  error is ApiException
                      ? error.message
                      : 'Could not load your profile.',
                  textAlign: TextAlign.center,
                ),
                const SizedBox(height: 16),
                OutlinedButton.icon(
                  onPressed: () => ref.invalidate(myWorkerProfileProvider),
                  icon: const Icon(Icons.refresh),
                  label: const Text('Try again'),
                ),
              ],
            ),
          ),
        ),
        data: (worker) => RefreshIndicator(
          onRefresh: () async => invalidateOnboarding(ref),
          child: ListView(
            padding: const EdgeInsets.all(16),
            children: [
              _StatusBanner(worker: worker),
              const SizedBox(height: 20),
              _StepList(worker: worker),
            ],
          ),
        ),
      ),
    );
  }
}

class _StatusBanner extends StatelessWidget {
  const _StatusBanner({required this.worker});

  final WorkerProfile? worker;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);

    late final Color colour;
    late final IconData icon;
    late final String title;
    late final String body;

    if (worker == null) {
      colour = AppColors.info;
      icon = Icons.person_add_alt;
      title = 'Let’s get you set up';
      body = 'Three short steps and your society can approve you for work.';
    } else if (worker!.wasRejected) {
      colour = AppColors.danger;
      icon = Icons.error_outline;
      title = 'Your registration was not accepted';
      // The reason comes from the administrator and is shown verbatim: it is
      // the only thing that tells the worker what to actually fix.
      body = worker!.rejectionReason;
    } else if (worker!.isApproved) {
      colour = AppColors.success;
      icon = Icons.verified;
      title = 'You are verified';
      body = worker!.isSearchable
          ? 'Residents can find you and send you work.'
          : 'You are approved, but marked unavailable, so you will not appear '
              'in search. Turn availability back on when you are ready.';
    } else if (worker!.remainingSteps.isEmpty) {
      colour = AppColors.warning;
      icon = Icons.hourglass_top;
      title = 'Waiting for your society';
      body =
          'Everything is submitted. Your administrator will review it shortly.';
    } else {
      colour = AppColors.warning;
      icon = Icons.pending_actions;
      title = 'Almost there';
      body = 'Finish the steps below so your society can review you.';
    }

    return Container(
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        color: colour.withValues(alpha: 0.10),
        borderRadius: BorderRadius.circular(14),
        border: Border.all(color: colour.withValues(alpha: 0.35)),
      ),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Icon(icon, color: colour, size: 28),
          const SizedBox(width: 14),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  title,
                  style: theme.textTheme.titleMedium
                      ?.copyWith(fontWeight: FontWeight.w700, color: colour),
                ),
                if (body.isNotEmpty) ...[
                  const SizedBox(height: 4),
                  Text(body),
                ],
              ],
            ),
          ),
        ],
      ),
    );
  }
}

class _StepList extends StatelessWidget {
  const _StepList({required this.worker});

  final WorkerProfile? worker;

  @override
  Widget build(BuildContext context) {
    final hasProfile = worker != null;
    final hasPhoto = worker?.hasPhoto ?? false;
    final hasServices = (worker?.serviceTypes.isNotEmpty) ?? false;
    final kycStatus = worker?.kycStatus;

    return Column(
      children: [
        _StepCard(
          number: 1,
          title: 'Your details',
          subtitle: hasProfile
              ? '${worker!.serviceTypes.map((s) => s.name).join(', ')}'
                  '${worker!.yearsOfExperience > 0 ? ' · ${worker!.yearsOfExperience} yrs' : ''}'
              : 'The work you do, your experience and your hours',
          done: hasProfile && hasServices,
          // Nothing else can be done until a profile exists — the server
          // refuses a document upload without one.
          enabled: true,
          actionLabel: hasProfile ? 'Edit' : 'Start',
          onTap: () => context.push(Routes.workerProfileEdit),
        ),
        _StepCard(
          number: 2,
          title: 'Your photo',
          subtitle: hasPhoto
              ? 'Added'
              : 'Used to confirm it is you at the society gate. Required.',
          done: hasPhoto,
          enabled: hasProfile,
          actionLabel: hasPhoto ? 'Change' : 'Add',
          onTap: () => context.push(Routes.workerProfileEdit),
        ),
        _StepCard(
          number: 3,
          title: 'Your Aadhaar card',
          subtitle: _kycSubtitle(kycStatus),
          done: kycStatus == 'completed',
          enabled: hasProfile,
          actionLabel: kycStatus == null ? 'Upload' : 'View',
          onTap: () => context.push(Routes.kycUpload),
        ),
      ],
    );
  }

  String _kycSubtitle(String? status) {
    switch (status) {
      case 'completed':
        return 'Uploaded and read';
      case 'failed':
        return 'We could not read it — retake the photo or type your details';
      case 'processing':
      case 'pending':
        return 'Being read…';
      default:
        return 'Photograph the front of your card';
    }
  }
}

class _StepCard extends StatelessWidget {
  const _StepCard({
    required this.number,
    required this.title,
    required this.subtitle,
    required this.done,
    required this.enabled,
    required this.actionLabel,
    required this.onTap,
  });

  final int number;
  final String title;
  final String subtitle;
  final bool done;
  final bool enabled;
  final String actionLabel;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final colour = done ? AppColors.success : AppColors.textSecondary;

    return Card(
      margin: const EdgeInsets.only(bottom: 12),
      child: Opacity(
        opacity: enabled ? 1 : 0.5,
        child: Padding(
          padding: const EdgeInsets.all(16),
          child: Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              CircleAvatar(
                radius: 18,
                backgroundColor: colour.withValues(alpha: 0.14),
                child: done
                    ? const Icon(
                        Icons.check,
                        color: AppColors.success,
                        size: 20,
                      )
                    : Text(
                        '$number',
                        style: TextStyle(
                          color: colour,
                          fontWeight: FontWeight.w700,
                        ),
                      ),
              ),
              const SizedBox(width: 14),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      title,
                      style: theme.textTheme.titleMedium
                          ?.copyWith(fontWeight: FontWeight.w600),
                    ),
                    const SizedBox(height: 4),
                    Text(subtitle, style: theme.textTheme.bodySmall),
                  ],
                ),
              ),
              const SizedBox(width: 8),
              TextButton(
                onPressed: enabled ? onTap : null,
                child: Text(actionLabel),
              ),
            ],
          ),
        ),
      ),
    );
  }
}
