"""
Module 4 — Discovery & Hiring: API views.

Endpoint map (mounted at /api/v1/hiring/)::

    GET    workers/                     search available workers            (4.1)
    GET    workers/<id>/                full worker profile                 (4.2)

    POST   requests/                    send a hire request                 (4.4)
    GET    requests/                    own requests (role-aware)           (4.4)
    GET    requests/<id>/               one request
    POST   requests/<id>/respond/       worker accepts or declines          (4.4)
    POST   requests/<id>/withdraw/      resident retracts

    GET    engagements/                 own engagements (role-aware)        (4.5)
    GET    engagements/<id>/            one engagement
    POST   engagements/<id>/transition/ pause / resume / terminate          (4.5)

    POST   engagements/<id>/notice/     give ten days' notice               (4.6)
    POST   engagements/<id>/notice/withdraw/   take it back                 (4.6)

-------------------------------------------------------------------------------
WHO SEES WHAT
-------------------------------------------------------------------------------
Requests and engagements are two-sided, so a single flat queryset would be wrong
for every caller. ``_scope_to_caller`` narrows by role: a resident sees the rows
their household is party to, a worker sees theirs, an administrator sees their
society's, and nobody sees anyone else's. That is a row-level rule, so it lives
on the queryset — a permission class can only answer whether the endpoint may be
called at all (see apps/accounts/permissions.py).

-------------------------------------------------------------------------------
ONLY THE PRIMARY RESIDENT MAY HIRE
-------------------------------------------------------------------------------
Module 2.4 designates one primary account holder per flat and reserves creating
and editing hires to them, so that two people in one household cannot issue
conflicting arrangements with the same worker. Enforced by
``societies.services.primary_resident_or_403``, which Module 5 shares. The
first person to claim a flat automatically
becomes its primary, so this never blocks a single-occupant household.
"""

from __future__ import annotations

import logging

from django.db.models import Q
from django.utils.dateparse import parse_time
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import generics, status
from rest_framework.exceptions import NotFound, PermissionDenied
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import Role
from apps.accounts.permissions import (
    IsApprovedResident,
    IsApprovedSocietyAdmin,
    IsApprovedWorker,
    IsEngagementParty,
)
from apps.societies.services import primary_resident_or_403
from apps.workers.models import WorkerProfile

from .models import NOTICE_PERIOD_DAYS, Engagement, HireRequest, HireRequestStatus
from .serializers import (
    EngagementSerializer,
    EngagementTransitionSerializer,
    GiveNoticeSerializer,
    HireRequestCreateSerializer,
    HireRequestRespondSerializer,
    HireRequestSerializer,
    HireRequestWithdrawSerializer,
    WorkerDetailSerializer,
    WorkerSearchResultSerializer,
)
from .settlement import outstanding_settlement, settlement_due
from .services import (
    DuesOutstanding,
    DuplicateEngagement,
    HiringError,
    NoticeTooShort,
    RequestNotActionable,
    accept_hire_request,
    annotate_hiring_stats,
    build_scoring_inputs,
    close_engagements_past_notice,
    decline_hire_request,
    earliest_last_working_day,
    give_notice,
    rank_workers,
    searchable_workers,
    withdraw_notice,
)
from .scoring import score

logger = logging.getLogger(__name__)

#: Ceiling on how many workers are pulled into memory for Python-side ranking.
#: A society has hundreds of workers, not millions, so this is generous — but it
#: bounds the request rather than trusting that to stay true. Beyond the cap the
#: database's own ordering (trust score descending) decides who is considered,
#: so the most promising candidates are the ones that survive truncation.
RANKING_CANDIDATE_LIMIT = 500


def _error(code: str, message: str, http_status: int, details: dict | None = None):
    """Build the platform-standard error envelope (see apps/core/exceptions.py)."""
    return Response(
        {"error": {"code": code, "message": message, "details": details or {}}},
        status=http_status,
    )


