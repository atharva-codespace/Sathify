"""Module 12 — AI Layer: serializers."""

from __future__ import annotations

from rest_framework import serializers


class ChatRequestSerializer(serializers.Serializer):
    """Module 12.2 — one question.

    Length-capped well below any provider's limit. A 4,000-character "question"
    is either a paste accident or somebody probing the prompt, and neither is
    worth a provider call from a metered free tier.
    """

    question = serializers.CharField(max_length=500)


class ChatAnswerSerializer(serializers.Serializer):
    intent = serializers.CharField(read_only=True)
    text = serializers.CharField(read_only=True)

    #: The same answer, structured, so the app renders rows rather than
    #: re-parsing the sentence.
    facts = serializers.ListField(read_only=True)

    #: "ai" or "keywords" — where the *intent* came from. Never where the data
    #: came from: the data is always the database.
    intent_source = serializers.CharField(read_only=True)
    suggestions = serializers.ListField(
        child=serializers.CharField(), read_only=True
    )


class ReviewSummarySerializer(serializers.Serializer):
    """Module 12.5 — a worker's reviews, condensed."""

    headline = serializers.CharField(read_only=True)
    strengths = serializers.ListField(child=serializers.CharField(), read_only=True)
    concerns = serializers.ListField(child=serializers.CharField(), read_only=True)
    review_count = serializers.IntegerField(read_only=True)

    #: Which engine produced this — a provider name, or "fallback". Exposed so
    #: the app can label a keyword-derived summary as what it is rather than
    #: letting a resident read it as an assessment.
    engine = serializers.CharField(read_only=True)
    is_ai = serializers.BooleanField(read_only=True)


class ComplaintClassificationSerializer(serializers.Serializer):
    """Module 12.5 — a suggested category for free text."""

    category = serializers.CharField(read_only=True)
    confidence = serializers.FloatField(read_only=True)
    rationale = serializers.CharField(read_only=True)
    is_confident = serializers.BooleanField(read_only=True)
    engine = serializers.CharField(read_only=True)


class ClassifyRequestSerializer(serializers.Serializer):
    subject = serializers.CharField(max_length=150, required=False, allow_blank=True)
    description = serializers.CharField(max_length=2000)


class AiStatusSerializer(serializers.Serializer):
    """What this deployment can actually do.

    Read by the app on startup so a screen can hide a chat button rather than
    offering one that always answers "no provider configured". Deliberately
    reports capability, never keys.
    """

    enabled = serializers.BooleanField(read_only=True)
    providers_configured = serializers.ListField(
        child=serializers.CharField(), read_only=True
    )
    chat_available = serializers.BooleanField(read_only=True)
    face_available = serializers.BooleanField(read_only=True)
    ocr_available = serializers.BooleanField(read_only=True)
    recommendation_engine = serializers.CharField(read_only=True)
