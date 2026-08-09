"""
Module 8 — Payments & Payouts: serializers.

Amounts go out in **both** forms: ``*_paise`` for the client to compare and sum,
and ``*_display`` for it to show. Deriving the display string client-side would
eventually produce an app screen and a PDF receipt that disagree about the same
payment, which is precisely the kind of discrepancy that turns into a dispute.

Nothing here ever accepts an amount in rupees as a float, and nothing exposes a
gateway secret or a signature.
"""

from __future__ import annotations

from rest_framework import serializers

from .models import (
    DisputeReason,
    Payment,
    PaymentDispute,
    PaymentKind,
    ReplacementSplit,
    SocietySubscription,
    format_paise,
)


class PaymentSerializer(serializers.ModelSerializer):
    """One ledger row, as both parties see it (Module 8.2)."""

    # A method field rather than `source="worker.user.get_full_name"`: the
    # emergency surcharge (Module 5.5) is owed to the platform and has no
    # worker, and a dotted source over a null FK is dropped from the payload
    # entirely, which reads to a client as "the server forgot" rather than as
    # "there is nobody".
    worker_name = serializers.SerializerMethodField()
    resident_name = serializers.CharField(
        source="resident.user.get_full_name", read_only=True
    )

    def get_worker_name(self, obj) -> str:
        if obj.worker_id is None:
            return "Sathify" if obj.is_platform_charge else ""
        return obj.worker.user.get_full_name()
    flat_label = serializers.CharField(source="resident.flat.__str__", read_only=True)
    kind_display = serializers.CharField(source="get_kind_display", read_only=True)

    total_paise = serializers.IntegerField(read_only=True)
    net_paise = serializers.IntegerField(read_only=True)
    worker_receives_paise = serializers.IntegerField(read_only=True)
    is_overdue = serializers.BooleanField(read_only=True)
    days_overdue = serializers.IntegerField(read_only=True)
    amount_display = serializers.SerializerMethodField()
    total_display = serializers.SerializerMethodField()

    class Meta:
        model = Payment
        fields = [
            "id",
            "receipt_number",
            "kind",
            "kind_display",
            "status",
            "worker",
            "worker_name",
            "resident",
            "resident_name",
            "flat_label",
            "engagement",
            "booking",
            "amount_paise",
            "tip_paise",
            "platform_fee_paise",
            "refunded_paise",
            "total_paise",
            "net_paise",
            "worker_receives_paise",
            "amount_display",
            "total_display",
            "period_start",
            "period_end",
            "due_at",
            "is_overdue",
            "days_overdue",
            "note",
            "failure_reason",
            "paid_at",
            "refunded_at",
            "created_at",
            # razorpay_order_id is included: the app needs it to reopen an
            # abandoned checkout. The signature and key secret never are.
            "razorpay_order_id",
        ]
        read_only_fields = fields

    def get_amount_display(self, obj) -> str:
        return format_paise(obj.amount_paise)

    def get_total_display(self, obj) -> str:
        return format_paise(obj.total_paise)


class SalaryBasisSerializer(serializers.Serializer):
    """The attendance arithmetic behind a suggested salary amount.

    Returned before the resident commits so they can see how the figure was
    reached — and so a worker can contest it against something concrete rather
    than against an unexplained number.
    """

    expected_visits = serializers.IntegerField(read_only=True)
    attended_visits = serializers.IntegerField(read_only=True)
    full_rate_paise = serializers.IntegerField(read_only=True)
    suggested_paise = serializers.IntegerField(read_only=True)
    period_start = serializers.DateField(read_only=True)
    period_end = serializers.DateField(read_only=True)
    is_full = serializers.BooleanField(read_only=True)
    explanation = serializers.CharField(source="explain", read_only=True)


class CreateEngagementPaymentSerializer(serializers.Serializer):
    """Module 8.1 — a resident pays a month's salary."""

    engagement = serializers.IntegerField()
    period_start = serializers.DateField()
    period_end = serializers.DateField()

    #: Omit to accept the attendance-derived suggestion. Supplied explicitly
    #: when the resident chooses to pay a different amount, which they are
    #: entitled to do — see services.salary_basis.
    amount_paise = serializers.IntegerField(required=False, min_value=1)
    tip_paise = serializers.IntegerField(required=False, min_value=0, default=0)
    note = serializers.CharField(
        required=False, allow_blank=True, default="", max_length=300
    )

    def validate(self, attrs):
        if attrs["period_end"] < attrs["period_start"]:
            raise serializers.ValidationError(
                {"period_end": "The period ends before it starts."}
            )
        return attrs


