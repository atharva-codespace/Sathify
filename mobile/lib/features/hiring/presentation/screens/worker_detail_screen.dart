import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../../../core/errors/api_exception.dart';
import '../../../../core/routing/app_router.dart';
import '../../../../shared/design_system.dart';
import '../../../ai/presentation/widgets/review_summary_card.dart';
import '../../data/models/hiring_models.dart';
import '../providers/hiring_provider.dart';
import '../widgets/match_badge.dart';
import 'hire_request_sheet.dart';

/// Module 4.2 — the profile a resident reads before sending a hire request.
///
/// The point of this screen is real signal, so it leads with verification and
/// the match breakdown rather than with marketing copy.
class WorkerDetailScreen extends ConsumerWidget {
  const WorkerDetailScreen({required this.workerId, super.key});

  final int workerId;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final worker = ref.watch(workerDetailProvider(workerId));

    return Scaffold(
      appBar: AppBar(
        title: const Text('Worker profile'),
        actions: [
          // Module 11.3. Raised from here rather than only from the complaints
          // list, because this is where a resident actually is when something
          // has gone wrong — and it prefills who the complaint is about.
          IconButton(
            tooltip: 'Report a problem',
            icon: const Icon(Icons.report_gmailerrorred_outlined),
            onPressed: () => context.push(
              Routes.raiseComplaintAboutWorker(
                workerId,
                name: worker.valueOrNull?.fullName ?? '',
              ),
            ),
          ),
        ],
      ),
      body: AppSwitcher(
        child: worker.when(
          loading: () => const AppSkeletonList(count: 4),
          error: (error, _) => AppErrorState(
            message: error is ApiException
                ? error.message
                : 'Could not load this profile.',
            onRetry: () => ref.invalidate(workerDetailProvider(workerId)),
          ),
          data: (detail) => _Profile(detail: detail),
        ),
      ),
    );
  }
}

class _Profile extends ConsumerWidget {
  const _Profile({required this.detail});

  final WorkerDetail detail;

  Future<void> _openHireSheet(BuildContext context, WidgetRef ref) async {
    final sent = await showModalBottomSheet<bool>(
      context: context,
      isScrollControlled: true,
      builder: (_) => HireRequestSheet(worker: detail),
    );

    if (sent == true && context.mounted) {
      invalidateHiring(ref);
      showAppSnackBar(
        context,
        'Request sent to ${detail.fullName}',
        tone: AppTone.success,
      );
    }
  }

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final theme = Theme.of(context);
    final summary = detail.summary;

