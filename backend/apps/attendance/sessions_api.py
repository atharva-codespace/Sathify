"""
Module 7.7 — the worker's and resident's view of work sessions.

Separate from ``views.py`` because that file is the *gate*: a guard's scanner, a
roster, an offline sync queue. This is the household's side of the same day —
which flat, which hours, what it costs — and the two have different callers,
different permissions and different failure modes.

-------------------------------------------------------------------------------
NOTHING HERE CAN COST A WORKER HER DAY
-------------------------------------------------------------------------------
Every failure mode in this file resolves in the worker's favour, because the
alternative is a wage lost to a bug she cannot see and cannot argue with:

* Starting a session never fails on a geofence. If the location does not check
  out, the session opens anyway at a lower capture tier and is flagged.
* Stopping twice is a no-op, not an error.
* A session the nightly job closed is presented to her as a yes/no question
  about her own day, not as a correction she must dispute.
"""

from __future__ import annotations

import datetime as dt

from django.db import IntegrityError, transaction
from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework import serializers, status
from rest_framework.generics import ListAPIView
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.permissions import IsApprovedResident, IsApprovedUser, IsApprovedWorker
from apps.hiring.models import Engagement, EngagementStatus, RateBasis
from apps.payments.hourly import SessionTiming, price_session

from .models import SessionSource, SessionStatus, WorkSession

#: How far a check-in may sit from the society's coordinates and still count as
#: tier 1. Generous — a phone's first fix indoors is routinely 100m out, and the
#: cost of being strict lands entirely on the worker.
GEOFENCE_METRES = 300


# ---------------------------------------------------------------------------
# Serializers
# ---------------------------------------------------------------------------


class WorkSessionSerializer(serializers.ModelSerializer):
    """One flat's day, as both apps render it."""

    flat = serializers.SerializerMethodField()
    resident_name = serializers.SerializerMethodField()
    worker_name = serializers.SerializerMethodField()
    scheduled_start = serializers.SerializerMethodField()
    scheduled_end = serializers.SerializerMethodField()
    tier = serializers.IntegerField(read_only=True)
    total_paise = serializers.IntegerField(read_only=True)
    #: Whether extra time is worth offering on this screen at all.
    can_request_overtime = serializers.SerializerMethodField()

    class Meta:
        model = WorkSession
        fields = [
            "id", "engagement", "visit_date", "started_at", "ended_at",
            "source", "tier", "status", "needs_review", "review_note",
            "flat", "resident_name", "worker_name",
            "scheduled_start", "scheduled_end",
            "approved_ot_minutes", "billable_minutes", "overtime_minutes",
            "unbilled_extra_minutes", "time_paise", "overtime_paise",
            "visit_fee_paise", "total_paise", "priced_at",
            "can_request_overtime",
        ]
        read_only_fields = fields

    def _timing(self, obj) -> SessionTiming:
        return SessionTiming.for_engagement(obj.engagement)

    def get_flat(self, obj) -> str:
        flat = getattr(obj.engagement.resident, "flat", None)
        return str(flat) if flat else ""

    def get_resident_name(self, obj) -> str:
        return obj.engagement.resident.user.get_full_name()

    def get_worker_name(self, obj) -> str:
        return obj.worker.user.get_full_name()

    def get_scheduled_start(self, obj) -> str:
        return self._timing(obj).arrival.isoformat()

    def get_scheduled_end(self, obj) -> str:
        return self._timing(obj).departure.isoformat()

    def get_can_request_overtime(self, obj) -> bool:
        return obj.status == SessionStatus.OPEN and obj.engagement.rate_basis == RateBasis.HOURLY


class StartSessionSerializer(serializers.Serializer):
    """Starting a visit.

    ``id`` is supplied by the client, not the server. Same reason
    ``AttendanceEvent`` does it: she taps Start in a stairwell with no signal,
    and replaying the queued write on reconnect must not open a second session.
    """

    id = serializers.UUIDField(required=False)
    engagement = serializers.IntegerField()
    latitude = serializers.FloatField(required=False, allow_null=True)
    longitude = serializers.FloatField(required=False, allow_null=True)
    started_at = serializers.DateTimeField(required=False)


