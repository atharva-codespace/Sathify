"""
Module 8 — Payments & Payouts.

Moves money between residents and workers with a complete paper trail, without
the platform ever holding a card or bank detail — all of that is Razorpay's.

-------------------------------------------------------------------------------
MONEY IS STORED IN PAISE, AS INTEGERS. ALWAYS.
-------------------------------------------------------------------------------
Every amount in this module is an integer count of paise (1 rupee = 100 paise).
Never a float, never a Decimal, never rupees.

Floats cannot represent 0.1 exactly, so a float ledger drifts by fractions of a
paisa per row and eventually fails to reconcile against Razorpay — which counts
in paise and is right to. Rupees-as-integers would lose every sub-rupee amount,
and Razorpay quotes fees in paise.

**The boundary is here.** Modules 4 and 5 store ``Engagement.monthly_rate`` and
``Booking.quoted_price`` as whole rupees, because that is what a resident and a
worker agree out loud. :func:`rupees_to_paise` is the single crossing point, and
nothing in this module should ever see a rupee figure again after it.

-------------------------------------------------------------------------------
A PAYMENT ROW IS A CLAIM UNTIL RAZORPAY SAYS OTHERWISE
-------------------------------------------------------------------------------
``status`` starts at CREATED and only ever reaches PAID through a
signature-verified message from Razorpay — either the client handing back a
signed checkout response or a webhook. Nothing a client asserts about payment
success is trusted on its own, because the client is the party that benefits
from lying about it.
"""

from __future__ import annotations

import uuid

from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.models import SocietyScopedModel, TimeStampedModel, UUIDPrimaryKeyModel

#: Minor units per rupee. Named rather than inlined so the conversions below
#: read as conversions instead of as arbitrary arithmetic.
PAISE_PER_RUPEE = 100


def rupees_to_paise(rupees: int) -> int:
    """Cross from the rupee figures Modules 4 and 5 hold into paise.

    Takes an int because that is what those modules store. Passing a float here
    is a bug — see the module docstring — so it is rejected rather than rounded.
    """
    if isinstance(rupees, bool) or not isinstance(rupees, int):
        raise TypeError(
            "Amounts cross into this module as whole rupees (int). "
            f"Got {type(rupees).__name__}."
        )
    return rupees * PAISE_PER_RUPEE


def format_paise(paise: int) -> str:
    """Display form, e.g. 450050 -> '₹4,500.50'."""
    rupees, remainder = divmod(int(paise), PAISE_PER_RUPEE)
    return f"₹{rupees:,}.{remainder:02d}"


class PaymentKind(models.TextChoices):
    ENGAGEMENT_SALARY = "engagement_salary", _("Monthly salary for a recurring hire")
    BOOKING = "booking", _("One-day booking")
    TIP = "tip", _("Tip")
    REFUND = "refund", _("Refund")
    #: A same-day replacement's share, split per Module 8.5's rule.
    REPLACEMENT = "replacement", _("Replacement worker's share")


class PaymentStatus(models.TextChoices):
    CREATED = "created", _("Order created, not yet attempted")
    PENDING = "pending", _("Awaiting confirmation from Razorpay")
    PAID = "paid", _("Paid")
    FAILED = "failed", _("Failed")
    REFUNDED = "refunded", _("Refunded")
    CANCELLED = "cancelled", _("Cancelled before payment")


class PaymentQuerySet(models.QuerySet):
    def settled(self):
        return self.filter(status=PaymentStatus.PAID)

    def for_period(self, start, end):
        """Payments settled within a date range, by settlement date.

        Deliberately keyed on ``paid_at`` rather than ``created_at``: a salary
        summary is about money that actually moved that month, not about orders
        that happened to be opened then.
        """
        return self.settled().filter(paid_at__date__gte=start, paid_at__date__lte=end)


