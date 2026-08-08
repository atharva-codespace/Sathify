"""
Module 6 — Scheduling & Task Management: API views.

Endpoint map (mounted at /api/v1/scheduling/)::

    GET  me/today/                  the caller's own day                   (6.1)
    GET  me/agenda/?from=&to=       the caller's own date range            (6.1)
    GET  workers/<id>/agenda/       one worker's schedule (admin)          (6.1)
    GET  society/agenda/            every visit in the society (admin)     (6.1)

    GET  timing/<engagement_id>/    arrival/departure expectations         (6.2)
    PUT  timing/<engagement_id>/    set them (resident)

    GET  conflicts/check/           would this slot collide?               (6.3)

    GET  reminders/due/             reminders ready to deliver             (6.4)
    POST reminders/<id>/delivered/  Module 10 reports back

``me/`` resolves by role: a worker gets their own commitments, a resident gets
their household's. One endpoint rather than two so the client does not have to
branch on role before it can render a home screen.
"""

from __future__ import annotations

import datetime as dt
import logging

from django.db.models import Q
from django.utils import timezone
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import generics, status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import Role
from apps.accounts.permissions import (
    IsApprovedSocietyAdmin,
    IsEngagementParty,
    IsSocietyAdmin,
)
from apps.hiring.models import Engagement
from apps.societies.services import primary_resident_or_403
from apps.workers.models import WorkerProfile

from .models import LeaveRequest, Reminder, TaskTiming
from .schedule import (
    MAX_SCHEDULE_DAYS,
    ScheduleRangeTooWide,
    find_overlaps,
    resident_schedule,
    society_schedule,
    worker_schedule,
)
from .serializers import (
    ConflictQuerySerializer,
    ConflictReportSerializer,
    LeaveCreateSerializer,
    LeaveRequestSerializer,
    LeaveResponseSerializer,
    ReminderDeliverySerializer,
    ReminderSerializer,
    ReplacementAssignSerializer,
    ReplacementCandidateSerializer,
    ScheduleConflictPairSerializer,
    ScheduleItemSerializer,
    TaskTimingSerializer,
    TaskTimingWriteSerializer,
)
from .services import (
    DuplicateLeave,
    LeaveError,
    assign_replacement,
    check_conflict,
    close_lapsed_leave,
    due_reminders,
    effective_timing,
    ensure_reminders_for_worker,
    replacement_candidates,
    request_leave,
    respond_to_leave,
    withdraw_leave,
)

logger = logging.getLogger(__name__)


def _error(code: str, message: str, http_status: int, details: dict | None = None):
    """The platform-standard error envelope (apps/core/exceptions.py)."""
    return Response(
        {"error": {"code": code, "message": message, "details": details or {}}},
        status=http_status,
    )


def _requested_range(request, *, default_days: int = 7):
    """Parse ``?from=``/``?to=``, defaulting to a week from today."""
    today = timezone.localdate()

    start = request.query_params.get("from")
    end = request.query_params.get("to")

    start_date = dt.date.fromisoformat(start) if start else today
    end_date = (
        dt.date.fromisoformat(end)
        if end
        else start_date + dt.timedelta(days=default_days - 1)
    )
    return start_date, end_date


def _schedule_for_caller(user, start: dt.date, end: dt.date):
    """The caller's own schedule, from whichever side they are on."""
    if user.role == Role.WORKER:
        profile = WorkerProfile.objects.filter(user=user).first()
        if profile is None:
            return []
        return worker_schedule(profile.pk, start, end)

    if user.role == Role.RESIDENT:
        from apps.societies.models import Resident

        resident = Resident.objects.filter(user=user).first()
        if resident is None:
            return []
        return resident_schedule(resident.pk, start, end)

    if user.is_society_admin and user.society_id is not None:
        return society_schedule(user.society_id, start, end)

    return []


