"""
Module 5 — One-Day Service Booking: API views.

Endpoint map (mounted at /api/v1/bookings/)::

    GET    categories/                  the service catalogue               (5.1)

    GET    availability/                own day availability (worker)       (5.3)
    PUT    availability/                set one date's availability
    GET    availability/<worker_id>/    a worker's open dates (resident)

    GET    match/                       workers free for a slot             (5.3)
    POST   ./                           create a booking                    (5.2)
    GET    ./                           own bookings (role-aware)
    GET    <id>/                        one booking
    POST   <id>/respond/                worker confirms or declines         (5.4)
    GET    <id>/cancellation-quote/     what cancelling now would cost      (5.4)
    POST   <id>/cancel/                 cancel, charging any fee            (5.4)
    POST   <id>/complete/               mark a finished job done

Visibility follows the same rule as Module 4: requests are two-sided, so a flat
queryset would be wrong for every caller. ``_scope_to_caller`` narrows by role,
and only a flat's primary account holder may book or cancel on its behalf
(Module 2.4).
"""

from __future__ import annotations

import logging

from django.utils import timezone
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import generics, status
from rest_framework.permissions import IsAuthenticated
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

from .models import Booking, CancelledBy, DayAvailability, ServiceCategory
from .serializers import (
    BookingCancelSerializer,
    BookingCreateSerializer,
    BookingRespondSerializer,
    BookingSerializer,
    CancellationQuoteSerializer,
    DayAvailabilitySerializer,
    MatchedWorkerSerializer,
    MatchQuerySerializer,
    ServiceCategorySerializer,
)
from .services import (
    BookingError,
    BookingNotActionable,
    NoticeTooShort,
    SlotConflict,
    WorkerUnavailable,
    cancel_booking,
    cancellation_quote,
    complete_booking,
    confirm_booking,
    create_booking,
    decline_booking,
    match_workers,
)

logger = logging.getLogger(__name__)

#: Business-rule refusals that map onto 409 rather than 400 — the request was
#: well-formed, the world just would not allow it.
_CONFLICT_ERRORS = (SlotConflict, BookingNotActionable, WorkerUnavailable)


def _error(code: str, message: str, http_status: int, details: dict | None = None):
    """The platform-standard error envelope (apps/core/exceptions.py)."""
    return Response(
        {"error": {"code": code, "message": message, "details": details or {}}},
        status=http_status,
    )


def _booking_error_response(exc: BookingError):
    http_status = (
        status.HTTP_409_CONFLICT
        if isinstance(exc, _CONFLICT_ERRORS)
        else status.HTTP_400_BAD_REQUEST
    )
    return _error(exc.code, str(exc), http_status)


def _sweep_expired(user) -> None:
    """Mark un-confirmed past bookings expired before they are read.

    Lazy expiry, as in Module 4: no scheduled worker on the free tier, so rows
    are swept on read. Scoped to the caller's society to keep the UPDATE narrow.
    """
    queryset = Booking.objects.all()
    society_id = getattr(user, "society_id", None)
    if society_id is not None:
        queryset = queryset.filter(society_id=society_id)
    queryset.expire_stale()


def _scope_to_caller(queryset, user):
    """Narrow a booking queryset to what this caller may see."""
    if user.is_superuser:
        return queryset
    if user.role == Role.RESIDENT:
        return queryset.filter(resident__user=user)
    if user.role == Role.WORKER:
        return queryset.filter(worker__user=user)
    if user.is_society_admin:
        if user.society_id is None:
            return queryset.none()
        return queryset.filter(society_id=user.society_id)
    return queryset.none()


def _booking_queryset(user):
    return _scope_to_caller(
        Booking.objects.select_related(
            "worker__user",
            "resident__user",
            "resident__flat__tower",
            "category__service_type",
        ),
        user,
    )


# ---------------------------------------------------------------------------
# 5.1 Service catalogue
# ---------------------------------------------------------------------------


@extend_schema(
    tags=["Bookings"],
    summary="List bookable service categories",
    description="Module 5.1. Each category carries the expected duration and "
    "price guidance shown to the resident before they commit.",
)
class ServiceCategoryListView(generics.ListAPIView):
    serializer_class = ServiceCategorySerializer
    permission_classes = [IsAuthenticated]
    queryset = ServiceCategory.objects.filter(is_active=True).select_related(
        "service_type"
    )
    pagination_class = None  # A short, fixed catalogue; paging it helps nobody.


