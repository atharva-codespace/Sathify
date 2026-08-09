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
    SocietySubscription,
    UpiSettlement,
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
    # `settled_via` is filterable on purpose: "which payments rest on somebody's
    # word rather than a signature?" is a question an operator should be able to
    # answer with one click (Module 8.9).
    list_filter = ("status", "settled_via", "kind", "society", "created_at")
    search_fields = (
        "receipt_number",
        "razorpay_order_id",
        "razorpay_payment_id",
        "upi_settlement__utr",
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

    # --- Module 8.9: UPI reconciliation ------------------------------------

    actions = ["confirm_upi_transfer"]

    @admin.action(description="Confirm UPI transfer arrived (enter UTR)")
    def confirm_upi_transfer(self, request, queryset):
        """Settle selected payments against bank references.

        -------------------------------------------------------------------
        ONE AT A TIME, AND THAT IS THE POINT
        -------------------------------------------------------------------
        A UTR identifies exactly one transfer, so a bulk action cannot be
        meaningful: settling five payments would need five references, and any
        interface that let one reference clear five rows would be the precise
        thing the unique constraint exists to prevent. Selecting more than one
        row is therefore refused rather than quietly applied to the first.

        Everything else — the amount check, the closed-payment check, the audit
        row, and the ``on_payment_settled`` hook that broadcasts a reconciled
        emergency — comes from ``services.confirm_upi_settlement``. This form is
        a thin way in for an operator with a statement open, not a second
        implementation.
        """
        from django.contrib import messages

        from .services import PaymentError, confirm_upi_settlement

        payments = list(queryset)
        if len(payments) != 1:
            self.message_user(
                request,
                "Select exactly one payment. A UTR settles one transfer, so "
                "confirming in bulk is not something that can be done honestly.",
                level=messages.ERROR,
            )
            return

        payment = payments[0]
        utr = (request.POST.get("utr") or "").strip()
        if not utr:
            # First pass: the action was chosen but no reference typed yet.
            self.message_user(
                request,
                f"Enter the UTR for {payment.receipt_number} "
                f"({format_paise(payment.total_paise)}) in the UTR box, then "
                "run the action again.",
                level=messages.WARNING,
            )
            return

        try:
            confirm_upi_settlement(
                payment,
                utr=utr,
                # The admin has the payment's own figure in front of them in the
                # list; the API path is the one that makes them re-key it. Here
                # the guard that matters is the unique UTR.
                amount_paise=payment.total_paise,
                confirmed_by=request.user,
                note="Confirmed from the Django admin.",
            )
        except PaymentError as exc:
            self.message_user(request, str(exc), level=messages.ERROR)
            return

        self.message_user(
            request,
            f"{payment.receipt_number} settled against UTR {utr.upper()}.",
            level=messages.SUCCESS,
        )


@admin.register(UpiSettlement)
class UpiSettlementAdmin(admin.ModelAdmin):
    """Module 8.9 — every payment that was settled on somebody's word.

    ---------------------------------------------------------------------------
    WHY CONFIRMING IS NOT DONE FROM THIS FORM
    ---------------------------------------------------------------------------
    Adding a row here would settle a payment, and a Django admin form is the
    wrong instrument for that: it would skip the amount check, skip the closed-
    payment check, and skip ``on_payment_settled`` — so a reconciled emergency
    surcharge would never broadcast and the worker would never be told. The
    confirmation goes through ``services.confirm_upi_settlement``, exposed at
    ``POST /payments/<id>/settle-upi/`` and as the action on the payment list.

    This registration exists to *read* the trail: who confirmed what, when, and
    against which UTR.
    """

    list_display = ("created_at", "utr", "payment", "amount", "confirmed_by")
    search_fields = (
        "utr",
        "payment__receipt_number",
        "confirmed_by__first_name",
        "confirmed_by__last_name",
        "confirmed_by__phone_number",
    )
    date_hierarchy = "created_at"
    list_select_related = ("payment", "confirmed_by")
    raw_id_fields = ("payment", "confirmed_by")

    def get_readonly_fields(self, request, obj=None):
        return [field.name for field in self.model._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        # Deleting one would orphan a PAID payment from the only evidence that
        # justified it, and free the UTR to settle a second charge.
        return False

    @admin.display(description="Amount seen")
    def amount(self, obj):
        return format_paise(obj.amount_paise)


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


@admin.register(SocietySubscription)
class SocietySubscriptionAdmin(admin.ModelAdmin):
    """Module 8.7 — tiers, sold and set by hand.

    This *is* the checkout for now, deliberately: there is no self-serve
    purchase flow until a society has actually paid for a tier, and building one
    before that would be guessing at a funnel nobody has walked yet.
    """

    list_display = ("society", "tier", "valid_until", "is_active", "updated_at")
    list_filter = ("tier",)
    search_fields = ("society__name", "provider_reference")
    autocomplete_fields = ("society",)
    readonly_fields = ("created_at", "updated_at")

    @admin.display(boolean=True, description="Active")
    def is_active(self, obj):
        return obj.is_active
