"""
Module 14 — the Superadmin console API.

Endpoint map (mounted at /api/v1/console/)::

    GET  overview/                    tiles, work queue, integrity        (Plate 01)
    GET  billing-integrity/           capture tiers, auto-close, disputes

    GET  transactions/                cross-society ledger                (Plate 02)
    GET  transactions/<receipt>/      the detail drawer
    GET  transactions/reconciliation/ webhook gaps + unsigned settlements
    GET  invoices/                    hourly invoices, filterable by hold

    GET  activity/sessions/           recent work sessions                (Plate 03)
    GET  activity/access-log/         who read whose records, and why
    GET  activity/impersonations/     the sensitive-actions tab

    GET  societies/                   list with tier and cap              (Plate 05)
    GET  societies/<id>/              detail, config, integrity
    POST societies/<id>/suspend/      narrows reporting; never the gate
    POST societies/<id>/tier/         change the subscription tier

    GET  users/                       global search across roles          (Plate 05)
    POST users/<id>/reveal/           logged contact reveal, reason required

    POST impersonation/               start a grant (Support only)
    POST impersonation/<id>/end/      close it

-------------------------------------------------------------------------------
EVERY ENDPOINT HERE INVERTS THE CODEBASE'S CENTRAL INVARIANT
-------------------------------------------------------------------------------
``SocietyScopedQuerysetMixin`` exists so that no request sees another society's
rows. These views deliberately do the opposite, so three things are true of all
of them without exception:

* ``IsPlatformOperator`` gates every route. There is no console endpoint an
  ordinary society administrator can reach.
* Reads of person-shaped models go through ``PlatformScopedQuerysetMixin``,
  which writes the audit row before returning anything.
* Writes are narrow. Only two of them change a society's own data, both are
  reason-gated, and the more dangerous one requires the operator to confirm in
  the request body that they know what it does not do.
"""

from __future__ import annotations

import datetime as dt

from django.db.models import Q
from django.http import HttpResponse
from django.utils import timezone
from django.views.generic import TemplateView
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import generics, status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import (
    ImpersonationGrant,
    PlatformAccessLog,
    Role,
    SuperadminLevel,
    User,
)
from apps.accounts.permissions import CanImpersonate, IsPlatformOperator
from apps.administration import report_jobs
from apps.administration.models import ReportJob
from apps.attendance.models import WorkSession
from apps.core.pagination import StandardResultsSetPagination
from apps.core.platform import PlatformScopedQuerysetMixin, client_ip, record_platform_access
from apps.payments.models import (
    Invoice,
    Payment,
    PaymentStatus,
    SocietySubscription,
    SubscriptionTier,
)
from apps.societies.models import Society, SocietyStatus

from . import metrics
from .serializers import (
    BillingIntegritySerializer,
    CreateReportJobSerializer,
    ChangeTierSerializer,
    ConsoleUserSerializer,
    ImpersonationGrantSerializer,
    InvoiceRowSerializer,
    PaymentDetailSerializer,
    PaymentRowSerializer,
    PlatformAccessLogSerializer,
    ReconciliationSerializer,
    ReportJobSerializer,
    RevealContactSerializer,
    RevealedContactSerializer,
    SocietyDetailSerializer,
    SocietyRowSerializer,
    StartImpersonationSerializer,
    SuspendSocietySerializer,
    SuspensionResultSerializer,
    SweepResultSerializer,
    TierResultSerializer,
    WorkSessionRowSerializer,
)


class ConsoleView(APIView):
    """Base for the non-list console endpoints. Read-wide by default."""

    permission_classes = [IsPlatformOperator]


class ConsoleListView(generics.ListAPIView):
    permission_classes = [IsPlatformOperator]
    pagination_class = StandardResultsSetPagination


# ---------------------------------------------------------------------------
# 14.1 Overview
# ---------------------------------------------------------------------------


class OverviewView(ConsoleView):
    """Plate 01. Four tiles, a work queue, and the integrity metric.

    ``revenue`` and ``gmv`` are separate keys and are never summed — see
    ``metrics`` for why that matters more than it looks.
    """

    @extend_schema(
        parameters=[OpenApiParameter("days", int, description="Window, default 30.")],
        responses={200: dict},
    )
    def get(self, request):
        days = int(request.query_params.get("days", 30))
        return Response(metrics.overview(days=min(max(days, 1), 365)))