def _sweep_expired(user) -> None:
    """Flip lapsed requests to EXPIRED before they are read or acted on.

    Expiry is lazy (see apps/hiring/models.py), so every endpoint that reads or
    acts on requests sweeps first. The sweep is scoped to the caller's society
    rather than the whole table: the response-rate statistics that depend on it
    are computed per society anyway, and a narrower UPDATE means one society's
    traffic cannot contend with another's on a shared free-tier database.

    Platform staff have no society, and sweep everything.
    """
    queryset = HireRequest.objects.all()
    society_id = getattr(user, "society_id", None)
    if society_id is not None:
        queryset = queryset.filter(society_id=society_id)
    queryset.expire_lapsed()


def _scope_to_caller(queryset, user):
    """Narrow a request/engagement queryset to what this caller may see."""
    if user.is_superuser:
        return queryset
    if user.role == Role.RESIDENT:
        return queryset.filter(resident__user=user)
    if user.role == Role.WORKER:
        return queryset.filter(worker__user=user)
    if user.is_society_admin:
        # Fail closed: an administrator with no society sees nothing rather
        # than everything.
        if user.society_id is None:
            return queryset.none()
        return queryset.filter(society_id=user.society_id)
    return queryset.none()


# ---------------------------------------------------------------------------
# 4.1 Search & filters
# ---------------------------------------------------------------------------


@extend_schema(
    tags=["Hiring"],
    summary="Search available workers",
    description=(
        "Module 4.1. Returns approved, available workers in the caller's society, "
        "ranked by the Module 4.3 recommendation score unless another sort is asked "
        "for. Each row carries a `match_percentage`."
    ),
    parameters=[
        OpenApiParameter("service_type", int, description="ServiceType id"),
        OpenApiParameter("service", str, description="ServiceType slug"),
        OpenApiParameter("q", str, description="Name search"),
        OpenApiParameter("min_rate", int, description="Lowest acceptable monthly rate (INR)"),
        OpenApiParameter("max_rate", int, description="Highest acceptable monthly rate (INR)"),
        OpenApiParameter("min_rating", float, description="Minimum average rating (0-5)"),
        OpenApiParameter("min_trust", float, description="Minimum trust score (0-100)"),
        OpenApiParameter("available_from", str, description="Requested window start, HH:MM"),
        OpenApiParameter("available_until", str, description="Requested window end, HH:MM"),
        OpenApiParameter(
            "strict_availability",
            bool,
            description="Drop workers who cannot cover the whole requested window, "
            "instead of merely ranking them lower.",
        ),
        OpenApiParameter(
            "sort",
            str,
            description="recommended (default) | rating | trust | rate_asc | experience",
        ),
    ],
)
class WorkerSearchView(generics.ListAPIView):
    """Module 4.1 — the discovery screen.

    Two execution paths, chosen by ``sort``:

    * **recommended** — the score is computed in Python (the formula is not
      expressible in SQL), so candidates are materialised up to
      ``RANKING_CANDIDATE_LIMIT``, ranked, and then paginated as a list.
    * **any other sort** — the database orders and paginates, and scores are
      computed only for the page actually being returned.

    Both paths run over ``annotate_hiring_stats``, so neither issues per-worker
    queries for history.
    """

    serializer_class = WorkerSearchResultSerializer
    permission_classes = [IsApprovedResident]
    queryset = WorkerProfile.objects.none()  # declared for schema generation

    _SQL_SORTS = {
        "rating": ["-average_rating", "-trust_score"],
        "trust": ["-trust_score", "-average_rating"],
        "rate_asc": ["expected_monthly_rate", "-trust_score"],
        "experience": ["-years_of_experience", "-trust_score"],
    }

    def _requested_window(self):
        params = self.request.query_params
        return (
            parse_time(params.get("available_from") or ""),
            parse_time(params.get("available_until") or ""),
        )

    def get_queryset(self):
        params = self.request.query_params
        queryset = annotate_hiring_stats(searchable_workers(self.request.user.society_id))

        service_type = params.get("service_type")
        if service_type:
            queryset = queryset.filter(service_types__id=service_type)

        service_slug = params.get("service")
        if service_slug:
            queryset = queryset.filter(service_types__slug=service_slug)

        min_rate, max_rate = params.get("min_rate"), params.get("max_rate")
        if min_rate:
            queryset = queryset.filter(expected_monthly_rate__gte=min_rate)
        if max_rate:
            # A worker who has not stated a rate is kept in the results: an
            # unstated rate is negotiable, not automatically over budget.
            queryset = queryset.filter(
                Q(expected_monthly_rate__lte=max_rate)
                | Q(expected_monthly_rate__isnull=True)
            )

        if params.get("min_rating"):
            queryset = queryset.filter(average_rating__gte=params["min_rating"])
        if params.get("min_trust"):
            queryset = queryset.filter(trust_score__gte=params["min_trust"])

        search = params.get("q")
        if search:
            queryset = queryset.filter(
                Q(user__first_name__icontains=search)
                | Q(user__last_name__icontains=search)
            )

        if params.get("strict_availability") in {"true", "1"}:
            window_from, window_until = self._requested_window()
            if window_from and window_until:
                queryset = queryset.filter(
                    available_from__lte=window_from, available_until__gte=window_until
                )

        # Filtering on a many-to-many (service_types) multiplies rows; without
        # this a worker offering two matching services would appear twice.
        return queryset.distinct()

    def list(self, request, *args, **kwargs):
        queryset = self.get_queryset()
        window_from, window_until = self._requested_window()
        resident_society = request.user.society

        sort = request.query_params.get("sort", "recommended")
        scoring_kwargs = {
            "resident_society": resident_society,
            "requested_from": window_from,
            "requested_until": window_until,
        }

        if sort in self._SQL_SORTS:
            page = self.paginate_queryset(queryset.order_by(*self._SQL_SORTS[sort]))
            workers = page if page is not None else list(queryset)
            for worker in workers:
                worker._match_score = score(build_scoring_inputs(worker, **scoring_kwargs))
        else:
            candidates = list(queryset.order_by("-trust_score")[:RANKING_CANDIDATE_LIMIT])
            ranked = rank_workers(candidates, **scoring_kwargs)
            for worker, match in ranked:
                worker._match_score = match
            ordered = [worker for worker, _ in ranked]
            page = self.paginate_queryset(ordered)
            workers = page if page is not None else ordered

        serializer = self.get_serializer(workers, many=True)
        if page is not None:
            return self.get_paginated_response(serializer.data)
        return Response(serializer.data)


