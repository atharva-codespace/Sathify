"""
Module 6 — Scheduling & Task Management.

The calendar layer underneath Modules 4 and 5. It deliberately stores very
little, because almost everything a calendar would hold already exists:

* a recurring visit is an ``Engagement`` (days of week, start time, duration),
* a one-off visit is a ``Booking`` (date, start time, duration), and
* which dates a worker will take one-off work is a ``DayAvailability``.

Materialising those into per-date calendar rows would create a second source of
truth that has to be kept in step with the first, and every bug in that sync
shows up as a worker being told to be somewhere nobody expects them. So the
schedule is **derived on read** (see ``schedule.py``) and this module stores only
the two things that genuinely have nowhere else to live:

* :class:`TaskTiming` — the resident's expected arrival and departure window for
  an engagement (Module 6.2), which attendance and reminders are measured
  against. The engagement says a worker comes at 09:00; the timing says how late
  is late.
* :class:`Reminder` — a scheduled notification job (Module 6.4), delivered by
  Module 10.

-------------------------------------------------------------------------------
WHY REMINDERS ARE ROWS AND NOT A CRON JOB
-------------------------------------------------------------------------------
There is no scheduler on the free tier — the same constraint that made hire
requests and bookings expire lazily. A reminder is therefore a durable row with a
``send_after`` timestamp, and whatever eventually does the sending (Module 10, an
external pinger hitting the due-reminders endpoint, or a real queue later) drains
the same table. Making the job durable rather than in-memory is what lets that
choice change without redesigning Module 6.
"""

from __future__ import annotations

import datetime as dt

from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.models import SocietyScopedModel, TimeStampedModel

#: How much lateness is tolerated before a visit counts as late, unless the
#: resident sets their own. Fifteen minutes is forgiving of traffic without
#: making an hour-late arrival look punctual.
DEFAULT_GRACE_MINUTES = 15


class TaskTiming(TimeStampedModel):
    """Module 6.2 — the resident's expectations for one engagement.

    Separate from ``Engagement`` rather than more columns on it, because the two
    are owned by different people and changed at different times: the engagement
    records what both parties agreed when the worker accepted, while the timing
    is the resident's ongoing expectation and can be tightened or relaxed without
    reopening the agreement. Attendance (Module 7) and reminders (6.4) measure
    against this, not against the engagement.

    Absent means "use the engagement's own times with the default grace" — see
    :func:`effective_timing`, which every consumer should go through rather than
    branching on ``None`` themselves.
    """

    engagement = models.OneToOneField(
        "hiring.Engagement", on_delete=models.CASCADE, related_name="task_timing"
    )

    expected_arrival = models.TimeField(
        null=True,
        blank=True,
        help_text=_("Defaults to the engagement's own start time."),
    )
    arrival_grace_minutes = models.PositiveSmallIntegerField(
        default=DEFAULT_GRACE_MINUTES,
        help_text=_("How late an arrival may be before it counts as late."),
    )

    expected_departure = models.TimeField(
        null=True,
        blank=True,
        help_text=_("Defaults to arrival plus the engagement's expected duration."),
    )
    departure_grace_minutes = models.PositiveSmallIntegerField(
        default=DEFAULT_GRACE_MINUTES,
        help_text=_("How early a departure may be before it counts as short-served."),
    )

    task_notes = models.TextField(
        blank=True,
        max_length=1000,
        help_text=_("What the resident expects done, shown to the worker each visit."),
    )

    #: Reminders are opt-out per engagement: a resident who does not want a
    #: notification before every single visit should not have to mute the whole
    #: app (Module 10 handles category-level muting).
    reminders_enabled = models.BooleanField(default=True)
    reminder_lead_minutes = models.PositiveSmallIntegerField(
        default=60, help_text=_("How far ahead of the expected arrival to remind.")
    )

    updated_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="updated_task_timings",
    )

    class Meta:
        verbose_name = _("task timing")
        verbose_name_plural = _("task timings")
        ordering = ["-updated_at"]

    def __str__(self):
        return f"Timing for engagement {self.engagement_id}"

    @property
    def arrival(self) -> dt.time:
        """The expected arrival, falling back to the engagement's start time."""
        return self.expected_arrival or self.engagement.start_time

    @property
    def departure(self) -> dt.time:
        """The expected departure, falling back to arrival plus duration."""
        if self.expected_departure:
            return self.expected_departure
        base = dt.datetime.combine(dt.date.min, self.arrival)
        return (
            base + dt.timedelta(minutes=self.engagement.expected_duration_minutes)
        ).time()

    def lateness_minutes(self, actual_arrival: dt.time) -> int:
        """How many minutes past the grace window an arrival was. 0 if on time.

        Used by Module 7 to judge a gate entry and by Module 9 to score
        reliability, so the definition of "late" lives here rather than being
        re-derived by each of them.
        """
        expected = self.arrival.hour * 60 + self.arrival.minute + self.arrival_grace_minutes
        actual = actual_arrival.hour * 60 + actual_arrival.minute
        return max(0, actual - expected)


