"""
Module 11 — Admin, Reporting & Complaints: API views.

Endpoint map (mounted at /api/v1/admin-tools/)::

    GET  directory/workers/         searchable worker directory            (11.1)
    GET  directory/residents/       searchable resident directory          (11.1)

    GET  reports/<kind>/            attendance | payments | complaints     (11.2)
    GET  reports/<kind>/csv/        the same, as a file
    GET  reports/<kind>/pdf/

    GET  complaints/                the caller's own, or the whole queue   (11.3)
    POST complaints/                raise one
    GET  complaints/<id>/           with its full history
    POST complaints/<id>/updates/   add a note
    POST complaints/<id>/start/     an administrator picks it up
    POST complaints/<id>/close/     resolve or reject
    POST complaints/<id>/withdraw/  the raiser takes it back
    POST complaints/escalate/       run the overdue sweep

    GET  dashboard/                 the analytics panels                   (11.4)
    GET  unmet-demand/              requests nobody could fill

-------------------------------------------------------------------------------
WHO SEES WHICH COMPLAINTS
-------------------------------------------------------------------------------
An administrator sees their society's queue. Everyone else sees complaints they
raised, plus complaints raised *about them* — a worker accused of something has
to be able to read the accusation, or the process is one they cannot answer.

Internal notes are the one exception, filtered out in the serializer.
"""

from __future__ import annotations

import datetime as dt
import logging

from django.db.models import Count, Q
from django.http import HttpResponse
from django.utils import timezone
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import generics, status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.permissions import IsApprovedSocietyAdmin, IsApprovedUser
from apps.core.pagination import StandardResultsSetPagination
from apps.societies.models import Resident
from apps.workers.models import WorkerProfile

from . import analytics, reports, services
from .models import CLOSED_STATUSES, Complaint, ComplaintStatus, UnmetDemand
from .serializers import (
    AddUpdateSerializer,
    CloseComplaintSerializer,
    ComplaintDetailSerializer,
    ComplaintSerializer,
    DashboardSerializer,
    DirectoryResidentSerializer,
    DirectoryWorkerSerializer,
    RaiseComplaintSerializer,
    ReportQuerySerializer,
    ReportSerializer,
    UnmetDemandSerializer,
)

logger = logging.getLogger(__name__)

#: How far back a report reaches when no period is given.
DEFAULT_REPORT_DAYS = 30


def _error(code: str, message: str, http_status: int, details: dict | None = None):
    """The platform-standard error envelope (apps/core/exceptions.py)."""
    return Response(
        {"error": {"code": code, "message": message, "details": details or {}}},
        status=http_status,
    )


def _open_complaint_count(relation: str):
    """Count of complaints against a directory row that are still open.

    Expressed as a conditional aggregate so the directory stays one query. A
    per-row count would be a query per worker on a paginated list — the classic
    way to make a free-tier admin screen unusable.
    """
    return Count(
        relation,
        filter=~Q(**{f"{relation}__status__in": list(CLOSED_STATUSES)}),
        distinct=True,
    )


# ---------------------------------------------------------------------------
# 11.1 Directory
# ---------------------------------------------------------------------------


@extend_schema(
    tags=["Administration"],
    summary="Worker directory",
    parameters=[
        OpenApiParameter("search", str, description="Name or phone number"),
        OpenApiParameter("service", str, description="Service type slug"),
        OpenApiParameter("approved", bool),
        OpenApiParameter("available", bool),
    ],
)
class WorkerDirectoryView(generics.ListAPIView):
    """Module 11.1 — every worker in the society, searchable.

    The mobile counterpart to the Django admin screen the modspec asks for.
    Both exist because an administrator at a desk and one standing at a gate
    need the same data through different doors.
    """

    serializer_class = DirectoryWorkerSerializer
    permission_classes = [IsApprovedSocietyAdmin]
    pagination_class = StandardResultsSetPagination
    queryset = WorkerProfile.objects.none()  # declared for schema generation

    def get_queryset(self):
        queryset = (
            WorkerProfile.objects.filter(user__society_id=self.request.user.society_id)
            .select_related("user")
            .prefetch_related("service_types")
            .annotate(open_complaints=_open_complaint_count("complaints_against"))
            .order_by("-trust_score", "user__first_name")
        )

        params = self.request.query_params

        search = (params.get("search") or "").strip()
        if search:
            queryset = queryset.filter(
                Q(user__first_name__icontains=search)
                | Q(user__last_name__icontains=search)
                | Q(user__phone_number__icontains=search)
            )

        service = params.get("service")
        if service:
            queryset = queryset.filter(service_types__slug=service)

        approved = params.get("approved")
        if approved in {"true", "false"}:
            queryset = queryset.filter(user__is_approved=(approved == "true"))

        available = params.get("available")
        if available in {"true", "false"}:
            queryset = queryset.filter(is_available=(available == "true"))

        return queryset


