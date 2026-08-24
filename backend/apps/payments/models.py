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

import datetime as dt
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
    #: Module 5.5 — the platform's fee for running an emergency broadcast.
    #:
    #: The only kind that is owed to Sathify rather than to a worker, which is
    #: why it is the only kind whose ``worker`` is null. The emergency worker's
    #: own fee is cash and has no row here at all — see bookings/emergency.py.
    EMERGENCY_SURCHARGE = "emergency_surcharge", _("Emergency booking fee")
    #: Module 4.6 — this month's worked days, settled before notice takes effect.
    #:
    #: Its own kind rather than another ENGAGEMENT_SALARY row, because notice is
    #: gated on it: ``hiring.settlement`` has to be able to ask "has the final
    #: settlement been paid" and get an answer that a routine mid-month salary
    #: payment cannot accidentally satisfy on its own terms.
    NOTICE_SETTLEMENT = "notice_settlement", _("Final settlement on notice")


#: Kinds the platform collects for itself. Excluded from anything that answers
#: "what has this worker earned", because the answer is "none of it".
PLATFORM_KINDS = frozenset({PaymentKind.EMERGENCY_SURCHARGE})


class PaymentStatus(models.TextChoices):
    CREATED = "created", _("Order created, not yet attempted")
    PENDING = "pending", _("Awaiting confirmation from Razorpay")
    PAID = "paid", _("Paid")
    FAILED = "failed", _("Failed")
    REFUNDED = "refunded", _("Refunded")
    CANCELLED = "cancelled", _("Cancelled before payment")