@extend_schema(
    tags=["Scheduling"],
    summary="Your schedule for today",
    responses=ScheduleItemSerializer(many=True),
)
class MyTodayView(APIView):
    """Module 6.1 — one true schedule for today, across both systems.

    A worker seeing their recurring visits and one-day jobs in one list is the
    whole point of this module; two separate screens is the thing it exists to
    remove.
    """

    permission_classes = [IsEngagementParty | IsApprovedSocietyAdmin]
    serializer_class = ScheduleItemSerializer

    def get(self, request):
        today = timezone.localdate()
        items = _schedule_for_caller(request.user, today, today)

        # Module 6.4 — generation is lazy for the same reason expiry is: there
        # is no scheduler on the free tier. Opening the app is the trigger.
        if request.user.role == Role.WORKER:
            profile = WorkerProfile.objects.filter(user=request.user).first()
            if profile is not None:
                ensure_reminders_for_worker(profile)

        return Response(
            {
                "date": today,
                "count": len(items),
                "results": ScheduleItemSerializer(items, many=True).data,
            }
        )


@extend_schema(
    tags=["Scheduling"],
    summary="Your schedule over a date range",
    parameters=[
        OpenApiParameter("from", str, description="YYYY-MM-DD, defaults to today"),
        OpenApiParameter("to", str, description=f"YYYY-MM-DD, at most {MAX_SCHEDULE_DAYS} days"),
    ],
    responses=ScheduleItemSerializer(many=True),
)
class MyAgendaView(APIView):
    permission_classes = [IsEngagementParty | IsApprovedSocietyAdmin]
    serializer_class = ScheduleItemSerializer

    def get(self, request):
        try:
            start, end = _requested_range(request)
            items = _schedule_for_caller(request.user, start, end)
        except ValueError as exc:
            return _error("validation_error", str(exc), status.HTTP_400_BAD_REQUEST)

        return Response(
            {
                "from": start,
                "to": end,
                "count": len(items),
                "results": ScheduleItemSerializer(items, many=True).data,
            }
        )


@extend_schema(
    tags=["Scheduling"],
    summary="One worker's schedule (administrators)",
    parameters=[
        OpenApiParameter("from", str, description="YYYY-MM-DD"),
        OpenApiParameter("to", str, description="YYYY-MM-DD"),
    ],
    responses=ScheduleItemSerializer(many=True),
)
class WorkerAgendaView(APIView):
    """Also surfaces existing double-bookings.

    Module 6.3 stops new ones, but rows created before the check existed — or
    through the admin — still need to be visible rather than silently wrong.
    """

    permission_classes = [IsSocietyAdmin]
    serializer_class = ScheduleItemSerializer

    def get(self, request, worker_id):
        worker = WorkerProfile.objects.filter(
            pk=worker_id, user__society_id=request.user.society_id
        ).first()
        if worker is None:
            return _error("not_found", "Worker not found.", status.HTTP_404_NOT_FOUND)

        try:
            start, end = _requested_range(request)
            items = worker_schedule(worker.pk, start, end)
        except (ValueError, ScheduleRangeTooWide) as exc:
            return _error("validation_error", str(exc), status.HTTP_400_BAD_REQUEST)

        clashes = [
            {"first": first, "second": second} for first, second in find_overlaps(items)
        ]

        return Response(
            {
                "from": start,
                "to": end,
                "count": len(items),
                "results": ScheduleItemSerializer(items, many=True).data,
                "conflicts": ScheduleConflictPairSerializer(clashes, many=True).data,
            }
        )