@extend_schema(tags=["Hiring"], summary="Worker profile")
class WorkerDetailView(generics.RetrieveAPIView):
    """Module 4.2 — everything a resident needs before sending a request."""

    serializer_class = WorkerDetailSerializer
    permission_classes = [IsApprovedResident]
    queryset = WorkerProfile.objects.none()  # declared for schema generation

    def get_queryset(self):
        return annotate_hiring_stats(searchable_workers(self.request.user.society_id))

    def get_object(self):
        worker = super().get_object()
        worker._match_score = score(
            build_scoring_inputs(worker, resident_society=self.request.user.society)
        )
        return worker


# ---------------------------------------------------------------------------
# 4.4 Hire requests
# ---------------------------------------------------------------------------


@extend_schema(tags=["Hiring"], summary="Send or list hire requests")
class HireRequestListCreateView(generics.ListCreateAPIView):
    """Module 4.4.

    Residents send requests; both sides list the ones they are party to.
    """

    queryset = HireRequest.objects.none()  # declared for schema generation

    def get_serializer_class(self):
        if self.request.method == "POST":
            return HireRequestCreateSerializer
        return HireRequestSerializer

    def get_permissions(self):
        if self.request.method == "POST":
            return [IsApprovedResident()]
        return [IsEngagementParty()]

    def get_queryset(self):
        # Sweep first: a lapsed request must not be listed as still pending.
        _sweep_expired(self.request.user)

        queryset = HireRequest.objects.select_related(
            "worker__user", "resident__user", "resident__flat__tower", "service_type"
        ).prefetch_related("engagement")

        queryset = _scope_to_caller(queryset, self.request.user)

        status_filter = self.request.query_params.get("status")
        if status_filter == "pending":
            queryset = queryset.pending()
        elif status_filter:
            queryset = queryset.filter(status=status_filter)

        return queryset

    def get_serializer_context(self):
        context = super().get_serializer_context()
        if self.request.method == "POST":
            context["resident"] = primary_resident_or_403(self.request.user)
        return context

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        hire_request = serializer.save()

        logger.info(
            "Hire request %s sent by resident %s to worker %s",
            hire_request.pk,
            hire_request.resident_id,
            hire_request.worker_id,
        )
        return Response(
            {
                "request": HireRequestSerializer(hire_request).data,
                "message": "Request sent. The worker has "
                f"{int((hire_request.expires_at - hire_request.created_at).total_seconds() // 3600)}"
                " hours to respond.",
            },
            status=status.HTTP_201_CREATED,
        )