@extend_schema(
    tags=["Administration"],
    summary="Resident directory",
    parameters=[
        OpenApiParameter("search", str, description="Name, phone number or flat"),
        OpenApiParameter("approved", bool),
    ],
)
class ResidentDirectoryView(generics.ListAPIView):
    """Module 11.1 — every resident in the society."""

    serializer_class = DirectoryResidentSerializer
    permission_classes = [IsApprovedSocietyAdmin]
    pagination_class = StandardResultsSetPagination
    queryset = Resident.objects.none()  # declared for schema generation

    def get_queryset(self):
        queryset = (
            Resident.objects.filter(
                flat__tower__society_id=self.request.user.society_id
            )
            .select_related("user", "flat__tower")
            .annotate(open_complaints=_open_complaint_count("complaints_against"))
            .order_by("flat__tower__name", "flat__number")
        )

        search = (self.request.query_params.get("search") or "").strip()
        if search:
            queryset = queryset.filter(
                Q(user__first_name__icontains=search)
                | Q(user__last_name__icontains=search)
                | Q(user__phone_number__icontains=search)
                | Q(flat__number__icontains=search)
            )

        approved = self.request.query_params.get("approved")
        if approved in {"true", "false"}:
            queryset = queryset.filter(user__is_approved=(approved == "true"))

        return queryset


# ---------------------------------------------------------------------------
# 11.2 Reports
# ---------------------------------------------------------------------------


def _report_period(request) -> tuple[dt.date, dt.date]:
    """Resolve ``?start=&end=``, defaulting to the last month.

    Raises DRF's ``ValidationError`` on a malformed range — unlike the
    dashboard, a report is a compliance document and quietly covering a
    different period than the one asked for would be worse than an error.
    """
    params = request.query_params
    if not params.get("start") and not params.get("end"):
        end = timezone.localdate()
        return end - dt.timedelta(days=DEFAULT_REPORT_DAYS), end

    query = ReportQuerySerializer(data=params)
    query.is_valid(raise_exception=True)
    return query.validated_data["start"], query.validated_data["end"]


def _build_report(request, kind: str):
    """Returns ``(report, error_response)`` — exactly one of which is None."""
    if kind not in reports.REPORT_BUILDERS:
        return None, _error(
            "unknown_report",
            f"There is no '{kind}' report. Choose one of: "
            + ", ".join(sorted(reports.REPORT_BUILDERS)),
            status.HTTP_404_NOT_FOUND,
        )

    start, end = _report_period(request)
    return reports.build(kind, request.user.society, start=start, end=end), None


@extend_schema(
    tags=["Administration"],
    summary="A report as JSON",
    parameters=[OpenApiParameter("start", dt.date), OpenApiParameter("end", dt.date)],
    responses=ReportSerializer,
)
class ReportView(APIView):
    """Module 11.2 — attendance, payments or complaints, on screen.

    The same assembled report the file exports render from, so a figure shown
    in the app and a figure in a downloaded PDF cannot disagree.
    """

    permission_classes = [IsApprovedSocietyAdmin]
    serializer_class = ReportSerializer

    def get(self, request, kind):
        report, error = _build_report(request, kind)
        if error is not None:
            return error
        return Response(report.as_dict())


@extend_schema(
    tags=["Administration"],
    summary="A report as a CSV file",
    parameters=[OpenApiParameter("start", dt.date), OpenApiParameter("end", dt.date)],
    responses={(200, "text/csv"): OpenApiTypes.BINARY},
)
class ReportCsvView(APIView):
    permission_classes = [IsApprovedSocietyAdmin]

    def get(self, request, kind):
        report, error = _build_report(request, kind)
        if error is not None:
            return error

        response = HttpResponse(
            reports.render_csv(report), content_type="text/csv; charset=utf-8"
        )
        response["Content-Disposition"] = (
            f'attachment; filename="{_filename(report, "csv")}"'
        )
        return response