@extend_schema(
    tags=["Scheduling"],
    summary="Every expected visit in the society (administrators)",
    responses=ScheduleItemSerializer(many=True),
)
class SocietyAgendaView(APIView):
    """Feeds the gate roster Module 7 will need: who is expected, and when."""

    permission_classes = [IsSocietyAdmin]
    serializer_class = ScheduleItemSerializer

    def get(self, request):
        if request.user.society_id is None:
            return _error(
                "no_society",
                "You are not attached to a society yet.",
                status.HTTP_400_BAD_REQUEST,
            )

        try:
            start, end = _requested_range(request, default_days=1)
            items = society_schedule(request.user.society_id, start, end)
        except (ValueError, ScheduleRangeTooWide) as exc:
            return _error("validation_error", str(exc), status.HTTP_400_BAD_REQUEST)

        return Response(
            {
                "from": start,
                "to": end,
                "count": len(items),
                "results": ScheduleItemSerializer(items, many=True).data,
            }
        )


# ---------------------------------------------------------------------------
# 6.2 Task timing
# ---------------------------------------------------------------------------


@extend_schema(
    tags=["Scheduling"],
    summary="Read or set an engagement's arrival and departure expectations",
    request=TaskTimingWriteSerializer,
    responses=TaskTimingSerializer,
)
class TaskTimingView(APIView):
    """Module 6.2.

    ``PUT`` upserts, because there is exactly one timing per engagement and a
    resident adjusting it twice on a flaky connection must converge rather than
    fail the second time.

    Both parties may read it — a worker needs to know what is expected of them —
    but only the flat's primary account holder may set it, the same rule that
    governs hiring and booking (Module 2.4).
    """

    permission_classes = [IsEngagementParty | IsApprovedSocietyAdmin]
    serializer_class = TaskTimingSerializer

    def _engagement_or_none(self, request, engagement_id):
        queryset = Engagement.objects.select_related(
            "resident__user", "worker__user"
        ).filter(pk=engagement_id)

        user = request.user
        if user.is_superuser:
            return queryset.first()
        if user.role == Role.RESIDENT:
            return queryset.filter(resident__user=user).first()
        if user.role == Role.WORKER:
            return queryset.filter(worker__user=user).first()
        if user.is_society_admin and user.society_id is not None:
            return queryset.filter(society_id=user.society_id).first()
        return None

    def get(self, request, engagement_id):
        engagement = self._engagement_or_none(request, engagement_id)
        if engagement is None:
            return _error("not_found", "Engagement not found.", status.HTTP_404_NOT_FOUND)

        return Response(TaskTimingSerializer(effective_timing(engagement)).data)

    def put(self, request, engagement_id):
        engagement = self._engagement_or_none(request, engagement_id)
        if engagement is None:
            return _error("not_found", "Engagement not found.", status.HTTP_404_NOT_FOUND)

        if request.user.role == Role.WORKER:
            return _error(
                "permission_denied",
                "The resident sets the expected times for a visit.",
                status.HTTP_403_FORBIDDEN,
            )
        if request.user.role == Role.RESIDENT:
            primary_resident_or_403(request.user)

        existing = TaskTiming.objects.filter(engagement=engagement).first()
        serializer = TaskTimingWriteSerializer(existing, data=request.data, partial=bool(existing))
        serializer.is_valid(raise_exception=True)
        serializer.save(engagement=engagement, updated_by=request.user)

        engagement.refresh_from_db()
        return Response(
            {
                "timing": TaskTimingSerializer(effective_timing(engagement)).data,
                "message": "Expected times updated.",
            },
            status=status.HTTP_200_OK if existing else status.HTTP_201_CREATED,
        )


# ---------------------------------------------------------------------------
# 6.3 Conflict detection
# ---------------------------------------------------------------------------