@extend_schema(tags=["Hiring"], summary="Retrieve a hire request")
class HireRequestDetailView(generics.RetrieveAPIView):
    serializer_class = HireRequestSerializer
    permission_classes = [IsEngagementParty]
    queryset = HireRequest.objects.none()  # declared for schema generation

    def get_queryset(self):
        _sweep_expired(self.request.user)
        return _scope_to_caller(
            HireRequest.objects.select_related(
                "worker__user", "resident__user", "resident__flat__tower", "service_type"
            ),
            self.request.user,
        )


@extend_schema(
    tags=["Hiring"],
    summary="Accept or decline a hire request",
    request=HireRequestRespondSerializer,
)
class HireRequestRespondView(APIView):
    """Module 4.4 — the worker's side of the negotiation.

    Accepting is what creates the engagement, and it happens inside a locked
    transaction in ``services.accept_hire_request``.
    """

    permission_classes = [IsApprovedWorker]
    serializer_class = HireRequestRespondSerializer

    def post(self, request, pk):
        _sweep_expired(request.user)

        hire_request = (
            _scope_to_caller(HireRequest.objects.all(), request.user)
            .select_related("worker__user", "resident__user", "service_type", "society")
            .filter(pk=pk)
            .first()
        )
        if hire_request is None:
            return _error("not_found", "Hire request not found.", status.HTTP_404_NOT_FOUND)

        serializer = HireRequestRespondSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        note = serializer.validated_data.get("note", "")

        try:
            if serializer.validated_data["accept"]:
                engagement = accept_hire_request(hire_request, note=note)
                return Response(
                    {
                        "engagement": EngagementSerializer(engagement).data,
                        "message": "Request accepted. The engagement is now active.",
                    },
                    status=status.HTTP_201_CREATED,
                )

            declined = decline_hire_request(hire_request, note=note)
            return Response(
                {
                    "request": HireRequestSerializer(declined).data,
                    "message": "Request declined.",
                }
            )
        except DuplicateEngagement as exc:
            return _error(exc.code, str(exc), status.HTTP_409_CONFLICT)
        except RequestNotActionable as exc:
            return _error(exc.code, str(exc), status.HTTP_409_CONFLICT)
        except HiringError as exc:  # pragma: no cover — defensive
            return _error(exc.code, str(exc), status.HTTP_400_BAD_REQUEST)