class ReminderKind(models.TextChoices):
    UPCOMING_ENGAGEMENT = "upcoming_engagement", _("Recurring visit due")
    UPCOMING_BOOKING = "upcoming_booking", _("One-day booking due")
    BOOKING_UNCONFIRMED = "booking_unconfirmed", _("Booking still unconfirmed")


class ReminderStatus(models.TextChoices):
    SCHEDULED = "scheduled", _("Waiting to be sent")
    SENT = "sent", _("Sent")
    CANCELLED = "cancelled", _("Cancelled before sending")
    FAILED = "failed", _("Delivery failed")


class ReminderQuerySet(models.QuerySet):
    def due(self, *, now=None):
        """Scheduled reminders whose time has come and which are not stale.

        The upper bound matters: a reminder about a visit that already happened
        is worse than no reminder, so anything left undelivered past its event
        is skipped rather than sent late.
        """
        now = now or timezone.now()
        return self.filter(
            status=ReminderStatus.SCHEDULED,
            send_after__lte=now,
            event_at__gt=now,
        )

    def stale(self, *, now=None):
        """Scheduled reminders whose event has already passed."""
        now = now or timezone.now()
        return self.filter(status=ReminderStatus.SCHEDULED, event_at__lte=now)


class Reminder(SocietyScopedModel, TimeStampedModel):
    """Module 6.4 — a notification job waiting to be delivered by Module 10.

    Idempotent by construction: the unique constraint on
    (recipient, kind, event_at) means regenerating the schedule for a date range
    cannot produce a second copy of a reminder already queued, which is what
    makes generation safe to run on every read.
    """

    recipient = models.ForeignKey(
        "accounts.User", on_delete=models.CASCADE, related_name="reminders"
    )
    kind = models.CharField(max_length=30, choices=ReminderKind.choices)

    #: What the reminder is about. Nullable and non-exclusive because an
    #: engagement reminder has no booking and vice versa; exactly one is set.
    engagement = models.ForeignKey(
        "hiring.Engagement",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="reminders",
    )
    booking = models.ForeignKey(
        "bookings.Booking",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="reminders",
    )

    #: When the visit itself is expected. Used to suppress stale reminders.
    event_at = models.DateTimeField(db_index=True)
    #: The earliest this may be delivered.
    send_after = models.DateTimeField(db_index=True)

    title = models.CharField(max_length=120)
    body = models.CharField(max_length=300)

    status = models.CharField(
        max_length=20,
        choices=ReminderStatus.choices,
        default=ReminderStatus.SCHEDULED,
        db_index=True,
    )
    sent_at = models.DateTimeField(null=True, blank=True)
    failure_reason = models.CharField(max_length=200, blank=True)

    objects = ReminderQuerySet.as_manager()

    class Meta:
        ordering = ["send_after"]
        constraints = [
            models.UniqueConstraint(
                fields=["recipient", "kind", "event_at"],
                name="one_reminder_per_recipient_event",
            ),
        ]
        indexes = [
            models.Index(fields=["status", "send_after"]),
            models.Index(fields=["recipient", "status"]),
        ]

    def __str__(self):
        return f"{self.get_kind_display()} for {self.recipient} at {self.event_at}"

    @property
    def is_stale(self) -> bool:
        """The event has passed, so sending this now would only confuse."""
        return self.status == ReminderStatus.SCHEDULED and self.event_at <= timezone.now()

    def mark_sent(self) -> bool:
        """Idempotent — a redelivered webhook must not rewrite the timestamp."""
        if self.status == ReminderStatus.SENT:
            return False
        self.status = ReminderStatus.SENT
        self.sent_at = timezone.now()
        self.save(update_fields=["status", "sent_at", "updated_at"])
        return True

    def mark_failed(self, reason: str = "") -> bool:
        if self.status == ReminderStatus.SENT:
            return False
        self.status = ReminderStatus.FAILED
        self.failure_reason = reason[:200]
        self.save(update_fields=["status", "failure_reason", "updated_at"])
        return True