@extend_schema(
    tags=["Scheduling"],
    summary="Would this slot collide with an existing commitment?",
    parameters=[
        OpenApiParameter("worker", int, required=True),
        OpenApiParameter("date", str, required=True, description="YYYY-MM-DD"),
        OpenApiParameter("start_time", str, required=True, description="HH:MM"),
        OpenApiParameter("duration_minutes", int, required=True),
        OpenApiParameter("exclude_booking", int, description="Ignore this booking"),
    ],
    responses=ConflictReportSerializer,
)
class ConflictCheckView(APIView):
    """Module 6.3, as a pre-flight check.

    The authoritative check still runs inside ``bookings.services.create_booking``
    under a row lock — this one lets the app warn before the resident fills in a
    whole form, and lets an administrator inspect a clash rather than only be
    refused by it.
    """

    permission_classes = [IsEngagementParty | IsApprovedSocietyAdmin]
    serializer_class = ConflictReportSerializer

    def get(self, request):
        query = ConflictQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        data = query.validated_data

        worker = WorkerProfile.objects.filter(
            pk=data["worker"], user__society_id=request.user.society_id
        ).first()
        if worker is None:
            return _error("not_found", "Worker not found.", status.HTTP_404_NOT_FOUND)

        report = check_conflict(
            worker.pk,
            on_date=data["date"],
            start_time=data["start_time"],
            duration_minutes=data["duration_minutes"],
            exclude_booking_id=data.get("exclude_booking"),
        )

        return Response(
            {
                "has_conflict": report.has_conflict,
                "summary": report.summary,
                "clashes": ScheduleItemSerializer(report.clashes, many=True).data,
            }
        )


# ---------------------------------------------------------------------------
# 6.4 Reminders
# ---------------------------------------------------------------------------


@extend_schema(tags=["Scheduling"], summary="Reminders ready to be delivered")
class DueRemindersView(generics.ListAPIView):
    """Module 6.4 — the queue Module 10 drains.

    A worker or resident sees only their own; an administrator sees their
    society's, which is what an external pinger would authenticate as to drive
    delivery without a scheduler.
    """

    serializer_class = ReminderSerializer
    permission_classes = [IsEngagementParty | IsApprovedSocietyAdmin]
    queryset = Reminder.objects.none()  # declared for schema generation
    pagination_class = None

    def get_queryset(self):
        user = self.request.user
        if user.is_society_admin and user.society_id is not None:
            return due_reminders(society_id=user.society_id)
        return due_reminders(recipient=user)


@extend_schema(
    tags=["Scheduling"],
    summary="Report a reminder as delivered or failed",
    request=ReminderDeliverySerializer,
    responses=ReminderSerializer,
)
class ReminderDeliveredView(APIView):
    """Module 10 reports back after attempting delivery. Idempotent."""

    permission_classes = [IsEngagementParty | IsApprovedSocietyAdmin]
    serializer_class = ReminderDeliverySerializer

    def post(self, request, pk):
        user = request.user
        queryset = Reminder.objects.all()
        if user.is_society_admin and user.society_id is not None:
            queryset = queryset.filter(society_id=user.society_id)
        else:
            queryset = queryset.filter(recipient=user)

        reminder = queryset.filter(pk=pk).first()
        if reminder is None:
            return _error("not_found", "Reminder not found.", status.HTTP_404_NOT_FOUND)

        serializer = ReminderDeliverySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        if serializer.validated_data["delivered"]:
            reminder.mark_sent()
            message = "Reminder marked sent."
        else:
            reminder.mark_failed(serializer.validated_data.get("failure_reason", ""))
            message = "Reminder marked failed."

        return Response({"reminder": ReminderSerializer(reminder).data, "message": message})


# ---------------------------------------------------------------------------
# 6.5 Urgent leave ("chutti")
# ---------------------------------------------------------------------------


def _visible_leave(user):
    """Leave requests this caller is entitled to see.

    Scoped by relationship, not by society alone: a resident sees leave for
    their own engagements, a worker sees their own days off *and* the days they
    have agreed to cover, and an administrator sees their society's. Anyone
    else sees nothing.
    """
    from apps.societies.models import Resident

    base = LeaveRequest.objects.select_related(
        "worker__user",
        "replacement__user",
        "engagement__resident__user",
        "engagement__resident__flat__tower",
    )

    if user.role == Role.WORKER:
        profile = WorkerProfile.objects.filter(user=user).first()
        if profile is None:
            return LeaveRequest.objects.none()
        return base.filter(Q(worker=profile) | Q(replacement=profile)).distinct()

    if user.role == Role.RESIDENT:
        from apps.societies.models import Resident as _Resident

        resident = _Resident.objects.filter(user=user).first()
        if resident is None:
            return LeaveRequest.objects.none()
        return base.filter(engagement__resident=resident)

    if user.is_society_admin and user.society_id is not None:
        return base.filter(society_id=user.society_id)

    return LeaveRequest.objects.none()