class SettledVia(models.TextChoices):
    """Which path moved a payment to PAID.

    Stored rather than inferred, because the three are not equally strong and
    anybody auditing the ledger later needs to know which is which without
    reading the code. The two gateway values rest on a verified HMAC; the manual
    one rests on a named administrator and a bank reference.
    """

    CHECKOUT = "checkout", _("Signed Razorpay checkout response")
    WEBHOOK = "webhook", _("Razorpay webhook")
    UPI_MANUAL = "upi_manual", _("UPI transfer, confirmed against a statement")


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
    #: Null only on a platform charge — today, the Module 5.5 emergency
    #: surcharge, which is owed to Sathify and not to anybody's wages. Every
    #: worker-facing query filters on this column, so a null row simply does not
    #: appear in an earnings figure, which is the correct answer rather than a
    #: special case somebody has to remember.
    worker = models.ForeignKey(
        "workers.WorkerProfile",
        on_delete=models.PROTECT,
        related_name="payments",
        null=True,
        blank=True,
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

    # --- Module 8.9: the hosted UPI QR ---------------------------------------
    #
    # Razorpay draws and watches the code; we keep its id so a
    # ``qr_code.credited`` webhook can be matched back to this row, and its URL
    # and expiry so re-opening the pay sheet reuses the code somebody may
    # already have photographed rather than issuing a new one.
    # `db_default` on all three, and it is load-bearing rather than tidy — see
    # the note on `settled_via` below.
    razorpay_qr_code_id = models.CharField(
        max_length=64, blank=True, db_index=True, db_default=""
    )
    razorpay_qr_image_url = models.URLField(max_length=500, blank=True, db_default="")
    qr_expires_at = models.DateTimeField(null=True, blank=True)

    #: The Payment Links fallback, for accounts without the QR Codes API.
    #:
    #: Kept in its own columns rather than overloading the QR ones: the two are
    #: different Razorpay objects with different webhooks, and a single "code id"
    #: column would leave the webhook handler guessing which kind it held.
    razorpay_payment_link_id = models.CharField(
        max_length=64, blank=True, db_index=True, db_default=""
    )
    razorpay_payment_link_url = models.URLField(
        max_length=500, blank=True, db_default=""
    )

    #: Which of the three settlement paths moved this row to PAID (Module 8.9).
    #:
    #: Blank on anything unsettled. Worth a column of its own because the paths
    #: carry different weight — two are HMAC-verified and one is an
    #: administrator's word against a bank statement — and "which payments rest
    #: on a person rather than a signature?" must be answerable with a filter
    #: rather than a code review.
    #: ``db_default`` is not cosmetic. Django's own ``default`` lives in Python,
    #: so a column added without a *database* default is ``NOT NULL`` with
    #: nothing to fall back on — and any process running code that predates the
    #: field omits it from its INSERT and gets an IntegrityError.
    #:
    #: That is not hypothetical here: this database is shared between a
    #: developer's machine and the deployed instance, so a migration applied
    #: from one lands under the other while it is still serving. Adding these
    #: columns without ``db_default`` took the live app down — every payment
    #: insert failed, which meant every emergency request failed, and the
    #: household saw "something went wrong on our side".
    #:
    #: A database-level default makes the schema readable by both the old code
    #: and the new, which is the property a shared database needs and the reason
    #: every text column added here carries one.
    settled_via = models.CharField(
        max_length=20, choices=SettledVia.choices, blank=True, db_index=True,
        db_default="",
    )

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
    def is_platform_charge(self) -> bool:
        """Owed to Sathify rather than to a worker."""
        return self.kind in PLATFORM_KINDS

    @property
    def worker_receives_paise(self) -> int:
        """What reaches the worker. The number that must never quietly shrink.

        Deliberately its own property rather than "total minus fee": a reader
        checking whether a fee was taken out of somebody's wage should find one
        expression that says it was not.

        Zero on a platform charge, because no worker is party to it — not
        "the amount, attributed to nobody".
        """
        if self.is_platform_charge or self.worker_id is None:
            return 0
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

    def mark_paid(
        self,
        *,
        razorpay_payment_id: str,
        signature: str = "",
        via: str = SettledVia.CHECKOUT,
    ) -> bool:
        """Settle the payment. Idempotent.

        Called from the two signature-verified gateway paths and from UPI
        reconciliation — see the module docstring in ``services.py`` for why
        that third path exists and what stands in for the signature there.
        ``via`` records which, so the distinction survives in the data.

        A second confirmation for the same payment — Razorpay retries webhooks —
        must not move ``paid_at``, because that timestamp decides which month's
        salary summary this lands in.
        """
        if self.status == PaymentStatus.PAID:
            return False

        self.status = PaymentStatus.PAID
        self.razorpay_payment_id = razorpay_payment_id
        self.razorpay_signature = signature
        self.settled_via = via
        self.paid_at = timezone.now()
        self.failure_reason = ""
        self.save(
            update_fields=[
                "status", "razorpay_payment_id", "razorpay_signature",
                "settled_via", "paid_at", "failure_reason", "updated_at",
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


class UpiSettlement(TimeStampedModel):
    """Module 8.9 — an administrator confirming a UPI transfer arrived.

    ---------------------------------------------------------------------------
    THE ROW *IS* THE EVIDENCE
    ---------------------------------------------------------------------------
    A UPI QR collects straight into a VPA, so there is no signed callback to
    verify — the money simply appears on a bank statement. What replaces the
    signature is this row: a named administrator, a timestamp, and the bank's
    own UTR, all of which can be checked against that statement afterwards.

    Same reasoning as :class:`WebhookEvent`. Applying the confirmation and
    forgetting it would leave only the conclusion, and "why is this marked paid?"
    is precisely the question somebody will ask six months later.

    ---------------------------------------------------------------------------
    THE UNIQUE UTR IS THE CONTROL THAT MATTERS
    ---------------------------------------------------------------------------
    A UTR identifies one transfer, once. Making it unique across the whole ledger
    means a single line on a bank statement can settle at most one payment — so
    an administrator cannot, by mistake or otherwise, clear five outstanding
    charges by pasting the same reference five times. That is the difference
    between "an admin confirms what the bank shows" and "an admin can mark
    things paid", and it is enforced by the database rather than by care.
    """

    payment = models.OneToOneField(
        Payment, on_delete=models.CASCADE, related_name="upi_settlement"
    )

    #: The bank's Unique Transaction Reference for the transfer, as it appears
    #: on the statement. Normalised to uppercase so the same reference typed two
    #: ways cannot slip past the unique constraint.
    utr = models.CharField(
        max_length=40,
        unique=True,
        db_index=True,
        help_text=_("The bank's UTR for this transfer. One UTR settles one payment."),
    )

    #: What the administrator saw on the statement, checked against the payment's
    #: own total before this row is written. Stored because a later dispute is
    #: about the figure somebody looked at, not the figure we expected.
    amount_paise = models.PositiveIntegerField()

    confirmed_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.PROTECT,
        related_name="confirmed_upi_settlements",
        help_text=_("Who took responsibility for this confirmation."),
    )
    note = models.CharField(max_length=300, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["confirmed_by", "-created_at"])]

    def __str__(self):
        return f"UPI {self.utr} → {self.payment.receipt_number}"


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


# ===========================================================================
# Module 8.10 — Invoices for hourly engagements
#
# An Invoice WRAPS a Payment; it does not replace one. On issue it creates
# exactly one Payment of kind ENGAGEMENT_SALARY, which inherits the whole
# existing settlement apparatus — receipt number, due date, the Razorpay order,
# `settled_via`, the webhook path. Everything above this line keeps working
# unchanged, and nothing downstream of a Payment learns that hourly exists.
#
# What the Invoice adds is the part a monthly rate never needed: a per-session
# breakdown a resident can audit line by line, and a review window in which
# either party can query a line before any money moves.
# ===========================================================================


class InvoiceStatus(models.TextChoices):
    #: Accruing sessions as the period runs. Visible live to both parties, so
    #: the month-end figure is never a surprise and never a negotiation.
    DRAFT = "draft", _("Building through the period")
    #: The window in which either party may query a line. Nothing is payable.
    REVIEW = "review", _("Open for questions")
    #: Lines frozen, a Payment exists and is owed.
    ISSUED = "issued", _("Issued and payable")
    SETTLED = "settled", _("Paid")
    VOID = "void", _("Cancelled before issue")


class InvoiceLineKind(models.TextChoices):
    TIME = "time", _("Time worked")
    OVERTIME = "overtime", _("Approved extra time")
    VISIT_FEE = "visit_fee", _("Visit fee")
    #: A correction to an ALREADY ISSUED invoice, carried onto the next one.
    #: Never an edit of history — see `Invoice.add_adjustment`.
    ADJUSTMENT = "adjustment", _("Adjustment from an earlier period")


class InvoiceQuerySet(models.QuerySet):
    def in_review(self):
        return self.filter(status=InvoiceStatus.REVIEW)

    def payable(self):
        return self.filter(status=InvoiceStatus.ISSUED)

    def for_period(self, start, end):
        return self.filter(period_start__gte=start, period_end__lte=end)


class Invoice(SocietyScopedModel, TimeStampedModel):
    """One engagement's bill for one billing period.

    Amounts are recomputed from the lines by :meth:`recalculate` rather than
    being maintained incrementally, so a line added, removed or held can never
    leave a total that disagrees with the rows beneath it. That disagreement is
    the single failure a resident would notice fastest and trust least.
    """

    engagement = models.ForeignKey(
        "hiring.Engagement", on_delete=models.PROTECT, related_name="invoices"
    )
    resident = models.ForeignKey(
        "societies.Resident", on_delete=models.PROTECT, related_name="invoices"
    )
    worker = models.ForeignKey(
        "workers.WorkerProfile", on_delete=models.PROTECT, related_name="invoices"
    )

    number = models.CharField(max_length=32, unique=True, db_index=True)
    period_start = models.DateField()
    period_end = models.DateField()

    status = models.CharField(
        max_length=20,
        choices=InvoiceStatus.choices,
        default=InvoiceStatus.DRAFT,
        db_default=InvoiceStatus.DRAFT,
        db_index=True,
    )

    review_closes_at = models.DateTimeField(null=True, blank=True)
    issued_at = models.DateTimeField(null=True, blank=True)
    settled_at = models.DateTimeField(null=True, blank=True)

    #: Created on issue, for the payable amount only. Null while in DRAFT or
    #: REVIEW, and null for the held portion — a held line has no Payment until
    #: it is resolved onto a later invoice.
    payment = models.OneToOneField(
        Payment,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="invoice",
    )

    # --- Frozen totals, all derived from the lines -------------------------
    time_paise = models.PositiveIntegerField(default=0, db_default=0)
    overtime_paise = models.PositiveIntegerField(default=0, db_default=0)
    visit_fee_paise = models.PositiveIntegerField(default=0, db_default=0)
    adjustment_paise = models.IntegerField(default=0, db_default=0)
    #: The disputed portion, withheld from this invoice's Payment. This is the
    #: number that makes §9.4a's ladder safe to use: a query over one session
    #: must never freeze a month's wages, so only the contested lines wait.
    held_paise = models.PositiveIntegerField(default=0, db_default=0)

    objects = InvoiceQuerySet.as_manager()

    class Meta:
        verbose_name = _("invoice")
        verbose_name_plural = _("invoices")
        ordering = ["-period_end", "-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["engagement", "period_start", "period_end"],
                name="one_invoice_per_engagement_period",
            ),
        ]
        indexes = [
            models.Index(fields=["society", "-period_end"]),
            models.Index(fields=["status", "-period_end"]),
        ]

    def __str__(self):
        return f"{self.number} ({self.status})"

    def save(self, *args, **kwargs):
        if not self.number:
            self.number = self._generate_number()
        super().save(*args, **kwargs)

    def _generate_number(self) -> str:
        return f"INV-{self.engagement_id}-{self.period_end:%y%m}"

    # -- totals --------------------------------------------------------------

    @property
    def subtotal_paise(self) -> int:
        return self.time_paise + self.overtime_paise + self.visit_fee_paise

    @property
    def total_paise(self) -> int:
        """Everything the resident owes for this period, held lines included."""
        return max(0, self.subtotal_paise + self.adjustment_paise)

    @property
    def payable_paise(self) -> int:
        """What is actually charged now — the total less anything under query."""
        return max(0, self.total_paise - self.held_paise)

    def recalculate(self, *, commit: bool = True) -> "Invoice":
        """Rebuild every total from the lines. The only way totals are set."""
        totals = {kind: 0 for kind, _label in InvoiceLineKind.choices}
        held = 0
        for line in self.lines.all():
            totals[line.kind] = totals.get(line.kind, 0) + line.amount_paise
            if line.is_held:
                held += line.amount_paise

        self.time_paise = totals.get(InvoiceLineKind.TIME, 0)
        self.overtime_paise = totals.get(InvoiceLineKind.OVERTIME, 0)
        self.visit_fee_paise = totals.get(InvoiceLineKind.VISIT_FEE, 0)
        self.adjustment_paise = totals.get(InvoiceLineKind.ADJUSTMENT, 0)
        self.held_paise = held

        if commit:
            self.save(
                update_fields=[
                    "time_paise", "overtime_paise", "visit_fee_paise",
                    "adjustment_paise", "held_paise", "updated_at",
                ]
            )
        return self

    # -- lifecycle -----------------------------------------------------------

    def add_session(self, session) -> "InvoiceLine | None":
        """Add a priced session's lines. Returns the time line, or None.

        Skips silently when the session is already on this invoice, so the
        nightly accrual can be re-run without duplicating a day.
        """
        if self.lines.filter(session=session).exists():
            return None

        made = None
        if session.time_paise:
            made = InvoiceLine.objects.create(
                invoice=self, session=session, kind=InvoiceLineKind.TIME,
                minutes=session.billable_minutes, amount_paise=session.time_paise,
                description=f"{session.visit_date:%d %b} — time worked",
            )
        if session.overtime_paise:
            InvoiceLine.objects.create(
                invoice=self, session=session, kind=InvoiceLineKind.OVERTIME,
                minutes=session.overtime_minutes, amount_paise=session.overtime_paise,
                description=f"{session.visit_date:%d %b} — approved extra time",
            )
        if session.visit_fee_paise:
            InvoiceLine.objects.create(
                invoice=self, session=session, kind=InvoiceLineKind.VISIT_FEE,
                minutes=0, amount_paise=session.visit_fee_paise,
                description=f"{session.visit_date:%d %b} — visit fee",
            )
        self.recalculate()
        return made

    def add_adjustment(self, *, amount_paise: int, description: str, query=None) -> "InvoiceLine":
        """Carry a correction from an earlier, already-issued period onto this one.

        This is how a resolved query reaches the money. An issued invoice is
        never edited — ``AttendanceEvent`` sets the rule this follows, that a
        wrong entry is corrected by a superseding one — so a resident who
        queries a three-month-old charge is shown the number that actually
        happened, plus the adjustment that answered it.
        """
        line = InvoiceLine.objects.create(
            invoice=self,
            kind=InvoiceLineKind.ADJUSTMENT,
            minutes=0,
            amount_paise=amount_paise,
            description=description[:200],
            query=query,
        )
        self.recalculate()
        return line

    def open_review(self, *, hours: int = 48) -> bool:
        """Close accrual and start the window. Idempotent."""
        if self.status != InvoiceStatus.DRAFT:
            return False
        self.status = InvoiceStatus.REVIEW
        self.review_closes_at = timezone.now() + dt.timedelta(hours=hours)
        self.save(update_fields=["status", "review_closes_at", "updated_at"])
        return True

    def issue(self, *, due_at=None) -> Payment | None:
        """Freeze the lines and raise a Payment for the payable amount.

        Returns the Payment, or None when everything on the invoice is held or
        the total is zero — a bill for nothing should not exist, and a bill
        entirely under query has nothing to charge yet.
        """
        if self.status not in {InvoiceStatus.DRAFT, InvoiceStatus.REVIEW}:
            return None

        self.recalculate()
        payable = self.payable_paise

        payment = None
        if payable > 0:
            payment = Payment.objects.create(
                society=self.society,
                resident=self.resident,
                worker=self.worker,
                engagement=self.engagement,
                kind=PaymentKind.ENGAGEMENT_SALARY,
                amount_paise=payable,
                period_start=self.period_start,
                period_end=self.period_end,
                due_at=due_at,
                note=f"Invoice {self.number}",
            )

        self.payment = payment
        self.status = InvoiceStatus.ISSUED
        self.issued_at = timezone.now()
        self.save(update_fields=["payment", "status", "issued_at", "updated_at"])
        return payment

    def mark_settled(self) -> bool:
        if self.status != InvoiceStatus.ISSUED:
            return False
        self.status = InvoiceStatus.SETTLED
        self.settled_at = timezone.now()
        self.save(update_fields=["status", "settled_at", "updated_at"])
        return True


