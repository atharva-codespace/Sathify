"""
Module 12 — AI Layer: API views.

Endpoint map (mounted at /api/v1/ai/)::

    GET  status/                     what this deployment can do
    POST chat/                       ask a question                       (12.2)
    GET  reviews/<worker_id>/summary/  condensed reviews                  (12.5)
    POST complaints/classify/        suggest a category                   (12.5)

-------------------------------------------------------------------------------
THERE IS NO GENERIC "ASK THE MODEL" ENDPOINT
-------------------------------------------------------------------------------
Every route here is a specific task with a validated shape. Exposing a raw
prompt endpoint would hand an authenticated user this project's metered free-tier
quota to spend on anything they liked, and would make the "the model never
composes an answer from data" property in :mod:`apps.ai_services.chatbot`
impossible to hold.
"""

from __future__ import annotations

import logging

from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.permissions import IsApprovedUser

from . import analysis, chatbot, face_service, ocr_service, providers, recommendation
from .serializers import (
    AiStatusSerializer,
    ChatAnswerSerializer,
    ChatRequestSerializer,
    ClassifyRequestSerializer,
    ComplaintClassificationSerializer,
    ReviewSummarySerializer,
)

logger = logging.getLogger(__name__)

#: How many reviews one summary request will read from the database.
SUMMARY_SAMPLE = 40


def _error(code: str, message: str, http_status: int, details: dict | None = None):
    """The platform-standard error envelope (apps/core/exceptions.py)."""
    return Response(
        {"error": {"code": code, "message": message, "details": details or {}}},
        status=http_status,
    )


@extend_schema(
    tags=["AI"], summary="What this deployment can do", responses=AiStatusSerializer
)
class AiStatusView(APIView):
    """Capability probe.

    Reports which features are usable, never which keys are set. The app reads
    this to decide whether to show a chat button at all, rather than offering
    one that always answers "no provider is configured".
    """

    permission_classes = [IsAuthenticated]
    serializer_class = AiStatusSerializer

    def get(self, request):
        configured = [tier.name for tier in providers.tiers() if tier.is_configured]
        enabled = providers.is_enabled()

        return Response(
            {
                "enabled": enabled,
                "providers_configured": configured,
                # Chat works with no provider at all — the keyword intent pass
                # and the database lookups behind it need nothing external.
                "chat_available": True,
                "face_available": face_service.is_available(),
                "ocr_available": ocr_service.is_available(),
                "recommendation_engine": recommendation.engine_name(),
            }
        )


@extend_schema(
    tags=["AI"],
    summary="Ask a question",
    request=ChatRequestSerializer,
    responses=ChatAnswerSerializer,
)
class ChatView(APIView):
    """Module 12.2.

    Answers only about the caller's own records. Every figure in the reply is
    read from the database — the model, when one is available, is used solely to
    work out which lookup to run.
    """

    permission_classes = [IsApprovedUser]
    serializer_class = ChatRequestSerializer

    def post(self, request):
        serializer = ChatRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        reply = chatbot.answer(request.user, serializer.validated_data["question"])

        return Response(
            {
                "intent": reply.intent,
                "text": reply.text,
                "facts": reply.facts,
                "intent_source": reply.intent_source,
                "suggestions": reply.suggestions,
            }
        )


@extend_schema(
    tags=["AI"],
    summary="Summarise a worker's reviews",
    responses=ReviewSummarySerializer,
)
class ReviewSummaryView(APIView):
    """Module 12.5.

    Reads the same visible-reviews queryset Module 9 exposes, so a review
    withheld pending an administrator's decision is absent from the summary as
    well as from the score. Summarising a withheld review would leak exactly the
    thing withholding it was meant to suppress.
    """

    permission_classes = [IsApprovedUser]
    serializer_class = ReviewSummarySerializer

    def get(self, request, worker_id):
        from apps.ratings.models import Rating
        from apps.workers.models import WorkerProfile

        worker = WorkerProfile.objects.filter(
            pk=worker_id, user__society_id=request.user.society_id
        ).select_related("user").first()
        if worker is None:
            return _error("not_found", "Worker not found.", status.HTTP_404_NOT_FOUND)

        texts = list(
            Rating.objects.visible()
            .of_worker(worker)
            .exclude(review="")
            .order_by("-created_at")
            .values_list("review", flat=True)[:SUMMARY_SAMPLE]
        )

        summary = analysis.summarise_reviews(
            texts, worker_name=worker.user.get_full_name(), user=request.user
        )

        return Response(
            {
                "headline": summary.value.headline,
                "strengths": summary.value.strengths,
                "concerns": summary.value.concerns,
                "review_count": summary.value.review_count,
                "engine": summary.engine,
                "is_ai": summary.from_ai,
            }
        )


@extend_schema(
    tags=["AI"],
    summary="Suggest a category for a complaint",
    request=ClassifyRequestSerializer,
    responses=ComplaintClassificationSerializer,
)
class ClassifyComplaintView(APIView):
    """Module 12.5, feeding Module 11.3.

    A suggestion the app may prefill. Module 11 keeps whatever category the
    person actually chose — they know what their complaint is about better than
    a model does, and overwriting that would be both wrong and infuriating.
    """

    permission_classes = [IsApprovedUser]
    serializer_class = ClassifyRequestSerializer

    def post(self, request):
        serializer = ClassifyRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        result = analysis.classify_complaint(
            data.get("subject", ""), data["description"], user=request.user
        )

        return Response(
            {
                "category": result.value.category,
                "confidence": result.value.confidence,
                "rationale": result.value.rationale,
                "is_confident": result.value.is_confident,
                "engine": result.engine,
            }
        )