class BillingIntegrityView(ConsoleView):
    """The leading indicator of whether wage figures here are trustworthy."""

    @extend_schema(
        parameters=[
            OpenApiParameter("society", int, description="Narrow to one society."),
            OpenApiParameter("days", int, description="Window, default 30."),
        ],
        responses={200: BillingIntegritySerializer},
    )
    def get(self, request):
        society_id = request.query_params.get("society")
        days = int(request.query_params.get("days", 30))
        return Response(
            metrics.billing_integrity(days=min(max(days, 1), 365), society_id=society_id)
        )


# ---------------------------------------------------------------------------
# 14.2 Transactions
# ---------------------------------------------------------------------------


class TransactionListView(ConsoleListView):
    """The cross-society ledger.

    Payments are about a transaction rather than an identifiable person, so this
    does not go through ``PlatformScopedQuerysetMixin`` — reading the ledger is
    the operator's daily job and logging every page of it would bury the reads
    that actually matter. The scoping is still explicit: cross-society access
    here is granted by ``IsPlatformOperator`` and by nothing else.
    """

    serializer_class = PaymentRowSerializer
    filterset_fields = ["society", "kind", "status", "settled_via"]
    search_fields = ["receipt_number", "razorpay_payment_id", "note"]
    ordering_fields = ["created_at", "paid_at", "amount_paise", "due_at"]
    ordering = ["-created_at"]

    def get_queryset(self):
        queryset = Payment.objects.select_related(
            "society", "resident", "resident__flat", "worker", "worker__user"
        )
        params = self.request.query_params

        if params.get("since"):
            queryset = queryset.filter(created_at__date__gte=dt.date.fromisoformat(params["since"]))
        if params.get("until"):
            queryset = queryset.filter(created_at__date__lte=dt.date.fromisoformat(params["until"]))
        if params.get("overdue") == "true":
            queryset = queryset.filter(
                due_at__lt=timezone.now(),
                status__in=[PaymentStatus.CREATED, PaymentStatus.PENDING],
            )
        # The saved view that `settled_via` was added to make possible.
        if params.get("unsigned") == "true":
            queryset = queryset.filter(id__in=metrics.unsigned_settlements().values("id"))
        if params.get("webhook_gap") == "true":
            queryset = queryset.filter(id__in=metrics.webhook_gaps().values("id"))
        return queryset


class TransactionDetailView(ConsoleView):
    """The drawer, keyed on receipt number rather than the internal UUID.

    A finance operator arrives here from a bank statement or a support email,
    both of which carry the receipt number and neither of which carries a UUID.
    """

    @extend_schema(responses={200: PaymentDetailSerializer})
    def get(self, request, receipt_number):
        payment = (
            Payment.objects.select_related("society", "resident", "worker")
            .filter(receipt_number=receipt_number)
            .first()
        )
        if payment is None:
            return Response({"detail": "No payment with that receipt number."}, status=404)
        return Response(PaymentDetailSerializer(payment).data)


class ReconciliationView(ConsoleView):
    """The console's reason for existing: money that moved out there, not here."""

    @extend_schema(responses={200: ReconciliationSerializer})
    def get(self, request):
        gaps = metrics.webhook_gaps()
        unsigned = metrics.unsigned_settlements()
        return Response({
            "webhook_gaps": {
                "count": gaps.count(),
                "results": PaymentRowSerializer(gaps[:50], many=True).data,
            },
            "unsigned_settlements": {
                "count": unsigned.count(),
                "results": PaymentRowSerializer(unsigned[:50], many=True).data,
                "note": (
                    "These rest on an administrator's word against a bank "
                    "statement, not on a verified signature."
                ),
            },
        })


class InvoiceListView(ConsoleListView):
    serializer_class = InvoiceRowSerializer
    filterset_fields = ["society", "status"]
    ordering = ["-period_end"]

    def get_queryset(self):
        queryset = Invoice.objects.select_related("society")
        if self.request.query_params.get("held") == "true":
            queryset = queryset.filter(held_paise__gt=0)
        return queryset


# ---------------------------------------------------------------------------
# 14.3 Activity
# ---------------------------------------------------------------------------


class SessionActivityView(PlatformScopedQuerysetMixin, ConsoleListView):
    """Work sessions across societies. Logged — these name a worker.

    ``queryset`` is a class attribute rather than a ``get_queryset`` override,
    and that is load-bearing. Defining ``get_queryset`` here would sit *ahead* of
    ``PlatformScopedQuerysetMixin`` in the MRO and shadow it completely: the
    endpoint would keep working, keep returning every society's rows, and
    silently stop writing an audit row. A guard that fails open and quiet is
    worse than no guard, so the seam is left where the mixin can reach it.
    """

    serializer_class = WorkSessionRowSerializer
    filterset_fields = ["society", "status", "source", "needs_review"]
    ordering = ["-visit_date"]
    queryset = WorkSession.objects.select_related("society", "worker", "worker__user")