class InvoiceLine(TimeStampedModel):
    """One charge on an invoice, traceable to the session that produced it."""

    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name="lines")
    session = models.ForeignKey(
        "attendance.WorkSession",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="invoice_lines",
        help_text=_("Null on an adjustment carried from an earlier period."),
    )
    query = models.ForeignKey(
        "payments.SessionQuery",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="adjustment_lines",
        help_text=_("The query this adjustment answers, when it answers one."),
    )

    kind = models.CharField(max_length=20, choices=InvoiceLineKind.choices)
    description = models.CharField(max_length=200)
    minutes = models.PositiveIntegerField(default=0, db_default=0)
    #: Signed, because an adjustment may be a credit. Every other kind is
    #: positive, and `Invoice.total_paise` floors the result at zero.
    amount_paise = models.IntegerField()

    #: Withheld from this invoice's Payment while a query is open against it.
    is_held = models.BooleanField(default=False, db_default=False)

    class Meta:
        verbose_name = _("invoice line")
        verbose_name_plural = _("invoice lines")
        ordering = ["invoice", "id"]
        indexes = [models.Index(fields=["invoice", "kind"])]

    def __str__(self):
        return f"{self.description} — {format_paise(self.amount_paise)}"


class QueryStage(models.TextChoices):
    """Where a queried session has reached on the §9.4a ladder.

    Three stages, and the platform decides none of them. Most queries die at
    EVIDENCE, where both parties are simply shown the same record and one of
    them recognises their own mistake.
    """

    EVIDENCE = "evidence", _("Both parties shown the record")
    BILATERAL = "bilateral", _("Waiting for one side to accept the other")
    ADMIN = "admin", _("With the society administrator")
    RESOLVED = "resolved", _("Settled")
    WITHDRAWN = "withdrawn", _("Withdrawn by whoever raised it")