class StopSessionSerializer(serializers.Serializer):
    ended_at = serializers.DateTimeField(required=False)


class RequestOvertimeSerializer(serializers.Serializer):
    minutes = serializers.IntegerField(min_value=5, max_value=240)


class ApproveOvertimeSerializer(serializers.Serializer):
    minutes = serializers.IntegerField(min_value=0, max_value=240)


class ConfirmSessionSerializer(serializers.Serializer):
    """Her answer to "we closed this for you — is that right?"."""

    correct = serializers.BooleanField()
    note = serializers.CharField(max_length=300, required=False, allow_blank=True)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sessions_for(user):
    """Every session this user is a party to, and no others.

    A worker sees her own; a resident sees the sessions worked in their flat.
    Nobody sees a third party's, which is why this is one function rather than a
    filter written slightly differently in each view.
    """
    queryset = WorkSession.objects.select_related(
        "engagement", "engagement__resident", "engagement__resident__user",
        "engagement__resident__flat", "worker", "worker__user", "society",
    )
    if user.is_worker:
        return queryset.filter(worker__user=user)
    if user.is_resident:
        return queryset.filter(engagement__resident__user=user)
    if user.is_society_admin:
        return queryset.filter(society_id=user.society_id)
    return queryset.none()


def _capture_tier(engagement, latitude, longitude) -> tuple[str, bool]:
    """Decide the capture tier from what the device managed to tell us.

    Returns ``(source, needs_review)``. A missing or distant fix is **never** a
    refusal — it lowers the tier and raises a flag, and she starts work. The
    cost of a false rejection here is a day's pay for a GPS glitch, and that is
    not a trade this product makes.
    """
    if latitude is None or longitude is None:
        return SessionSource.RESIDENT_CONFIRM, True

    society = engagement.society
    origin_lat = getattr(society, "latitude", None)
    origin_lng = getattr(society, "longitude", None)
    if origin_lat is None or origin_lng is None:
        # The society never recorded coordinates. That is our gap, not hers, so
        # the reading is accepted at face value.
        return SessionSource.SELF, False

    # Equirectangular approximation. At city scale the error is metres, and the
    # threshold is already generous enough that precision here is theatre.
    from math import cos, radians, sqrt

    mean_lat = radians((float(origin_lat) + latitude) / 2)
    dx = (longitude - float(origin_lng)) * 111_320 * cos(mean_lat)
    dy = (latitude - float(origin_lat)) * 110_574
    distance = sqrt(dx * dx + dy * dy)

    if distance <= GEOFENCE_METRES:
        return SessionSource.SELF, False
    return SessionSource.RESIDENT_CONFIRM, True


# ---------------------------------------------------------------------------
# Views
# ---------------------------------------------------------------------------


class MySessionsView(ListAPIView):
    """The day's sessions for whoever is asking.

    ``?date=`` for one day (the worker's Today screen), ``?from=``/``?to=`` for
    a range (the month view and the resident's history).
    """

    permission_classes = [IsApprovedUser]
    serializer_class = WorkSessionSerializer
    pagination_class = None

    def get_queryset(self):
        queryset = _sessions_for(self.request.user)
        params = self.request.query_params

        if params.get("date"):
            return queryset.filter(visit_date=dt.date.fromisoformat(params["date"]))
        if params.get("from"):
            queryset = queryset.filter(visit_date__gte=dt.date.fromisoformat(params["from"]))
        if params.get("to"):
            queryset = queryset.filter(visit_date__lte=dt.date.fromisoformat(params["to"]))
        if params.get("engagement"):
            queryset = queryset.filter(engagement_id=params["engagement"])
        if params.get("needs_review") == "true":
            queryset = queryset.filter(needs_review=True)
        return queryset