class AccessLogView(ConsoleListView):
    """Who on the platform read whose records, and why.

    Readable by operators here; the same rows are exposed to each society for
    its own data elsewhere. Being watchable is the price of holding the bypass.
    """

    serializer_class = PlatformAccessLogSerializer
    filterset_fields = ["society", "superadmin", "model_label", "action"]
    ordering = ["-created_at"]

    def get_queryset(self):
        return PlatformAccessLog.objects.select_related("superadmin", "society")


class ImpersonationLogView(ConsoleListView):
    """The sensitive-actions tab. Reasons render inline, never behind a hover."""

    serializer_class = ImpersonationGrantSerializer
    filterset_fields = ["society", "superadmin"]
    ordering = ["-started_at"]

    def get_queryset(self):
        return ImpersonationGrant.objects.select_related(
            "superadmin", "target", "society"
        )


# ---------------------------------------------------------------------------
# 14.4 Societies
# ---------------------------------------------------------------------------


class SocietyListView(ConsoleListView):
    serializer_class = SocietyRowSerializer
    filterset_fields = ["status", "city", "state"]
    search_fields = ["name", "city", "pincode"]
    ordering = ["name"]

    def get_queryset(self):
        queryset = Society.objects.select_related("subscription").prefetch_related("users")
        tier = self.request.query_params.get("tier")
        if tier:
            if tier == SubscriptionTier.FREE:
                queryset = queryset.filter(
                    Q(subscription__isnull=True) | Q(subscription__tier=SubscriptionTier.FREE)
                )
            else:
                queryset = queryset.filter(subscription__tier=tier)
        return queryset


class SocietyDetailView(generics.RetrieveAPIView):
    permission_classes = [IsPlatformOperator]
    serializer_class = SocietyDetailSerializer
    queryset = Society.objects.select_related("subscription")


class SuspendSocietyView(ConsoleView):
    """Narrow the reporting surface. Never the gate.

    ``docs/monetisation.md`` makes this a hard product rule: *"Locking a society
    out of its own attendance record for an unpaid invoice would put workers'
    wages behind a billing dispute."* So this endpoint changes ``Society.status``
    and nothing else — attendance writes, gate checks and complaint intake all
    read the same tables afterwards as before.

    The operator must acknowledge that scope in the request body. It is the one
    console action most likely to be believed to do more than it does, and being
    told afterwards is too late to be useful.
    """

    @extend_schema(
        request=SuspendSocietySerializer,
        responses={200: SuspensionResultSerializer},
    )
    def post(self, request, pk):
        society = Society.objects.filter(pk=pk).first()
        if society is None:
            return Response({"detail": "No such society."}, status=404)

        serializer = SuspendSocietySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        society.status = SocietyStatus.SUSPENDED
        society.save(update_fields=["status", "updated_at"])

        record_platform_access(
            user=request.user,
            model_label="accounts.User",  # a suspension reaches every user in it
            society=society,
            action="society.suspend",
            reason=serializer.validated_data["reason"],
            row_count=society.users.count(),
            ip_address=client_ip(request),
        )
        return Response({
            "status": society.status,
            "scope": {
                "stopped": ["reporting", "new onboarding"],
                "still_working": ["gate checks", "attendance writes", "complaints"],
            },
        })


class ChangeTierView(ConsoleView):
    @extend_schema(request=ChangeTierSerializer, responses={200: TierResultSerializer})
    def post(self, request, pk):
        society = Society.objects.filter(pk=pk).first()
        if society is None:
            return Response({"detail": "No such society."}, status=404)

        serializer = ChangeTierSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        subscription, _created = SocietySubscription.objects.update_or_create(
            society=society,
            defaults={"tier": data["tier"], "valid_until": data.get("valid_until")},
        )
        record_platform_access(
            user=request.user,
            model_label="accounts.User",
            society=society,
            action="society.tier_change",
            reason=data["reason"],
            ip_address=client_ip(request),
        )
        return Response({
            "tier": subscription.tier,
            "effective_tier": subscription.effective_tier,
            "valid_until": subscription.valid_until,
            "is_active": subscription.is_active,
        })


# ---------------------------------------------------------------------------
# 14.5 Users
# ---------------------------------------------------------------------------