@extend_schema(
    tags=["Hiring"],
    summary="Withdraw a hire request",
    request=HireRequestWithdrawSerializer,
)
class HireRequestWithdrawView(APIView):
    """The resident retracts a request the worker has not yet answered.

    Withdrawn requests are excluded from the worker's response-rate statistics —
    a request that was taken away is not one they failed to answer.
    """

    permission_classes = [IsApprovedResident]
    serializer_class = HireRequestWithdrawSerializer

    def post(self, request, pk):
        _sweep_expired(request.user)

        hire_request = (
            _scope_to_caller(HireRequest.objects.all(), request.user).filter(pk=pk).first()
        )
        if hire_request is None:
            return _error("not_found", "Hire request not found.", status.HTTP_404_NOT_FOUND)

        if not hire_request.is_actionable:
            return _error(
                "request_not_actionable",
                "Only a request that is still open can be withdrawn.",
                status.HTTP_409_CONFLICT,
            )

        serializer = HireRequestWithdrawSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        hire_request.status = HireRequestStatus.WITHDRAWN
        hire_request.response_note = serializer.validated_data.get("reason", "")
        hire_request.save(update_fields=["status", "response_note", "updated_at"])

        logger.info("Hire request %s withdrawn by resident %s", pk, request.user.pk)
        return Response(
            {
                "request": HireRequestSerializer(hire_request).data,
                "message": "Request withdrawn.",
            }
        )


# ---------------------------------------------------------------------------
# 4.5 Engagement lifecycle
# ---------------------------------------------------------------------------


def _engagement_queryset(user):
    return _scope_to_caller(
        Engagement.objects.select_related(
            "worker__user", "resident__user", "resident__flat__tower", "service_type"
        ),
        user,
    )


@extend_schema(
    tags=["Engagements"],
    summary="List engagements",
    parameters=[
        OpenApiParameter("status", str, description="active | paused | terminated"),
        OpenApiParameter("live", bool, description="Active and paused only"),
    ],
)
class EngagementListView(generics.ListAPIView):
    serializer_class = EngagementSerializer
    permission_classes = [IsEngagementParty | IsApprovedSocietyAdmin]
    queryset = Engagement.objects.none()  # declared for schema generation

    def get_queryset(self):
        # Module 4.6 — engagements whose notice has run out are closed here.
        # There is no scheduler on the free tier (docs/free-tier-constraints.md
        # §7), so the sweep is idempotent and hangs off a read, exactly as hire
        # request expiry and Module 6.5's leave already do.
        close_engagements_past_notice()

        queryset = _engagement_queryset(self.request.user)

        status_filter = self.request.query_params.get("status")
        if status_filter:
            queryset = queryset.filter(status=status_filter)
        elif self.request.query_params.get("live") in {"true", "1"}:
            queryset = queryset.live()

        return queryset


@extend_schema(tags=["Engagements"], summary="Retrieve an engagement")
class EngagementDetailView(generics.RetrieveAPIView):
    serializer_class = EngagementSerializer
    permission_classes = [IsEngagementParty | IsApprovedSocietyAdmin]
    queryset = Engagement.objects.none()  # declared for schema generation

    def get_queryset(self):
        return _engagement_queryset(self.request.user)


@extend_schema(
    tags=["Engagements"],
    summary="Pause, resume or terminate an engagement",
    request=EngagementTransitionSerializer,
)
class EngagementTransitionView(APIView):
    """Module 4.5.

    A single action endpoint rather than a PATCH on ``status``: these are
    transitions with rules — terminated is terminal — and exposing the field
    directly would invite a client to set any value it liked.

    Residents may only act through their flat's primary account holder, for the
    same reason they may only hire through them (Module 2.4).
    """

    permission_classes = [IsEngagementParty | IsApprovedSocietyAdmin]
    serializer_class = EngagementTransitionSerializer

    def post(self, request, pk):
        engagement = _engagement_queryset(request.user).filter(pk=pk).first()
        if engagement is None:
            return _error("not_found", "Engagement not found.", status.HTTP_404_NOT_FOUND)

        if request.user.role == Role.RESIDENT:
            primary_resident_or_403(request.user)

        serializer = EngagementTransitionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        action = serializer.validated_data["action"]

        if action == "pause":
            changed = engagement.pause(reason=serializer.validated_data.get("note", ""))
            message = "Engagement paused." if changed else "Engagement was already paused."
        elif action == "resume":
            changed = engagement.resume()
            if not changed and engagement.status != "active":
                return _error(
                    "invalid_transition",
                    "A terminated engagement cannot be resumed. Send a new hire request instead.",
                    status.HTTP_409_CONFLICT,
                )
            message = "Engagement resumed." if changed else "Engagement was already active."
        else:
            changed = engagement.terminate(
                reason=serializer.validated_data["reason"],
                note=serializer.validated_data.get("note", ""),
                by=request.user,
            )
            message = (
                "Engagement terminated." if changed else "Engagement was already terminated."
            )

        if changed:
            logger.info(
                "Engagement %s %sd by user %s", engagement.pk, action, request.user.pk
            )

        return Response(
            {"engagement": EngagementSerializer(engagement).data, "message": message}
        )


