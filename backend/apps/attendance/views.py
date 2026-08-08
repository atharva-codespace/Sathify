"""
Module 7 — Attendance & Gate Verification: API views.

Endpoint map (mounted at /api/v1/attendance/)::

    GET  my-pass/               the worker's own QR payload             (7.1)
    POST my-pass/rotate/        reissue it (lost card)

    GET  roster/                the day's expected visits + pass codes  (7.2/7.4)
    POST scan/                  resolve a scanned code. Creates nothing (7.2)
    GET  events/                the audit trail                         (7.6)
    POST events/                log one decision                        (7.2/7.5/7.6)
    POST sync/                  replay an offline queue, idempotent     (7.4)

    POST events/<uuid>/face/    submit a live photo                     (7.3)
    POST events/<uuid>/resolve/ guard decides a pending face check      (7.3)

    GET  registers/             photographed paper registers            (7.5)
    POST registers/             upload one

-------------------------------------------------------------------------------
THE ROSTER IS THE SENSITIVE ONE
-------------------------------------------------------------------------------
``roster/`` returns gate pass codes — the things that open gates — for every
worker expected that day. It is restricted to gate staff of that society, and
that permission is doing real work rather than being decorative.
"""

from __future__ import annotations

import datetime as dt
import logging

from django.utils import timezone
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import generics, status
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import Role
from apps.accounts.permissions import IsGateStaff, IsSocietyAdmin, IsWorker
from apps.societies.models import Gate
from apps.workers.models import WorkerProfile

from .models import AttendanceEvent, Decision, RegisterScan
from .serializers import (
    AttendanceEventSerializer,
    FaceCheckSerializer,
    FaceResultSerializer,
    GatePassSerializer,
    GateRosterEntrySerializer,
    OverrideSerializer,
    RecordEventSerializer,
    RegisterScanSerializer,
    ScanLookupSerializer,
    ScanResultSerializer,
    SelfCheckInResultSerializer,
    SelfCheckInSerializer,
    SyncRequestSerializer,
    SyncResultSerializer,
)
from .services import (
    SelfCheckInDisabled,
    UnknownPass,
    WrongSociety,
    ensure_gate_pass,
    gate_roster,
    look_up_pass,
    record_event,
    run_face_check,
    self_check_in,
    sync_events,
)

logger = logging.getLogger(__name__)


def _error(code: str, message: str, http_status: int, details: dict | None = None):
    """The platform-standard error envelope (apps/core/exceptions.py)."""
    return Response(
        {"error": {"code": code, "message": message, "details": details or {}}},
        status=http_status,
    )


def _requested_day(request) -> dt.date:
    value = request.query_params.get("date")
    return dt.date.fromisoformat(value) if value else timezone.localdate()


def _visit_payload(visit):
    return {
        "source": visit.source,
        "source_id": visit.source_id,
        "title": visit.title,
        "start_time": visit.start_time,
        "end_time": visit.end_time,
        "flat_label": visit.flat_label,
        "is_confirmed": visit.is_confirmed,
    }


# ---------------------------------------------------------------------------
# 7.1 Gate pass
# ---------------------------------------------------------------------------


@extend_schema(tags=["Attendance"], summary="Your gate pass QR code")
class MyGatePassView(APIView):
    """Module 7.1 — the code a worker shows at the gate.

    Created on first read rather than at approval, so a worker approved before
    this module existed still gets one the first time they look.
    """

    permission_classes = [IsWorker]
    serializer_class = GatePassSerializer

    def get(self, request):
        profile = WorkerProfile.objects.filter(user=request.user).first()
        if profile is None:
            return _error(
                "no_profile",
                "Complete your worker profile first.",
                status.HTTP_404_NOT_FOUND,
            )

        return Response(GatePassSerializer(ensure_gate_pass(profile)).data)


@extend_schema(
    tags=["Attendance"],
    summary="Reissue your gate pass",
    request=None,
    responses=GatePassSerializer,
)
class RotateGatePassView(APIView):
    """Replaces a lost card. The old code stops working immediately."""

    permission_classes = [IsWorker]
    serializer_class = GatePassSerializer

    def post(self, request):
        profile = WorkerProfile.objects.filter(user=request.user).first()
        if profile is None:
            return _error(
                "no_profile",
                "Complete your worker profile first.",
                status.HTTP_404_NOT_FOUND,
            )

        gate_pass = ensure_gate_pass(profile)
        gate_pass.rotate(reason="Reissued by the worker")

        return Response(
            {
                "pass": GatePassSerializer(gate_pass).data,
                "message": "New code issued. Your old card no longer works.",
            }
        )


