"""
Module 14 — console serializers.

-------------------------------------------------------------------------------
PHONE NUMBERS ARE MASKED BY DEFAULT
-------------------------------------------------------------------------------
The console is the largest privacy surface in the product: one screen, every
resident and worker on the platform. So contact details are masked in every list
and detail response, and revealing one is a separate, logged action carrying a
stated reason (``views.RevealContactView``).

This is deliberately the harder default. Masking later is a migration and a
retrofit of every screen; unmasking later is a config change. Choosing the
direction that is cheap to loosen and expensive to tighten is the only way round
that ends up where you want it.
"""

from __future__ import annotations

from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from apps.accounts.models import ImpersonationGrant, PlatformAccessLog, Role
from apps.administration.models import (
    ReportFormat,
    ReportJob,
    ReportKind,
    ReportScope,
)
from apps.attendance.models import SOURCE_TIER, WorkSession
from apps.payments.models import (
    Invoice,
    Payment,
    SubscriptionTier,
    TIER_LIMITS,
    format_paise,
)
from apps.societies.models import Society


def mask_phone(number: str) -> str:
    """``9876543210`` -> ``98xxxxxx10``.

    Keeps enough to recognise a number you already know and not enough to dial
    one you do not.
    """
    if not number:
        return ""
    digits = number[-10:]
    if len(digits) < 10:
        return "x" * len(digits)
    return f"{digits[:2]}{'x' * 6}{digits[-2:]}"


@extend_schema_field(
    {
        "type": "object",
        "properties": {
            "paise": {"type": "integer", "description": "Integer minor units. The real number."},
            "display": {"type": "string", "example": "₹4,200.00"},
        },
        "required": ["paise", "display"],
    }
)
class MoneyField(serializers.Field):
    """Paise on the wire, with a formatted twin for display.

    Both, because the console is read by people and by spreadsheets. Sending
    only the formatted string would make the client parse currency back out of
    text; sending only paise would have every screen re-implement the same
    formatting, differently.

    The schema annotation is not cosmetic: without it drf-spectacular types this
    as a plain string, and a console generated from that contract would try to
    render an object as text on every money column in the product.
    """

    def to_representation(self, value):
        value = int(value or 0)
        return {"paise": value, "display": format_paise(value)}

    def to_internal_value(self, data):
        return int(data)


# ---------------------------------------------------------------------------
# Transactions
# ---------------------------------------------------------------------------


class PaymentRowSerializer(serializers.ModelSerializer):
    """One row in the cross-society ledger (Plate 02)."""

    society_name = serializers.CharField(source="society.name", read_only=True)
    resident_label = serializers.SerializerMethodField()
    worker_label = serializers.SerializerMethodField()
    amount = MoneyField(source="amount_paise", read_only=True)
    total = MoneyField(source="total_paise", read_only=True)
    #: The one column a finance operator scans for. Two settlement paths are
    #: HMAC-verified and one is a person's word against a bank statement.
    rests_on_a_person = serializers.SerializerMethodField()
    days_overdue = serializers.IntegerField(read_only=True)

    class Meta:
        model = Payment
        fields = [
            "id", "receipt_number", "created_at", "paid_at", "due_at",
            "society", "society_name", "resident_label", "worker_label",
            "kind", "status", "settled_via", "rests_on_a_person",
            "amount", "total", "is_overdue", "days_overdue",
        ]

    def get_resident_label(self, obj) -> str:
        resident = obj.resident
        if resident is None:
            return ""
        flat = getattr(resident, "flat", None)
        return str(flat) if flat else str(resident)

    def get_worker_label(self, obj) -> str:
        return str(obj.worker) if obj.worker_id else "(platform)"

    def get_rests_on_a_person(self, obj) -> bool:
        from apps.payments.models import SettledVia

        return obj.settled_via == SettledVia.UPI_MANUAL