class CreateBookingPaymentSerializer(serializers.Serializer):
    """Module 8.1 — a resident pays for a one-day booking."""

    booking = serializers.IntegerField()
    tip_paise = serializers.IntegerField(required=False, min_value=0, default=0)
    note = serializers.CharField(
        required=False, allow_blank=True, default="", max_length=300
    )


class CheckoutPayloadSerializer(serializers.Serializer):
    """What the app hands to Razorpay Checkout.

    ``key`` is the public key id, which identifies the merchant in the checkout
    sheet. The key secret never leaves the server — which is why order creation
    happens there and not in the app.
    """

    key = serializers.CharField(read_only=True)
    order_id = serializers.CharField(read_only=True)
    amount = serializers.IntegerField(read_only=True)
    currency = serializers.CharField(read_only=True)
    name = serializers.CharField(read_only=True)
    description = serializers.CharField(read_only=True)
    test_mode = serializers.BooleanField(read_only=True)


class ConfirmUpiSettlementSerializer(serializers.Serializer):
    """Module 8.9 — what an administrator reads off a bank statement.

    ``amount_paise`` is required rather than assumed from the payment. Asking
    for the figure they can see, and refusing when it disagrees, is what makes
    this a *reconciliation* rather than a button that marks things paid — the
    administrator has to have actually looked.
    """

    utr = serializers.CharField(
        max_length=40,
        help_text="The bank's transaction reference. One UTR settles one payment.",
    )
    amount_paise = serializers.IntegerField(
        min_value=1,
        help_text="The amount shown on the statement, in paise. Must match.",
    )
    note = serializers.CharField(
        required=False, allow_blank=True, max_length=300, default=""
    )


class ConfirmCheckoutSerializer(serializers.Serializer):
    """The signed response Razorpay Checkout hands back to the app.

    All three fields are required: the signature is computed over the order and
    payment ids together, so a confirmation missing any of them cannot be
    verified and must not be accepted.
    """

    razorpay_payment_id = serializers.CharField(max_length=64)
    razorpay_signature = serializers.CharField(max_length=128)


class ReplacementSplitSerializer(serializers.ModelSerializer):
    """Module 8.5 — the agreed rule for paying a same-day replacement."""

    original_share_percent = serializers.IntegerField(read_only=True)

    class Meta:
        model = ReplacementSplit
        fields = ["replacement_share_percent", "original_share_percent", "note"]

    def validate_replacement_share_percent(self, value):
        if not 0 <= value <= 100:
            raise serializers.ValidationError("A share is between 0 and 100 percent.")
        return value


class PaymentDisputeSerializer(serializers.ModelSerializer):
    """Module 8.6 — a raised dispute, on its way to Module 11's queue."""

    raised_by_name = serializers.CharField(
        source="raised_by.get_full_name", read_only=True
    )
    reason_display = serializers.CharField(source="get_reason_display", read_only=True)
    receipt_number = serializers.CharField(
        source="payment.receipt_number", read_only=True
    )
    is_open = serializers.BooleanField(read_only=True)

    class Meta:
        model = PaymentDispute
        fields = [
            "id",
            "payment",
            "receipt_number",
            "raised_by",
            "raised_by_name",
            "reason",
            "reason_display",
            "description",
            "status",
            "is_open",
            "resolution",
            "resolved_at",
            "created_at",
        ]
        read_only_fields = [
            "id", "receipt_number", "raised_by", "raised_by_name", "reason_display",
            "status", "is_open", "resolution", "resolved_at", "created_at",
        ]


class RaiseDisputeSerializer(serializers.Serializer):
    reason = serializers.ChoiceField(choices=DisputeReason.choices)
    description = serializers.CharField(max_length=1000)

    def validate_description(self, value):
        if len(value.strip()) < 10:
            # An administrator cannot mediate "it's wrong". A few words of
            # context is the minimum that makes the queue actionable.
            raise serializers.ValidationError(
                "Please describe what went wrong, so it can be looked into."
            )
        return value.strip()


