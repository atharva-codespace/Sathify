import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../../core/errors/api_exception.dart';
import '../../../../shared/design_system.dart';
import '../../data/models/rating_models.dart';
import '../providers/rating_provider.dart';
import '../widgets/star_rating.dart';

/// Module 9.4 — the administrator's queue of flagged ratings.
///
/// The framing matters. These are heuristics, and every one has an innocent
/// explanation: a burst is a resident catching up on a month of bookings,
/// uniform five stars is a genuinely good worker, repeated text is someone with
/// little to say. So the screen presents a flag as a question rather than an
/// accusation, and "this is genuine" is the primary action.
class ReviewFlagsScreen extends ConsumerWidget {
  const ReviewFlagsScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final flags = ref.watch(reviewFlagsProvider);

    return Scaffold(
      appBar: AppBar(title: const Text('Reviews to check')),
      body: flags.when(
        loading: () => const AppSkeletonList(),
        error: (error, _) => AppErrorState(
          message: error is ApiException
              ? error.message
              : 'Could not load the queue.',
          onRetry: () => ref.invalidate(reviewFlagsProvider),
        ),
        data: (items) {
          if (items.isEmpty) {
            return const AppEmptyState(
              icon: Icons.verified_user_outlined,
              title: 'Nothing to check',
              message: 'No reviews have been flagged.',
            );
          }
          return RefreshIndicator(
            onRefresh: () async => ref.invalidate(reviewFlagsProvider),
            child: ListView.builder(
              padding: const EdgeInsets.symmetric(vertical: 8),
              itemCount: items.length,
              itemBuilder: (context, index) => _FlagCard(flag: items[index]),
            ),
          );
        },
      ),
    );
  }
}

class _FlagCard extends ConsumerStatefulWidget {
  const _FlagCard({required this.flag});

  final ReviewFlag flag;

  @override
  ConsumerState<_FlagCard> createState() => _FlagCardState();
}

class _FlagCardState extends ConsumerState<_FlagCard> {
  bool _isBusy = false;

  Future<void> _resolve({required bool upheld}) async {
    final note = await showDialog<String>(
      context: context,
      builder: (_) => _NoteDialog(
        title: upheld ? 'Keep this review hidden?' : 'Count this review?',
        hint: upheld
            ? 'e.g. clearly manufactured, same text from one account'
            : 'e.g. genuine — resident was catching up on a month of bookings',
      ),
    );
    if (note == null || note.trim().isEmpty) return;

    setState(() => _isBusy = true);
    try {
      await ref.read(ratingRepositoryProvider).resolveFlag(
            widget.flag.id,
            upheld: upheld,
            note: note.trim(),
          );
      if (!mounted) return;
      invalidateRatings(ref);
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(
          content: Text(
            upheld ? 'Review kept hidden.' : 'Review restored and counted.',
          ),
        ),
      );
    } on ApiException catch (error) {
      if (!mounted) return;
      setState(() => _isBusy = false);
      ScaffoldMessenger.of(context)
          .showSnackBar(SnackBar(content: Text(error.message)));
    }
  }

  @override
  Widget build(BuildContext context) {
    final flag = widget.flag;
    final rating = flag.rating;
    final theme = Theme.of(context);

    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                const Icon(Icons.flag_outlined, color: AppColors.warning),
                const SizedBox(width: 8),
                Expanded(
                  child: Text(
                    flag.reasonDisplay,
                    style: theme.textTheme.titleMedium
                        ?.copyWith(fontWeight: FontWeight.w600),
                  ),
                ),
              ],
            ),
            if (flag.detail.isNotEmpty) ...[
              const SizedBox(height: 6),
              Text(
                flag.detail,
                style: const TextStyle(
                  fontSize: 13,
                  color: AppColors.textSecondary,
                ),
              ),
            ],
            if (rating != null) ...[
              const SizedBox(height: 14),
              Container(
                width: double.infinity,
                padding: const EdgeInsets.all(12),
                decoration: BoxDecoration(
                  color: AppColors.surfaceMuted,
                  borderRadius: BorderRadius.circular(10),
                ),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    StarDisplay(stars: rating.stars, size: 16),
                    if (rating.hasReview) ...[
                      const SizedBox(height: 8),
                      Text('“${rating.review}”'),
                    ],
                    const SizedBox(height: 8),
                    Text(
                      'by ${rating.raterName} · about '
                      '${rating.subjectIsWorker ? rating.workerName : rating.residentName}',
                      style: const TextStyle(
                        fontSize: 12,
                        color: AppColors.textSecondary,
                      ),
                    ),
                  ],
                ),
              ),
            ],
            const SizedBox(height: 8),
            const Text(
              'This review is not counting toward anyone’s score until you decide.',
              style: TextStyle(fontSize: 12.5, color: AppColors.textSecondary),
            ),
            const SizedBox(height: 14),
            if (_isBusy)
              const Center(child: CircularProgressIndicator())
            else
              Row(
                children: [
                  Expanded(
                    child: OutlinedButton.icon(
                      onPressed: () => _resolve(upheld: true),
                      icon: const Icon(Icons.visibility_off_outlined),
                      label: const Text('Keep hidden'),
                      style: OutlinedButton.styleFrom(
                        foregroundColor: AppColors.danger,
                        minimumSize: const Size.fromHeight(48),
                      ),
                    ),
                  ),
                  const SizedBox(width: 12),
                  Expanded(
                    // The primary action: most flags are false positives.
                    child: ElevatedButton.icon(
                      onPressed: () => _resolve(upheld: false),
                      icon: const Icon(Icons.check),
                      label: const Text('It’s genuine'),
                      style: ElevatedButton.styleFrom(
                        minimumSize: const Size.fromHeight(48),
                      ),
                    ),
                  ),
                ],
              ),
          ],
        ),
      ),
    );
  }
}

class _NoteDialog extends StatefulWidget {
  const _NoteDialog({required this.title, required this.hint});

  final String title;
  final String hint;

  @override
  State<_NoteDialog> createState() => _NoteDialogState();
}

class _NoteDialogState extends State<_NoteDialog> {
  final _controller = TextEditingController();

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return AlertDialog(
      title: Text(widget.title),
      content: Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const Text(
            'Your reason is recorded against this decision.',
            style: TextStyle(fontSize: 13, color: AppColors.textSecondary),
          ),
          const SizedBox(height: 12),
          TextField(
            controller: _controller,
            autofocus: true,
            maxLines: 2,
            decoration: InputDecoration(hintText: widget.hint),
          ),
        ],
      ),
      actions: [
        TextButton(
          onPressed: () => Navigator.of(context).pop(),
          child: const Text('Cancel'),
        ),
        FilledButton(
          onPressed: () => Navigator.of(context).pop(_controller.text),
          child: const Text('Confirm'),
        ),
      ],
    );
  }
}