# ---------------------------------------------------------------------------
# 5.3 Day availability
# ---------------------------------------------------------------------------


@extend_schema(tags=["Bookings"], summary="Read or set your own day availability")
class MyAvailabilityView(APIView):
    """Module 5.3 — the worker's opt-in for specific dates.

    ``PUT`` upserts one date. A worker toggling the same day twice on a flaky
    connection must not create two contradictory rows, which is why this is an
    idempotent upsert keyed on (worker, date) rather than a POST.
    """

    permission_classes = [IsApprovedWorker]
    serializer_class = DayAvailabilitySerializer

    def _profile_or_error(self, request):
        profile = WorkerProfile.objects.filter(user=request.user).first()
        if profile is None:
            return None, _error(
                "no_worker_profile",
                "Complete your worker profile before setting availability.",
                status.HTTP_403_FORBIDDEN,
            )
        return profile, None

    @extend_schema(
        parameters=[
            OpenApiParameter("from", str, description="Earliest date, YYYY-MM-DD"),
        ]
    )
    def get(self, request):
        profile, error = self._profile_or_error(request)
        if error:
            return error

        rows = DayAvailability.objects.filter(worker=profile)
        # Past dates are noise on this screen; the default is "today onward".
        rows = rows.filter(
            date__gte=request.query_params.get("from") or timezone.localdate()
        )

        return Response(DayAvailabilitySerializer(rows.order_by("date"), many=True).data)

    def put(self, request):
        profile, error = self._profile_or_error(request)
        if error:
            return error

        serializer = DayAvailabilitySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        row, created = DayAvailability.objects.update_or_create(
            worker=profile,
            date=data["date"],
            defaults={
                "is_available": data.get("is_available", True),
                "start_time": data.get("start_time"),
                "end_time": data.get("end_time"),
                "note": data.get("note", ""),
            },
        )

        return Response(
            DayAvailabilitySerializer(row).data,
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )


@extend_schema(tags=["Bookings"], summary="A worker's open dates")
class WorkerAvailabilityView(generics.ListAPIView):
    """What a resident sees on a worker's calendar before choosing a slot."""

    serializer_class = DayAvailabilitySerializer
    permission_classes = [IsApprovedResident]
    queryset = DayAvailability.objects.none()  # declared for schema generation

    def get_queryset(self):
        user = self.request.user
        return DayAvailability.objects.filter(
            worker_id=self.kwargs["worker_id"],
            # Society isolation: never expose another society's worker.
            worker__user__society_id=user.society_id,
            is_available=True,
            date__gte=timezone.localdate(),
        ).order_by("date")


# ---------------------------------------------------------------------------
# 5.3 Matching
# ---------------------------------------------------------------------------


def _record_unmet_demand(request, category, data) -> None:
    """Log a search that found nobody (Module 11.4).

    Lazily imported and non-raising: a failure to write an analytics row must
    never turn a legitimate "nobody is free" into an error the resident sees.
    """
    try:
        from apps.administration.models import DemandKind
        from apps.administration.services import record_unmet_demand

        record_unmet_demand(
            society=request.user.society,
            kind=DemandKind.NO_MATCH,
            service_label=category.name,
            requested_by=request.user,
            requested_date=data["date"],
            requested_time=data["start_time"],
            detail="No worker was free for this slot.",
        )
    except Exception:  # noqa: BLE001 — analytics must not break a search
        logger.exception("Could not record unmet demand for a booking search")


