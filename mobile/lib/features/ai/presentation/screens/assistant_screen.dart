import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../../shared/design_system.dart';
import '../../data/models/ai_models.dart';
import '../providers/ai_provider.dart';

/// Module 12.2 — the assistant.
///
/// -----------------------------------------------------------------------
/// EVERY FIGURE ON THIS SCREEN CAME OUT OF THE DATABASE
/// -----------------------------------------------------------------------
/// The server uses a model for exactly one thing: working out which lookup a
/// question is asking for. The numbers, dates and receipt references are read
/// from the user's own records by the same querysets the corresponding screens
/// use. Nothing here is composed by a model.
///
/// That is why [_FactList] renders structured rows rather than the sentence:
/// the sentence is a summary the server wrote from the same data, and showing
/// the rows underneath it makes the answer checkable. A user who does not
/// believe "₹4,500 paid" can see the two receipts it came from.
///
/// -----------------------------------------------------------------------
/// IT WORKS WITH NO PROVIDER AT ALL
/// -----------------------------------------------------------------------
/// With no API key configured the server falls back to a keyword pass for
/// understanding the question, and the answer is identical — because the answer
/// never depended on the model. So this screen is never hidden, and the only
/// visible difference is the small "matched by keywords" note.
class AssistantScreen extends ConsumerStatefulWidget {
  const AssistantScreen({super.key});

  @override
  ConsumerState<AssistantScreen> createState() => _AssistantScreenState();
}

class _AssistantScreenState extends ConsumerState<AssistantScreen> {
  final _controller = TextEditingController();
  final _scrollController = ScrollController();

  @override
  void dispose() {
    _controller.dispose();
    _scrollController.dispose();
    super.dispose();
  }

  Future<void> _send(String question) async {
    if (question.trim().isEmpty) return;
    _controller.clear();

    await ref.read(chatProvider.notifier).ask(question);
    _scrollToEnd();
  }

  void _scrollToEnd() {
    // After the frame the new turn was added in, or the extent is still the old
    // one and the newest message stays just off screen.
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (!_scrollController.hasClients) return;
      _scrollController.animateTo(
        _scrollController.position.maxScrollExtent,
        duration: const Duration(milliseconds: 250),
        curve: Curves.easeOut,
      );
    });
  }

  @override
  Widget build(BuildContext context) {
    final turns = ref.watch(chatProvider);

    return Scaffold(
      appBar: AppBar(
        title: const Text('Assistant'),
        actions: [
          if (turns.isNotEmpty)
            IconButton(
              tooltip: 'Clear',
              icon: const Icon(Icons.delete_outline),
              onPressed: () => ref.read(chatProvider.notifier).clear(),
            ),
        ],
      ),
      body: Column(
        children: [
          Expanded(
            child: turns.isEmpty
                ? _Welcome(onAsk: _send)
                : ListView.builder(
                    controller: _scrollController,
                    padding: const EdgeInsets.fromLTRB(12, 16, 12, 8),
                    itemCount: turns.length,
                    itemBuilder: (context, index) => _TurnBubble(
                      turn: turns[index],
                      onSuggestion: _send,
                    ),
                  ),
          ),
          const _PrivacyNote(),
          _Composer(controller: _controller, onSend: _send),
        ],
      ),
    );
  }
}

class _Welcome extends ConsumerWidget {
  const _Welcome({required this.onAsk});

  final ValueChanged<String> onAsk;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final status = ref.watch(aiStatusProvider);

    return ListView(
      padding: const EdgeInsets.all(24),
      children: [
        const SizedBox(height: 24),
        const Icon(
          Icons.forum_outlined,
          size: 64,
          color: AppColors.textTertiary,
        ),
        const SizedBox(height: 16),
        Text(
          'Ask about your own records',
          textAlign: TextAlign.center,
          style: Theme.of(context)
              .textTheme
              .titleMedium
              ?.copyWith(fontWeight: FontWeight.w700),
        ),
        const SizedBox(height: 8),
        const Text(
          'Your schedule, payments, bookings and complaints. '
          'Answers are read from your records, not written by a model.',
          textAlign: TextAlign.center,
          style: TextStyle(color: AppColors.textSecondary),
        ),
        const SizedBox(height: 24),
        // Offered before the first question. An empty chat box and no idea what
        // to type is the most common way an assistant goes unused.
        for (final suggestion in openingSuggestions)
          Padding(
            padding: const EdgeInsets.only(bottom: 8),
            child: OutlinedButton(
              onPressed: () => onAsk(suggestion),
              child: Text(suggestion),
            ),
          ),
        if (status.valueOrNull?.hasProvider == false) ...[
          const SizedBox(height: 16),
          const Text(
            'No language model is configured on this server, so questions are '
            'matched by keywords. The answers are the same either way.',
            textAlign: TextAlign.center,
            style: TextStyle(fontSize: 12, color: AppColors.textSecondary),
          ),
        ],
      ],
    );
  }

  /// Deliberately not role-specific. All four read sensibly for a resident and
  /// a worker alike, and branching on role here would duplicate a decision the
  /// server already makes when it answers.
  static const openingSuggestions = [
    'What is on my schedule this week?',
    'What have I paid this month?',
    'Do I have any bookings coming up?',
    'What complaints have I raised?',
  ];
}

class _TurnBubble extends StatelessWidget {
  const _TurnBubble({required this.turn, required this.onSuggestion});

