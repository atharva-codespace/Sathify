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

    GET    emergency/quote/             what the surcharge would be         (5.5)
    POST   emergency/                   raise a request + open the surcharge
    GET    emergency/live/              both dashboards' poll endpoint
    GET    emergency/offers/            requests offered to this worker
    POST   emergency/<id>/accept/       claim it — first one wins
    POST   emergency/<id>/decline/      pass

Visibility follows the same rule as Module 4: requests are two-sided, so a flat
queryset would be wrong for every caller. ``_scope_to_caller`` narrows by role,
and only a flat's primary account holder may book or cancel on its behalf
(Module 2.4).
"""

from __future__ import annotations

import datetime as dt
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

from . import emergency as emergency_service
from .models import (
    EMERGENCY_OPEN_STATUSES,
    Booking,
    BookingOffer,
    BookingStatus,
    CancelledBy,
    DayAvailability,
    OfferState,
    ServiceCategory,
)
from .policy import emergency_surcharge
from .serializers import (
    BookingCancelSerializer,
    BookingCreateSerializer,
    BookingOfferSerializer,
    BookingRespondSerializer,
    BookingSerializer,
    CancellationQuoteSerializer,
    DayAvailabilitySerializer,
    EmergencyRequestSerializer,
    MatchedWorkerSerializer,
    MatchQuerySerializer,
    ServiceCategorySerializer,
)
from .services import (
    BookingError,
    BookingNotActionable,
    EmergencyMustBroadcast,
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
_CONFLICT_ERRORS = (
    SlotConflict,
    BookingNotActionable,
    WorkerUnavailable,
    # Not the caller's mistake so much as the wrong door: the request is
    # well-formed, it just has to go through the broadcast flow.
    EmergencyMustBroadcast,
    # Losing the race for an emergency is a 409: the request was well-formed and
    # arrived a moment too late. It must never read as a 400, because the app
    # shows a 400 as "you did something wrong" and she did not.
    emergency_service.OfferGone,
)


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
    """Close out anything that is over in fact but not yet in the database.

    Lazy expiry, as in Module 4: no scheduled worker on the free tier, so rows
    are swept on read. Scoped to the caller's society to keep the UPDATE narrow.

    Two sweeps, because there are two ways a booking runs out of time: a
    directed one whose worker never answered, and a broadcast one nobody
    claimed. The second refunds a surcharge, so it is deliberately run wherever
    a household might be looking at the request and wondering.
    """
    society_id = getattr(user, "society_id", None)

    queryset = Booking.objects.all()
    if society_id is not None:
        queryset = queryset.filter(society_id=society_id)
    queryset.expire_stale()

    emergency_service.expire_unclaimed(society_id=society_id)


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


# ---------------------------------------------------------------------------
# 5.5 Emergency broadcast
# ---------------------------------------------------------------------------


def _worker_profile_or_error(request):
    profile = WorkerProfile.objects.filter(user=request.user).first()
    if profile is None:
        return None, _error(
            "no_worker_profile",
            "Complete your worker profile first.",
            status.HTTP_403_FORBIDDEN,
        )
    return profile, None


@extend_schema(
    tags=["Bookings"],
    summary="What raising an emergency now would cost",
    parameters=[OpenApiParameter("date", str, description="YYYY-MM-DD; defaults to today")],
)
class EmergencySurchargeQuoteView(APIView):
    """Module 5.5 — the surcharge, shown before the resident commits to it.

    Same principle as the cancellation quote: a charge discovered after the fact
    is the kind of surprise that costs an app its users. The client renders this
    number rather than reconstructing it from a table it would have to keep in
    step with the server's.
    """

    permission_classes = [IsApprovedResident]

    def get(self, request):
        raw = request.query_params.get("date")
        try:
            day = dt.date.fromisoformat(raw) if raw else timezone.localdate()
        except ValueError:
            return _error(
                "validation_error", "date must be YYYY-MM-DD.", status.HTTP_400_BAD_REQUEST
            )

        quote = emergency_surcharge(
            scheduled_date=day, raised_on=timezone.localdate()
        )
        return Response(
            {
                "surcharge_paise": quote.paise,
                "surcharge_rupees": quote.rupees,
                "lead_days": quote.lead_days,
                "rationale": quote.rationale,
                # Said explicitly, on the screen where the household is about to
                # be charged, so nobody can be surprised later by a second
                # payment they were not expecting. It is a *different* amount at
                # a *different* time, and that is the whole point of saying it.
                "worker_fee_settlement": "app",
                "worker_fee_note": (
                    "This fee is Sathify's, for finding somebody quickly. The "
                    "worker's own charge is separate, and you are asked for it "
                    "in the app once the job is done."
                ),
            }
        )


@extend_schema(
    tags=["Bookings"],
    summary="Raise an emergency request",
    request=EmergencyRequestSerializer,
    responses=BookingSerializer,
)
class RaiseEmergencyView(APIView):
    """Module 5.5 — open a request and the surcharge that unlocks it.

    Returns the booking *and* the payment, because the very next thing the app
    must do is take the household through Razorpay checkout for that payment.
    Nothing is broadcast until it settles — see ``emergency.raise_emergency``.
    """

    permission_classes = [IsApprovedResident]
    serializer_class = EmergencyRequestSerializer

    def post(self, request):
        from apps.payments.serializers import PaymentSerializer

        resident = primary_resident_or_403(request.user)

        serializer = EmergencyRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        now = timezone.localtime()
        try:
            booking, payment = emergency_service.raise_emergency(
                resident=resident,
                society=request.user.society,
                category=data["category"],
                # Defaults are "right now", because that is what an emergency
                # means. The directed flow makes the resident pick a slot; here
                # every extra field is a field somebody fills in one-handed
                # while something is going wrong.
                scheduled_date=data.get("scheduled_date") or now.date(),
                start_time=data.get("start_time") or now.time().replace(microsecond=0),
                duration_minutes=data.get("expected_duration_minutes"),
                quoted_price=data.get("quoted_price"),
                notes=data.get("notes", ""),
            )
        except BookingError as exc:
            return _booking_error_response(exc)

        return Response(
            {
                "booking": BookingSerializer(booking, context={"request": request}).data,
                "payment": PaymentSerializer(payment).data,
                "message": (
                    "Pay the emergency fee to send this to available workers."
                ),
            },
            status=status.HTTP_201_CREATED,
        )


@extend_schema(
    tags=["Bookings"],
    summary="Live emergency state for the caller (poll this)",
)
class EmergencyLiveView(APIView):
    """Both dashboards' refresh endpoint, and deliberately the smallest one.

    ---------------------------------------------------------------------------
    THIS IS WHAT "REAL TIME" MEANS ON THIS DEPLOYMENT
    ---------------------------------------------------------------------------
    There is no Channels, no Redis and no second process to run them in
    (docs/free-tier-constraints.md §7), so there is no socket to push a claimed
    job down. What there is instead is this: a response small enough that both
    sides can ask for it every few seconds, asked for *only* while a request is
    actually in flight, and not at all otherwise.

    That bounds the cost to the few minutes an emergency is live, which is the
    only window in which anybody needs second-level freshness. A worker's
    dashboard stops polling the moment she has no open offer; a resident's stops
    the moment somebody has accepted.

    The response carries ``version`` — a monotonic stamp over the rows involved
    — so the client can skip a rebuild when nothing has moved.
    """

    permission_classes = [IsEngagementParty]

    def get(self, request):
        user = request.user

        # Both read paths trigger the lapse sweep. Whoever is watching pays for
        # it, which is the free tier's substitute for a scheduler.
        emergency_service.expire_unclaimed(society_id=getattr(user, "society_id", None))

        if user.role == Role.WORKER:
            return Response(self._for_worker(request))
        return Response(self._for_resident(request))

    def _for_worker(self, request) -> dict:
        profile, error = _worker_profile_or_error(request)
        if profile is None:
            return {"role": "worker", "offers": [], "version": ""}

        offers = (
            BookingOffer.objects.filter(worker=profile, state=OfferState.OFFERED)
            .filter(booking__status=BookingStatus.BROADCAST)
            .select_related(
                "booking__category", "booking__resident__flat__tower"
            )
            .order_by("rank")
        )
        rows = BookingOfferSerializer(offers, many=True).data
        return {
            "role": "worker",
            "offers": rows,
            "count": len(rows),
            "version": self._version(offers),
        }

    def _for_resident(self, request) -> dict:
        """Every emergency this household has open, and who has taken it.

        Includes freshly-claimed ones as well as still-open ones: the single
        most important update a waiting resident can receive is "Sunita is on
        her way", and a view that only returned unclaimed requests would drop
        the request from the response at exactly the moment it mattered most.
        """
        watched = list(EMERGENCY_OPEN_STATUSES) + [BookingStatus.CONFIRMED]
        bookings = (
            Booking.objects.filter(resident__user=request.user, status__in=watched)
            .filter(category__bypasses_notice_period=True)
            .select_related("worker__user", "category", "resident__flat__tower")
            .order_by("-created_at")[:10]
        )
        rows = BookingSerializer(
            bookings, many=True, context={"request": request}
        ).data
        return {
            "role": "resident",
            "requests": rows,
            "count": len(rows),
            "version": self._version(bookings),
        }

    @staticmethod
    def _version(rows) -> str:
        """A cheap change stamp: the latest ``updated_at`` across the rows.

        Enough for the client to skip a rebuild, and it costs nothing extra —
        the rows have already been fetched by the time this runs.
        """
        stamps = [getattr(row, "updated_at", None) for row in rows]
        stamps = [stamp for stamp in stamps if stamp is not None]
        return max(stamps).isoformat() if stamps else ""


@extend_schema(
    tags=["Bookings"],
    summary="Emergency requests offered to this worker",
    responses=BookingOfferSerializer(many=True),
)
class MyEmergencyOffersView(generics.ListAPIView):
    """The maid's side of a broadcast.

    Open offers only, and only while the booking behind them is still up for
    grabs. A job somebody else claimed a second ago disappears from here on the
    next poll, which is the behaviour the flow calls for.
    """

    serializer_class = BookingOfferSerializer
    permission_classes = [IsApprovedWorker]
    pagination_class = None
    queryset = BookingOffer.objects.none()  # declared for schema generation

    def get_queryset(self):
        emergency_service.expire_unclaimed(
            society_id=getattr(self.request.user, "society_id", None)
        )
        profile = WorkerProfile.objects.filter(user=self.request.user).first()
        if profile is None:
            return BookingOffer.objects.none()

        return (
            BookingOffer.objects.filter(worker=profile, state=OfferState.OFFERED)
            .filter(booking__status=BookingStatus.BROADCAST)
            .select_related("booking__category", "booking__resident__flat__tower")
            .order_by("rank")
        )


@extend_schema(
    tags=["Bookings"],
    summary="Claim an emergency request",
    request=None,
    responses=BookingSerializer,
)
class AcceptEmergencyView(APIView):
    """The race. See ``emergency.accept_offer`` for how it is decided.

    ``pk`` is the **booking** id, not the offer id: that is what the push
    notification carries, and asking the app to hold a second identifier for the
    same thing would be one more thing to get wrong at the moment it matters.
    """

    permission_classes = [IsApprovedWorker]
    serializer_class = BookingSerializer

    def post(self, request, pk):
        profile, error = _worker_profile_or_error(request)
        if error:
            return error

        try:
            booking = emergency_service.accept_offer(booking_id=pk, worker=profile)
        except BookingError as exc:
            return _booking_error_response(exc)

        return Response(
            {
                "booking": BookingSerializer(booking, context={"request": request}).data,
                "message": "This job is yours. The household has been told.",
            }
        )


@extend_schema(
    tags=["Bookings"],
    summary="Pass on an emergency request",
    request=None,
    responses=BookingOfferSerializer,
)
class DeclineEmergencyView(APIView):
    """Declining removes the card from this worker's dashboard and nothing else.

    It never withdraws the request from anybody else — a broadcast that shrank
    every time somebody passed would fail slowly instead of succeeding fast.
    """

    permission_classes = [IsApprovedWorker]
    serializer_class = BookingOfferSerializer

    def post(self, request, pk):
        profile, error = _worker_profile_or_error(request)
        if error:
            return error

        try:
            offer = emergency_service.decline_offer(booking_id=pk, worker=profile)
        except BookingError as exc:
            return _booking_error_response(exc)

        return Response({"offer": BookingOfferSerializer(offer).data})


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