@extend_schema(
    tags=["Bookings"],
    summary="Workers available for a one-day slot",
    description="Module 5.3. Ranked by Module 4.3's recommendation score, "
    "narrowed to workers who opted into that date and have no conflicting "
    "booking or recurring engagement.",
    parameters=[
        OpenApiParameter("category", int, required=True, description="ServiceCategory id"),
        OpenApiParameter("date", str, required=True, description="YYYY-MM-DD"),
        OpenApiParameter("start_time", str, required=True, description="HH:MM"),
        OpenApiParameter(
            "duration_minutes", int, description="Defaults to the category's duration"
        ),
    ],
    responses=MatchedWorkerSerializer(many=True),
)
class BookingMatchView(APIView):
    permission_classes = [IsApprovedResident]
    serializer_class = MatchedWorkerSerializer

    def get(self, request):
        query = MatchQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        data = query.validated_data

        category = data["category"]
        duration = data.get("duration_minutes") or category.expected_duration_minutes

        ranked = match_workers(
            request.user.society_id,
            category=category,
            on_date=data["date"],
            start_time=data["start_time"],
            duration_minutes=duration,
            resident_society=request.user.society,
        )

        workers = []
        for worker, match in ranked:
            worker._match_score = match
            workers.append(worker)

        if not workers:
            # Module 11.4's unmet-demand log. An empty result is a legitimate
            # answer here, not an error — but it is also the single most
            # actionable fact a society committee can be given, so it is
            # recorded rather than discarded.
            _record_unmet_demand(request, category, data)

        return Response(
            {
                "count": len(workers),
                "duration_minutes": duration,
                "results": MatchedWorkerSerializer(
                    workers, many=True, context={"request": request}
                ).data,
            }
        )


# ---------------------------------------------------------------------------
# 5.2 Booking creation & listing
# ---------------------------------------------------------------------------


@extend_schema(tags=["Bookings"], summary="Create or list one-day bookings")
class BookingListCreateView(generics.ListCreateAPIView):
    queryset = Booking.objects.none()  # declared for schema generation

    def get_serializer_class(self):
        return (
            BookingCreateSerializer
            if self.request.method == "POST"
            else BookingSerializer
        )

    def get_permissions(self):
        if self.request.method == "POST":
            return [IsApprovedResident()]
        # Compose the classes and instantiate the result: DRF's `|` builds an
        # OperandHolder from permission *classes*, and OR-ing two already
        # instantiated permissions is a TypeError.
        return [(IsEngagementParty | IsApprovedSocietyAdmin)()]

    def get_queryset(self):
        _sweep_expired(self.request.user)
        queryset = _booking_queryset(self.request.user)

        status_filter = self.request.query_params.get("status")
        if status_filter:
            queryset = queryset.filter(status=status_filter)
        elif self.request.query_params.get("upcoming") in {"true", "1"}:
            queryset = queryset.live().filter(scheduled_date__gte=timezone.localdate())

        return queryset

    def create(self, request, *args, **kwargs):
        resident = primary_resident_or_403(request.user)

        serializer = BookingCreateSerializer(
            data=request.data, context={"request": request}
        )
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        try:
            booking = create_booking(
                resident=resident,
                worker=data["worker"],
                category=data["category"],
                society=request.user.society,
                scheduled_date=data["scheduled_date"],
                start_time=data["start_time"],
                duration_minutes=data["expected_duration_minutes"],
                quoted_price=data["quoted_price"],
                notes=data.get("notes", ""),
            )
        except NoticeTooShort as exc:
            return _error(exc.code, str(exc), status.HTTP_400_BAD_REQUEST)
        except BookingError as exc:
            return _booking_error_response(exc)

        return Response(
            {
                "booking": BookingSerializer(booking).data,
                "message": "Booking requested. The worker will confirm shortly.",
            },
            status=status.HTTP_201_CREATED,
        )


@extend_schema(tags=["Bookings"], summary="Retrieve a booking")
class BookingDetailView(generics.RetrieveAPIView):
    serializer_class = BookingSerializer
    permission_classes = [IsEngagementParty | IsApprovedSocietyAdmin]
    queryset = Booking.objects.none()  # declared for schema generation

    def get_queryset(self):
        _sweep_expired(self.request.user)
        return _booking_queryset(self.request.user)


# ---------------------------------------------------------------------------
# 5.4 Confirmation, cancellation, completion
# ---------------------------------------------------------------------------


class _BookingActionView(APIView):
    """Shared lookup for the action endpoints."""

    def get_booking(self, request, pk):
        _sweep_expired(request.user)
        return _booking_queryset(request.user).filter(pk=pk).first()


