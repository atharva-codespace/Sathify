"""Module 9 — Ratings, Reviews & Trust Score: serializers."""

from __future__ import annotations

from rest_framework import serializers

from .models import (
    MAX_STARS,
    MIN_STARS,
    Rating,
    RatingDirection,
    ReviewFlag,
    ReviewSentiment,
    TrustScoreLog,
)


class ReviewSentimentSerializer(serializers.ModelSerializer):
    """Module 9.2 — a model's reading of a review.

    ``is_reliable`` is exposed so the client can withhold a low-confidence
    verdict rather than presenting a keyword guess as a finding. The built-in
    lexicon is a stopgap until Module 12, and it says so through this field.
    """

    is_reliable = serializers.BooleanField(read_only=True)

    class Meta:
        model = ReviewSentiment
        fields = [
            "label",
            "polarity",
            "confidence",
            "themes",
            "detected_language",
            "engine",
            "is_reliable",
        ]
        read_only_fields = fields


class RatingSerializer(serializers.ModelSerializer):
    """One rating, as shown on a profile."""

    rater_name = serializers.CharField(source="rater.get_full_name", read_only=True)
    worker_name = serializers.CharField(source="worker.user.get_full_name", read_only=True)
    resident_name = serializers.CharField(
        source="resident.user.get_full_name", read_only=True
    )
    direction_display = serializers.CharField(
        source="get_direction_display", read_only=True
    )
    sentiment = ReviewSentimentSerializer(read_only=True)
    subject_is_worker = serializers.BooleanField(read_only=True)

    class Meta:
        model = Rating
        fields = [
            "id",
            "direction",
            "direction_display",
            "subject_is_worker",
            "stars",
            "review",
            "rater",
            "rater_name",
            "worker",
            "worker_name",
            "resident",
            "resident_name",
            "engagement",
            "booking",
            "sentiment",
            # Exposed so a rater can see their own review is under review, and
            # an administrator can see why. Not hidden — a withheld rating that
            # silently vanished would be indistinguishable from a bug.
            "is_flagged",
            "is_withheld",
            "created_at",
        ]
        read_only_fields = fields


class SubmitRatingSerializer(serializers.Serializer):
    """Module 9.1 — one side rates a completed job.

    Exactly one of ``engagement`` or ``booking``: a rating attaches to one piece
    of work, and the uniqueness rule that prevents review spam is keyed on it.
    """

    stars = serializers.IntegerField(min_value=MIN_STARS, max_value=MAX_STARS)
    review = serializers.CharField(
        required=False, allow_blank=True, default="", max_length=1000
    )
    engagement = serializers.IntegerField(required=False, allow_null=True)
    booking = serializers.IntegerField(required=False, allow_null=True)

    def validate(self, attrs):
        has_engagement = bool(attrs.get("engagement"))
        has_booking = bool(attrs.get("booking"))

        if has_engagement == has_booking:
            raise serializers.ValidationError(
                "Rate either an engagement or a booking, not both and not neither."
            )
        return attrs


class RateableJobSerializer(serializers.Serializer):
    """A completed job still awaiting this user's rating."""

    kind = serializers.CharField(read_only=True)
    id = serializers.IntegerField(read_only=True)
    title = serializers.CharField(read_only=True)
    counterparty_name = serializers.CharField(read_only=True)
    flat_label = serializers.CharField(read_only=True)
    finished_on = serializers.DateField(read_only=True, allow_null=True)


class TrustComponentSerializer(serializers.Serializer):
    """One weighted input behind a trust score."""

    key = serializers.CharField(read_only=True)
    label = serializers.CharField(read_only=True)
    weight = serializers.FloatField(read_only=True)
    score = serializers.FloatField(read_only=True)
    contribution = serializers.FloatField(read_only=True)

    #: A sentence a person can read. "attendance: 0.72" explains nothing to a
    #: worker asking why their score fell.
    detail = serializers.CharField(read_only=True)


class TrustScoreSerializer(serializers.Serializer):
    """Module 9.3 — a score with the breakdown that justifies it.

    The breakdown is not optional decoration. The modspec makes explainability
    the key requirement, because a score nobody can justify gets disputed — and
    this score decides whether someone gets hired.
    """

    subject_type = serializers.CharField(read_only=True)
    subject_id = serializers.IntegerField(read_only=True)
    subject_name = serializers.CharField(read_only=True)
    score = serializers.FloatField(read_only=True)
    average_rating = serializers.FloatField(read_only=True)
    rating_count = serializers.IntegerField(read_only=True)
    components = TrustComponentSerializer(many=True, read_only=True)

    #: What is costing the most, so "how do I improve this?" has an answer.
    weakest = TrustComponentSerializer(read_only=True, allow_null=True)


class TrustScoreLogSerializer(serializers.ModelSerializer):
    """The audit trail. Read-only everywhere — it is the record, not a worksheet."""

    delta = serializers.DecimalField(max_digits=6, decimal_places=2, read_only=True)

    class Meta:
        model = TrustScoreLog
        fields = [
            "id",
            "subject_type",
            "worker",
            "resident",
            "previous_score",
            "new_score",
            "delta",
            "components",
            "trigger",
            "created_at",
        ]
        read_only_fields = fields


class ReviewFlagSerializer(serializers.ModelSerializer):
    """Module 9.4 — a suspicious rating awaiting an administrator."""

    reason_display = serializers.CharField(source="get_reason_display", read_only=True)
    rating_detail = RatingSerializer(source="rating", read_only=True)
    is_open = serializers.BooleanField(read_only=True)

    class Meta:
        model = ReviewFlag
        fields = [
            "id",
            "rating",
            "rating_detail",
            "reason",
            "reason_display",
            "detail",
            "status",
            "is_open",
            "review_note",
            "reviewed_at",
            "created_at",
        ]
        read_only_fields = fields


class ResolveFlagSerializer(serializers.Serializer):
    """An administrator decides whether a flagged rating counts.

    ``upheld=True`` keeps it withheld; ``False`` restores it to scoring. A note
    is required either way — these are heuristics with innocent explanations,
    and the decision to suppress somebody's rating should be answerable for.
    """

    upheld = serializers.BooleanField()
    note = serializers.CharField(max_length=300)

    def validate_note(self, value):
        if not value.strip():
            raise serializers.ValidationError("Say why you decided this.")
        return value.strip()
