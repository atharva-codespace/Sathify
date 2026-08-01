"""Module 4 — Discovery & Hiring: serializers.

The two worker-facing serializers here are read-only projections of
``workers.WorkerProfile``. Module 3 owns that model and will own its own
write serializers; this module only ever reads it, which is why these live here
rather than being imported from ``apps.workers``.
"""

from __future__ import annotations

from rest_framework import serializers

from apps.workers.models import WorkerProfile

# ServiceType is Module 3's model, so its projection lives with it. Imported
# rather than redefined: two shapes for one row is how a Dart client ends up
# with two incompatible enums for the same concept.
from apps.workers.serializers import ServiceTypeSerializer

from .models import (
    Engagement,
    EngagementEndReason,
    HireRequest,
    validate_days_of_week,
)
from .services import has_live_engagement, worker_verification


class _MatchScoreMixin(serializers.Serializer):
    """Exposes the match score the view attached to each worker.

    The view computes scores in bulk (one pass over an annotated queryset) and
    stashes the result on the instance as ``_match_score``. Recomputing it inside
    the serializer would re-query per row, which is exactly the N+1 that
    ``annotate_hiring_stats`` exists to prevent.
    """

    match_percentage = serializers.SerializerMethodField()

    def get_match_percentage(self, obj) -> int | None:
        match = getattr(obj, "_match_score", None)
        return match.percentage if match else None


class _WorkerScoreFields(serializers.Serializer):
    """Emits the two score fields as JSON numbers rather than strings.

    ``trust_score`` and ``average_rating`` are model ``DecimalField``s, and DRF
    renders those as strings by default (COERCE_DECIMAL_TO_STRING) — a sensible
    default for money, wrong for scores. Left alone, the Dart client's numeric
    parse silently yields null and every worker renders as unrated.

    Fixed here rather than by flipping the global setting, because Module 8's
    payment amounts genuinely should stay strings: float is the wrong type for
    currency, and the setting is all-or-nothing.
    """

    trust_score = serializers.FloatField(read_only=True)
    average_rating = serializers.FloatField(read_only=True)


class WorkerSearchResultSerializer(
    _MatchScoreMixin, _WorkerScoreFields, serializers.ModelSerializer
):
    """Module 4.1 — one row in the search results."""

    full_name = serializers.CharField(source="user.get_full_name", read_only=True)
    service_types = ServiceTypeSerializer(many=True, read_only=True)
    engagement_count = serializers.IntegerField(read_only=True, default=0)

    class Meta:
        model = WorkerProfile
        fields = [
            "id",
            "full_name",
            "photo",
            "service_types",
            "years_of_experience",
            "languages_spoken",
            "expected_monthly_rate",
            "available_from",
            "available_until",
            "average_rating",
            "trust_score",
            "completed_engagements",
            "engagement_count",
            "match_percentage",
        ]
        read_only_fields = fields


class WorkerDetailSerializer(
    _MatchScoreMixin, _WorkerScoreFields, serializers.ModelSerializer
):
    """Module 4.2 — the full profile a resident reads before deciding.

    Carries the match *breakdown* as well as the headline percentage: the point
    of this screen is to give the resident real signal, and a bare "92%" with no
    account of where it came from is not signal.
    """

    full_name = serializers.CharField(source="user.get_full_name", read_only=True)
    service_types = ServiceTypeSerializer(many=True, read_only=True)
    verification = serializers.SerializerMethodField()
    engagement_count = serializers.IntegerField(read_only=True, default=0)
    response_rate = serializers.SerializerMethodField()
    match_breakdown = serializers.SerializerMethodField()

    class Meta:
        model = WorkerProfile
        fields = [
            "id",
            "full_name",
            "photo",
            "bio",
            "service_types",
            "years_of_experience",
            "languages_spoken",
            "expected_monthly_rate",
            "is_available",
            "available_from",
            "available_until",
            "average_rating",
            "trust_score",
            "completed_engagements",
            "engagement_count",
            "response_rate",
            "verification",
            "match_percentage",
            "match_breakdown",
        ]
        read_only_fields = fields

    def get_verification(self, obj) -> dict:
        return worker_verification(obj)

    def get_response_rate(self, obj) -> float | None:
        """Observed share of requests answered, or ``None`` with no history.

        Deliberately the raw rate, not the smoothed one used in ranking: telling
        a resident a worker "answers 80% of requests" when they have never
        received one would be presenting a prior as a fact.
        """
        answered = getattr(obj, "answered_requests", 0) or 0
        ignored = getattr(obj, "ignored_requests", 0) or 0
        total = answered + ignored
        return round(answered / total, 4) if total else None

    def get_match_breakdown(self, obj) -> list | None:
        match = getattr(obj, "_match_score", None)
        return match.explain() if match else None


# ---------------------------------------------------------------------------
# 4.4 Hire requests
# ---------------------------------------------------------------------------


class _RecurringTermsFields(serializers.Serializer):
    """The proposed/agreed terms, shared by the request and engagement payloads."""

    days_of_week = serializers.ListField(
        child=serializers.IntegerField(min_value=0, max_value=6),
        allow_empty=False,
    )
    start_time = serializers.TimeField()
    expected_duration_minutes = serializers.IntegerField(min_value=15, max_value=720)
    monthly_rate = serializers.IntegerField(min_value=1)

    def validate_days_of_week(self, value):
        # Reuse the model validator so the API and the database agree on what a
        # valid schedule is, rather than enforcing two similar-looking rules.
        validate_days_of_week(value)
        return sorted(set(value))


