"""
Module 10 — Notifications.

Keeps every role informed across a primary channel and a fallback, so one
delivery failure never becomes a missed booking or a missed gate entry.

-------------------------------------------------------------------------------
THE IN-APP RECORD IS THE DURABLE ONE. PUSH AND SMS ARE ATTEMPTS ON TOP OF IT.
-------------------------------------------------------------------------------
A :class:`Notification` row is written **before** anything is sent and survives
whatever happens next. Push may fail, SMS may be disabled, a phone may be off —
none of that loses the message, because the notification centre (Module 10.3) is
the system of record and the two channels are best-effort attempts against it.

Building it the other way round — send first, log on success — is how a worker
ends up never learning their society approved them because a token had expired.

-------------------------------------------------------------------------------
SAFETY-CRITICAL CATEGORIES CANNOT BE MUTED
-------------------------------------------------------------------------------
Modspec 10.4 requires per-category opt-out "with safety-critical categories such
as gate-entry alerts and urgent-leave notices excluded from being muted". That
is enforced in :meth:`NotificationPreference.is_muted`, not merely hidden in the
UI — a client that posted a mute for one of those categories would otherwise
silence something the platform has a duty to deliver.
"""

from __future__ import annotations

from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.models import SocietyScopedModel, TimeStampedModel


class NotificationCategory(models.TextChoices):
    """What a notification is about. Drives both routing and mute preferences."""

    ACCOUNT = "account", _("Account and verification")
    HIRE = "hire", _("Hire requests")
    BOOKING = "booking", _("One-day bookings")
    SCHEDULE = "schedule", _("Upcoming visits")
    ATTENDANCE = "attendance", _("Attendance")
    GATE_ENTRY = "gate_entry", _("Gate entry alerts")
    URGENT_LEAVE = "urgent_leave", _("Urgent leave and replacements")
    PAYMENT = "payment", _("Payments")
    RATING = "rating", _("Ratings and reviews")
    COMPLAINT = "complaint", _("Complaints")


#: Categories a user may never switch off (modspec 10.4).
#:
#: The modspec names gate entry and urgent leave. ``ACCOUNT`` is included on the
#: same reasoning: a worker who is never told their registration was rejected
#: cannot correct it, which is exactly the harm this exclusion exists to
#: prevent. If that is judged too broad it is a one-line change here — but it
#: should be a deliberate one.
SAFETY_CRITICAL_CATEGORIES = frozenset(
    {
        NotificationCategory.GATE_ENTRY,
        NotificationCategory.URGENT_LEAVE,
        NotificationCategory.ACCOUNT,
    }
)


class DeliveryState(models.TextChoices):
    PENDING = "pending", _("Not attempted yet")
    SENT = "sent", _("Accepted by the provider")
    FAILED = "failed", _("The provider refused or could not be reached")
    SKIPPED = "skipped", _("Not attempted — muted, unavailable, or no address")


class NotificationQuerySet(models.QuerySet):
    def unread(self):
        return self.filter(read_at__isnull=True)

    def needing_sms_fallback(self):
        """Delivered by neither channel, and still worth trying by SMS.

        Module 10.2 triggers the fallback when push fails. A push that was never
        attempted because the user has no device is the same situation from the
        recipient's side, so both qualify.
        """
        return self.filter(
            push_state__in=[DeliveryState.FAILED, DeliveryState.SKIPPED],
            sms_state=DeliveryState.PENDING,
        )