class PaymentDetailSerializer(PaymentRowSerializer):
    """The drawer: the row, plus what settled it and what it was for."""

    platform_fee = MoneyField(source="platform_fee_paise", read_only=True)
    worker_receives = MoneyField(source="worker_receives_paise", read_only=True)
    settlement_evidence = serializers.SerializerMethodField()
    invoice = serializers.SerializerMethodField()

    class Meta(PaymentRowSerializer.Meta):
        fields = PaymentRowSerializer.Meta.fields + [
            "platform_fee", "worker_receives", "period_start", "period_end",
            "note", "settlement_evidence", "invoice",
        ]

    def get_settlement_evidence(self, obj) -> dict:
        """What this payment's PAID status actually rests on.

        Spelled out rather than left to be inferred from ``settled_via``,
        because the distinction between a verified signature and somebody's
        assertion is the single most important thing on this screen.
        """
        settlement = getattr(obj, "upi_settlement", None)
        if settlement is not None:
            return {
                "kind": "assertion",
                "utr": settlement.utr,
                "amount_seen": format_paise(settlement.amount_paise),
                "confirmed_by": str(settlement.confirmed_by) if settlement.confirmed_by else "",
                "note": settlement.note,
                "warning": "No gateway signature. Confirmed against a bank statement by a person.",
            }
        if obj.razorpay_signature:
            return {
                "kind": "signature",
                "razorpay_payment_id": obj.razorpay_payment_id,
                "verified": True,
            }
        return {"kind": "none", "verified": False}

    def get_invoice(self, obj) -> dict | None:
        invoice = getattr(obj, "invoice", None)
        if invoice is None:
            return None
        return {
            "number": invoice.number,
            "sessions": invoice.lines.filter(session__isnull=False)
            .values("session_id").distinct().count(),
            "billable_minutes": sum(line.minutes for line in invoice.lines.all()),
            "held": format_paise(invoice.held_paise),
        }


class InvoiceRowSerializer(serializers.ModelSerializer):
    total = MoneyField(source="total_paise", read_only=True)
    payable = MoneyField(source="payable_paise", read_only=True)
    held = MoneyField(source="held_paise", read_only=True)
    society_name = serializers.CharField(source="society.name", read_only=True)

    class Meta:
        model = Invoice
        fields = [
            "id", "number", "society", "society_name", "status",
            "period_start", "period_end", "review_closes_at", "issued_at",
            "total", "payable", "held",
        ]


# ---------------------------------------------------------------------------
# Societies
# ---------------------------------------------------------------------------


class SocietyRowSerializer(serializers.ModelSerializer):
    tier = serializers.SerializerMethodField()
    workers = serializers.SerializerMethodField()
    worker_cap = serializers.SerializerMethodField()
    over_cap = serializers.SerializerMethodField()

    class Meta:
        model = Society
        fields = [
            "id", "name", "city", "state", "status", "total_flats",
            "tier", "workers", "worker_cap", "over_cap", "created_at",
        ]

    def _tier(self, obj) -> str:
        subscription = getattr(obj, "subscription", None)
        return subscription.effective_tier if subscription else SubscriptionTier.FREE

    def get_tier(self, obj) -> str:
        return self._tier(obj)

    def get_workers(self, obj) -> int:
        return obj.users.filter(role=Role.WORKER, is_approved=True).count()

    def get_worker_cap(self, obj) -> int | None:
        """None means unlimited, which is what the paid tiers grant."""
        return TIER_LIMITS[self._tier(obj)]["workers"]

    def get_over_cap(self, obj) -> bool:
        cap = self.get_worker_cap(obj)
        return cap is not None and self.get_workers(obj) > cap


