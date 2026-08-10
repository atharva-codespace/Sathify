"""
Module 9 — Ratings, Reviews & Trust Score: API views.

Endpoint map (mounted at /api/v1/ratings/)::

    GET  pending/                what the caller can still rate          (9.1)
    POST ./                      submit a rating                          (9.1)
    GET  ./                      ratings the caller can see
    GET  workers/<id>/           a worker's public ratings

    GET  trust/me/               own trust score + breakdown              (9.3)
    GET  trust/workers/<id>/     a worker's score + breakdown
    GET  trust/history/          the audit trail                          (9.3)

    GET  flags/                  flagged ratings (admin)                  (9.4)
    POST flags/<id>/resolve/     uphold or dismiss                        (9.4)

-------------------------------------------------------------------------------
A TRUST SCORE IS ALWAYS RETURNED WITH ITS BREAKDOWN
-------------------------------------------------------------------------------
There is deliberately no endpoint that returns the bare number. The modspec
makes explainability the key requirement, and an API that made the explanation
optional would guarantee some screen eventually showed the number without it.
"""

from __future__ import annotations

import logging

from drf_spectacular.utils import extend_schema
from rest_framework import generics, status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import Role
from apps.accounts.permissions import IsApprovedSocietyAdmin, IsEngagementParty
from apps.societies.models import Resident
from apps.workers.models import WorkerProfile

from .models import (
    FlagStatus,
    Rating,
    RatingDirection,
    ReviewFlag,
    TrustScoreLog,
)
from .serializers import (
    RateableJobSerializer,
    RatingSerializer,
    ResolveFlagSerializer,
    ReviewFlagSerializer,
    SubmitRatingSerializer,
    TrustScoreLogSerializer,
    TrustScoreSerializer,
)
from .services import (
    AlreadyRated,
    NotRateable,
    RatingError,
    rateable_bookings,
    rateable_engagements,
    resolve_flag,
    submit_rating,
)
from .trust import TrustScore, resident_trust, worker_trust
from .services import resident_trust_inputs, worker_trust_inputs

logger = logging.getLogger(__name__)


def _error(code: str, message: str, http_status: int, details: dict | None = None):
    """The platform-standard error envelope (apps/core/exceptions.py)."""
    return Response(
        {"error": {"code": code, "message": message, "details": details or {}}},
        status=http_status,
    )


def _direction_for(user) -> str:
    """Which way a rating runs, from who is submitting it."""
    return (
        RatingDirection.RESIDENT_TO_WORKER
        if user.role == Role.RESIDENT
        else RatingDirection.WORKER_TO_RESIDENT
    )


def _trust_payload(*, subject_type, subject_id, subject_name, score: TrustScore,
                   average_rating, rating_count) -> dict:
    weakest = score.weakest()
    return {
        "subject_type": subject_type,
        "subject_id": subject_id,
        "subject_name": subject_name,
        "score": score.value,
        "average_rating": float(average_rating or 0),
        "rating_count": rating_count or 0,
        "components": score.explain(),
        "weakest": weakest.as_dict() if weakest else None,
    }


# ---------------------------------------------------------------------------
# 9.1 Rating
# ---------------------------------------------------------------------------


@extend_schema(
    tags=["Ratings"],
    summary="Jobs you can still rate",
    responses=RateableJobSerializer(many=True),
)
class PendingRatingsView(APIView):
    """Module 9.1 — completed work awaiting this user's verdict.

    Driven from what is actually rateable rather than from a notification, so a
    user who dismissed a prompt can still find the job later.
    """

    permission_classes = [IsEngagementParty]
    serializer_class = RateableJobSerializer

    def get(self, request):
        direction = _direction_for(request.user)
        is_resident = request.user.role == Role.RESIDENT

        jobs = []
        for engagement in rateable_engagements(request.user, direction=direction):
            jobs.append(
                {
                    "kind": "engagement",
                    "id": engagement.pk,
                    "title": (
                        engagement.service_type.name
                        if engagement.service_type_id
                        else "Regular work"
                    ),
                    "counterparty_name": (
                        engagement.worker.user.get_full_name()
                        if is_resident
                        else engagement.resident.user.get_full_name()
                    ),
                    "flat_label": str(engagement.resident.flat),
                    "finished_on": (
                        engagement.ended_at.date() if engagement.ended_at else None
                    ),
                }
            )

        for booking in rateable_bookings(request.user, direction=direction):
            jobs.append(
                {
                    "kind": "booking",
                    "id": booking.pk,
                    "title": booking.category.name if booking.category_id else "Booking",
                    "counterparty_name": (
                        booking.worker.user.get_full_name()
                        if is_resident
                        else booking.resident.user.get_full_name()
                    ),
                    "flat_label": str(booking.resident.flat),
                    "finished_on": booking.scheduled_date,
                }
            )

        return Response({"count": len(jobs), "results": jobs})