@extend_schema(
    tags=["Hiring"],
    summary="Give notice on an engagement",
    request=GiveNoticeSerializer,
    responses=EngagementSerializer,
)
class GiveNoticeView(APIView):
    """Module 4.6 — either side ends the arrangement, with ten days' notice.

    Deliberately not folded into the pause/resume/terminate endpoint. Notice is
    the ordinary way an engagement ends; terminate is the exceptional one, and
    giving them the same shape is how the exceptional path gets taken by
    accident.

    The engagement stays ACTIVE throughout. Its visits keep appearing on both
    schedules, the gate keeps admitting the worker, and attendance keeps
    counting - which is what makes "paid in full for the days worked" true
    rather than merely promised.
    """

    permission_classes = [IsEngagementParty | IsApprovedSocietyAdmin]
    serializer_class = GiveNoticeSerializer

    def post(self, request, pk):
        engagement = _engagement_queryset(request.user).filter(pk=pk).first()
        if engagement is None:
            return _error("not_found", "Engagement not found.", status.HTTP_404_NOT_FOUND)

        if request.user.role == Role.RESIDENT:
            primary_resident_or_403(request.user)

        serializer = GiveNoticeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        try:
            engagement = give_notice(
                engagement,
                by=request.user,
                reason=data["reason"],
                requested_last_day=data.get("last_working_day"),
            )
        except NoticeTooShort as exc:
            return _error(
                exc.code,
                str(exc),
                status.HTTP_400_BAD_REQUEST,
                details={
                    "earliest_last_working_day": earliest_last_working_day(),
                    "notice_period_days": NOTICE_PERIOD_DAYS,
                },
            )
        except DuesOutstanding as exc:
            # The figure travels with the refusal, so the app can put the
            # breakdown straight in front of the resident rather than making a
            # second round trip to find out what it is being asked for.
            outstanding = outstanding_settlement(engagement)
            return _error(
                exc.code,
                str(exc),
                status.HTTP_409_CONFLICT,
                details=outstanding.as_dict() if outstanding else {},
            )
        except HiringError as exc:
            return _error(exc.code, str(exc), status.HTTP_409_CONFLICT)

        visits = engagement.visits_remaining()
        return Response(
            {
                "engagement": EngagementSerializer(engagement).data,
                "message": (
                    f"Notice recorded. The last working day is "
                    f"{engagement.last_working_day:%d %b %Y} - "
                    f"{visits} more visit{'s' if visits != 1 else ''}, "
                    "all of them paid."
                ),
            }
        )