class SocietyDetailSerializer(SocietyRowSerializer):
    gates = serializers.SerializerMethodField()
    admins = serializers.SerializerMethodField()
    billing = serializers.SerializerMethodField()
    integrity = serializers.SerializerMethodField()
    #: Restated on every detail response so no operator can reach the suspend
    #: button without having been told what it does and does not stop.
    suspension_scope = serializers.SerializerMethodField()

    class Meta(SocietyRowSerializer.Meta):
        fields = SocietyRowSerializer.Meta.fields + [
            "address_line", "pincode", "total_towers",
            "gates", "admins", "billing", "integrity", "suspension_scope",
        ]

    def get_gates(self, obj) -> int:
        return obj.gates.count() if hasattr(obj, "gates") else 0

    def get_admins(self, obj) -> list:
        return [
            {"id": u.id, "name": u.get_full_name(), "phone": mask_phone(u.phone_number)}
            for u in obj.users.filter(role=Role.SOCIETY_ADMIN)
        ]

    def get_billing(self, obj) -> dict:
        from apps.societies.models import SocietyBillingConfig

        config = SocietyBillingConfig.for_society(obj)
        return {
            "visit_overhead_minutes": config.visit_overhead_minutes,
            "visit_fee_policy": config.visit_fee_policy,
            "round_minutes": config.round_minutes,
            "round_up_in_workers_favour": config.round_up_in_workers_favour,
            "ot_multiplier_bp": config.ot_multiplier_bp,
            "review_window_hours": config.review_window_hours,
        }

    def get_integrity(self, obj) -> dict:
        from . import metrics

        return metrics.billing_integrity(society_id=obj.id)

    def get_suspension_scope(self, obj) -> dict:
        return {
            "stops": ["reporting", "new onboarding", "subscription features"],
            "keeps_working": ["gate checks", "attendance writes", "complaint intake"],
            "why": (
                "Locking a society out of its own attendance record for an unpaid "
                "invoice would put workers' wages behind a billing dispute."
            ),
        }


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------


class ConsoleUserSerializer(serializers.Serializer):
    """Global user search. Contact details masked — see the module docstring."""

    id = serializers.IntegerField(read_only=True)
    name = serializers.SerializerMethodField()
    role = serializers.CharField(read_only=True)
    phone = serializers.SerializerMethodField()
    society = serializers.IntegerField(source="society_id", read_only=True)
    society_name = serializers.SerializerMethodField()
    is_approved = serializers.BooleanField(read_only=True)
    is_phone_verified = serializers.BooleanField(read_only=True)
    last_login = serializers.DateTimeField(read_only=True)
    date_joined = serializers.DateTimeField(read_only=True)

    def get_name(self, obj) -> str:
        return obj.get_full_name() or "(no name)"

    def get_phone(self, obj) -> str:
        return mask_phone(obj.phone_number)

    def get_society_name(self, obj) -> str:
        return obj.society.name if obj.society_id else ""


class RevealContactSerializer(serializers.Serializer):
    """A reason is required, and it is stored. That is the whole point."""

    reason = serializers.CharField(max_length=300)

    def validate_reason(self, value: str) -> str:
        if len(value.strip()) < 10:
            raise serializers.ValidationError(
                "State why this contact detail is needed — this is recorded and "
                "is visible to the society the person belongs to."
            )
        return value.strip()


# ---------------------------------------------------------------------------
# Activity and audit
# ---------------------------------------------------------------------------


class WorkSessionRowSerializer(serializers.ModelSerializer):
    society_name = serializers.CharField(source="society.name", read_only=True)
    worker_name = serializers.SerializerMethodField()
    tier = serializers.SerializerMethodField()
    total = MoneyField(source="total_paise", read_only=True)

    class Meta:
        model = WorkSession
        fields = [
            "id", "society", "society_name", "worker_name", "visit_date",
            "started_at", "ended_at", "source", "tier", "status",
            "billable_minutes", "overtime_minutes", "unbilled_extra_minutes",
            "needs_review", "review_note", "total",
        ]

    def get_worker_name(self, obj) -> str:
        return str(obj.worker)

    def get_tier(self, obj) -> int:
        return SOURCE_TIER.get(obj.source, 5)


class ImpersonationGrantSerializer(serializers.ModelSerializer):
    superadmin_name = serializers.SerializerMethodField()
    target_name = serializers.SerializerMethodField()
    society_name = serializers.CharField(source="society.name", read_only=True)
    is_live = serializers.BooleanField(read_only=True)

    class Meta:
        model = ImpersonationGrant
        fields = [
            "id", "superadmin", "superadmin_name", "target", "target_name",
            "society", "society_name", "reason", "started_at", "expires_at",
            "ended_at", "is_live", "reads", "writes",
        ]
        read_only_fields = ["started_at", "expires_at", "ended_at", "reads", "writes"]

    def get_superadmin_name(self, obj) -> str:
        return str(obj.superadmin) if obj.superadmin_id else ""

    def get_target_name(self, obj) -> str:
        return str(obj.target) if obj.target_id else ""


