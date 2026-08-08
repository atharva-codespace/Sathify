"""
Module 7 — Attendance & Gate Verification.

The physical trust layer: it confirms that the verified worker is genuinely the
person walking through the gate, logs the decision, and feeds Modules 8 and 9.

-------------------------------------------------------------------------------
OFFLINE-FIRST IS A SCHEMA DECISION, NOT A CLIENT ONE
-------------------------------------------------------------------------------
Gate connectivity cannot be assumed, so a scan must succeed on a device with no
network and reconcile later. Three things in this file exist for that:

* :class:`AttendanceEvent` has a **client-generated UUID primary key**. The
  guard's device mints the id before the server has ever seen the record, which
  is what makes ``/attendance/sync/`` idempotent — replaying a queued event
  cannot create a second row (see ``core.UUIDPrimaryKeyModel``).
* ``occurred_at`` and ``recorded_at`` are separate. The first is when the person
  actually walked through the gate, taken from the guard's device; the second is
  when the server heard about it. Collapsing them would make a batch synced at
  6pm look like forty people arriving simultaneously at 6pm.
* Nothing about a decision depends on a server round trip. The guard's device
  holds the day's roster and decides locally; the server re-checks on sync but
  the worker is not left standing at the gate waiting for a reply.

-------------------------------------------------------------------------------
A FAILED FACE MATCH NEVER DENIES ENTRY
-------------------------------------------------------------------------------
``FACE_SETTINGS["ALLOW_MANUAL_OVERRIDE"]`` is not a convenience toggle. Face
recognition fails more often for exactly the people this platform serves —
poorer lighting, older phone cameras, faces underrepresented in training data —
and a false rejection means someone loses a day's pay for a model's mistake. So
a below-threshold match produces ``PENDING_REVIEW``, never ``DENIED``, and a
guard resolves it with an explicit, separately logged override.
"""

from __future__ import annotations

import datetime as dt
import uuid

from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.models import SocietyScopedModel, TimeStampedModel, UUIDPrimaryKeyModel


class GatePass(TimeStampedModel):
    """Module 7.1 — the QR credential a worker presents at the gate.

    One active pass per worker. The payload is an opaque UUID rather than a
    worker id: a sequential id printed on a laminated card would let anyone
    guess a colleague's code, and the card is handled by dozens of people a day.

    The same code serves both delivery routes the modspec asks for — printed on
    a laminated card for a worker without a smartphone, or rendered in-app for
    one who has. Rotating it (``rotate``) is what replaces a lost card, and the
    old code stops working the moment it happens.
    """

    worker = models.OneToOneField(
        "workers.WorkerProfile", on_delete=models.CASCADE, related_name="gate_pass"
    )
    code = models.UUIDField(default=uuid.uuid4, unique=True, editable=False, db_index=True)

    is_active = models.BooleanField(default=True)
    issued_at = models.DateTimeField(default=timezone.now)
    revoked_at = models.DateTimeField(null=True, blank=True)
    revoked_reason = models.CharField(max_length=200, blank=True)

    #: How many times this code has been reissued. Useful when a worker keeps
    #: losing a card, which is worth an administrator noticing.
    rotation_count = models.PositiveSmallIntegerField(default=0)

    class Meta:
        verbose_name = _("gate pass")
        verbose_name_plural = _("gate passes")
        ordering = ["-created_at"]

    def __str__(self):
        return f"Gate pass for {self.worker}"

    @property
    def is_usable(self) -> bool:
        """Active, not revoked, and belonging to an approved worker.

        Approval is re-checked here rather than trusted from issue time: a
        worker whose approval was withdrawn must stop getting through the gate
        immediately, without anyone having to remember to revoke the card too.
        """
        return bool(self.is_active and self.revoked_at is None and self.worker.user.is_approved)

    def rotate(self, *, reason: str = "") -> uuid.UUID:
        """Issue a new code, invalidating the old one. Returns the new code."""
        self.code = uuid.uuid4()
        self.rotation_count += 1
        self.issued_at = timezone.now()
        self.is_active = True
        self.revoked_at = None
        self.revoked_reason = reason
        self.save(
            update_fields=[
                "code", "rotation_count", "issued_at", "is_active",
                "revoked_at", "revoked_reason", "updated_at",
            ]
        )
        return self.code

    def revoke(self, *, reason: str = "") -> bool:
        """Stop this pass working. Idempotent."""
        if self.revoked_at is not None:
            return False
        self.is_active = False
        self.revoked_at = timezone.now()
        self.revoked_reason = reason
        self.save(update_fields=["is_active", "revoked_at", "revoked_reason", "updated_at"])
        return True