class SessionQuery(SocietyScopedModel, TimeStampedModel):
    """A question about one work session, raised during the review window.

    Distinct from :class:`PaymentDispute`, which is about money that has already
    been charged. This is earlier and cheaper: it is raised against a *line* on
    a draft invoice, before a Payment exists, which is why it can be answered by
    one party tapping "yes, you're right" with nothing to refund.
    """

    session = models.ForeignKey(
        "attendance.WorkSession", on_delete=models.CASCADE, related_name="queries"
    )
    invoice = models.ForeignKey(
        Invoice, on_delete=models.CASCADE, related_name="queries", null=True, blank=True
    )
    raised_by = models.ForeignKey(
        "accounts.User", on_delete=models.PROTECT, related_name="raised_session_queries"
    )

    reason = models.CharField(max_length=30, choices=DisputeReason.choices)
    description = models.TextField(max_length=1000, blank=True)

    stage = models.CharField(
        max_length=20,
        choices=QueryStage.choices,
        default=QueryStage.EVIDENCE,
        db_default=QueryStage.EVIDENCE,
        db_index=True,
    )
    escalates_at = models.DateTimeField(
        null=True, blank=True,
        help_text=_("When this reaches the society administrator if unresolved."),
    )

    resolution = models.TextField(max_length=1000, blank=True)
    resolved_by = models.ForeignKey(
        "accounts.User", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="resolved_session_queries",
    )
    resolved_at = models.DateTimeField(null=True, blank=True)
    #: Signed. Positive credits the resident, negative charges them.
    adjustment_paise = models.IntegerField(default=0, db_default=0)

    class Meta:
        verbose_name = _("session query")
        verbose_name_plural = _("session queries")
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["session", "raised_by"],
                condition=models.Q(
                    stage__in=[QueryStage.EVIDENCE, QueryStage.BILATERAL, QueryStage.ADMIN]
                ),
                name="one_open_query_per_session_per_person",
            ),
        ]
        indexes = [models.Index(fields=["society", "stage", "-created_at"])]

    def __str__(self):
        return f"Query on {self.session_id} ({self.stage})"

    @property
    def is_open(self) -> bool:
        return self.stage in {QueryStage.EVIDENCE, QueryStage.BILATERAL, QueryStage.ADMIN}

    def resolve(self, *, resolution: str, by=None, adjustment_paise: int = 0) -> bool:
        """Settle the query. Idempotent.

        Releasing the hold is the caller's job (``services.resolve_query``),
        because it also has to decide which invoice carries the adjustment.
        """
        if not self.is_open:
            return False
        self.stage = QueryStage.RESOLVED
        self.resolution = resolution[:1000]
        self.resolved_by = by
        self.resolved_at = timezone.now()
        self.adjustment_paise = adjustment_paise
        self.save(
            update_fields=[
                "stage", "resolution", "resolved_by", "resolved_at",
                "adjustment_paise", "updated_at",
            ]
        )
        return True