@extend_schema(
    tags=["Ratings"],
    summary="Submit or list ratings",
    request=SubmitRatingSerializer,
    responses=RatingSerializer,
)
class RatingListCreateView(generics.ListCreateAPIView):
    """Module 9.1 — two-way rating after a completed job."""

    permission_classes = [IsEngagementParty | IsApprovedSocietyAdmin]
    queryset = Rating.objects.none()  # declared for schema generation

    def get_serializer_class(self):
        return (
            SubmitRatingSerializer
            if self.request.method == "POST"
            else RatingSerializer
        )

    def get_queryset(self):
        user = self.request.user
        queryset = Rating.objects.select_related(
            "rater", "worker__user", "resident__user", "sentiment"
        )

        if user.is_society_admin and user.society_id is not None:
            return queryset.filter(society_id=user.society_id)
        # A party sees ratings they gave and ratings about them — being rated
        # without being able to see it would defeat the point of a two-way
        # system.
        if user.role == Role.RESIDENT:
            return queryset.filter(resident__user=user)
        if user.role == Role.WORKER:
            return queryset.filter(worker__user=user)
        return queryset.none()

    def create(self, request, *args, **kwargs):
        serializer = SubmitRatingSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        direction = _direction_for(request.user)
        engagement = booking = None

        if data.get("engagement"):
            engagement = (
                rateable_engagements(request.user, direction=direction)
                .filter(pk=data["engagement"])
                .first()
            )
            if engagement is None:
                return _error(
                    "not_rateable",
                    "That job is not finished, is not yours, or you have already "
                    "rated it.",
                    status.HTTP_404_NOT_FOUND,
                )
        else:
            booking = (
                rateable_bookings(request.user, direction=direction)
                .filter(pk=data["booking"])
                .first()
            )
            if booking is None:
                return _error(
                    "not_rateable",
                    "That job is not finished, is not yours, or you have already "
                    "rated it.",
                    status.HTTP_404_NOT_FOUND,
                )

        try:
            rating = submit_rating(
                rater=request.user,
                direction=direction,
                stars=data["stars"],
                review=data.get("review", ""),
                engagement=engagement,
                booking=booking,
            )
        except AlreadyRated as exc:
            return _error(exc.code, str(exc), status.HTTP_409_CONFLICT)
        except NotRateable as exc:
            return _error(exc.code, str(exc), status.HTTP_400_BAD_REQUEST)
        except RatingError as exc:  # pragma: no cover — defensive
            return _error(exc.code, str(exc), status.HTTP_400_BAD_REQUEST)

        return Response(
            {
                "rating": RatingSerializer(rating).data,
                "message": (
                    "Thank you. This is under review before it counts."
                    if rating.is_flagged
                    else "Thank you for your rating."
                ),
            },
            status=status.HTTP_201_CREATED,
        )


@extend_schema(tags=["Ratings"], summary="A worker's ratings")
class WorkerRatingsView(generics.ListAPIView):
    """What a resident reads on a worker's profile before hiring.

    Withheld ratings are excluded: a rating under review has not been judged
    genuine, and showing it would let the flagging system be bypassed simply by
    it being visible.
    """

    serializer_class = RatingSerializer
    permission_classes = [IsEngagementParty | IsApprovedSocietyAdmin]
    queryset = Rating.objects.none()  # declared for schema generation

    def get_queryset(self):
        return (
            Rating.objects.visible()
            .filter(
                worker_id=self.kwargs["worker_id"],
                direction=RatingDirection.RESIDENT_TO_WORKER,
                worker__user__society_id=self.request.user.society_id,
            )
            .select_related("rater", "sentiment")
        )


# ---------------------------------------------------------------------------
# 9.3 Trust scores
# ---------------------------------------------------------------------------


@extend_schema(
    tags=["Trust"], summary="Your own trust score", responses=TrustScoreSerializer
)
class MyTrustScoreView(APIView):
    """Module 9.3 — the caller's own score, always with its breakdown.

    Computed live rather than read from the stored column, so somebody checking
    "why is my score low?" sees the current answer and its reasons together.
    """

    permission_classes = [IsEngagementParty]
    serializer_class = TrustScoreSerializer

    def get(self, request):
        if request.user.role == Role.WORKER:
            worker = WorkerProfile.objects.filter(user=request.user).first()
            if worker is None:
                return _error(
                    "not_found",
                    "Complete your worker profile first.",
                    status.HTTP_404_NOT_FOUND,
                )
            return Response(
                _trust_payload(
                    subject_type="worker",
                    subject_id=worker.pk,
                    subject_name=worker.user.get_full_name(),
                    score=worker_trust(worker_trust_inputs(worker)),
                    average_rating=worker.average_rating,
                    rating_count=worker.rating_count,
                )
            )

        resident = Resident.objects.filter(user=request.user).first()
        if resident is None:
            return _error(
                "not_found", "Claim your flat first.", status.HTTP_404_NOT_FOUND
            )
        return Response(
            _trust_payload(
                subject_type="resident",
                subject_id=resident.pk,
                subject_name=resident.user.get_full_name(),
                score=resident_trust(resident_trust_inputs(resident)),
                average_rating=resident.average_rating,
                rating_count=resident.rating_count,
            )
        )


