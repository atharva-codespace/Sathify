import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../../core/errors/api_exception.dart';
import '../../data/models/ai_models.dart';
import '../../data/repositories/ai_repository.dart';

final aiRepositoryProvider = Provider<AiRepository>((ref) => AiRepository());

/// What this deployment can do.
///
/// Not autoDisposed: capability does not change while the app is running, and
/// re-fetching it on every screen that wants to know would be a request per
/// navigation for an answer that is always the same.
final aiStatusProvider = FutureProvider<AiStatus>(
  (ref) => ref.read(aiRepositoryProvider).fetchStatus(),
);

/// Module 12.5 — a worker's condensed reviews, for their profile.
final reviewSummaryProvider =
    FutureProvider.autoDispose.family<ReviewSummary, int>(
  (ref, workerId) =>
      ref.read(aiRepositoryProvider).fetchReviewSummary(workerId),
);

/// Module 12.2 — the on-screen conversation.
///
/// The history lives here and nowhere else. The server is stateless per
/// question and stores no transcript, so the app storing one would create the
/// only durable copy of what people asked — which is precisely what the backend
/// declined to keep.
class ChatNotifier extends AutoDisposeNotifier<List<ChatTurn>> {
  @override
  List<ChatTurn> build() => const [];

  Future<void> ask(String question) async {
    final text = question.trim();
    if (text.isEmpty) return;

    state = [...state, ChatTurn.user(text), ChatTurn.pending()];

    try {
      final reply = await ref.read(aiRepositoryProvider).ask(text);
      _replacePending(ChatTurn.assistant(reply));
    } on ApiException catch (error) {
      // Rendered as a turn rather than a snackbar: the failure belongs in the
      // thread it happened in, so a user scrolling back can see which question
      // went unanswered.
      _replacePending(ChatTurn.failure(error.message));
    }
  }

  void clear() => state = const [];

  void _replacePending(ChatTurn resolved) {
    final index = state.lastIndexWhere((turn) => turn.isPending);
    if (index < 0) {
      state = [...state, resolved];
      return;
    }
    state = [...state]..[index] = resolved;
  }
}

final chatProvider =
    AutoDisposeNotifierProvider<ChatNotifier, List<ChatTurn>>(ChatNotifier.new);

/// Module 12.5 — the suggested category for whatever is currently typed into
/// the complaint form.
///
/// A family keyed on the text so the same description is not re-classified on
/// every rebuild. Each distinct question costs a provider call against a
/// metered free tier.
final complaintSuggestionProvider =
    FutureProvider.autoDispose.family<ComplaintSuggestion, String>(
  (ref, description) => ref
      .read(aiRepositoryProvider)
      .classifyComplaint(description: description),
);