class Payment(UUIDPrimaryKeyModel, SocietyScopedModel, TimeStampedModel):
    """Module 8.2 — one line in the ledger.

    A UUID primary key because the id is handed to Razorpay as the order receipt
    and appears in URLs; a sequential integer would leak transaction volume and
    let anyone enumerate the platform's payments.
    """

    resident = models.ForeignKey(
        "societies.Resident", on_delete=models.PROTECT, related_name="payments"
    )
    worker = models.ForeignKey(
        "workers.WorkerProfile", on_delete=models.PROTECT, related_name="payments"
    )

    # What this pays for. Both nullable and non-exclusive: a tip belongs to
    # whichever of the two produced it, and a refund may reference either.
    engagement = models.ForeignKey(
        "hiring.Engagement",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="payments",
    )
    booking = models.ForeignKey(
        "bookings.Booking",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="payments",
    )

    kind = models.CharField(max_length=30, choices=PaymentKind.choices, db_index=True)

    # --- Money. Paise, integers. See the module docstring. -----------------
    amount_paise = models.PositiveIntegerField(help_text=_("Base amount, in paise."))
    tip_paise = models.PositiveIntegerField(
        default=0,
        help_text=_(
            "Module 8.4 — charged in the same Razorpay transaction, not "
            "separately, so the resident authorises one amount."
        ),
    )
    #: Module 8.7 — Sathify's own share, frozen at creation.
    #:
    #: Stored rather than derived on read: deriving it would mean a rate change
    #: silently rewrites every historical receipt, so a resident who queried a
    #: charge from three months ago would be shown a number that never happened.
    #: Zero on everything today — see ``apps/payments/fees.py`` for why the
    #: column ships before the price does.
    platform_fee_paise = models.PositiveIntegerField(
        default=0,
        help_text=_("Platform fee charged on top of the amount, in paise."),
    )

    refunded_paise = models.PositiveIntegerField(default=0)

    status = models.CharField(
        max_length=20,
        choices=PaymentStatus.choices,
        default=PaymentStatus.CREATED,
        db_index=True,
    )
    failure_reason = models.CharField(max_length=200, blank=True)

    # --- Razorpay's side. The platform never stores card or bank details. ---
    razorpay_order_id = models.CharField(max_length=64, blank=True, db_index=True)
    razorpay_payment_id = models.CharField(max_length=64, blank=True, db_index=True)
    #: Kept for the audit trail: it is the evidence that PAID was justified.
    razorpay_signature = models.CharField(max_length=128, blank=True)

    # --- Module 8.3 ---------------------------------------------------------
    receipt_number = models.CharField(max_length=32, unique=True, db_index=True)
    period_start = models.DateField(
        null=True, blank=True, help_text=_("For a monthly salary payment.")
    )
    period_end = models.DateField(null=True, blank=True)

    #: Module 8.8 — when this is owed, shown to the resident at confirmation.
    #:
    #: Nullable because rows created before this field existed genuinely have no
    #: answer, and back-filling a guessed date would be worse than an honest
    #: blank: "we don't know when this was due" is true, "it was due on the 1st"
    #: might not be. Derived by ``services.payment_due_at`` for new rows.
    due_at = models.DateTimeField(null=True, blank=True, db_index=True)

    paid_at = models.DateTimeField(null=True, blank=True, db_index=True)
    refunded_at = models.DateTimeField(null=True, blank=True)
    note = models.CharField(max_length=300, blank=True)

    objects = PaymentQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["worker", "-paid_at"]),
            models.Index(fields=["resident", "-created_at"]),
            models.Index(fields=["status", "-created_at"]),
        ]

    def __str__(self):
        return f"{self.receipt_number} — {format_paise(self.total_paise)} ({self.status})"

    def save(self, *args, **kwargs):
        if not self.receipt_number:
            self.receipt_number = self._generate_receipt_number()
        super().save(*args, **kwargs)

    def _generate_receipt_number(self) -> str:
        """Human-readable and unique.

        Date-prefixed so a worker can find last March's receipt by eye, with a
        UUID tail rather than a running counter — a counter would need locking
        and would leak how many payments the platform has processed.
        """
        stamp = timezone.localdate().strftime("%Y%m")
        return f"SATH-{stamp}-{uuid.uuid4().hex[:8].upper()}"

    # --- Derived money ------------------------------------------------------

    @property
    def total_paise(self) -> int:
        """What the resident is actually charged — tip and platform fee included.

        The fee rides *on top of* the amount rather than being taken out of it,
        so the worker's figure never moves because Sathify changed its pricing.
        Zero today (Module 8.7), which is why adding it here changes nothing yet.
        """
        return self.amount_paise + self.tip_paise + self.platform_fee_paise

    @property
    def is_overdue(self) -> bool:
        """Past its due date and still unsettled.

        A refunded or cancelled payment is never overdue — there is nothing
        outstanding to be late with.
        """
        if self.due_at is None:
            return False
        if self.status in {
            PaymentStatus.PAID,
            PaymentStatus.REFUNDED,
            PaymentStatus.CANCELLED,
        }:
            return False
        return self.due_at < timezone.now()

    @property
    def days_overdue(self) -> int:
        return (timezone.now() - self.due_at).days if self.is_overdue else 0

    @property
    def worker_receives_paise(self) -> int:
        """What reaches the worker. The number that must never quietly shrink.

        Deliberately its own property rather than "total minus fee": a reader
        checking whether a fee was taken out of somebody's wage should find one
        expression that says it was not.
        """
        return self.amount_paise + self.tip_paise

    @property
    def net_paise(self) -> int:
        """What stands after any refund."""
        return max(0, self.total_paise - self.refunded_paise)

    @property
    def amount_display(self) -> str:
        return format_paise(self.total_paise)

    # --- State --------------------------------------------------------------

    @property
    def is_settled(self) -> bool:
        return self.status == PaymentStatus.PAID

    @property
    def is_open(self) -> bool:
        """Still awaiting an outcome — the resident may still pay it."""
        return self.status in {PaymentStatus.CREATED, PaymentStatus.PENDING}

    def mark_paid(self, *, razorpay_payment_id: str, signature: str = "") -> bool:
        """Settle the payment. Idempotent.

        Only ever called from a signature-verified path. A second confirmation
        for the same payment — Razorpay retries webhooks — must not move
        ``paid_at``, because that timestamp decides which month's salary
        summary this lands in.
        """
        if self.status == PaymentStatus.PAID:
            return False

        self.status = PaymentStatus.PAID
        self.razorpay_payment_id = razorpay_payment_id
        self.razorpay_signature = signature
        self.paid_at = timezone.now()
        self.failure_reason = ""
        self.save(
            update_fields=[
                "status", "razorpay_payment_id", "razorpay_signature",
                "paid_at", "failure_reason", "updated_at",
            ]
        )
        return True

    def mark_failed(self, *, reason: str = "") -> bool:
        """Record a failure. A settled payment is never un-settled by one."""
        if self.status in {PaymentStatus.PAID, PaymentStatus.REFUNDED}:
            return False

        self.status = PaymentStatus.FAILED
        self.failure_reason = reason[:200]
        self.save(update_fields=["status", "failure_reason", "updated_at"])
        return True

    def mark_refunded(self, *, amount_paise: int | None = None) -> bool:
        """Record a refund, full or partial."""
        if self.status != PaymentStatus.PAID:
            return False

        refund = self.total_paise if amount_paise is None else min(amount_paise, self.total_paise)
        self.refunded_paise = refund
        self.refunded_at = timezone.now()
        # A partial refund leaves the payment settled — it happened, and the
        # ledger has to keep saying so.
        if refund >= self.total_paise:
            self.status = PaymentStatus.REFUNDED
        self.save(
            update_fields=["refunded_paise", "refunded_at", "status", "updated_at"]
        )
        return True


