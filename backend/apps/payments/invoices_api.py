"""
Module 8.10 — the resident's bill, and the worker's side of it.

The screen this serves has one job the monthly rate never needed: **make the
number auditable**. A resident who cannot see how ₹6,700 was arrived at will
argue about it over WhatsApp, and the argument will land on a worker who has no
record of her own to answer with. So the detail response carries every line,
every session behind it, and the visit fee as its own item — never folded into
an hourly figure.
"""

from __future__ import annotations

from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework import serializers, status
from rest_framework.generics import ListAPIView
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.permissions import IsApprovedUser
from apps.attendance.models import WorkSession

from .invoicing import resolve_query
from .models import (
    DisputeReason,
    Invoice,
    InvoiceLine,
    InvoiceStatus,
    QueryStage,
    SessionQuery,
    format_paise,
)


def _invoices_for(user):
    """Only invoices this user is a party to."""
    queryset = Invoice.objects.select_related(
        "society", "engagement", "resident", "resident__user", "resident__flat",
        "worker", "worker__user", "payment",
    )
    if user.is_resident:
        return queryset.filter(resident__user=user)
    if user.is_worker:
        return queryset.filter(worker__user=user)
    if user.is_society_admin:
        return queryset.filter(society_id=user.society_id)
    return queryset.none()


class InvoiceLineSerializer(serializers.ModelSerializer):
    amount_display = serializers.SerializerMethodField()

    class Meta:
        model = InvoiceLine
        fields = [
            "id", "kind", "description", "minutes", "amount_paise",
            "amount_display", "is_held", "session", "query",
        ]

    def get_amount_display(self, obj) -> str:
        return format_paise(obj.amount_paise)


class InvoiceSerializer(serializers.ModelSerializer):
    worker_name = serializers.SerializerMethodField()
    flat = serializers.SerializerMethodField()
    total_paise = serializers.IntegerField(read_only=True)
    payable_paise = serializers.IntegerField(read_only=True)
    total_display = serializers.SerializerMethodField()
    payable_display = serializers.SerializerMethodField()
    #: True while either party may still query a line without anything having
    #: been charged. The screen leads with this.
    in_review = serializers.SerializerMethodField()

    class Meta:
        model = Invoice
        fields = [
            "id", "number", "status", "in_review", "period_start", "period_end",
            "review_closes_at", "issued_at", "settled_at",
            "worker_name", "flat", "payment",
            "time_paise", "overtime_paise", "visit_fee_paise",
            "adjustment_paise", "held_paise",
            "total_paise", "payable_paise", "total_display", "payable_display",
        ]

    def get_worker_name(self, obj) -> str:
        return obj.worker.user.get_full_name()

    def get_flat(self, obj) -> str:
        flat = getattr(obj.resident, "flat", None)
        return str(flat) if flat else ""

    def get_total_display(self, obj) -> str:
        return format_paise(obj.total_paise)

    def get_payable_display(self, obj) -> str:
        return format_paise(obj.payable_paise)

    def get_in_review(self, obj) -> bool:
        return obj.status == InvoiceStatus.REVIEW


class OpenQuerySerializer(serializers.ModelSerializer):
    """An open question about one visit on this bill.

    Exposed so stage two of the ladder is reachable at all. Without a query id
    on the wire the client cannot offer "accept their version", every query
    survives its 48 hours, and a design meant to keep volunteer committee
    members out of other people's disputes delivers all of them instead.
    """

    raised_by_name = serializers.SerializerMethodField()
    can_accept = serializers.SerializerMethodField()

    class Meta:
        model = SessionQuery
        fields = [
            "id", "session", "reason", "description", "stage",
            "raised_by", "raised_by_name", "can_accept", "created_at",
        ]

    def get_raised_by_name(self, obj) -> str:
        return obj.raised_by.get_full_name() or "Someone"

    def get_can_accept(self, obj) -> bool:
        """Only the *other* party may accept. Computed here rather than on the
        client, which would otherwise need to know its own user id and would get
        it wrong for a household with two residents."""
        request = self.context.get("request")
        if request is None or not request.user.is_authenticated:
            return False
        return obj.is_open and obj.raised_by_id != request.user.id


class InvoiceDetailSerializer(InvoiceSerializer):
    """The auditable version: every line, and the days behind them."""

    lines = InvoiceLineSerializer(many=True, read_only=True)
    days = serializers.SerializerMethodField()
    unbilled_extra_minutes = serializers.SerializerMethodField()
    open_queries = serializers.SerializerMethodField()

    class Meta(InvoiceSerializer.Meta):
        fields = InvoiceSerializer.Meta.fields + [
            "lines", "days", "unbilled_extra_minutes", "open_queries",
        ]

    def get_open_queries(self, obj) -> list:
        rows = obj.queries.exclude(
            stage__in=[QueryStage.RESOLVED, QueryStage.WITHDRAWN]
        ).select_related("raised_by")
        return OpenQuerySerializer(rows, many=True, context=self.context).data

    def _sessions(self, obj):
        ids = obj.lines.exclude(session__isnull=True).values_list("session_id", flat=True)
        return WorkSession.objects.filter(id__in=set(ids))

    def get_days(self, obj) -> dict:
        from apps.attendance.models import SessionStatus

        sessions = self._sessions(obj)
        return {
            "billed": sessions.count(),
            "full": sessions.filter(status=SessionStatus.CLOSED).count(),
            "auto_closed": sessions.filter(status=SessionStatus.AUTO_CLOSED).count(),
            "cancelled_at_door": sessions.filter(
                status=SessionStatus.CANCELLED_AT_DOOR
            ).count(),
        }

    def get_unbilled_extra_minutes(self, obj) -> int:
        """Extra time worked and not charged.

        Surfaced deliberately: the resident sees goodwill they did not pay for,
        and the worker sees that the app noticed. Hiding it would make the
        approval rule look like a way of not paying her.
        """
        return sum(s.unbilled_extra_minutes for s in self._sessions(obj))