class UserSearchView(PlatformScopedQuerysetMixin, ConsoleListView):
    """Global search across every role. Contact details masked.

    Logged through the platform mixin, because every row here is a person.
    """

    serializer_class = ConsoleUserSerializer
    filterset_fields = ["role", "society", "is_approved"]
    search_fields = ["first_name", "last_name", "phone_number"]
    ordering = ["-date_joined"]

    # A class attribute, not a `get_queryset` override — see SessionActivityView
    # for why. Platform operators are excluded from the directory: the console
    # exists to look at the platform's users, and an operator searching for
    # colleagues is not a use case this screen should quietly enable.
    queryset = User.objects.select_related("society").exclude(role=Role.SUPERADMIN)


class RevealContactView(ConsoleView):
    """Unmask one phone number, with a stated reason, on the record.

    The reason is not decoration. It is written to ``PlatformAccessLog``, which
    the person's own society can read — so an operator explaining themselves
    here is explaining themselves to the committee, not to a log file nobody
    opens.
    """

    @extend_schema(
        request=RevealContactSerializer,
        responses={200: RevealedContactSerializer},
    )
    def post(self, request, pk):
        target = User.objects.filter(pk=pk).exclude(role=Role.SUPERADMIN).first()
        if target is None:
            return Response({"detail": "No such user."}, status=404)

        serializer = RevealContactSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        record_platform_access(
            user=request.user,
            model_label="accounts.User",
            society=target.society,
            action="pii.reveal",
            reason=serializer.validated_data["reason"],
            row_count=1,
            ip_address=client_ip(request),
        )
        return Response({
            "id": target.id,
            "phone_number": target.phone_number,
            "email": target.email,
            "logged": True,
            "visible_to_society": bool(target.society_id),
        })


# ---------------------------------------------------------------------------
# 14.6 Impersonation — the only write path into a society's own data
# ---------------------------------------------------------------------------