class Direction(models.TextChoices):
    ENTRY = "entry", _("Entry")
    EXIT = "exit", _("Exit")


class VerificationMethod(models.TextChoices):
    QR = "qr", _("QR code scanned")
    FACE = "face", _("Face verified")
    MANUAL = "manual", _("Logged manually by the guard")
    SELF_CHECKIN = "self_checkin", _("Worker checked in from the app")
    #: Module 13.3 tier 2.5 — the resident scans the worker's printed card.
    #:
    #: For the case neither of the other fallbacks reaches: no guard on the
    #: gate *and* the worker has no smartphone. Tier 2 needs her phone; tier 3
    #: needs a guard with a paper register. This needs neither — the card is
    #: laminated cardboard and the scanner is the resident's own phone, which
    #: they already have because they are using this app to let her in.
    RESIDENT_SCAN = "resident_scan", _("Resident scanned the worker's card")
    REGISTER = "register", _("Transcribed from the paper register")


class Decision(models.TextChoices):
    ALLOWED = "allowed", _("Allowed")
    DENIED = "denied", _("Denied")
    #: A face check came back below threshold. NOT a denial — a guard decides.
    PENDING_REVIEW = "pending_review", _("Needs the guard's decision")


class AttendanceEventQuerySet(models.QuerySet):
    def allowed(self):
        return self.filter(decision=Decision.ALLOWED)

    def for_day(self, day: dt.date):
        """Everything that happened on one local calendar day."""
        return self.filter(occurred_at__date=day)

    def unresolved(self):
        """Face checks still waiting for a guard to decide."""
        return self.filter(decision=Decision.PENDING_REVIEW)