# ---------------------------------------------------------------------------
# 7.2 / 7.4 Roster and scanning
# ---------------------------------------------------------------------------


@extend_schema(
    tags=["Attendance"],
    summary="The day's gate roster",
    description="Module 7.2/7.4. Cached on the guard's device so scanning keeps "
    "working without connectivity. Contains gate pass codes, so it is restricted "
    "to gate staff of that society.",
    parameters=[
        OpenApiParameter("date", str, description="YYYY-MM-DD, defaults to today")
    ],
    responses=GateRosterEntrySerializer(many=True),
)
class GateRosterView(APIView):
    permission_classes = [IsGateStaff]
    serializer_class = GateRosterEntrySerializer

    def get(self, request):
        if request.user.society_id is None:
            return _error(
                "no_society",
                "You are not attached to a society.",
                status.HTTP_400_BAD_REQUEST,
            )

        try:
            day = _requested_day(request)
        except ValueError:
            return _error(
                "validation_error", "Invalid date.", status.HTTP_400_BAD_REQUEST
            )

        roster = gate_roster(request.user.society_id, day)
        return Response({"date": day, "count": len(roster), "results": roster})


@extend_schema(
    tags=["Attendance"],
    summary="Resolve a scanned QR code",
    request=ScanLookupSerializer,
    responses=ScanResultSerializer,
)
class ScanView(APIView):
    """Module 7.2 — who is this, and are they expected?

    Deliberately creates nothing. The guard then logs their decision through
    ``events/``, which is the same call their device makes after an offline
    scan — one code path, online or not.
    """

    permission_classes = [IsGateStaff]
    serializer_class = ScanLookupSerializer

    def post(self, request):
        serializer = ScanLookupSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            lookup = look_up_pass(
                serializer.validated_data["code"], request.user.society_id
            )
        except UnknownPass as exc:
            return _error(exc.code, str(exc), status.HTTP_404_NOT_FOUND)
        except WrongSociety as exc:
            return _error(exc.code, str(exc), status.HTTP_403_FORBIDDEN)

        worker = lookup.worker
        return Response(
            {
                "worker_id": worker.pk,
                "worker_name": worker.user.get_full_name(),
                "worker_photo": worker.photo.url if worker.photo else None,
                "is_usable": lookup.is_usable,
                "reason": lookup.reason,
                "is_expected": lookup.is_expected,
                "recommendation": lookup.recommendation,
                "expected_visits": [
                    _visit_payload(visit) for visit in lookup.expected_visits
                ],
            }
        )


# ---------------------------------------------------------------------------
# 7.2 / 7.5 / 7.6 Recording
# ---------------------------------------------------------------------------