class WebhookEvent(TimeStampedModel):
    """Module 8.1 — every webhook Razorpay sends, verified and stored.

    Kept as its own table rather than being applied and discarded, for two
    reasons:

    * **Idempotency.** Razorpay retries until it gets a 2xx, so the same event
      arrives repeatedly. ``event_id`` is unique, which makes replaying one a
      no-op rather than a double settlement.
    * **Audit.** When a payment's status is disputed, the raw signed message
      that caused it is the evidence. Applying and forgetting would leave only
      the conclusion.

    Rows with ``signature_valid=False`` are stored too. A stream of them is
    someone probing the endpoint, and that is exactly what an operator wants to
    be able to see.
    """

    event_id = models.CharField(
        max_length=128,
        unique=True,
        db_index=True,
        help_text=_("Razorpay's x-razorpay-event-id header."),
    )
    event_type = models.CharField(max_length=64, blank=True, db_index=True)
    payload = models.JSONField(default=dict, blank=True)

    signature_valid = models.BooleanField(default=False)
    processed = models.BooleanField(default=False, db_index=True)
    processed_at = models.DateTimeField(null=True, blank=True)
    process_error = models.CharField(max_length=300, blank=True)

    payment = models.ForeignKey(
        Payment,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="webhook_events",
    )

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["event_type", "-created_at"])]

    def __str__(self):
        return f"{self.event_type or 'webhook'} ({self.event_id})"

    def mark_processed(self, *, error: str = "") -> None:
        self.processed = True
        self.processed_at = timezone.now()
        self.process_error = error[:300]
        self.save(update_fields=["processed", "processed_at", "process_error", "updated_at"])