class ResolveDisputeSerializer(serializers.Serializer):
    upheld = serializers.BooleanField()
    resolution = serializers.CharField(max_length=1000)

    def validate_resolution(self, value):
        if not value.strip():
            raise serializers.ValidationError("Say how this was resolved.")
        return value.strip()


class MonthlySummarySerializer(serializers.Serializer):
    """Module 8.3 — the JSON form of a salary statement."""

    worker_name = serializers.CharField(read_only=True)
    society_name = serializers.CharField(read_only=True)
    year = serializers.IntegerField(read_only=True)
    month = serializers.IntegerField(read_only=True)
    month_name = serializers.CharField(read_only=True)
    payment_count = serializers.IntegerField(read_only=True)
    total_paise = serializers.IntegerField(read_only=True)
    total_display = serializers.CharField(read_only=True)
    tips_paise = serializers.IntegerField(read_only=True)
    tips_display = serializers.CharField(read_only=True)
    refunded_paise = serializers.IntegerField(read_only=True)
    refunded_display = serializers.CharField(read_only=True)
    lines = serializers.ListField(child=serializers.DictField(), read_only=True)


class ReceiptSerializer(serializers.Serializer):
    """A single transaction's receipt, for either party."""

    receipt_number = serializers.CharField(read_only=True)
    status = serializers.CharField(read_only=True)
    kind = serializers.CharField(read_only=True)
    description = serializers.CharField(read_only=True)
    paid_at = serializers.DateTimeField(read_only=True, allow_null=True)
    worker_name = serializers.CharField(read_only=True)
    resident_name = serializers.CharField(read_only=True)
    flat = serializers.CharField(read_only=True)
    amount_paise = serializers.IntegerField(read_only=True)
    amount_display = serializers.CharField(read_only=True)
    tip_paise = serializers.IntegerField(read_only=True)
    tip_display = serializers.CharField(read_only=True)
    total_paise = serializers.IntegerField(read_only=True)
    total_display = serializers.CharField(read_only=True)
    refunded_paise = serializers.IntegerField(read_only=True)
    net_paise = serializers.IntegerField(read_only=True)
    net_display = serializers.CharField(read_only=True)
    gateway_payment_id = serializers.CharField(read_only=True, allow_blank=True)


# ---------------------------------------------------------------------------
# 8.7 Fees, subscription, tip settlement
# ---------------------------------------------------------------------------


class FeeQuoteSerializer(serializers.Serializer):
    """What a booking costs, broken out, before the resident confirms."""

    amount_paise = serializers.IntegerField(read_only=True)
    platform_fee_paise = serializers.IntegerField(read_only=True)
    total_paise = serializers.IntegerField(read_only=True)

    #: False while fees are switched off, so the screen can stay silent rather
    #: than render a "₹0.00 platform fee" line nobody needs to read.
    fee_applies = serializers.BooleanField(read_only=True)


class SocietySubscriptionSerializer(serializers.ModelSerializer):
    """A society's entitlements.

    ``effective_tier`` rather than ``tier`` is what callers should read: a
    lapsed paid tier reports as FREE, so an expiry cannot be missed at a call
    site that only looked at the stored value.
    """

    tier_display = serializers.CharField(source="get_tier_display", read_only=True)
    effective_tier = serializers.CharField(read_only=True)
    is_active = serializers.BooleanField(read_only=True)
    includes_reports = serializers.BooleanField(read_only=True)
    worker_limit = serializers.IntegerField(read_only=True, allow_null=True)

    class Meta:
        model = SocietySubscription
        fields = [
            "id",
            "society",
            "tier",
            "tier_display",
            "effective_tier",
            "valid_until",
            "is_active",
            "includes_reports",
            "worker_limit",
            "note",
            "created_at",
        ]
        read_only_fields = fields


class TipOwedSerializer(serializers.Serializer):
    """One worker's outstanding tips, for hand settlement (Module 8.7).

    Grouped per worker rather than per payment because the administrator is
    handing over one amount to one person; the receipt numbers travel alongside
    so it can be reconciled against the ledger afterwards.
    """

    worker_id = serializers.IntegerField(read_only=True)
    worker_name = serializers.CharField(read_only=True)
    worker_phone = serializers.CharField(read_only=True)
    tip_paise = serializers.IntegerField(read_only=True)
    tip_display = serializers.CharField(read_only=True)
    payment_count = serializers.IntegerField(read_only=True)
    receipts = serializers.ListField(child=serializers.CharField(), read_only=True)