    return Column(
      children: [
        Expanded(
          child: ListView(
            padding: const EdgeInsets.fromLTRB(
              AppSpacing.gutter,
              AppSpacing.xs,
              AppSpacing.gutter,
              AppSpacing.md,
            ),
            children: [
              // The hero. Every reference app opens a provider profile with a
              // large portrait and the headline numbers immediately beside it,
              // because that is the block the decision is actually made on.
              AppFadeIn(
                child: AppCard(
                  padding: const EdgeInsets.all(AppSpacing.lg),
                  child: Column(
                    children: [
                      Row(
                        children: [
                          AppAvatar(
                            name: summary.fullName,
                            imageUrl: summary.photoUrl,
                            seed: detail.id,
                            size: 84,
                          ),
                          const SizedBox(width: AppSpacing.md),
                          Expanded(
                            child: Column(
                              crossAxisAlignment: CrossAxisAlignment.start,
                              children: [
                                Text(
                                  summary.fullName,
                                  style: theme.textTheme.titleLarge,
                                ),
                                if (summary.availabilityLabel.isNotEmpty) ...[
                                  const SizedBox(height: 2),
                                  Text(
                                    summary.availabilityLabel,
                                    style: theme.textTheme.bodySmall,
                                  ),
                                ],
                                if (summary.matchPercentage != null) ...[
                                  const SizedBox(height: AppSpacing.xs),
                                  MatchBadge(
                                    percentage: summary.matchPercentage!,
                                    compact: false,
                                  ),
                                ],
                              ],
                            ),
                          ),
                        ],
                      ),
                      const SizedBox(height: AppSpacing.md),
                      const Divider(height: 1),
                      const SizedBox(height: AppSpacing.sm),
                      Row(
                        children: [
                          _HeroStat(
                            label: 'Rating',
                            value: summary.hasRating
                                ? summary.averageRating.toStringAsFixed(1)
                                : 'New',
                          ),
                          const _HeroRule(),
                          _HeroStat(
                            label: 'Jobs',
                            value: '${summary.engagementCount}',
                          ),
                          const _HeroRule(),
                          _HeroStat(
                            label: 'Trust',
                            value: summary.trustScore > 0
                                ? summary.trustScore.toStringAsFixed(0)
                                : '—',
                          ),
                        ],
                      ),
                    ],
                  ),
                ),
              ),
              const SizedBox(height: AppSpacing.sm),
              AppFadeIn(
                index: 1,
                child: _VerificationCard(verification: detail.verification),
              ),
              const SizedBox(height: AppSpacing.sm),
              AppFadeIn(index: 2, child: _FactsCard(detail: detail)),
              // Module 12.5 — what the reviews say, above the button that opens
              // them. Renders nothing when there is nothing to summarise, which
              // is why it carries its own margin rather than a SizedBox here.
              ReviewSummaryCard(workerId: detail.id),
              const SizedBox(height: AppSpacing.sm),
              // Module 9 — the evidence behind the numbers above. A resident
              // deciding who enters their home should be able to read the
              // reviews and see how the trust score was arrived at.
              Row(
                children: [
                  Expanded(
                    child: AppButton.secondary(
                      label: 'Reviews',
                      icon: Icons.rate_review_outlined,
                      onPressed: () =>
                          context.push(Routes.workerReviewsPath(detail.id)),
                    ),
                  ),
                  const SizedBox(width: AppSpacing.sm),
                  Expanded(
                    child: AppButton.secondary(
                      label: 'Trust score',
                      icon: Icons.verified_outlined,
                      onPressed: () =>
                          context.push(Routes.workerTrustPath(detail.id)),
                    ),
                  ),
                ],
              ),
              if (detail.bio.isNotEmpty) ...[
                const SizedBox(height: AppSpacing.sm),
                AppCard(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text('About', style: theme.textTheme.titleSmall),
                      const SizedBox(height: AppSpacing.xs),
                      Text(detail.bio, style: theme.textTheme.bodyMedium),
                    ],
                  ),
                ),
              ],
              if (detail.matchBreakdown.isNotEmpty) ...[
                const SizedBox(height: AppSpacing.sm),
                AppCard(
                  child: MatchBreakdown(components: detail.matchBreakdown),
                ),
              ],
            ],
          ),
        ),

        // A pinned action bar rather than a button at the end of the scroll.
        // This is the conversion step, and in every reference app it stays on
        // screen — a resident who has read half the profile should not have to
        // scroll to the bottom to act on it.
        //
        // Deliberately flat: the persistent bottom navigation sits directly
        // beneath this, and giving both a shadow made the two read as two
        // separate slabs of chrome stacked on top of each other. A hairline is
        // enough to separate the CTA from the scrolling content, and the nav
        // bar's own shadow then closes the assembly.
        Container(
          decoration: const BoxDecoration(
            color: AppColors.surface,
            border: Border(top: BorderSide(color: AppColors.border)),
          ),
          child: SafeArea(
            top: false,
            child: Padding(
              padding: const EdgeInsets.all(AppSpacing.sm),
              child: AppButton(
                label: 'Send hire request',
                icon: Icons.send_outlined,
                onPressed: () => _openHireSheet(context, ref),
              ),
            ),
          ),
        ),
      ],
    );
  }
}

/// One headline number in the hero card.
class _HeroStat extends StatelessWidget {
  const _HeroStat({required this.label, required this.value});

  final String label;
  final String value;

  @override
  Widget build(BuildContext context) {
    return Expanded(
      child: Column(
        children: [
          Text(
            value,
            style: const TextStyle(
              fontSize: 19,
              fontWeight: FontWeight.w700,
              letterSpacing: -0.3,
              color: AppColors.textPrimary,
            ),
          ),
          const SizedBox(height: 1),
          Text(
            label,
            style: const TextStyle(
              fontSize: 11.5,
              fontWeight: FontWeight.w600,
              color: AppColors.textTertiary,
            ),
          ),
        ],
      ),
    );
  }
}

class _HeroRule extends StatelessWidget {
  const _HeroRule();