class TodayScreenView(APIView):
    """Everything the worker's Today screen needs, in one call.

    Deliberately one request rather than "list engagements, then list sessions,
    then reconcile them on the client". The screen is a stack of flats and each
    card's state depends on both, so assembling it here means the phone renders
    one payload instead of resolving a join over a patchy connection.
    """

    permission_classes = [IsApprovedWorker]

    @extend_schema(responses={200: dict})
    def get(self, request):
        day = request.query_params.get("date")
        day = dt.date.fromisoformat(day) if day else timezone.localdate()

        worker = request.user.worker_profile
        engagements = [
            engagement
            for engagement in Engagement.objects.filter(
                worker=worker, status=EngagementStatus.ACTIVE
            ).select_related("resident", "resident__user", "resident__flat", "society")
            if engagement.occurs_on(day)
        ]

        sessions = {
            session.engagement_id: session
            for session in _sessions_for(request.user).filter(visit_date=day)
        }

        cards, earned, minutes, done = [], 0, 0, 0
        for engagement in engagements:
            session = sessions.get(engagement.id)
            timing = SessionTiming.for_engagement(engagement)
            if session is not None and session.priced_at:
                earned += session.total_paise
                minutes += session.billable_minutes + session.overtime_minutes
            if session is not None and session.status in {
                SessionStatus.CLOSED, SessionStatus.AUTO_CLOSED
            }:
                done += 1

            cards.append({
                "engagement": engagement.id,
                "flat": str(getattr(engagement.resident, "flat", "")),
                "resident_name": engagement.resident.user.get_full_name(),
                "scheduled_start": timing.arrival.isoformat(),
                "scheduled_end": timing.departure.isoformat(),
                "is_hourly": engagement.rate_basis == RateBasis.HOURLY,
                "hourly_rate": engagement.hourly_rate,
                "visit_fee": engagement.visit_fee,
                "session": WorkSessionSerializer(session).data if session else None,
            })

        cards.sort(key=lambda card: card["scheduled_start"])
        return Response({
            "date": day.isoformat(),
            "earned_paise": earned,
            "billed_minutes": minutes,
            "flats_total": len(cards),
            "flats_done": done,
            "cards": cards,
        })


class StartSessionView(APIView):
    """Open a session. Idempotent on the client-generated id."""

    permission_classes = [IsApprovedWorker]

    @extend_schema(request=StartSessionSerializer, responses={201: WorkSessionSerializer})
    def post(self, request):
        serializer = StartSessionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        worker = request.user.worker_profile
        engagement = Engagement.objects.filter(
            pk=data["engagement"], worker=worker, status=EngagementStatus.ACTIVE
        ).select_related("society").first()
        if engagement is None:
            return Response({"detail": "No active engagement of yours with that id."}, status=404)

        day = timezone.localdate()
        existing = WorkSession.objects.filter(engagement=engagement, visit_date=day).first()
        if existing is not None:
            # Already open, or the resident's scan beat her phone to it. Both are
            # the same answer: this day is under way, here it is.
            return Response(WorkSessionSerializer(existing).data, status=status.HTTP_200_OK)

        source, needs_review = _capture_tier(
            engagement, data.get("latitude"), data.get("longitude")
        )
        try:
            with transaction.atomic():
                session = WorkSession.objects.create(
                    id=data.get("id") or None,
                    society=engagement.society,
                    engagement=engagement,
                    worker=worker,
                    visit_date=day,
                    started_at=data.get("started_at") or timezone.now(),
                    source=source,
                    status=SessionStatus.OPEN,
                    opened_by=request.user,
                    needs_review=needs_review,
                    review_note=(
                        "Location could not be confirmed. Ask the resident to confirm."
                        if needs_review else ""
                    ),
                )
        except IntegrityError:
            # Two devices raced. The row that won is the answer.
            session = WorkSession.objects.get(engagement=engagement, visit_date=day)
            return Response(WorkSessionSerializer(session).data, status=status.HTTP_200_OK)

        return Response(WorkSessionSerializer(session).data, status=status.HTTP_201_CREATED)