@extend_schema(
    tags=["Trust"],
    summary="A worker's trust score and breakdown",
    responses=TrustScoreSerializer,
)
class WorkerTrustScoreView(APIView):
    """What a resident sees when they ask why a worker ranks where they do."""

    permission_classes = [IsEngagementParty | IsApprovedSocietyAdmin]
    serializer_class = TrustScoreSerializer

    def get(self, request, worker_id):
        worker = WorkerProfile.objects.filter(
            pk=worker_id, user__society_id=request.user.society_id
        ).select_related("user").first()
        if worker is None:
            return _error("not_found", "Worker not found.", status.HTTP_404_NOT_FOUND)

        return Response(
            _trust_payload(
                subject_type="worker",
                subject_id=worker.pk,
                subject_name=worker.user.get_full_name(),
                score=worker_trust(worker_trust_inputs(worker)),
                average_rating=worker.average_rating,
                rating_count=worker.rating_count,
            )
        )


@extend_schema(tags=["Trust"], summary="Trust score history")
class TrustHistoryView(generics.ListAPIView):
    """Module 9.3 — every change, with the breakdown as it was at the time.

    This is what answers a dispute months later. Recomputing an old score today
    would give today's answer against today's data, not the number that was
    acted on.
    """

    serializer_class = TrustScoreLogSerializer
    permission_classes = [IsEngagementParty | IsApprovedSocietyAdmin]
    queryset = TrustScoreLog.objects.none()  # declared for schema generation

    def get_queryset(self):
        user = self.request.user
        queryset = TrustScoreLog.objects.all()

        if user.is_society_admin and user.society_id is not None:
            return queryset.filter(society_id=user.society_id)
        if user.role == Role.WORKER:
            return queryset.filter(worker__user=user)
        if user.role == Role.RESIDENT:
            return queryset.filter(resident__user=user)
        return queryset.none()


# ---------------------------------------------------------------------------
# 9.4 Flagged reviews
# ---------------------------------------------------------------------------


@extend_schema(tags=["Ratings"], summary="Flagged ratings awaiting review")
class ReviewFlagListView(generics.ListAPIView):
    """Module 9.4 — the administrator's queue.

    Flags are raised by heuristics, every one of which has an innocent
    explanation, so a human decides. Nothing is deleted automatically.

    Approved administrators only. An administrator who registers a society is
    bound to it immediately while their own account stays unapproved and the
    society stays PENDING (societies/serializers.SocietyRegistrationSerializer),
    so the role check alone would open this queue — every flagged review in the
    society, in full, with both parties named — to somebody nobody has verified.
    """

    serializer_class = ReviewFlagSerializer
    permission_classes = [IsApprovedSocietyAdmin]
    queryset = ReviewFlag.objects.none()  # declared for schema generation

    def get_queryset(self):
        user = self.request.user
        if user.society_id is None:
            return ReviewFlag.objects.none()

        queryset = ReviewFlag.objects.filter(society_id=user.society_id).select_related(
            "rating__rater", "rating__worker__user", "rating__resident__user"
        )
        if self.request.query_params.get("status"):
            return queryset.filter(status=self.request.query_params["status"])
        return queryset.filter(status=FlagStatus.OPEN)


@extend_schema(
    tags=["Ratings"],
    summary="Uphold or dismiss a flagged rating",
    request=ResolveFlagSerializer,
    responses=ReviewFlagSerializer,
)
class ResolveFlagView(APIView):
    """Module 9.4.

    Dismissing restores the rating to scoring and recomputes the subject's score
    with it — clearing a false positive but leaving the penalty in place would
    be the worst of both outcomes.

    Approved administrators only, for the reason given on ReviewFlagListView —
    and more sharply here, because this decides whether somebody's rating counts
    and moves the trust score that decides whether they get hired.
    """

    permission_classes = [IsApprovedSocietyAdmin]
    serializer_class = ResolveFlagSerializer

    def post(self, request, pk):
        flag = ReviewFlag.objects.filter(
            pk=pk, society_id=request.user.society_id
        ).select_related("rating__worker__user", "rating__resident__flat__tower").first()
        if flag is None:
            return _error("not_found", "Flag not found.", status.HTTP_404_NOT_FOUND)

        serializer = ResolveFlagSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        changed = resolve_flag(
            flag,
            upheld=serializer.validated_data["upheld"],
            by=request.user,
            note=serializer.validated_data["note"],
        )
        if not changed:
            return _error(
                "already_resolved",
                "This flag has already been decided.",
                status.HTTP_409_CONFLICT,
            )

        return Response(
            {
                "flag": ReviewFlagSerializer(flag).data,
                "message": (
                    "Rating will stay withheld."
                    if flag.status == FlagStatus.UPHELD
                    else "Rating restored and counted."
                ),
            }
        )