class ReplacementSplit(TimeStampedModel):
    """Module 8.5 — how a same-day replacement is paid, per engagement.

    Configured per engagement rather than platform-wide because the fair answer
    genuinely differs: a household that arranged the cover themselves may pay
    the replacement in full, while one whose regular worker sent a substitute
    may expect the regular worker to bear part of it. The rule is agreed once
    and then applied automatically, rather than argued about on the day.
    """

    engagement = models.OneToOneField(
        "hiring.Engagement", on_delete=models.CASCADE, related_name="replacement_split"
    )
    replacement_share_percent = models.PositiveSmallIntegerField(
        default=100,
        help_text=_("Share of the day's pay going to the replacement worker."),
    )
    note = models.CharField(max_length=300, blank=True)
    updated_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="updated_replacement_splits",
    )

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(replacement_share_percent__lte=100),
                name="replacement_share_at_most_100",
            )
        ]

    def __str__(self):
        return f"{self.replacement_share_percent}% to the replacement"

    @property
    def original_share_percent(self) -> int:
        """Whatever is left. Derived so the two can never fail to total 100."""
        return 100 - self.replacement_share_percent

    def split(self, day_rate_paise: int) -> tuple[int, int]:
        """Divide a day's pay. Returns ``(replacement, original)``.

        The remainder goes to the replacement — they did the work, and rounding
        against the person who turned up is the wrong default.
        """
        replacement = day_rate_paise * self.replacement_share_percent // 100
        return replacement, day_rate_paise - replacement


class SubscriptionTier(models.TextChoices):
    FREE = "free", _("Free")
    STANDARD = "standard", _("Standard")
    PLUS = "plus", _("Plus")


#: What each tier unlocks. A table rather than per-tier ``if`` branches scattered
#: through views, so "what does Standard actually get?" has exactly one answer.
#:
#: ``None`` means unlimited.
TIER_LIMITS: dict[str, dict] = {
    SubscriptionTier.FREE: {
        "workers": 25,
        "history_days": 30,
        "admins": 1,
        "reports": False,
        "waives_booking_fee": False,
    },
    SubscriptionTier.STANDARD: {
        "workers": None,
        "history_days": 365,
        "admins": 3,
        "reports": True,
        "waives_booking_fee": False,
    },
    SubscriptionTier.PLUS: {
        "workers": None,
        "history_days": 1095,
        "admins": 10,
        "reports": True,
        "waives_booking_fee": True,
    },
}