@extend_schema(
    tags=["Attendance"],
    summary="Log a gate decision, or list the audit trail",
    request=RecordEventSerializer,
    responses=AttendanceEventSerializer,
    parameters=[
        OpenApiParameter("date", str, description="Filter by day, YYYY-MM-DD"),
        OpenApiParameter("needs_review", bool, description="Pending face checks only"),
    ],
)
class AttendanceEventListCreateView(generics.ListCreateAPIView):
    """Modules 7.2, 7.5 and 7.6.

    ``POST`` covers a scanned entry, a manual log when scanning failed, and a
    transcription from the paper register — they differ only in ``method``,
    because they are the same event with different provenance.
    """

    queryset = AttendanceEvent.objects.none()  # declared for schema generation

    def get_serializer_class(self):
        return (
            RecordEventSerializer
            if self.request.method == "POST"
            else AttendanceEventSerializer
        )

    def get_permissions(self):
        if self.request.method == "POST":
            return [IsGateStaff()]
        # Reading the trail: gate staff and administrators see their society's,
        # a worker sees only their own.
        return [(IsGateStaff | IsWorker)()]

    def get_queryset(self):
        user = self.request.user
        queryset = AttendanceEvent.objects.select_related(
            "worker__user", "gate", "recorded_by", "overridden_by"
        )

        if user.role == Role.WORKER:
            queryset = queryset.filter(worker__user=user)
        elif user.society_id is not None:
            queryset = queryset.filter(society_id=user.society_id)
        else:
            return queryset.none()

        day = self.request.query_params.get("date")
        if day:
            queryset = queryset.filter(occurred_at__date=day)
        if self.request.query_params.get("needs_review") in {"true", "1"}:
            queryset = queryset.filter(decision=Decision.PENDING_REVIEW)

        return queryset

    def create(self, request, *args, **kwargs):
        serializer = RecordEventSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        worker = (
            WorkerProfile.objects.filter(
                pk=data["worker"], user__society_id=request.user.society_id
            )
            .select_related("user")
            .first()
        )
        if worker is None:
            return _error(
                "not_found",
                "That worker is not in your society.",
                status.HTTP_404_NOT_FOUND,
            )

        gate = None
        if data.get("gate"):
            gate = Gate.objects.filter(
                pk=data["gate"], society_id=request.user.society_id
            ).first()

        event, created = record_event(
            event_id=data["id"],
            worker=worker,
            society=request.user.society,
            gate=gate,
            direction=data["direction"],
            method=data["method"],
            decision=data["decision"],
            decision_reason=data.get("decision_reason", ""),
            occurred_at=data["occurred_at"],
            recorded_by=request.user,
            device_id=data.get("device_id", ""),
            was_offline=data.get("was_offline", False),
        )

        return Response(
            {
                "event": AttendanceEventSerializer(event).data,
                # A replay is a success, not an error — it is the expected
                # outcome of a device that retried after losing its connection.
                "created": created,
                "message": "Entry logged." if created else "Already logged.",
            },
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )


@extend_schema(
    tags=["Attendance"],
    summary="Sync a guard device's offline queue",
    request=SyncRequestSerializer,
    responses=SyncResultSerializer,
)
class AttendanceSyncView(APIView):
    """Module 7.4 — idempotent bulk push.

    Each event carries the UUID the device generated when it was queued, so a
    retried batch cannot double-log anyone. Rows are handled independently: one
    bad event must not reject the other thirty-nine, or the device would retry
    the whole batch forever and the day's attendance would never land.
    """

    permission_classes = [IsGateStaff]
    serializer_class = SyncRequestSerializer

    def post(self, request):
        if request.user.society_id is None:
            return _error(
                "no_society",
                "You are not attached to a society.",
                status.HTTP_400_BAD_REQUEST,
            )

        serializer = SyncRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        outcome = sync_events(
            serializer.validated_data["events"],
            guard=request.user,
            society=request.user.society,
        )
        return Response(outcome.as_dict())


# ---------------------------------------------------------------------------
# 13.3 Tier 2 — worker self check-in
# ---------------------------------------------------------------------------


@extend_schema(
    tags=["Attendance"],
    summary="Record your own arrival (no guard on duty)",
    request=SelfCheckInSerializer,
    responses=SelfCheckInResultSerializer,
)
class SelfCheckInView(APIView):
    """Module 13.3's secondary attendance tier.

    For the gate with nobody on it. Without this a worker who turned up and did
    the job has no record of having done so, and Module 8 bills from that
    record — so the failure this prevents is somebody not being paid.

    The outcome is never a denial. Outside the geofence, or with no position at
    all, the event is logged as PENDING_REVIEW for an administrator to settle.
    A GPS fix in a courtyard between two towers is routinely 150 m out, and
    refusing a day's wages over that would be punishing somebody for physics.
    """

    permission_classes = [IsWorker]
    serializer_class = SelfCheckInSerializer

    def post(self, request):
        worker = WorkerProfile.objects.filter(user=request.user).first()
        if worker is None:
            return _error(
                "no_profile",
                "Complete your worker profile before checking in.",
                status.HTTP_400_BAD_REQUEST,
            )

        if request.user.society_id is None:
            return _error(
                "no_society",
                "You are not attached to a society.",
                status.HTTP_400_BAD_REQUEST,
            )

        serializer = SelfCheckInSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        try:
            result = self_check_in(
                event_id=data["id"],
                worker=worker,
                society=request.user.society,
                direction=data["direction"],
                occurred_at=data["occurred_at"],
                latitude=data.get("latitude"),
                longitude=data.get("longitude"),
                accuracy_metres=data.get("accuracy_metres"),
                device_id=data.get("device_id", ""),
                was_offline=data.get("was_offline", False),
            )
        except SelfCheckInDisabled as exc:
            return _error(exc.code, str(exc), status.HTTP_403_FORBIDDEN)

        return Response(
            result.as_dict(),
            status=status.HTTP_201_CREATED if result.created else status.HTTP_200_OK,
        )