@extend_schema(
    tags=["Administration"],
    summary="A report as a PDF file",
    parameters=[OpenApiParameter("start", dt.date), OpenApiParameter("end", dt.date)],
    responses={(200, "application/pdf"): OpenApiTypes.BINARY},
)
class ReportPdfView(APIView):
    permission_classes = [IsApprovedSocietyAdmin]

    def get(self, request, kind):
        report, error = _build_report(request, kind)
        if error is not None:
            return error

        try:
            pdf = reports.render_pdf(report)
        except ImportError:
            # reportlab is not installed everywhere. The CSV export covers the
            # same ground, so this degrades to a clear message rather than 500s
            # — the same rule every optional dependency follows in this codebase.
            return _error(
                "pdf_unavailable",
                "PDF export is not available on this server. Use the CSV export.",
                status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        response = HttpResponse(pdf, content_type="application/pdf")
        response["Content-Disposition"] = (
            f'attachment; filename="{_filename(report, "pdf")}"'
        )
        return response


def _filename(report, extension: str) -> str:
    slug = report.title.lower().replace(" ", "-")
    return (
        f"sathify-{slug}-{report.period_start:%Y%m%d}"
        f"-{report.period_end:%Y%m%d}.{extension}"
    )


# ---------------------------------------------------------------------------
# 11.3 Complaints
# ---------------------------------------------------------------------------


def _visible_complaints(user):
    """What one user may see.

    An administrator gets their society's queue. Everybody else gets what they
    raised plus what was raised about them — being able to read the accusation
    against you is not a nicety, it is what makes the process answerable.
    """
    queryset = Complaint.objects.select_related(
        "raised_by", "against_worker__user", "against_resident__user", "society"
    )

    if getattr(user, "is_society_admin", False):
        return queryset.filter(society_id=user.society_id)

    return queryset.filter(
        Q(raised_by=user)
        | Q(against_worker__user=user)
        | Q(against_resident__user=user)
    ).distinct()


@extend_schema(
    tags=["Administration"],
    summary="Complaints",
    parameters=[
        OpenApiParameter("status", str),
        OpenApiParameter("category", str),
        OpenApiParameter("open", bool, description="Open complaints only"),
        OpenApiParameter("overdue", bool, description="Past the SLA window"),
    ],
)
class ComplaintListCreateView(generics.ListCreateAPIView):
    """Module 11.3 — the queue, and the form that feeds it."""

    permission_classes = [IsApprovedUser]
    pagination_class = StandardResultsSetPagination
    queryset = Complaint.objects.none()  # declared for schema generation

    def get_serializer_class(self):
        return (
            RaiseComplaintSerializer
            if self.request.method == "POST"
            else ComplaintSerializer
        )

    def get_queryset(self):
        user = self.request.user

        # The escalation sweep runs here rather than on a timer. An
        # administrator opening the queue is the most reliable trigger this
        # deployment has, because the free tier has no scheduler.
        if getattr(user, "is_society_admin", False):
            services.escalate_overdue(society_id=user.society_id)

        queryset = _visible_complaints(user)
        params = self.request.query_params

        if params.get("open") in {"true", "1"}:
            queryset = queryset.open()
        if params.get("overdue") in {"true", "1"}:
            queryset = queryset.overdue()
        if params.get("status"):
            queryset = queryset.filter(status=params["status"])
        if params.get("category"):
            queryset = queryset.filter(category=params["category"])

        # Open before closed, then soonest deadline first: the order somebody
        # working a queue needs, rather than newest-first.
        return queryset.order_by("status", "sla_due_at", "-created_at")

    def create(self, request, *args, **kwargs):
        serializer = RaiseComplaintSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        target, error = _resolve_target(request.user, data)
        if error is not None:
            return error

        try:
            complaint = services.raise_complaint(
                raised_by=request.user,
                society=request.user.society,
                category=data["category"],
                subject=data["subject"],
                description=data["description"],
                photo=data.get("photo"),
                **target,
            )
        except Exception:  # noqa: BLE001 — any storage error, not just one library's
            # Storing the evidence photo is the one step that leaves this
            # process. A media backend that is down must not swallow somebody's
            # complaint — least of all a safety one — so this is retryable
            # rather than a bare 500.
            logger.exception(
                "Could not raise complaint for user %s", request.user.pk
            )
            return _error(
                "storage_unavailable",
                "We could not save that just now. Please try again in a moment.",
                status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        return Response(
            {
                "complaint": ComplaintSerializer(
                    complaint, context={"request": request}
                ).data,
                "message": (
                    f"Raised as {complaint.reference}. Your society "
                    "administrator has been notified."
                ),
            },
            status=status.HTTP_201_CREATED,
        )


def _resolve_target(user, data) -> tuple[dict, Response | None]:
    """Look up who a complaint is about, inside the caller's own society.

    Resolved here rather than trusted from the request: an id from a client is
    a claim, and without this check a complaint could be filed against somebody
    in another society entirely.
    """
    worker_id = data.get("against_worker")
    resident_id = data.get("against_resident")

    if worker_id:
        worker = WorkerProfile.objects.filter(
            pk=worker_id, user__society_id=user.society_id
        ).first()
        if worker is None:
            return {}, _error(
                "not_found",
                "That worker is not in your society.",
                status.HTTP_404_NOT_FOUND,
            )
        return {"against_worker": worker}, None

    if resident_id:
        resident = Resident.objects.filter(
            pk=resident_id, flat__tower__society_id=user.society_id
        ).first()
        if resident is None:
            return {}, _error(
                "not_found",
                "That resident is not in your society.",
                status.HTTP_404_NOT_FOUND,
            )
        return {"against_resident": resident}, None

    # Neither: a complaint about the society itself. Legitimate — a broken gate
    # or a guard who will not scan has no individual to name, and forcing one
    # would put somebody's name on a complaint that was never about them.
    return {}, None


@extend_schema(tags=["Administration"], summary="One complaint, with its history")
class ComplaintDetailView(generics.RetrieveAPIView):
    serializer_class = ComplaintDetailSerializer
    permission_classes = [IsApprovedUser]
    queryset = Complaint.objects.none()  # declared for schema generation

    def get_queryset(self):
        return _visible_complaints(self.request.user).prefetch_related(
            "updates__author"
        )


class _ComplaintActionView(APIView):
    """Shared lookup for the action endpoints."""

    permission_classes = [IsApprovedUser]

    def get_complaint(self, request, pk):
        return _visible_complaints(request.user).filter(pk=pk).first()


@extend_schema(
    tags=["Administration"],
    summary="Add a note to a complaint",
    request=AddUpdateSerializer,
    responses=ComplaintDetailSerializer,
)
class AddComplaintUpdateView(_ComplaintActionView):
    serializer_class = AddUpdateSerializer

    def post(self, request, pk):
        complaint = self.get_complaint(request, pk)
        if complaint is None:
            return _error("not_found", "Complaint not found.", status.HTTP_404_NOT_FOUND)

        serializer = AddUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        # Only an administrator can write an internal note. A resident marking
        # their own comment internal would hide it from the only person who
        # needs to read it.
        is_admin = bool(getattr(request.user, "is_society_admin", False))
        internal = bool(serializer.validated_data.get("is_internal")) and is_admin

        services.add_update(
            complaint,
            author=request.user,
            note=serializer.validated_data["note"],
            is_internal=internal,
        )

        complaint.refresh_from_db()
        return Response(
            ComplaintDetailSerializer(complaint, context={"request": request}).data
        )


@extend_schema(
    tags=["Administration"],
    summary="Pick up a complaint",
    request=None,
    responses=ComplaintSerializer,
)
class StartComplaintView(_ComplaintActionView):
    """The administrator takes ownership. Stops the first-response clock."""

    permission_classes = [IsApprovedSocietyAdmin]
    serializer_class = ComplaintSerializer

    def post(self, request, pk):
        complaint = self.get_complaint(request, pk)
        if complaint is None:
            return _error("not_found", "Complaint not found.", status.HTTP_404_NOT_FOUND)

        if not services.start_progress(complaint, by=request.user):
            return _error(
                "not_open",
                "This complaint is not waiting to be picked up.",
                status.HTTP_409_CONFLICT,
            )

        return Response(
            ComplaintSerializer(complaint, context={"request": request}).data
        )


@extend_schema(
    tags=["Administration"],
    summary="Resolve or reject a complaint",
    request=CloseComplaintSerializer,
    responses=ComplaintSerializer,
)
class CloseComplaintView(_ComplaintActionView):
    permission_classes = [IsApprovedSocietyAdmin]
    serializer_class = CloseComplaintSerializer

    def post(self, request, pk):
        complaint = self.get_complaint(request, pk)
        if complaint is None:
            return _error("not_found", "Complaint not found.", status.HTTP_404_NOT_FOUND)

        serializer = CloseComplaintSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            services.close_complaint(
                complaint,
                status=serializer.validated_data["status"],
                resolution=serializer.validated_data["resolution"],
                by=request.user,
            )
        except services.AlreadyClosed as exc:
            return _error("already_closed", str(exc), status.HTTP_409_CONFLICT)

        return Response(
            ComplaintSerializer(complaint, context={"request": request}).data
        )


@extend_schema(
    tags=["Administration"],
    summary="Withdraw a complaint you raised",
    request=None,
    responses=ComplaintSerializer,
)
class WithdrawComplaintView(_ComplaintActionView):
    """Only the person who raised it. Withdrawal is recorded, not deleted.

    Someone who raised a complaint in anger and thinks better of it should be
    able to take it back — but the trail stays, because a complaint withdrawn
    under pressure looks exactly like one withdrawn freely, and only the record
    makes that difference visible later.
    """

    serializer_class = ComplaintSerializer

    def post(self, request, pk):
        complaint = self.get_complaint(request, pk)
        if complaint is None:
            return _error("not_found", "Complaint not found.", status.HTTP_404_NOT_FOUND)

        if complaint.raised_by_id != request.user.pk:
            return _error(
                "not_yours",
                "Only the person who raised a complaint can withdraw it.",
                status.HTTP_403_FORBIDDEN,
            )

        try:
            services.close_complaint(
                complaint,
                status=ComplaintStatus.WITHDRAWN,
                resolution=str(
                    request.data.get("reason") or "Withdrawn by the complainant."
                )[:2000],
                by=request.user,
            )
        except services.AlreadyClosed as exc:
            return _error("already_closed", str(exc), status.HTTP_409_CONFLICT)

        return Response(
            ComplaintSerializer(complaint, context={"request": request}).data
        )


@extend_schema(
    tags=["Administration"],
    summary="Escalate complaints past their SLA",
    request=None,
)
class EscalateComplaintsView(APIView):
    """The sweep, exposed so an external pinger can drive it.

    Same shape as Module 10's ``deliver-due/``: administrators only, cheap,
    idempotent, and the free tier's substitute for a scheduled job.
    """

    permission_classes = [IsApprovedSocietyAdmin]
    serializer_class = ComplaintSerializer

    def post(self, request):
        escalated = services.escalate_overdue(society_id=request.user.society_id)
        return Response(
            {
                "escalated": escalated,
                "message": (
                    "Nothing was overdue."
                    if not escalated
                    else f"{escalated} complaint(s) escalated."
                ),
            }
        )


# ---------------------------------------------------------------------------
# 11.4 Analytics
# ---------------------------------------------------------------------------


@extend_schema(
    tags=["Administration"],
    summary="Analytics dashboard",
    parameters=[OpenApiParameter("since", dt.date), OpenApiParameter("until", dt.date)],
    responses=DashboardSerializer,
)
class DashboardView(APIView):
    """Module 11.4 — sentiment, trust, complaints, unmet demand, availability."""

    permission_classes = [IsApprovedSocietyAdmin]
    serializer_class = DashboardSerializer

    def get(self, request):
        since = _optional_date(request.query_params.get("since"))
        until = _optional_date(request.query_params.get("until"))

        if since and until and until < since:
            return _error(
                "validation_error",
                "The end of the period cannot be before its start.",
                status.HTTP_400_BAD_REQUEST,
            )

        return Response(
            analytics.dashboard(request.user.society_id, since=since, until=until)
        )


def _optional_date(value):
    """Parse a date query parameter, ignoring anything unparseable.

    Unlike a report, the dashboard is a read-only overview. Refusing to render
    it because a query parameter was malformed helps nobody; falling back to the
    default window does.
    """
    if not value:
        return None
    try:
        return dt.date.fromisoformat(value)
    except (TypeError, ValueError):
        return None


@extend_schema(
    tags=["Administration"],
    summary="Requests nobody could fill",
    parameters=[OpenApiParameter("kind", str)],
)
class UnmetDemandListView(generics.ListAPIView):
    """Module 11.4 — the recruiting brief, as a list."""

    serializer_class = UnmetDemandSerializer
    permission_classes = [IsApprovedSocietyAdmin]
    pagination_class = StandardResultsSetPagination
    queryset = UnmetDemand.objects.none()  # declared for schema generation

    def get_queryset(self):
        queryset = UnmetDemand.objects.filter(society_id=self.request.user.society_id)
        kind = self.request.query_params.get("kind")
        return queryset.filter(kind=kind) if kind else queryset