  @override
  Widget build(BuildContext context) {
    return Container(width: 1, height: 28, color: AppColors.border);
  }
}

class _VerificationCard extends StatelessWidget {
  const _VerificationCard({required this.verification});

  final WorkerVerification verification;

  @override
  Widget build(BuildContext context) {
    return AppCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text('Verification', style: Theme.of(context).textTheme.titleSmall),
          const SizedBox(height: AppSpacing.sm),
          _CheckRow(
            passed: verification.isApproved,
            label: 'Approved by your society administrator',
          ),
          const SizedBox(height: AppSpacing.xs),
          // Reported separately from approval rather than merged into one
          // tick: a resident deciding who enters their home should see which
          // checks actually passed, not a single opaque badge.
          _CheckRow(
            passed: verification.idVerified,
            label: verification.idVerified
                ? 'Government ID verified ${verification.idMasked ?? ''}'.trim()
                : 'Government ID not yet verified',
          ),
        ],
      ),
    );
  }
}

class _CheckRow extends StatelessWidget {
  const _CheckRow({required this.passed, required this.label});

  final bool passed;
  final String label;

  @override
  Widget build(BuildContext context) {
    return Row(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Icon(
          passed ? Icons.verified_rounded : Icons.pending_outlined,
          size: AppIconSize.md,
          color: passed ? AppColors.success : AppColors.warning,
        ),
        const SizedBox(width: AppSpacing.xs),
        Expanded(
          child: Text(
            label,
            style: const TextStyle(
              fontSize: 14,
              height: 1.4,
              fontWeight: FontWeight.w500,
              color: AppColors.textPrimary,
            ),
          ),
        ),
      ],
    );
  }
}

class _FactsCard extends StatelessWidget {
  const _FactsCard({required this.detail});

  final WorkerDetail detail;

  @override
  Widget build(BuildContext context) {
    final summary = detail.summary;
    final responseRate = detail.responseRate;

    return AppCard(
      child: Column(
        children: [
          _FactRow(
            icon: Icons.star_rounded,
            label: 'Rating',
            value: summary.hasRating
                ? '${summary.averageRating.toStringAsFixed(1)} / 5'
                : 'Not rated yet',
          ),
          _FactRow(
            icon: Icons.shield_outlined,
            label: 'Trust score',
            value: summary.trustScore > 0
                ? summary.trustScore.toStringAsFixed(0)
                : 'Building up',
          ),
          _FactRow(
            icon: Icons.handshake_outlined,
            label: 'Engagements',
            value: '${summary.engagementCount}',
          ),
          _FactRow(
            icon: Icons.reply_outlined,
            label: 'Responds to requests',
            // Null means no request history. Showing "0%" would invent a
            // track record the worker has not had the chance to build.
            value: responseRate == null
                ? 'No requests yet'
                : '${(responseRate * 100).round()}%',
          ),
          if (summary.expectedMonthlyRate != null)
            _FactRow(
              icon: Icons.currency_rupee,
              label: 'Expected rate',
              value: '₹${summary.expectedMonthlyRate} / month',
            ),
          if (summary.availabilityLabel.isNotEmpty)
            _FactRow(
              icon: Icons.schedule,
              label: 'Available',
              value: summary.availabilityLabel,
            ),
          if (summary.languagesSpoken.isNotEmpty)
            _FactRow(
              icon: Icons.translate,
              label: 'Languages',
              value: summary.languagesSpoken,
            ),
        ],
      ),
    );
  }
}

class _FactRow extends StatelessWidget {
  const _FactRow({
    required this.icon,
    required this.label,
    required this.value,
  });

  final IconData icon;
  final String label;
  final String value;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: AppSpacing.xxs + 2),
      child: Row(
        children: [
          Icon(icon, size: AppIconSize.sm, color: AppColors.textTertiary),
          const SizedBox(width: AppSpacing.sm),
          Expanded(
            child: Text(
              label,
              style: const TextStyle(
                color: AppColors.textSecondary,
                fontSize: 14,
              ),
            ),
          ),
          Text(
            value,
            style: const TextStyle(
              fontWeight: FontWeight.w600,
              fontSize: 14,
              color: AppColors.textPrimary,
            ),
          ),
        ],
      ),
    );
  }
}
