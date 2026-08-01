"""
Module 8 — Payments & Payouts: Django Admin.

Payments are read-only here. A ledger row is a record of money that moved, and
editing one would leave the platform's books disagreeing with Razorpay's — which
is the one discrepancy nobody can reconcile after the fact. Refunds and
corrections go through the gateway, not through this form.

Webhook events are read-only for the same reason: they are the signed evidence
that a settlement was justified.
"""

from django.contrib import admin

from .models import (
    Payment,
    PaymentDispute,
    ReplacementSplit,
    WebhookEvent,
    format_paise,
)


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    """Module 8.2 — the ledger. Read-only by design."""

    list_display = (
        "receipt_number",
        "created_at",
        "kind",
        "status",
        "worker",
        "resident",
        "total",
        "refunded",
    )
    list_filter = ("status", "kind", "society", "created_at")
    search_fields = (
        "receipt_number",
        "razorpay_order_id",
        "razorpay_payment_id",
        "worker__user__first_name",
        "worker__user__last_name",
        "resident__user__first_name",
        "resident__user__last_name",
    )
    date_hierarchy = "created_at"
    list_select_related = ("worker__user", "resident__user")
    raw_id_fields = ("worker", "resident", "engagement", "booking", "society")

    def get_readonly_fields(self, request, obj=None):
        return [field.name for field in self.model._meta.fields] + ["total", "refunded"]

    def has_add_permission(self, request):
        # A payment is created by a resident paying, never by a form.
        return False

    def has_delete_permission(self, request, obj=None):
        # Financial records are kept (SRS 5.5).
        return False

    @admin.display(description="Total", ordering="amount_paise")
    def total(self, obj):
        return format_paise(obj.total_paise)

    @admin.display(description="Refunded")
    def refunded(self, obj):
        return format_paise(obj.refunded_paise) if obj.refunded_paise else "—"


@admin.register(WebhookEvent)
class WebhookEventAdmin(admin.ModelAdmin):
    """Module 8.1 — the signed messages behind every settlement.

    Filter on ``signature_valid=False`` when investigating: a run of invalid
    signatures means someone is probing the webhook endpoint.
    """

    list_display = (
        "created_at",
        "event_type",
        "event_id",
        "signature_valid",
        "processed",
        "payment",
    )
    list_filter = ("signature_valid", "processed", "event_type")
    search_fields = ("event_id", "event_type", "process_error")
    date_hierarchy = "created_at"
    raw_id_fields = ("payment",)

    def get_readonly_fields(self, request, obj=None):
        return [field.name for field in self.model._meta.fields]

    def has_add_permission(self, request):
        return False


@admin.register(ReplacementSplit)
class ReplacementSplitAdmin(admin.ModelAdmin):
    """Module 8.5 — the agreed rule for paying a same-day replacement."""

    list_display = (
        "engagement",
        "replacement_share_percent",
        "original_share_percent",
        "updated_at",
    )
    search_fields = (
        "engagement__worker__user__first_name",
        "engagement__worker__user__last_name",
        "engagement__resident__user__first_name",
        "engagement__resident__user__last_name",
    )
    raw_id_fields = ("engagement", "updated_by")
    readonly_fields = ("original_share_percent", "created_at", "updated_at")

    @admin.display(description="Original worker's share")
    def original_share_percent(self, obj):
        return f"{obj.original_share_percent}%"


@admin.register(PaymentDispute)
class PaymentDisputeAdmin(admin.ModelAdmin):
    """Module 8.6 — raised disputes. Module 11 will own the wider queue."""

    list_display = ("created_at", "payment", "raised_by", "reason", "status")
    list_filter = ("status", "reason", "society")
    search_fields = (
        "payment__receipt_number",
        "description",
        "resolution",
        "raised_by__first_name",
        "raised_by__last_name",
    )
    date_hierarchy = "created_at"
    list_select_related = ("payment", "raised_by")
    raw_id_fields = ("payment", "raised_by", "resolved_by", "society")
    readonly_fields = ("created_at", "updated_at", "resolved_at", "resolved_by")
