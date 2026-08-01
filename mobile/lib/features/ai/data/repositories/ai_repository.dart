import '../../../../core/config/api_endpoints.dart';
import '../../../../core/network/api_client.dart';
import '../models/ai_models.dart';

/// All Module 12 endpoints.
///
/// There is deliberately no `prompt()` method. The server exposes no generic
/// "ask the model" route, because that would hand an authenticated user this
/// project's metered free-tier quota to spend on anything they liked.
class AiRepository {
  AiRepository({ApiClient? client}) : _client = client ?? ApiClient();

  final ApiClient _client;

  Future<AiStatus> fetchStatus() async {
    final response =
        await _client.get(ApiEndpoints.aiStatus) as Map<String, dynamic>;
    return AiStatus.fromJson(response);
  }

  /// 12.2 — ask one question.
  ///
  /// Stateless on purpose: no conversation id, no history sent. The server
  /// classifies each question on its own and answers from the database, so
  /// there is nothing for context to add — and a transcript is a second copy of
  /// data the platform is otherwise careful about.
  Future<ChatReply> ask(String question) async {
    final response = await _client.post(
      ApiEndpoints.aiChat,
      data: {'question': question.trim()},
    ) as Map<String, dynamic>;

    return ChatReply.fromJson(response);
  }

  /// 12.5 — a worker's reviews, condensed. Withheld reviews are excluded
  /// server-side, so a review suppressed pending an administrator's decision
  /// cannot leak through the summary.
  Future<ReviewSummary> fetchReviewSummary(int workerId) async {
    final response = await _client.get(ApiEndpoints.reviewSummary(workerId))
        as Map<String, dynamic>;
    return ReviewSummary.fromJson(response);
  }

  /// 12.5 — what category this text looks like.
  ///
  /// A suggestion. The complaint is filed with whatever the person chose.
  Future<ComplaintSuggestion> classifyComplaint({
    required String description,
    String subject = '',
  }) async {
    final response = await _client.post(
      ApiEndpoints.classifyComplaint,
      data: {
        if (subject.trim().isNotEmpty) 'subject': subject.trim(),
        'description': description.trim(),
      },
    ) as Map<String, dynamic>;

    return ComplaintSuggestion.fromJson(response);
  }
}
