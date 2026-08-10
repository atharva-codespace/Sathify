import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../../core/errors/api_exception.dart';
import '../../../../shared/design_system.dart';
import '../../data/models/rating_models.dart';
import '../providers/rating_provider.dart';
import 'star_rating.dart';

/// Module 9.1 — the rating sheet, and the one way to open it.
///
/// -----------------------------------------------------------------------
/// WHY THIS IS SHARED AND NOT PRIVATE TO THE RATE SCREEN
/// -----------------------------------------------------------------------
/// It used to be a private widget inside the Rate Work screen, which meant the
/// only way to rate anything was to find that screen in the Account menu. A
/// finished job is the moment somebody actually has an opinion, so the booking
/// card and the engagement card now open the same sheet the moment work ends.
/// One copy, so the submit rules, the flagged-rating wording and the "your
/// rating is visible to both of you" disclosure cannot drift apart.

/// Opens the rating sheet for [job], then reports the outcome.
///
/// Returns the submitted rating, or null if the sheet was dismissed.
///
/// The messenger is captured before the await deliberately: callers open this
/// from a card that may well be rebuilt out from under them when the ratings
/// are invalidated, and a [BuildContext] used afterwards would throw.
Future<Rating?> showRateJobSheet(
  BuildContext context,
  WidgetRef ref,
  RateableJob job,
) async {
  final messenger = ScaffoldMessenger.of(context);

  final rating = await showModalBottomSheet<Rating>(
    context: context,
    isScrollControlled: true,
    builder: (_) => RateSheet(job: job),
  );
  if (rating == null) return null;

  invalidateRatings(ref);
  showAppSnackBarOn(
    messenger,
    // A flagged rating is not rejected and not hidden from the person who
    // wrote it — saying so here is what stops it reading as a silent failure.
    rating.isFlagged
        ? 'Thank you. This is under review before it counts.'
        : 'Thank you for your rating.',
    tone: AppTone.success,
  );
  return rating;
}

/// The sheet itself. Prefer [showRateJobSheet] over building this directly.
class RateSheet extends ConsumerStatefulWidget {
  const RateSheet({required this.job, super.key});

  final RateableJob job;

  @override
  ConsumerState<RateSheet> createState() => _RateSheetState();
}

class _RateSheetState extends ConsumerState<RateSheet> {
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
            if (job.title.isNotEmpty) ...[
              const SizedBox(height: 4),
              Text(
                job.title,
                textAlign: TextAlign.center,
                style: const TextStyle(color: AppColors.textSecondary),
              ),
            ],
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