@extend_schema(
    tags=["Hiring"],
    summary="What is owed before notice can be given",
    responses=None,
)
class NoticeSettlementView(APIView):
    """Module 4.6 — the pro-rata for this month, and the payment that clears it.

    ``GET`` returns the breakdown to show the resident *before* they commit:
    days worked, days scheduled, the rate, and the resulting amount. Every term
    is exposed because this is the last money to change hands in a relationship
    that is ending, and a figure nobody can account for at that moment is a
    figure that turns into a complaint.

    ``POST`` opens the ledger row for it. It does **not** settle it — that still
    happens only through a verified Razorpay signature or a webhook, like every
    other payment in Module 8.
    """

    permission_classes = [IsEngagementParty | IsApprovedSocietyAdmin]

    def get(self, request, pk):
        engagement = _engagement_queryset(request.user).filter(pk=pk).first()
        if engagement is None:
            return _error("not_found", "Engagement not found.", status.HTTP_404_NOT_FOUND)

        breakdown = settlement_due(engagement)
        outstanding = outstanding_settlement(engagement)
        return Response(
            {
                **breakdown.as_dict(),
                # Distinct from `amount_paise`: the pro-rata may be non-zero and
                # yet nothing be owed, because a salary already paid this month
                # covered it. A household must never be asked twice.
                "is_outstanding": outstanding is not None,
                "blocks_notice": outstanding is not None,
            }
        )

    def post(self, request, pk):
        from apps.payments.models import PaymentKind, PaymentStatus
        from apps.payments.serializers import PaymentSerializer
        from apps.payments.services import NothingToPay, create_payment

        engagement = _engagement_queryset(request.user).filter(pk=pk).first()
        if engagement is None:
            return _error("not_found", "Engagement not found.", status.HTTP_404_NOT_FOUND)

        if request.user.role == Role.RESIDENT:
            primary_resident_or_403(request.user)
        elif request.user.role == Role.WORKER:
            return _error(
                "permission_denied",
                "The household settles this, not the worker.",
                status.HTTP_403_FORBIDDEN,
            )

        outstanding = outstanding_settlement(engagement)
        if outstanding is None:
            return _error(
                "nothing_to_pay",
                "Nothing is outstanding for this month.",
                status.HTTP_409_CONFLICT,
                details=settlement_due(engagement).as_dict(),
            )

        # Idempotent on the engagement and month, like the booking payment: a
        # resident re-opening the screen or retrying on a poor connection must
        # resume the same row rather than opening a second demand for the same
        # wages.
        from apps.payments.models import Payment

        existing = (
            Payment.objects.filter(
                engagement=engagement,
                kind=PaymentKind.NOTICE_SETTLEMENT,
                period_start=outstanding.month_start,
            )
            .exclude(status__in=[PaymentStatus.FAILED, PaymentStatus.CANCELLED])
            .first()
        )
        if existing is not None:
            return Response(
                {"payment": PaymentSerializer(existing).data,
                 "settlement": outstanding.as_dict()},
                status=status.HTTP_200_OK,
            )

        try:
            payment = create_payment(
                resident=engagement.resident,
                worker=engagement.worker,
                society=engagement.society,
                kind=PaymentKind.NOTICE_SETTLEMENT,
                amount_paise=outstanding.amount_paise,
                engagement=engagement,
                period_start=outstanding.month_start,
                period_end=outstanding.month_end,
                note=outstanding.explain(),
            )
        except NothingToPay as exc:
            return _error(exc.code, str(exc), status.HTTP_400_BAD_REQUEST)

        return Response(
            {
                "payment": PaymentSerializer(payment).data,
                "settlement": outstanding.as_dict(),
            },
            status=status.HTTP_201_CREATED,
        )


@extend_schema(
    tags=["Hiring"],
    summary="Withdraw notice",
    responses=EngagementSerializer,
)
class WithdrawNoticeView(APIView):
    """Both sides changed their mind before the last working day."""

    permission_classes = [IsEngagementParty | IsApprovedSocietyAdmin]
    serializer_class = EngagementSerializer

    def post(self, request, pk):
        engagement = _engagement_queryset(request.user).filter(pk=pk).first()
        if engagement is None:
            return _error("not_found", "Engagement not found.", status.HTTP_404_NOT_FOUND)

        if request.user.role == Role.RESIDENT:
            primary_resident_or_403(request.user)

        try:
            engagement = withdraw_notice(engagement, by=request.user)
        except HiringError as exc:
            return _error(exc.code, str(exc), status.HTTP_409_CONFLICT)

        return Response(
            {
                "engagement": EngagementSerializer(engagement).data,
                "message": "Notice withdrawn. This arrangement continues as before.",
            }
        )