class SocietySubscription(TimeStampedModel):
    """Module 8.7 — what a society is entitled to.

    ---------------------------------------------------------------------------
    THE FREE TIER IS NOT A TRIAL
    ---------------------------------------------------------------------------
    A society with no row is FREE, and FREE is a permanent, fully working state.
    Every gate check, every attendance write, every complaint and every payment
    keeps functioning when a subscription lapses; only the administrator's
    reporting surface narrows.

    That is a deliberate commercial choice as much as an ethical one. A society
    will not move its attendance records onto a platform that can hold them
    hostage, and the records are the thing that makes leaving hard later. But it
    is also the plain answer to "should an unpaid invoice be able to stop a
    worker getting through the gate?" — no, and the code should make that
    difficult to get wrong rather than relying on nobody trying.
    """

    society = models.OneToOneField(
        "societies.Society", on_delete=models.CASCADE, related_name="subscription"
    )
    tier = models.CharField(
        max_length=20,
        choices=SubscriptionTier.choices,
        default=SubscriptionTier.FREE,
        db_index=True,
    )
    #: Null on FREE, which never expires.
    valid_until = models.DateField(null=True, blank=True)

    #: Razorpay subscription id, once billing is automated. Blank while tiers are
    #: sold by hand, which is deliberate — there is no self-serve checkout until
    #: somebody has actually paid for one.
    provider_reference = models.CharField(max_length=64, blank=True)
    note = models.CharField(max_length=300, blank=True)

    class Meta:
        verbose_name = _("society subscription")

    def __str__(self):
        return f"{self.society} — {self.get_tier_display()}"

    @property
    def is_active(self) -> bool:
        if self.tier == SubscriptionTier.FREE:
            return True
        return self.valid_until is not None and self.valid_until >= timezone.localdate()

    @property
    def effective_tier(self) -> str:
        """A lapsed paid tier reads as FREE rather than as itself.

        Everything downstream asks for this, never ``tier``, so an expiry cannot
        be forgotten at a call site.
        """
        return self.tier if self.is_active else SubscriptionTier.FREE

    @property
    def limits(self) -> dict:
        return TIER_LIMITS[self.effective_tier]

    @property
    def waives_booking_fee(self) -> bool:
        return bool(self.limits["waives_booking_fee"])

    @property
    def worker_limit(self):
        """``None`` means unlimited."""
        return self.limits["workers"]

    @property
    def includes_reports(self) -> bool:
        return bool(self.limits["reports"])

    @classmethod
    def for_society(cls, society) -> "SocietySubscription":
        """The society's subscription, creating a FREE one on first ask.

        Lazy rather than created alongside the society, so societies that
        predate this module behave identically to new ones.
        """
        subscription, _created = cls.objects.get_or_create(society=society)
        return subscription


class DisputeReason(models.TextChoices):
    NOT_PAID = "not_paid", _("Payment never arrived")
    WRONG_AMOUNT = "wrong_amount", _("The amount is wrong")
    HOURS_DISPUTED = "hours_disputed", _("The hours worked are disputed")
    NOT_PROVIDED = "not_provided", _("The service was not provided")
    OTHER = "other", _("Something else")


class DisputeStatus(models.TextChoices):
    OPEN = "open", _("Open")
    UNDER_REVIEW = "under_review", _("With the administrator")
    RESOLVED = "resolved", _("Resolved")
    REJECTED = "rejected", _("Rejected")


class PaymentDispute(SocietyScopedModel, TimeStampedModel):
    """Module 8.6 — a lightweight mediation record.

    Deliberately thin. The modspec routes disputes into the society
    administrator's complaint queue in Module 11 rather than building a parallel
    workflow here, so this captures the claim and its outcome and leaves the
    handling to that queue.
    """

    payment = models.ForeignKey(
        Payment, on_delete=models.CASCADE, related_name="disputes"
    )
    raised_by = models.ForeignKey(
        "accounts.User", on_delete=models.PROTECT, related_name="raised_payment_disputes"
    )
    reason = models.CharField(max_length=30, choices=DisputeReason.choices)
    description = models.TextField(max_length=1000)

    status = models.CharField(
        max_length=20, choices=DisputeStatus.choices, default=DisputeStatus.OPEN, db_index=True
    )
    resolution = models.TextField(blank=True, max_length=1000)
    resolved_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="resolved_payment_disputes",
    )
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            # One open dispute per person per payment. Without this, repeatedly
            # tapping "raise a dispute" floods the administrator's queue with
            # copies of the same complaint.
            models.UniqueConstraint(
                fields=["payment", "raised_by"],
                condition=models.Q(
                    status__in=[DisputeStatus.OPEN, DisputeStatus.UNDER_REVIEW]
                ),
                name="one_open_dispute_per_payment_per_person",
            )
        ]
        indexes = [models.Index(fields=["society", "status", "-created_at"])]

    def __str__(self):
        return f"Dispute on {self.payment.receipt_number} ({self.status})"

    @property
    def is_open(self) -> bool:
        return self.status in {DisputeStatus.OPEN, DisputeStatus.UNDER_REVIEW}

    def resolve(self, *, resolution: str, by, upheld: bool = True) -> bool:
        """Close a dispute. Idempotent."""
        if not self.is_open:
            return False

        self.status = DisputeStatus.RESOLVED if upheld else DisputeStatus.REJECTED
        self.resolution = resolution
        self.resolved_by = by
        self.resolved_at = timezone.now()
        self.save(
            update_fields=["status", "resolution", "resolved_by", "resolved_at", "updated_at"]
        )
        return True