class StartImpersonationSerializer(serializers.Serializer):
    target = serializers.IntegerField()
    reason = serializers.CharField(max_length=300)
    minutes = serializers.IntegerField(required=False, min_value=5, max_value=120)

    def validate_reason(self, value: str) -> str:
        if len(value.strip()) < 10:
            raise serializers.ValidationError(
                "State why you need to act as this administrator. This is logged "
                "and shown in the society's own audit trail."
            )
        return value.strip()


class PlatformAccessLogSerializer(serializers.ModelSerializer):
    superadmin_name = serializers.SerializerMethodField()
    society_name = serializers.SerializerMethodField()

    class Meta:
        model = PlatformAccessLog
        fields = [
            "id", "created_at", "superadmin", "superadmin_name",
            "society", "society_name", "model_label", "action",
            "reason", "row_count", "ip_address",
        ]

    def get_superadmin_name(self, obj) -> str:
        return str(obj.superadmin) if obj.superadmin_id else "(deleted)"

    def get_society_name(self, obj) -> str:
        return obj.society.name if obj.society_id else "(all societies)"


class SuspendSocietySerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=300)
    #: Required to be explicitly true. Suspension is the console action most
    #: likely to be believed to do more than it does, so the operator confirms
    #: the narrow scope rather than being told it afterwards.
    acknowledge_gate_keeps_working = serializers.BooleanField()

    def validate_acknowledge_gate_keeps_working(self, value: bool) -> bool:
        if not value:
            raise serializers.ValidationError(
                "Suspension never stops gate checks, attendance writes or "
                "complaints. Confirm you understand what it does not do."
            )
        return value

    def validate_reason(self, value: str) -> str:
        if len(value.strip()) < 10:
            raise serializers.ValidationError("State why this society is being suspended.")
        return value.strip()


class ChangeTierSerializer(serializers.Serializer):
    tier = serializers.ChoiceField(choices=SubscriptionTier.choices)
    valid_until = serializers.DateField(required=False, allow_null=True)
    reason = serializers.CharField(max_length=300)


# ---------------------------------------------------------------------------
# Response shapes
#
# These exist for the generated OpenAPI contract rather than for validation:
# config/urls.py calls the schema "the contract the client is generated
# against", and an endpoint drf-spectacular cannot introspect is simply absent
# from it. A console screen built against a schema with holes in it discovers
# the real response shape at runtime, which is the expensive way to find out.
# ---------------------------------------------------------------------------


class BillingIntegritySerializer(serializers.Serializer):
    sessions = serializers.IntegerField()
    trusted_capture_rate = serializers.FloatField(allow_null=True)
    auto_close_rate = serializers.FloatField(allow_null=True)
    flagged_rate = serializers.FloatField(allow_null=True)
    by_tier = serializers.DictField(child=serializers.IntegerField())
    hourly_billing_advised = serializers.BooleanField()
    window_days = serializers.IntegerField()


class ReconciliationBucketSerializer(serializers.Serializer):
    count = serializers.IntegerField()
    results = PaymentRowSerializer(many=True)
    note = serializers.CharField(required=False)


class ReconciliationSerializer(serializers.Serializer):
    webhook_gaps = ReconciliationBucketSerializer()
    unsigned_settlements = ReconciliationBucketSerializer()


class SuspensionScopeSerializer(serializers.Serializer):
    stopped = serializers.ListField(child=serializers.CharField())
    still_working = serializers.ListField(child=serializers.CharField())


class SuspensionResultSerializer(serializers.Serializer):
    status = serializers.CharField()
    scope = SuspensionScopeSerializer()