@extend_schema(
    tags=["Scheduling"],
    summary="Apply for urgent leave, or list your leave",
    request=LeaveCreateSerializer,
    responses=LeaveRequestSerializer,
)
class LeaveListCreateView(APIView):
    """Module 6.5 - a worker takes a day off, and it is approved immediately.

    There is no approval step and no administrator in the path. See
    ``LeaveRequest`` for why instant approval is the mechanism rather than a
    convenience: it is what buys the household enough notice to plan.
    """

    permission_classes = [IsEngagementParty | IsApprovedSocietyAdmin]
    serializer_class = LeaveRequestSerializer

    def get(self, request):
        # Lazy sweep, as everywhere else in this module: no scheduler exists, so
        # opening the list is what closes days nobody answered for.
        close_lapsed_leave()

        queryset = _visible_leave(request.user)
        upcoming = request.query_params.get("upcoming")
        if upcoming and upcoming.lower() not in {"0", "false"}:
            queryset = queryset.filter(leave_date__gte=timezone.localdate())

        items = list(queryset[:100])
        return Response(
            {
                "count": len(items),
                "results": LeaveRequestSerializer(items, many=True).data,
            }
        )

    def post(self, request):
        profile = WorkerProfile.objects.filter(user=request.user).first()
        if profile is None:
            return _error(
                "not_a_worker",
                "Only the worker on an engagement can take leave from it.",
                status.HTTP_403_FORBIDDEN,
            )

        serializer = LeaveCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        engagement = (
            Engagement.objects.filter(pk=data["engagement"], worker=profile)
            .select_related("resident__user", "worker__user", "society")
            .first()
        )
        if engagement is None:
            return _error(
                "not_found",
                "That engagement was not found, or is not yours.",
                status.HTTP_404_NOT_FOUND,
            )

        try:
            leave = request_leave(
                engagement,
                leave_date=data["leave_date"],
                reason=data["reason"],
                by=request.user,
            )
        except DuplicateLeave as exc:
            return _error(exc.code, str(exc), status.HTTP_409_CONFLICT)
        except LeaveError as exc:
            return _error(exc.code, str(exc), status.HTTP_400_BAD_REQUEST)

        return Response(
            {
                "leave": LeaveRequestSerializer(leave).data,
                "message": (
                    "Your leave is approved. The household has been told and "
                    "will say whether they need cover."
                ),
            },
            status=status.HTTP_201_CREATED,
        )


@extend_schema(
    tags=["Scheduling"],
    summary="Say whether cover is needed",
    request=LeaveResponseSerializer,
    responses=LeaveRequestSerializer,
)
class LeaveResponseView(APIView):
    """The household's only decision here: do you need someone else that day?"""

    permission_classes = [IsEngagementParty | IsApprovedSocietyAdmin]
    serializer_class = LeaveResponseSerializer

    def post(self, request, pk):
        leave = _visible_leave(request.user).filter(pk=pk).first()
        if leave is None:
            return _error("not_found", "Leave request not found.", status.HTTP_404_NOT_FOUND)

        if request.user.role == Role.WORKER:
            return _error(
                "not_the_household",
                "Only the household can say whether they need cover.",
                status.HTTP_403_FORBIDDEN,
            )

        serializer = LeaveResponseSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            leave = respond_to_leave(
                leave,
                needs_replacement=serializer.validated_data["needs_replacement"],
                by=request.user,
            )
        except LeaveError as exc:
            return _error(exc.code, str(exc), status.HTTP_409_CONFLICT)

        return Response({"leave": LeaveRequestSerializer(leave).data})