  final ChatTurn turn;
  final ValueChanged<String> onSuggestion;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);

    if (turn.isPending) {
      return const Align(
        alignment: Alignment.centerLeft,
        child: Padding(
          padding: EdgeInsets.symmetric(vertical: 12, horizontal: 8),
          child: SizedBox(
            width: 22,
            height: 22,
            child: CircularProgressIndicator(strokeWidth: 2),
          ),
        ),
      );
    }

    if (turn.isUser) {
      return Align(
        alignment: Alignment.centerRight,
        child: Container(
          margin: const EdgeInsets.only(bottom: 10, left: 48),
          padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
          decoration: BoxDecoration(
            color: theme.colorScheme.primaryContainer,
            borderRadius: BorderRadius.circular(14),
          ),
          child: Text(turn.text),
        ),
      );
    }

    return Align(
      alignment: Alignment.centerLeft,
      child: Container(
        margin: const EdgeInsets.only(bottom: 14, right: 32),
        padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 12),
        decoration: BoxDecoration(
          color: turn.isError
              ? AppColors.danger.withValues(alpha: 0.10)
              : theme.colorScheme.surfaceContainerHighest,
          borderRadius: BorderRadius.circular(14),
        ),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              turn.text,
              style: TextStyle(color: turn.isError ? AppColors.danger : null),
            ),
            if (turn.reply?.hasFacts ?? false) ...[
              const SizedBox(height: 10),
              _FactList(reply: turn.reply!),
            ],
            if (turn.reply != null &&
                turn.reply!.intent == ChatIntent.unknown &&
                turn.reply!.suggestions.isNotEmpty) ...[
              const SizedBox(height: 10),
              Wrap(
                spacing: 6,
                runSpacing: 6,
                children: [
                  for (final suggestion in turn.reply!.suggestions)
                    ActionChip(
                      label: Text(
                        suggestion,
                        style: const TextStyle(fontSize: 12),
                      ),
                      onPressed: () => onSuggestion(suggestion),
                    ),
                ],
              ),
            ],
            if (turn.reply?.intentSource == 'keywords' && !turn.isError) ...[
              const SizedBox(height: 8),
              const Text(
                'Matched by keywords',
                style: TextStyle(fontSize: 11, color: AppColors.textSecondary),
              ),
            ],
          ],
        ),
      ),
    );
  }
}

/// The structured half of an answer.
///
/// Rendered per intent because the server returns the fields each lookup
/// actually has. A single generic shape would mean either dropping fields or
/// padding every row with blanks.
class _FactList extends StatelessWidget {
  const _FactList({required this.reply});

  final ChatReply reply;

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        for (final fact in reply.facts.take(10))
          Padding(
            padding: const EdgeInsets.only(bottom: 4),
            child: Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                const Text('• ', style: TextStyle(height: 1.3)),
                Expanded(child: Text(_describe(reply.intent, fact))),
              ],
            ),
          ),
        if (reply.facts.length > 10)
          Padding(
            padding: const EdgeInsets.only(top: 4),
            child: Text(
              '…and ${reply.facts.length - 10} more.',
              style: const TextStyle(
                fontSize: 12,
                color: AppColors.textSecondary,
              ),
            ),
          ),
      ],
    );
  }

  String _describe(ChatIntent intent, Map<String, dynamic> fact) {
    String value(String key) => (fact[key] ?? '').toString();

    switch (intent) {
      case ChatIntent.schedule:
        final who = value('worker_name');
        return '${value('date')} at ${value('start_time')}'
            '${who.isEmpty ? '' : ' — $who'}';
      case ChatIntent.payments:
        return '${value('date')} · ${value('amount')} · ${value('kind')} '
            '(${value('receipt_number')})';
      case ChatIntent.bookings:
        return '${value('date')} at ${value('start_time')} — '
            '${value('category')} (${value('status')})';
      case ChatIntent.availability:
        final open = fact['available'] == true ? 'open' : 'blocked';
        final note = value('note');
        return '${value('date')}: $open${note.isEmpty ? '' : ' — $note'}';
      case ChatIntent.complaints:
        return '${value('reference')} — ${value('subject')} '
            '(${value('status')})';
      case ChatIntent.help:
      case ChatIntent.unknown:
        // No lookup produces facts for these, so this is unreachable in
        // practice. Rendering the raw pairs rather than dropping them means a
        // future intent shows *something* instead of silently nothing.
        return fact.entries
            .map((entry) => '${entry.key}: ${entry.value}')
            .join(', ');
    }
  }
}

class _PrivacyNote extends StatelessWidget {
  const _PrivacyNote();

  @override
  Widget build(BuildContext context) {
    return const Padding(
      padding: EdgeInsets.symmetric(horizontal: 16, vertical: 4),
      child: Row(
        children: [
          Icon(Icons.lock_outline, size: 13, color: AppColors.textSecondary),
          SizedBox(width: 6),
          Expanded(
            child: Text(
              'Only your own records. Questions are not saved.',
              style: TextStyle(fontSize: 11, color: AppColors.textSecondary),
            ),
          ),
        ],
      ),
    );
  }
}

class _Composer extends StatelessWidget {
  const _Composer({required this.controller, required this.onSend});

  final TextEditingController controller;
  final ValueChanged<String> onSend;

  @override
  Widget build(BuildContext context) {
    return SafeArea(
      top: false,
      child: Padding(
        padding: const EdgeInsets.fromLTRB(12, 4, 12, 12),
        child: Row(
          children: [
            Expanded(
              child: TextField(
                controller: controller,
                textInputAction: TextInputAction.send,
                onSubmitted: onSend,
                // Matches the server's cap. A longer "question" is a paste
                // accident, and refusing it here saves a round trip.
                maxLength: 500,
                decoration: const InputDecoration(
                  hintText: 'Ask a question',
                  counterText: '',
                ),
              ),
            ),
            const SizedBox(width: 8),
            IconButton.filled(
              onPressed: () => onSend(controller.text),
              icon: const Icon(Icons.send),
            ),
          ],
        ),
      ),
    );
  }
}