class TierResultSerializer(serializers.Serializer):
    tier = serializers.CharField()
    effective_tier = serializers.CharField()
    valid_until = serializers.DateField(allow_null=True)
    is_active = serializers.BooleanField()


class RevealedContactSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    phone_number = serializers.CharField()
    email = serializers.CharField(allow_blank=True)
    logged = serializers.BooleanField()
    visible_to_society = serializers.BooleanField()


# ---------------------------------------------------------------------------
# Module 11.5 — cross-society report jobs
# ---------------------------------------------------------------------------


class ReportJobSocietySerializer(serializers.Serializer):
    """Per-society state, so a partial job can say *which* societies are missing.

    A count would not do. An operator handed "3 failed" cannot tell whether the
    gap matters to the question they asked; a list of names can be read against
    it in a second.
    """

    society = serializers.IntegerField(source="society_id")
    society_name = serializers.CharField(source="society.name")
    status = serializers.CharField()
    attempts = serializers.IntegerField()
    row_count = serializers.IntegerField()
    last_error = serializers.CharField()


class ReportJobSerializer(serializers.ModelSerializer):
    requested_by_name = serializers.SerializerMethodField()
    progress = serializers.DictField(read_only=True)
    is_downloadable = serializers.BooleanField(read_only=True)
    period_label = serializers.CharField(read_only=True)
    failed_societies = serializers.SerializerMethodField()
    can_retry = serializers.SerializerMethodField()

    class Meta:
        model = ReportJob
        fields = [
            "id", "kind", "scope", "tier", "status", "period_start", "period_end",
            "period_label", "formats", "include_pii", "reason", "row_count",
            "attempts", "last_error", "created_at", "started_at", "finished_at",
            "expires_at", "requested_by", "requested_by_name", "progress",
            "is_downloadable", "failed_societies", "can_retry",
        ]

    def get_requested_by_name(self, obj) -> str:
        return str(obj.requested_by) if obj.requested_by_id else "(deleted)"

    def get_failed_societies(self, obj) -> list:
        from apps.administration.models import ReportJobStatus

        rows = obj.society_jobs.filter(status=ReportJobStatus.FAILED).select_related(
            "society"
        )
        return ReportJobSocietySerializer(rows, many=True).data

    def get_can_retry(self, obj) -> bool:
        return any(row.can_retry for row in obj.society_jobs.all())


class CreateReportJobSerializer(serializers.Serializer):
    kind = serializers.ChoiceField(choices=ReportKind.choices)
    scope = serializers.ChoiceField(choices=ReportScope.choices, default=ReportScope.ALL)
    tier = serializers.CharField(required=False, allow_blank=True, default="")
    societies = serializers.ListField(
        child=serializers.IntegerField(), required=False, default=list
    )
    period_start = serializers.DateField()
    period_end = serializers.DateField()
    formats = serializers.ListField(
        child=serializers.ChoiceField(choices=ReportFormat.choices),
        allow_empty=False,
    )
    include_pii = serializers.BooleanField(default=False)
    reason = serializers.CharField(max_length=300, required=False, allow_blank=True)

    def validate(self, attrs):
        if attrs["period_end"] < attrs["period_start"]:
            raise serializers.ValidationError(
                {"period_end": "The period ends before it starts."}
            )
        if attrs["scope"] == ReportScope.TIER and not attrs.get("tier"):
            raise serializers.ValidationError({"tier": "Choose a tier."})
        if attrs["scope"] == ReportScope.SELECTED and not attrs.get("societies"):
            raise serializers.ValidationError(
                {"societies": "Choose at least one society."}
            )
        # PII in a cross-society export is the largest privacy surface the
        # console has, so it costs a stated purpose — the same price the
        # single-record reveal charges.
        if attrs.get("include_pii") and len((attrs.get("reason") or "").strip()) < 10:
            raise serializers.ValidationError(
                {
                    "reason": "State why names are needed. This export is logged "
                    "and the reason is visible to the societies in it."
                }
            )
        return attrs


class SweepResultSerializer(serializers.Serializer):
    built = serializers.IntegerField()
    failed = serializers.IntegerField()
    finished = serializers.IntegerField()