class HireRequestSerializer(serializers.ModelSerializer):
    """Read projection of a hire request, for both sides of it."""

    worker_name = serializers.CharField(source="worker.user.get_full_name", read_only=True)
    worker_photo = serializers.ImageField(source="worker.photo", read_only=True)
    resident_name = serializers.CharField(source="resident.user.get_full_name", read_only=True)
    resident_flat = serializers.CharField(source="resident.flat.__str__", read_only=True)
    service_type = ServiceTypeSerializer(read_only=True)
    day_labels = serializers.ListField(child=serializers.CharField(), read_only=True)

    # Reports the deadline-aware status, so a lapsed row still reads as
    # "expired" even if the sweep has not run since it lapsed.
    status = serializers.CharField(source="effective_status", read_only=True)
    is_actionable = serializers.BooleanField(read_only=True)
    engagement_id = serializers.IntegerField(source="engagement.id", read_only=True, default=None)

    class Meta:
        model = HireRequest
        fields = [
            "id",
            "worker",
            "worker_name",
            "worker_photo",
            "resident",
            "resident_name",
            "resident_flat",
            "service_type",
            "days_of_week",
            "day_labels",
            "start_time",
            "expected_duration_minutes",
            "monthly_rate",
            "message",
            "status",
            "is_actionable",
            "expires_at",
            "responded_at",
            "response_note",
            "engagement_id",
            "created_at",
        ]
        read_only_fields = fields


class HireRequestCreateSerializer(_RecurringTermsFields, serializers.ModelSerializer):
    """Module 4.4 — a resident proposes an engagement.

    Every cross-entity rule is checked here rather than in the view, so that the
    same guarantees hold if a request is ever created from the AI chatbot
    (Module 12.2) instead of the search screen.
    """

    class Meta:
        model = HireRequest
        fields = [
            "worker",
            "service_type",
            "days_of_week",
            "start_time",
            "expected_duration_minutes",
            "monthly_rate",
            "message",
        ]

    def validate(self, attrs):
        request = self.context["request"]
        resident = self.context["resident"]
        worker = attrs["worker"]
        service_type = attrs["service_type"]

        # Society isolation. The worker id arrives from the client, so it is not
        # enough that search only ever showed same-society workers.
        if worker.user.society_id != request.user.society_id:
            raise serializers.ValidationError(
                {"worker": "That worker belongs to another society."}
            )

        if not worker.is_searchable:
            raise serializers.ValidationError(
                {
                    "worker": "This worker is not currently accepting hires — they are "
                    "unavailable or their verification is incomplete."
                }
            )

        if not worker.service_types.filter(pk=service_type.pk).exists():
            raise serializers.ValidationError(
                {"service_type": "This worker does not offer that service."}
            )

        if HireRequest.objects.pending().filter(
            resident=resident, worker=worker, service_type=service_type
        ).exists():
            raise serializers.ValidationError(
                {"worker": "You already have a pending request with this worker."}
            )

        if has_live_engagement(resident.pk, worker.pk, service_type.pk):
            raise serializers.ValidationError(
                {"worker": "You already have a running engagement with this worker."}
            )

        return attrs

    def create(self, validated_data):
        resident = self.context["resident"]
        return HireRequest.objects.create(
            resident=resident,
            society_id=self.context["request"].user.society_id,
            **validated_data,
        )


class HireRequestRespondSerializer(serializers.Serializer):
    """The worker's answer (Module 4.4)."""

    accept = serializers.BooleanField()
    note = serializers.CharField(
        required=False, allow_blank=True, max_length=500, default=""
    )


class HireRequestWithdrawSerializer(serializers.Serializer):
    """Resident retracts a request they no longer want answered."""

    reason = serializers.CharField(
        required=False, allow_blank=True, max_length=500, default=""
    )


# ---------------------------------------------------------------------------
# 4.5 Engagements
# ---------------------------------------------------------------------------


class EngagementSerializer(serializers.ModelSerializer):
    worker_name = serializers.CharField(source="worker.user.get_full_name", read_only=True)
    worker_photo = serializers.ImageField(source="worker.photo", read_only=True)
    worker_phone = serializers.CharField(source="worker.user.phone_number", read_only=True)
    resident_name = serializers.CharField(source="resident.user.get_full_name", read_only=True)
    resident_flat = serializers.CharField(source="resident.flat.__str__", read_only=True)
    service_type = ServiceTypeSerializer(read_only=True)
    day_labels = serializers.ListField(child=serializers.CharField(), read_only=True)
    end_time = serializers.TimeField(read_only=True)

    class Meta:
        model = Engagement
        fields = [
            "id",
            "worker",
            "worker_name",
            "worker_photo",
            "worker_phone",
            "resident",
            "resident_name",
            "resident_flat",
            "service_type",
            "days_of_week",
            "day_labels",
            "start_time",
            "end_time",
            "expected_duration_minutes",
            "monthly_rate",
            "status",
            "started_on",
            "paused_at",
            "pause_reason",
            "resumed_at",
            "ended_at",
            "end_reason",
            "end_note",
            "hire_request",
            "created_at",
        ]
        read_only_fields = fields


class EngagementTransitionSerializer(serializers.Serializer):
    """Module 4.5 — pause, resume, or terminate.

    One endpoint taking an action rather than a PATCH on ``status``, because
    these are transitions with their own rules and side effects, not a freely
    settable field. A PATCH would invite the client to jump straight from
    terminated back to active.
    """

    ACTIONS = ["pause", "resume", "terminate"]

    action = serializers.ChoiceField(choices=ACTIONS)
    reason = serializers.ChoiceField(
        choices=EngagementEndReason.choices, required=False, allow_blank=True
    )
    note = serializers.CharField(
        required=False, allow_blank=True, max_length=500, default=""
    )

    def validate(self, attrs):
        if attrs["action"] == "terminate" and not attrs.get("reason"):
            raise serializers.ValidationError(
                {"reason": "A reason is required when terminating an engagement."}
            )
        return attrs