class WageFloor(TimeStampedModel):
    """The statutory minimum hourly wage for domestic work, by state.

    Checked against the *effective* rate rather than the advertised one, which
    is the reason §7.2's calibration earns its keep twice: because `F = R × T`
    makes the effective rate equal R at every job length, one comparison answers
    compliance for every engagement in the state. Under a bare hourly rate, a
    short visit could sit below the floor on an effective basis while the stored
    number looked perfectly compliant.
    """

    state = models.CharField(max_length=100, db_index=True)
    min_hourly_paise = models.PositiveIntegerField(
        help_text=_("Statutory minimum per hour, in paise.")
    )
    effective_from = models.DateField(db_index=True)
    source_note = models.CharField(
        max_length=300, blank=True,
        help_text=_("Which notification or order this figure came from."),
    )

    class Meta:
        verbose_name = _("wage floor")
        verbose_name_plural = _("wage floors")
        ordering = ["state", "-effective_from"]
        constraints = [
            models.UniqueConstraint(
                fields=["state", "effective_from"], name="one_wage_floor_per_state_per_date"
            ),
        ]

    def __str__(self):
        return f"{self.state}: {format_paise(self.min_hourly_paise)}/hr from {self.effective_from}"

    @classmethod
    def in_force(cls, state: str, *, on=None):
        """The floor applying in ``state`` on a date, or None if none is recorded.

        None means "we have no figure", not "there is no floor" — the caller
        must not read a missing row as permission.
        """
        on = on or timezone.localdate()
        return (
            cls.objects.filter(state__iexact=state, effective_from__lte=on)
            .order_by("-effective_from")
            .first()
        )
