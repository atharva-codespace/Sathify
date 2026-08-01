import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../../core/errors/api_exception.dart';
import '../../../../shared/design_system.dart';
import '../../data/models/rating_models.dart';
import '../providers/rating_provider.dart';
import '../widgets/star_rating.dart';

/// Module 9.1 — rate a completed job.
///
/// Both sides use this screen. The server decides which direction a rating runs
/// from the caller's role, so the app cannot rate on the wrong side, and the
/// copy is the only thing that differs.
class RateJobScreen extends ConsumerWidget {
  const RateJobScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final pending = ref.watch(pendingRatingsProvider);

    return Scaffold(
      appBar: AppBar(title: const Text('Rate your recent work')),
      body: pending.when(
        loading: () => const AppSkeletonList(),
        error: (error, _) => AppErrorState(
          message: error is ApiException
              ? error.message
              : 'Could not load your jobs.',
          onRetry: () => ref.invalidate(pendingRatingsProvider),
        ),
        data: (jobs) {
          if (jobs.isEmpty) {
            return const _Empty();
          }
          return RefreshIndicator(
            onRefresh: () async => ref.invalidate(pendingRatingsProvider),
            child: ListView.builder(
              padding: const EdgeInsets.symmetric(vertical: 8),
              itemCount: jobs.length,
              itemBuilder: (context, index) => _JobCard(job: jobs[index]),
            ),
          );
        },
      ),
    );
  }
}

class _Empty extends StatelessWidget {
  const _Empty();

  @override
  Widget build(BuildContext context) {
    return const AppEmptyState(
      icon: Icons.star_border_rounded,
      title: 'Nothing to rate',
      message: 'Once a job finishes, you can rate it here.',
    );
  }
}

class _JobCard extends ConsumerStatefulWidget {
  const _JobCard({required this.job});

  final RateableJob job;

  @override
  ConsumerState<_JobCard> createState() => _JobCardState();
}

class _JobCardState extends ConsumerState<_JobCard> {
  Future<void> _rate() async {
    final result = await showModalBottomSheet<Rating>(
      context: context,
      isScrollControlled: true,
      builder: (_) => _RateSheet(job: widget.job),
    );
    if (result == null || !mounted) return;

    invalidateRatings(ref);
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(
        content: Text(
          result.isFlagged
              ? 'Thank you. This is under review before it counts.'
              : 'Thank you for your rating.',
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final job = widget.job;
    final theme = Theme.of(context);

    return Card(
      child: ListTile(
        contentPadding:
            const EdgeInsets.symmetric(horizontal: 16, vertical: 10),
        title: Text(
          job.counterpartyName.isEmpty ? job.title : job.counterpartyName,
          style: theme.textTheme.titleMedium
              ?.copyWith(fontWeight: FontWeight.w600),
        ),
        subtitle: Text(
          [
            job.title,
            if (job.flatLabel.isNotEmpty) job.flatLabel,
            if (job.finishedOn != null)
              'finished ${job.finishedOn!.day}/${job.finishedOn!.month}',
          ].join(' · '),
        ),
        trailing: FilledButton(onPressed: _rate, child: const Text('Rate')),
      ),
    );
  }
}

class _RateSheet extends ConsumerStatefulWidget {
  const _RateSheet({required this.job});

  final RateableJob job;

  @override
  ConsumerState<_RateSheet> createState() => _RateSheetState();
}

class _RateSheetState extends ConsumerState<_RateSheet> {
  int _stars = 0;
  final _controller = TextEditingController();
  bool _isSaving = false;
  String? _error;

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  Future<void> _submit() async {
    if (_stars < 1) {
      setState(() => _error = 'Choose a rating first.');
      return;
    }

    setState(() {
      _isSaving = true;
      _error = null;
    });

    try {
      final rating = await ref.read(ratingRepositoryProvider).submit(
            job: widget.job,
            stars: _stars,
            review: _controller.text,
          );
      if (mounted) Navigator.of(context).pop(rating);
    } on ApiException catch (error) {
      if (!mounted) return;
      setState(() {
        _isSaving = false;
        _error = error.message;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final job = widget.job;

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
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Text(
              'How did it go with '
              '${job.counterpartyName.isEmpty ? "this job" : job.counterpartyName}?',
              textAlign: TextAlign.center,
              style: Theme.of(context)
                  .textTheme
                  .titleLarge
                  ?.copyWith(fontWeight: FontWeight.w700),
            ),
            const SizedBox(height: 20),
            StarPicker(
              value: _stars,
              onChanged: (value) => setState(() {
                _stars = value;
                _error = null;
              }),
            ),
            const SizedBox(height: 20),
            TextField(
              controller: _controller,
              maxLines: 3,
              maxLength: 1000,
              decoration: const InputDecoration(
                labelText: 'Anything to add? (optional)',
                hintText: 'You can write in Hindi or English',
              ),
            ),
            const SizedBox(height: 4),
            const Text(
              'Your rating is shown on their profile, and both of you can see it.',
              textAlign: TextAlign.center,
              style: TextStyle(fontSize: 12.5, color: AppColors.textSecondary),
            ),
            if (_error != null) ...[
              const SizedBox(height: 12),
              Text(
                _error!,
                textAlign: TextAlign.center,
                style: const TextStyle(color: AppColors.danger),
              ),
            ],
            const SizedBox(height: 16),
            ElevatedButton.icon(
              onPressed: _isSaving ? null : _submit,
              icon: _isSaving
                  ? const SizedBox(
                      width: 20,
                      height: 20,
                      child: CircularProgressIndicator(strokeWidth: 2),
                    )
                  : const Icon(Icons.check),
              label: Text(_isSaving ? 'Sending…' : 'Submit'),
            ),
            TextButton(
              onPressed: _isSaving ? null : () => Navigator.of(context).pop(),
              child: const Text('Not now'),
            ),
          ],
        ),
      ),
    );
  }
}