# ---------------------------------------------------------------------------
# 7.3 Face verification
# ---------------------------------------------------------------------------


@extend_schema(
    tags=["Attendance"],
    summary="Verify a live photo against the registered one",
    request=FaceCheckSerializer,
    responses=FaceResultSerializer,
)
class FaceCheckView(APIView):
    """Module 7.3 / SRS 3.15.

    A below-threshold match moves the event to review — it never denies entry.
    See ``face.py`` for why that is not negotiable.
    """

    permission_classes = [IsGateStaff]
    parser_classes = [MultiPartParser, FormParser]
    serializer_class = FaceCheckSerializer

    def post(self, request, pk):
        event = (
            AttendanceEvent.objects.filter(pk=pk, society_id=request.user.society_id)
            .select_related("worker")
            .first()
        )
        if event is None:
            return _error("not_found", "Event not found.", status.HTTP_404_NOT_FOUND)

        serializer = FaceCheckSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        # As with KYC uploads, storing the file is the one step that talks to a
        # service outside this process. A storage failure gets a retryable
        # message rather than a 500 at the gate.
        try:
            event.face_photo = serializer.validated_data["photo"]
            event.save(update_fields=["face_photo", "updated_at"])
        except Exception:  # noqa: BLE001 — any storage error, not just one library's
            logger.exception("Could not store face photo for event %s", event.pk)
            return _error(
                "storage_unavailable",
                "We could not save that photo just now. Please try again in a moment.",
                status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        result = run_face_check(event)

        return Response(
            {
                "result": {
                    "available": result.available,
                    "verified": result.verified,
                    "score": result.score,
                    "engine": result.engine,
                    "reason": result.reason,
                    "needs_guard_review": result.needs_guard_review,
                },
                "event": AttendanceEventSerializer(event).data,
            }
        )


@extend_schema(
    tags=["Attendance"],
    summary="Resolve a pending face check",
    request=OverrideSerializer,
    responses=AttendanceEventSerializer,
)
class ResolveEventView(APIView):
    """The guard decides a below-threshold match, either way, with a reason."""

    permission_classes = [IsGateStaff]
    serializer_class = OverrideSerializer

    def post(self, request, pk):
        event = AttendanceEvent.objects.filter(
            pk=pk, society_id=request.user.society_id
        ).first()
        if event is None:
            return _error("not_found", "Event not found.", status.HTTP_404_NOT_FOUND)

        serializer = OverrideSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        changed = event.resolve(
            allow=serializer.validated_data["allow"],
            by=request.user,
            reason=serializer.validated_data["reason"],
        )
        if not changed:
            return _error(
                "already_resolved",
                "This entry has already been decided.",
                status.HTTP_409_CONFLICT,
            )

        return Response(
            {
                "event": AttendanceEventSerializer(event).data,
                "message": (
                    "Entry allowed."
                    if event.decision == Decision.ALLOWED
                    else "Entry refused."
                ),
            }
        )


# ---------------------------------------------------------------------------
# 7.5 Register digitisation
# ---------------------------------------------------------------------------


@extend_schema(tags=["Attendance"], summary="Photographed paper registers")
class RegisterScanListCreateView(generics.ListCreateAPIView):
    """Module 7.5 — the last-resort path when scanning failed all day.

    Not parsed. It preserves the evidence so an administrator can transcribe it
    rather than the day's attendance simply being lost.
    """

    serializer_class = RegisterScanSerializer
    parser_classes = [MultiPartParser, FormParser]
    queryset = RegisterScan.objects.none()  # declared for schema generation

    def get_permissions(self):
        if self.request.method == "POST":
            return [IsGateStaff()]
        return [IsSocietyAdmin()]

    def get_queryset(self):
        user = self.request.user
        if user.society_id is None:
            return RegisterScan.objects.none()
        return RegisterScan.objects.filter(society_id=user.society_id).select_related(
            "gate", "uploaded_by"
        )

    def perform_create(self, serializer):
        serializer.save(
            society_id=self.request.user.society_id,
            uploaded_by=self.request.user,
        )