class _SessionActionView(APIView):
    permission_classes = [IsApprovedUser]

    def get_session(self, request, pk):
        return _sessions_for(request.user).filter(pk=pk).first()


class StopSessionView(_SessionActionView):
    permission_classes = [IsApprovedWorker]

    @extend_schema(request=StopSessionSerializer, responses={200: WorkSessionSerializer})
    def post(self, request, pk):
        session = self.get_session(request, pk)
        if session is None:
            return Response({"detail": "No such session."}, status=404)

        serializer = StopSessionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        # Stopping twice is a no-op rather than an error: a flaky connection
        # makes a double tap the normal case, not the exceptional one.
        session.close(at=serializer.validated_data.get("ended_at"), by=request.user)
        price_session(session)
        return Response(WorkSessionSerializer(session).data)


class RequestOvertimeView(_SessionActionView):
    """She asks; the resident answers. Nothing is billed on this call.

    Framed as a request rather than a declaration because unapproved extra time
    is not paid, and an app that let her log it without telling her that would
    be quietly arranging for her to work for free.
    """

    permission_classes = [IsApprovedWorker]

    @extend_schema(request=RequestOvertimeSerializer, responses={200: dict})
    def post(self, request, pk):
        session = self.get_session(request, pk)
        if session is None:
            return Response({"detail": "No such session."}, status=404)

        serializer = RequestOvertimeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        minutes = serializer.validated_data["minutes"]

        from apps.notifications.models import NotificationCategory
        from apps.notifications.services import notify

        # `notify` never raises and returns None on failure, so there is no
        # try/except here on purpose: a push that does not land must not stop
        # her asking, and swallowing an exception that cannot occur would only
        # hide a real one later.
        notify(
            recipient=session.engagement.resident.user,
            category=NotificationCategory.ATTENDANCE,
            title=f"{session.worker.user.get_full_name()} is asking to stay longer",
            body=f"{minutes} more minutes at {session.engagement.resident.flat}.",
            data={"session": str(session.pk), "minutes": minutes, "kind": "overtime_request"},
            society=session.society,
        )

        return Response({
            "requested_minutes": minutes,
            "approved_minutes": session.approved_ot_minutes,
            "note": "Extra time is only paid once the resident approves it.",
        })


class ApproveOvertimeView(_SessionActionView):
    """The resident's answer. This is the only thing that makes OT billable."""

    permission_classes = [IsApprovedResident]

    @extend_schema(request=ApproveOvertimeSerializer, responses={200: WorkSessionSerializer})
    def post(self, request, pk):
        session = self.get_session(request, pk)
        if session is None:
            return Response({"detail": "No such session."}, status=404)
        if session.priced_at is not None:
            return Response(
                {"detail": "This visit has already been priced and cannot be changed."},
                status=status.HTTP_409_CONFLICT,
            )

        serializer = ApproveOvertimeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        session.approved_ot_minutes = serializer.validated_data["minutes"]
        session.save(update_fields=["approved_ot_minutes", "updated_at"])
        return Response(WorkSessionSerializer(session).data)


class ConfirmSessionView(_SessionActionView):
    """Her answer about a session the nightly job closed for her.

    A yes clears the flag. A no keeps it raised and hands it to the society
    admin — she is not asked to compute a correction, only to say the record is
    wrong, which is the only part she can actually know.
    """

    permission_classes = [IsApprovedWorker]

    @extend_schema(request=ConfirmSessionSerializer, responses={200: WorkSessionSerializer})
    def post(self, request, pk):
        session = self.get_session(request, pk)
        if session is None:
            return Response({"detail": "No such session."}, status=404)

        serializer = ConfirmSessionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        if data["correct"]:
            session.needs_review = False
            session.review_note = ""
        else:
            session.needs_review = True
            session.review_note = (
                data.get("note") or "The worker says this record is wrong."
            )[:300]
        session.save(update_fields=["needs_review", "review_note", "updated_at"])
        return Response(WorkSessionSerializer(session).data)