class RaiseQuerySerializer(serializers.Serializer):
    session = serializers.UUIDField()
    reason = serializers.ChoiceField(choices=DisputeReason.choices)
    description = serializers.CharField(max_length=1000, required=False, allow_blank=True)


class MyInvoicesView(ListAPIView):
    permission_classes = [IsApprovedUser]
    serializer_class = InvoiceSerializer
    pagination_class = None

    def get_queryset(self):
        queryset = _invoices_for(self.request.user)
        if self.request.query_params.get("engagement"):
            queryset = queryset.filter(
                engagement_id=self.request.query_params["engagement"]
            )
        return queryset[:24]


class InvoiceDetailView(APIView):
    permission_classes = [IsApprovedUser]

    @extend_schema(responses={200: InvoiceDetailSerializer})
    def get(self, request, pk):
        invoice = _invoices_for(request.user).filter(pk=pk).first()
        if invoice is None:
            return Response({"detail": "No such invoice."}, status=404)
        return Response(
            InvoiceDetailSerializer(invoice, context={'request': request}).data
        )


class RaiseQueryView(APIView):
    """Ask about one line, before any money moves.

    Holding the line is what makes this safe to offer. The rest of the invoice
    issues and pays on schedule, so raising a question costs a worker nothing —
    and a worker who believes a query freezes her month will never raise one,
    which leaves a record that looks unchallenged only because challenging it
    was too expensive.
    """

    permission_classes = [IsApprovedUser]

    @extend_schema(request=RaiseQuerySerializer, responses={201: InvoiceDetailSerializer})
    def post(self, request, pk):
        invoice = _invoices_for(request.user).filter(pk=pk).first()
        if invoice is None:
            return Response({"detail": "No such invoice."}, status=404)
        if invoice.status not in {InvoiceStatus.DRAFT, InvoiceStatus.REVIEW}:
            return Response(
                {"detail": "This bill has already been issued. Raise a payment dispute instead."},
                status=status.HTTP_409_CONFLICT,
            )

        serializer = RaiseQuerySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        line = invoice.lines.filter(session_id=data["session"]).first()
        if line is None:
            return Response({"detail": "That visit is not on this bill."}, status=404)

        # `get_or_create` cannot express "one *open* query per person" — that is
        # a partial unique constraint, and get_or_create would either miss the
        # resolved rows or collide with them. Ask the question directly.
        query = (
            SessionQuery.objects.filter(session_id=data["session"], raised_by=request.user)
            .exclude(stage__in=[QueryStage.RESOLVED, QueryStage.WITHDRAWN])
            .first()
        )

        if query is None:
            query = SessionQuery.objects.create(
                society=invoice.society,
                session_id=data["session"],
                invoice=invoice,
                raised_by=request.user,
                reason=data["reason"],
                description=data.get("description", ""),
                escalates_at=timezone.now() + timezone.timedelta(hours=48),
            )

        invoice.lines.filter(session_id=data["session"]).update(is_held=True)
        invoice.recalculate()

        return Response(
            InvoiceDetailSerializer(invoice, context={'request': request}).data,
            status=status.HTTP_201_CREATED,
        )


class AcceptQueryView(APIView):
    """The other party agrees, in one tap. Stage two of the ladder.

    Most queries end here or at stage one, which is the whole design: a
    volunteer committee member should only ever see what the two people involved
    could not settle between them.
    """

    permission_classes = [IsApprovedUser]

    @extend_schema(request=None, responses={200: InvoiceDetailSerializer})
    def post(self, request, pk):
        query = SessionQuery.objects.filter(pk=pk).select_related("invoice").first()
        if query is None or query.invoice is None:
            return Response({"detail": "No such query."}, status=404)
        if _invoices_for(request.user).filter(pk=query.invoice_id).first() is None:
            return Response({"detail": "No such query."}, status=404)
        if query.raised_by_id == request.user.id:
            return Response(
                {"detail": "The other party has to accept this, not you."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        invoice = resolve_query(
            query,
            resolution=f"Accepted by {request.user.get_full_name()}.",
            by=request.user,
        )
        return Response(
            InvoiceDetailSerializer(
                invoice or query.invoice, context={'request': request}
            ).data
        )