@extend_schema(
    tags=["Bookings"],
    summary="Confirm or decline a booking",
    request=BookingRespondSerializer,
)
class BookingRespondView(_BookingActionView):
    """Module 5.4 — the worker's answer."""

    permission_classes = [IsApprovedWorker]
    serializer_class = BookingRespondSerializer

    def post(self, request, pk):
        booking = self.get_booking(request, pk)
        if booking is None:
            return _error("not_found", "Booking not found.", status.HTTP_404_NOT_FOUND)

        serializer = BookingRespondSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        note = serializer.validated_data.get("note", "")

        try:
            if serializer.validated_data["confirm"]:
                updated = confirm_booking(booking, note=note)
                message = "Booking confirmed."
            else:
                updated = decline_booking(booking, note=note)
                message = "Booking declined."
        except BookingError as exc:
            return _booking_error_response(exc)

        return Response({"booking": BookingSerializer(updated).data, "message": message})


@extend_schema(
    tags=["Bookings"],
    summary="What cancelling now would cost",
    responses=CancellationQuoteSerializer,
)
class CancellationQuoteView(_BookingActionView):
    """Module 5.4 — shown before the user commits to cancelling.

    A fee that only appears after the fact is the kind of surprise that makes
    people stop trusting the app, so the client always asks first.
    """

    permission_classes = [IsEngagementParty | IsApprovedSocietyAdmin]
    serializer_class = CancellationQuoteSerializer

    def get(self, request, pk):
        booking = self.get_booking(request, pk)
        if booking is None:
            return _error("not_found", "Booking not found.", status.HTTP_404_NOT_FOUND)

        if not booking.can_be_cancelled:
            return _error(
                "booking_not_actionable",
                "Only a booking that has not started yet can be cancelled.",
                status.HTTP_409_CONFLICT,
            )

        return Response(cancellation_quote(booking))


@extend_schema(
    tags=["Bookings"],
    summary="Cancel a booking",
    request=BookingCancelSerializer,
)
class BookingCancelView(_BookingActionView):
    """Module 5.4. Either party may cancel; the fee depends on how late it is."""

    permission_classes = [IsEngagementParty | IsApprovedSocietyAdmin]
    serializer_class = BookingCancelSerializer

    def post(self, request, pk):
        booking = self.get_booking(request, pk)
        if booking is None:
            return _error("not_found", "Booking not found.", status.HTTP_404_NOT_FOUND)

        if request.user.role == Role.RESIDENT:
            primary_resident_or_403(request.user)
            cancelled_by = CancelledBy.RESIDENT
        elif request.user.role == Role.WORKER:
            cancelled_by = CancelledBy.WORKER
        else:
            cancelled_by = CancelledBy.ADMIN

        serializer = BookingCancelSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        # If a threshold was crossed while the confirmation dialog was open,
        # refuse rather than silently charging more than was shown.
        acknowledged = serializer.validated_data.get("acknowledged_fee")
        quote = cancellation_quote(booking)
        if acknowledged is not None and acknowledged != quote["fee"]:
            return _error(
                "fee_changed",
                "The cancellation fee changed while you were deciding. "
                "Please review it and try again.",
                status.HTTP_409_CONFLICT,
                details=quote,
            )

        try:
            updated, fee = cancel_booking(
                booking,
                cancelled_by=cancelled_by,
                reason=serializer.validated_data.get("reason", ""),
            )
        except BookingError as exc:
            return _booking_error_response(exc)

        return Response(
            {
                "booking": BookingSerializer(updated).data,
                "cancellation_fee": fee,
                "message": (
                    "Booking cancelled."
                    if fee == 0
                    else f"Booking cancelled. A fee of ₹{fee} applies."
                ),
            }
        )


@extend_schema(
    tags=["Bookings"],
    summary="Mark a booking complete",
    request=None,
    responses=BookingSerializer,
)
class BookingCompleteView(_BookingActionView):
    """Interim until Module 7 closes bookings out from gate attendance."""

    permission_classes = [IsEngagementParty | IsApprovedSocietyAdmin]
    serializer_class = BookingSerializer

    def post(self, request, pk):
        booking = self.get_booking(request, pk)
        if booking is None:
            return _error("not_found", "Booking not found.", status.HTTP_404_NOT_FOUND)

        try:
            updated = complete_booking(booking)
        except BookingError as exc:
            return _booking_error_response(exc)

        return Response(
            {"booking": BookingSerializer(updated).data, "message": "Booking completed."}
        )