class StartImpersonationView(ConsoleView):
    """Support operators only. Finance deliberately cannot do this.

    The separation is the point: one compromised console account should not be
    able to both alter a society's operational records and move money out of
    them.
    """

    permission_classes = [IsPlatformOperator, CanImpersonate]

    @extend_schema(
        request=StartImpersonationSerializer,
        responses={201: ImpersonationGrantSerializer},
    )
    def post(self, request):
        serializer = StartImpersonationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        target = User.objects.filter(pk=data["target"]).first()
        if target is None:
            return Response({"detail": "No such user."}, status=404)
        if target.role != Role.SOCIETY_ADMIN:
            # Impersonating a resident or a worker would let the platform act as
            # a private individual in their own home's records. Administrators
            # hold a delegated operational role; the others do not.
            return Response(
                {"detail": "Only a society administrator may be impersonated."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if target.society_id is None:
            return Response(
                {"detail": "That administrator belongs to no society."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        minutes = data.get("minutes") or ImpersonationGrant.DEFAULT_MINUTES
        grant = ImpersonationGrant.objects.create(
            superadmin=request.user,
            target=target,
            society=target.society,
            reason=data["reason"],
            expires_at=timezone.now() + dt.timedelta(minutes=minutes),
        )
        record_platform_access(
            user=request.user,
            model_label="accounts.User",
            society=target.society,
            action="impersonate.start",
            reason=data["reason"],
            row_count=1,
            ip_address=client_ip(request),
        )
        return Response(
            ImpersonationGrantSerializer(grant).data, status=status.HTTP_201_CREATED
        )


class EndImpersonationView(ConsoleView):
    permission_classes = [IsPlatformOperator, CanImpersonate]

    @extend_schema(request=None, responses={200: ImpersonationGrantSerializer})
    def post(self, request, pk):
        grant = ImpersonationGrant.objects.filter(pk=pk, superadmin=request.user).first()
        if grant is None:
            return Response({"detail": "No such grant."}, status=404)
        grant.end()
        return Response(ImpersonationGrantSerializer(grant).data)


# ---------------------------------------------------------------------------
# 14.7 Reports (Module 11.5)
#
# There is no task queue on this project's plan — see
# `docs/free-tier-constraints.md` §7, and the header of
# `administration/report_jobs.py`. These three views are two of the three
# triggers that stand in for one: the list drains a slice on the way past, and
# `run/` is the endpoint the external uptime pinger calls. The third is the
# `process_report_jobs` command.
# ---------------------------------------------------------------------------


class ReportJobListView(ConsoleListView):
    """Queued and finished reports — and, on the way past, a little work.

    Draining here is what makes the feature work without a scheduler: the
    operator most likely to load this screen is the one waiting for a report, so
    their own page load advances it. Bounded to a few societies so the request
    they are waiting on does not become the slow one.
    """

    serializer_class = ReportJobSerializer
    filterset_fields = ["kind", "status", "scope"]
    ordering = ["-created_at"]
    queryset = ReportJob.objects.prefetch_related(
        "society_jobs", "society_jobs__society"
    ).select_related("requested_by")

    def list(self, request, *args, **kwargs):
        # Before serialising, not after: the point is for the response to
        # reflect the work this very request just did.
        report_jobs.run_pending_jobs(limit=4)
        return super().list(request, *args, **kwargs)


class CreateReportJobView(ConsoleView):
    @extend_schema(
        request=CreateReportJobSerializer, responses={201: ReportJobSerializer}
    )
    def post(self, request):
        serializer = CreateReportJobSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        societies = None
        if data["scope"] == "selected":
            societies = list(Society.objects.filter(pk__in=data["societies"]))

        try:
            job = report_jobs.queue_report(
                kind=data["kind"],
                scope=data["scope"],
                tier=data.get("tier", ""),
                societies=societies,
                period_start=data["period_start"],
                period_end=data["period_end"],
                formats=data["formats"],
                include_pii=data["include_pii"],
                reason=data.get("reason", ""),
                requested_by=request.user,
            )
        except report_jobs.ReportJobError as error:
            return Response({"detail": str(error)}, status=status.HTTP_400_BAD_REQUEST)

        if job.include_pii:
            # A PII export is audited per society in scope, so each society can
            # see that its people appeared in a platform-wide extract and why.
            for slice_row in job.society_jobs.select_related("society"):
                record_platform_access(
                    user=request.user,
                    model_label="accounts.User",
                    society=slice_row.society,
                    action="report.export_pii",
                    reason=job.reason,
                    ip_address=client_ip(request),
                )

        return Response(
            ReportJobSerializer(job).data, status=status.HTTP_201_CREATED
        )


class RetryReportJobView(ConsoleView):
    """Re-queue only the societies that failed.

    One society timing out must not void the other hundred and twenty-seven, so
    the successful slices keep their cached rows and this costs one society's
    work rather than the whole build.
    """

    @extend_schema(request=None, responses={200: ReportJobSerializer})
    def post(self, request, pk):
        job = ReportJob.objects.filter(pk=pk).first()
        if job is None:
            return Response({"detail": "No such report."}, status=404)

        requeued = report_jobs.retry_failed_societies(job)
        if not requeued:
            return Response(
                {"detail": "Nothing to retry — every society either built or is out of attempts."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        job.refresh_from_db()
        return Response(ReportJobSerializer(job).data)


class RunReportSweepView(ConsoleView):
    """The sweep, exposed so the external uptime pinger can drive it.

    Same shape as Module 11.3's `complaints/escalate/` and Module 10's
    `deliver-due/`: cheap, idempotent, and this project's substitute for a
    scheduled job.
    """

    @extend_schema(request=None, responses={200: SweepResultSerializer})
    def post(self, request):
        limit = int(request.query_params.get("limit", report_jobs.DEFAULT_SWEEP_LIMIT))
        return Response(report_jobs.run_pending_jobs(limit=min(max(limit, 1), 200)))


class ReportJobDownloadView(ConsoleView):
    """Hand back the built file.

    Served through Django rather than by exposing the storage path: the file is
    a cross-society extract, and a URL that works without a token is one that
    keeps working after the operator's access is revoked.
    """

    def get(self, request, pk, fmt):
        job = ReportJob.objects.filter(pk=pk).first()
        if job is None:
            return Response({"detail": "No such report."}, status=404)
        if not job.is_downloadable:
            return Response(
                {"detail": "That report is not ready, or has expired."},
                status=status.HTTP_409_CONFLICT,
            )

        handle = job.csv_file if fmt == "csv" else job.pdf_file
        if not handle:
            return Response(
                {"detail": f"This report was not built as {fmt.upper()}."}, status=404
            )

        content_type = "text/csv" if fmt == "csv" else "application/pdf"
        response = HttpResponse(handle.read(), content_type=content_type)
        response["Content-Disposition"] = (
            f'attachment; filename="{job.kind}-{job.period_end:%Y%m}.{fmt}"'
        )
        return response


# ---------------------------------------------------------------------------
# 14.8 The console itself
# ---------------------------------------------------------------------------


class ConsoleAppView(TemplateView):
    """Serves the console's HTML shell.

    Deliberately unauthenticated, and that is not an oversight. The template
    contains no data — it is markup plus a script that then authenticates
    against `/api/v1/auth/login/` and reads the same JWT-gated endpoints as
    every other client. Gating the shell would mean adding session auth to a
    project that is otherwise entirely token-based, which is a second auth
    system to keep correct for no gain: an unauthenticated visitor sees a login
    form and nothing else.
    """

    template_name = "console/index.html"