class Notification(SocietyScopedModel, TimeStampedModel):
    """One message to one person (Module 10.3).

    Society-scoped so an administrator can see what their society has been told,
    and so retention and clean-up stay per-tenant.
    """

    recipient = models.ForeignKey(
        "accounts.User", on_delete=models.CASCADE, related_name="notifications"
    )
    category = models.CharField(
        max_length=30, choices=NotificationCategory.choices, db_index=True
    )

    title = models.CharField(max_length=120)
    body = models.CharField(max_length=400)

    data = models.JSONField(
        default=dict,
        blank=True,
        help_text=_(
            "Where tapping it should go — e.g. {'route': '/gate/log'}. Kept as "
            "data rather than a URL so the app decides its own navigation."
        ),
    )

    read_at = models.DateTimeField(null=True, blank=True)

    # --- Delivery attempts (Modules 10.1 and 10.2) --------------------------
    push_state = models.CharField(
        max_length=20, choices=DeliveryState.choices, default=DeliveryState.PENDING
    )
    sms_state = models.CharField(
        max_length=20, choices=DeliveryState.choices, default=DeliveryState.PENDING
    )
    delivery_note = models.CharField(
        max_length=300,
        blank=True,
        help_text=_("Why a channel failed or was skipped. For operators, not users."),
    )
    delivered_at = models.DateTimeField(null=True, blank=True)

    objects = NotificationQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["recipient", "-created_at"]),
            models.Index(fields=["recipient", "read_at"]),
            models.Index(fields=["push_state", "sms_state"]),
        ]

    def __str__(self):
        return f"{self.get_category_display()} → {self.recipient}"

    @property
    def is_read(self) -> bool:
        return self.read_at is not None

    @property
    def is_safety_critical(self) -> bool:
        return self.category in SAFETY_CRITICAL_CATEGORIES

    @property
    def was_delivered(self) -> bool:
        """Reached the person by at least one channel."""
        return DeliveryState.SENT in {self.push_state, self.sms_state}

    def mark_read(self) -> bool:
        """Idempotent — re-opening the list must not move the timestamp."""
        if self.read_at is not None:
            return False
        self.read_at = timezone.now()
        self.save(update_fields=["read_at", "updated_at"])
        return True

    def record_delivery(self, *, channel: str, state: str, note: str = "") -> None:
        """Record what one channel did.

        ``delivered_at`` is set by whichever channel succeeds first and then left
        alone: it answers "when did this person find out", and a later SMS
        attempt should not rewrite the moment a push already arrived.
        """
        if channel == "push":
            self.push_state = state
        else:
            self.sms_state = state

        if note:
            self.delivery_note = note[:300]
        if state == DeliveryState.SENT and self.delivered_at is None:
            self.delivered_at = timezone.now()

        self.save(
            update_fields=[
                "push_state", "sms_state", "delivery_note", "delivered_at", "updated_at"
            ]
        )


class NotificationPreference(TimeStampedModel):
    """Module 10.4 — one user's opt-out for one category.

    Stored as explicit mutes rather than a full per-category matrix, so a new
    category is on by default for everyone and nobody has to be migrated into
    receiving it. Missing row means "not muted".
    """

    user = models.ForeignKey(
        "accounts.User", on_delete=models.CASCADE, related_name="notification_preferences"
    )
    category = models.CharField(max_length=30, choices=NotificationCategory.choices)
    muted = models.BooleanField(default=True)

    class Meta:
        ordering = ["category"]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "category"], name="one_preference_per_user_category"
            )
        ]

    def __str__(self):
        state = "muted" if self.muted else "on"
        return f"{self.user} — {self.get_category_display()} ({state})"

    @staticmethod
    def is_muted(user, category: str) -> bool:
        """Whether this user has switched this category off.

        Safety-critical categories always answer False, whatever is stored. The
        check lives here rather than at each call site so there is exactly one
        place that decides, and no notification path can forget to ask.
        """
        if category in SAFETY_CRITICAL_CATEGORIES:
            return False

        return NotificationPreference.objects.filter(
            user=user, category=category, muted=True
        ).exists()

    @staticmethod
    def set_muted(user, category: str, *, muted: bool) -> bool:
        """Mute or unmute. Returns whether the request was honoured.

        A mute on a safety-critical category is refused rather than silently
        stored, so a client cannot believe it succeeded.
        """
        if muted and category in SAFETY_CRITICAL_CATEGORIES:
            return False

        NotificationPreference.objects.update_or_create(
            user=user, category=category, defaults={"muted": muted}
        )
        return True