class AttendanceEvent(UUIDPrimaryKeyModel, SocietyScopedModel, TimeStampedModel):
    """Modules 7.2–7.6 — one allow, deny, or override decision at the gate.

    The audit record the SRS's three-year retention requirement applies to
    (SRS 5.5), and the input Module 8 bills from and Module 9 scores on. It is
    therefore append-only in spirit: a wrong entry is corrected by a
    superseding one, never by editing history.
    """

    worker = models.ForeignKey(
        "workers.WorkerProfile", on_delete=models.PROTECT, related_name="attendance_events"
    )
    gate = models.ForeignKey(
        "societies.Gate",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="attendance_events",
    )
    recorded_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="recorded_attendance",
        help_text=_("The guard. Null for a worker's own self check-in."),
    )

    direction = models.CharField(max_length=10, choices=Direction.choices)
    method = models.CharField(max_length=20, choices=VerificationMethod.choices)
    decision = models.CharField(
        max_length=20, choices=Decision.choices, default=Decision.ALLOWED, db_index=True
    )
    decision_reason = models.CharField(max_length=200, blank=True)

    # --- What the visit was for --------------------------------------------
    # Both nullable and non-exclusive: a gate entry may not correspond to
    # anything scheduled (a worker arriving on the wrong day still walks through
    # a gate and still has to be logged), and the audit trail must record that
    # rather than refuse it.
    engagement = models.ForeignKey(
        "hiring.Engagement",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="attendance_events",
    )
    booking = models.ForeignKey(
        "bookings.Booking",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="attendance_events",
    )
    was_expected = models.BooleanField(
        default=False,
        help_text=_("Whether this matched something on the day's roster."),
    )

    # --- Time (see the module docstring) -----------------------------------
    occurred_at = models.DateTimeField(
        db_index=True, help_text=_("When it happened, from the guard's device.")
    )
    recorded_at = models.DateTimeField(
        default=timezone.now, help_text=_("When the server received it.")
    )

    # --- Module 7.3 face verification --------------------------------------
    face_checked = models.BooleanField(default=False)
    face_match_score = models.FloatField(
        null=True, blank=True, help_text=_("Similarity, 0-1. Null if not checked.")
    )
    face_verified = models.BooleanField(default=False)
    face_photo = models.ImageField(upload_to="attendance/faces/%Y/%m/", blank=True)

    overridden_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="attendance_overrides",
        help_text=_("The guard who resolved a below-threshold face match."),
    )
    override_reason = models.CharField(max_length=200, blank=True)
    overridden_at = models.DateTimeField(null=True, blank=True)

    # --- Module 7.4 sync ----------------------------------------------------
    device_id = models.CharField(
        max_length=64,
        blank=True,
        help_text=_("Which guard device queued this, for reconciling a bad batch."),
    )
    was_offline = models.BooleanField(
        default=False, help_text=_("Queued on the device before syncing.")
    )

    objects = AttendanceEventQuerySet.as_manager()

    class Meta:
        ordering = ["-occurred_at"]
        indexes = [
            models.Index(fields=["worker", "-occurred_at"]),
            models.Index(fields=["society", "-occurred_at"]),
            models.Index(fields=["decision", "-occurred_at"]),
        ]

    def __str__(self):
        return f"{self.worker} {self.direction} at {self.occurred_at:%Y-%m-%d %H:%M}"

    @property
    def needs_review(self) -> bool:
        return self.decision == Decision.PENDING_REVIEW

    @property
    def was_overridden(self) -> bool:
        return self.overridden_at is not None

    @property
    def sync_delay_seconds(self) -> float:
        """How long this sat in the offline queue. Zero when it went straight through."""
        return max(0.0, (self.recorded_at - self.occurred_at).total_seconds())

    def resolve(self, *, allow: bool, by, reason: str = "") -> bool:
        """A guard decides a pending face check (Module 7.3).

        Idempotent, and deliberately possible in both directions: the guard is
        the authority here, not the model. Every resolution is attributed and
        timestamped because overriding a biometric check is exactly the kind of
        decision that has to be answerable for later.
        """
        if self.decision != Decision.PENDING_REVIEW:
            return False

        self.decision = Decision.ALLOWED if allow else Decision.DENIED
        self.overridden_by = by
        self.override_reason = reason
        self.overridden_at = timezone.now()
        self.save(
            update_fields=[
                "decision", "overridden_by", "override_reason",
                "overridden_at", "updated_at",
            ]
        )
        return True


class RegisterScan(SocietyScopedModel, TimeStampedModel):
    """Module 7.5 — a photograph of the paper register, as a last resort.

    Not parsed. When scanning has failed all day and the guard has fallen back
    to paper, this preserves the evidence so an administrator can transcribe it
    rather than the day's attendance simply being lost. Transcribing it produces
    ordinary ``AttendanceEvent`` rows with ``method=REGISTER``.
    """

    gate = models.ForeignKey(
        "societies.Gate", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="register_scans",
    )
    uploaded_by = models.ForeignKey(
        "accounts.User", on_delete=models.SET_NULL, null=True,
        related_name="register_scans",
    )
    image = models.ImageField(upload_to="attendance/registers/%Y/%m/")
    for_date = models.DateField(db_index=True)
    note = models.CharField(max_length=300, blank=True)

    transcribed = models.BooleanField(default=False)
    transcribed_at = models.DateTimeField(null=True, blank=True)
    transcribed_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="transcribed_registers",
    )

    class Meta:
        ordering = ["-for_date", "-created_at"]
        indexes = [models.Index(fields=["society", "-for_date"])]

    def __str__(self):
        return f"Register for {self.for_date} at {self.gate or 'unknown gate'}"

    def mark_transcribed(self, *, by=None) -> bool:
        """Idempotent — a second tap must not rewrite who did it."""
        if self.transcribed:
            return False
        self.transcribed = True
        self.transcribed_at = timezone.now()
        self.transcribed_by = by
        self.save(
            update_fields=["transcribed", "transcribed_at", "transcribed_by", "updated_at"]
        )
        return True