@extend_schema(
    tags=["Scheduling"],
    summary="Workers who could cover this visit",
    responses=ReplacementCandidateSerializer(many=True),
)
class ReplacementCandidatesView(APIView):
    """Module 6.5 - who is free, ranked by Module 4.3's scorer."""

    permission_classes = [IsEngagementParty | IsApprovedSocietyAdmin]
    serializer_class = ReplacementCandidateSerializer

    def get(self, request, pk):
        leave = _visible_leave(request.user).filter(pk=pk).first()
        if leave is None:
            return _error("not_found", "Leave request not found.", status.HTTP_404_NOT_FOUND)

        ranked = replacement_candidates(leave)
        results = [
            {
                "worker_id": worker.pk,
                "name": worker.user.get_full_name(),
                "photo_url": worker.photo.url if worker.photo else "",
                "trust_score": float(worker.trust_score or 0),
                "average_rating": float(worker.average_rating or 0),
                "rating_count": getattr(worker, "rating_count", 0) or 0,
                "match_score": match.total,
                "match_percentage": match.percentage,
                "components": match.explain(),
            }
            for worker, match in ranked
        ]
        return Response({"count": len(results), "results": results})


@extend_schema(
    tags=["Scheduling"],
    summary="Confirm who is covering",
    request=ReplacementAssignSerializer,
    responses=LeaveRequestSerializer,
)
class AssignReplacementView(APIView):
    """Confirms the replacement and settles their pay in one transaction."""

    permission_classes = [IsEngagementParty | IsApprovedSocietyAdmin]
    serializer_class = ReplacementAssignSerializer

    def post(self, request, pk):
        leave = _visible_leave(request.user).filter(pk=pk).first()
        if leave is None:
            return _error("not_found", "Leave request not found.", status.HTTP_404_NOT_FOUND)

        if request.user.role == Role.WORKER:
            return _error(
                "not_the_household",
                "Only the household or an administrator can confirm cover.",
                status.HTTP_403_FORBIDDEN,
            )

        serializer = ReplacementAssignSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        replacement = (
            WorkerProfile.objects.filter(pk=serializer.validated_data["replacement"])
            .select_related("user")
            .first()
        )
        if replacement is None:
            return _error("not_found", "That worker was not found.", status.HTTP_404_NOT_FOUND)

        try:
            leave = assign_replacement(leave, replacement, by=request.user)
        except LeaveError as exc:
            return _error(exc.code, str(exc), status.HTTP_409_CONFLICT)

        return Response(
            {
                "leave": LeaveRequestSerializer(leave).data,
                "message": f"{replacement.user.get_full_name()} is covering this visit.",
            }
        )


@extend_schema(
    tags=["Scheduling"],
    summary="Withdraw leave",
    responses=LeaveRequestSerializer,
)
class WithdrawLeaveView(APIView):
    """The worker can come after all - refused once cover is confirmed."""

    permission_classes = [IsEngagementParty]
    serializer_class = LeaveRequestSerializer

    def post(self, request, pk):
        profile = WorkerProfile.objects.filter(user=request.user).first()
        if profile is None:
            return _error(
                "not_a_worker",
                "Only the worker who took the leave can withdraw it.",
                status.HTTP_403_FORBIDDEN,
            )

        leave = LeaveRequest.objects.filter(pk=pk, worker=profile).first()
        if leave is None:
            return _error("not_found", "Leave request not found.", status.HTTP_404_NOT_FOUND)

        try:
            leave = withdraw_leave(leave, by=request.user)
        except LeaveError as exc:
            return _error(exc.code, str(exc), status.HTTP_409_CONFLICT)

        return Response({"leave": LeaveRequestSerializer(leave).data})
