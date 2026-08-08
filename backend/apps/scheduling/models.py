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


class VisitStatus(models.TextChoices):
    """How far through the day's work a visit is.

    Composed on read from three signals rather than stored as a column, because
    two of the three already exist elsewhere and copying them would create a
    second version of the truth:

    * **PENDING** — nothing has happened yet.
    * **IN_PROGRESS** — the gate logged an entry (Module 7), or the worker
      self-checked-in (13.3 tier 2). Not stored here; read from attendance.
    * **COMPLETE** — the worker marked the task done. This *is* stored here,
      because it exists nowhere else. See :class:`TaskCompletion`.

    Departure is deliberately **not** a fourth state. A worker can finish the
    job and leave twenty minutes later, or finish and stay for a cup of tea;
    neither says anything about whether the work was done. It travels alongside
    as its own flag so the household can see both facts without one implying
    the other.
    """

    PENDING = "pending", _("Not started")
    IN_PROGRESS = "in_progress", _("In progress")
    COMPLETE = "complete", _("Complete")


class TaskCompletion(SocietyScopedModel, TimeStampedModel):
    """Module 6.6 — the worker marks a day's work done.

    ---------------------------------------------------------------------------
    WHY THIS IS A ROW WHEN THE SCHEDULE IS NOT
    ---------------------------------------------------------------------------
    ``schedule.py`` stores nothing: a visit is derived from an engagement's
    recurring terms and a date. So "the visit on the 14th was completed" has
    nowhere to live — it is not derivable from the engagement, the booking, the
    availability calendar, or the gate log. Same reasoning that gave
    ``LeaveRequest`` a row, and the same discipline: this stores *only* the fact
    that has no other home, and the schedule reads it back when it expands.

    ---------------------------------------------------------------------------
    WHY THE WORKER MARKS IT, AND WHY IT IS NOT A GATE EVENT
    ---------------------------------------------------------------------------
    A gate entry says somebody arrived. It does not say the kitchen was cleaned.
    Conflating the two would pay on presence rather than work, and would break
    entirely for the tiers where there is no guard at all (13.3).

    So the worker marks it from their own phone, and it is deliberately *not*
    conditional on a check-in having been recorded. A gate scanner that was
    broken, a guard who was on a break, a GPS fix that would not settle — none
    of those are the worker's fault, and none of them should stop her saying she
    finished the job. The household sees both facts separately and can ask.
    """

    engagement = models.ForeignKey(
        "hiring.Engagement",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="task_completions",
    )
    booking = models.ForeignKey(
        "bookings.Booking",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="task_completions",
    )
    worker = models.ForeignKey(
        "workers.WorkerProfile", on_delete=models.CASCADE, related_name="task_completions"
    )

    #: The day the visit was for, not the day the button was pressed. A worker
    #: finishing at 00:10 is completing yesterday's visit.
    visit_date = models.DateField(db_index=True)
    completed_at = models.DateTimeField(default=timezone.now)

    note = models.CharField(max_length=300, blank=True)

    #: Module 7 (Section 7) — proof of completion. Optional, always: requiring a
    #: photo would make a flat battery or a cracked camera into an unpaid day.
    photo = models.ImageField(upload_to="visits/completions/%Y/%m/", blank=True)

    class Meta:
        ordering = ["-visit_date", "-completed_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["engagement", "visit_date"],
                condition=models.Q(engagement__isnull=False),
                name="one_completion_per_engagement_day",
            ),
            models.UniqueConstraint(
                fields=["booking"],
                condition=models.Q(booking__isnull=False),
                name="one_completion_per_booking",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(engagement__isnull=False, booking__isnull=True)
                    | models.Q(engagement__isnull=True, booking__isnull=False)
                ),
                name="completion_belongs_to_exactly_one_visit",
            ),
        ]
        indexes = [models.Index(fields=["worker", "visit_date"])]

    def __str__(self):
        return f"{self.worker} completed {self.visit_date}"


class LeaveStatus(models.TextChoices):
    """Where a leave request has got to.

    There is deliberately no PENDING. See :class:`LeaveRequest`.
    """

    APPROVED = "approved", _("Approved")
    WAIVED = "waived", _("Resident needs no replacement")
    REPLACEMENT_REQUESTED = "replacement_requested", _("Looking for a replacement")
    REPLACEMENT_CONFIRMED = "replacement_confirmed", _("Replacement confirmed")
    UNFILLED = "unfilled", _("No replacement found")
    WITHDRAWN = "withdrawn", _("Withdrawn by the worker")


#: Statuses from which the worker may still withdraw. Once a replacement has
#: been confirmed they may not: somebody else has rearranged their day on the
#: strength of it, and un-booking them by surprise is the same harm this module
#: exists to prevent, pointed the other way.
WITHDRAWABLE_LEAVE_STATUSES = frozenset(
    {
        LeaveStatus.APPROVED,
        LeaveStatus.WAIVED,
        LeaveStatus.REPLACEMENT_REQUESTED,
    }
)

#: Statuses where the day is finished with, one way or another.
SETTLEABLE_LEAVE_STATUSES = frozenset(
    {
        LeaveStatus.WAIVED,
        LeaveStatus.REPLACEMENT_CONFIRMED,
        LeaveStatus.UNFILLED,
    }
)


class LeaveRequestQuerySet(models.QuerySet):
    def live(self):
        """Everything that still affects a schedule. Excludes withdrawals."""
        return self.exclude(status=LeaveStatus.WITHDRAWN)

    def for_dates(self, start: dt.date, end: dt.date):
        return self.filter(leave_date__gte=start, leave_date__lte=end)

    def awaiting_resident(self):
        return self.filter(status=LeaveStatus.APPROVED)


class LeaveRequest(SocietyScopedModel, TimeStampedModel):
    """Module 6.5 — urgent leave ("chutti"): one worker, one engagement, one day.

    ---------------------------------------------------------------------------
    THERE IS NO PENDING STATE, AND THAT IS THE WHOLE DESIGN
    ---------------------------------------------------------------------------
    Leave is approved the moment it is asked for. Not as a convenience — as the
    mechanism. A worker who must justify a sick child to an app before they can
    stay home with them does not wait for the answer; they simply do not turn up,
    and the household finds out at seven in the morning with no time to plan.
    Instant approval is what buys the notice, and the notice is the thing of
    value here.

    So the resident is never asked *whether* the worker may take the day. They
    are asked the only question they can actually answer: **do you need someone
    else today?**

    ---------------------------------------------------------------------------
    WHY THIS IS A ROW WHEN THE REST OF MODULE 6 IS DERIVED
    ---------------------------------------------------------------------------
    ``schedule.py`` stores nothing and expands engagements on read, because a
    materialised calendar drifts from the agreement it was built from. Leave is
    the exception that proves it: an absence is *not* derivable from the
    engagement, the booking, or the availability calendar. It is a fact about one
    day that exists nowhere else, so it gets a row — and ``schedule.py`` reads it
    back when it expands, rather than the row being a second copy of the visit.

    ---------------------------------------------------------------------------
    THE MONEY
    ---------------------------------------------------------------------------
    Settlement is recorded here but the deduction is **not** applied here. Salary
    is pro-rated from attendance (``payments.services.salary_basis``): a day not
    worked is already a day not counted, so deducting again in this model would
    dock the same absence twice. What this row adds is the *transfer* — what the
    replacement is owed — and a frozen record of the arithmetic that produced it.
    See :func:`apps.scheduling.services.settle_leave`.
    """

    engagement = models.ForeignKey(
        "hiring.Engagement", on_delete=models.CASCADE, related_name="leave_requests"
    )
    worker = models.ForeignKey(
        "workers.WorkerProfile", on_delete=models.CASCADE, related_name="leave_requests"
    )
    leave_date = models.DateField(db_index=True)

    #: Always optional. A worker should not have to describe a private
    #: emergency to a form in order to be believed, and a required field here
    #: would mostly collect fiction.
    reason = models.CharField(max_length=200, blank=True)

    status = models.CharField(
        max_length=30,
        choices=LeaveStatus.choices,
        default=LeaveStatus.APPROVED,
        db_index=True,
    )

    replacement = models.ForeignKey(
        "workers.WorkerProfile",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="replacement_assignments",
    )
    replacement_confirmed_at = models.DateTimeField(null=True, blank=True)
    resident_responded_at = models.DateTimeField(null=True, blank=True)

    #: Frozen at settlement rather than derived on read. The day rate moves with
    #: the month and with the engagement's terms, and a receipt that silently
    #: disagrees with the payment it explains is worse than no receipt.
    day_rate_paise = models.PositiveIntegerField(default=0)
    #: What the original worker forgoes — equal to what the replacement receives.
    forgone_paise = models.PositiveIntegerField(default=0)
    #: What the replacement is paid. Their ``Payment`` row is the authority; this
    #: is the copy that makes the leave record readable on its own.
    replacement_paise = models.PositiveIntegerField(default=0)
    settled_at = models.DateTimeField(null=True, blank=True)

    objects = LeaveRequestQuerySet.as_manager()

    class Meta:
        ordering = ["-leave_date", "-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["engagement", "leave_date"],
                name="one_leave_request_per_engagement_day",
            ),
            # A worker cannot stand in for their own absence.
            models.CheckConstraint(
                condition=~models.Q(replacement=models.F("worker")),
                name="replacement_is_not_the_absent_worker",
            ),
        ]
        indexes = [
            models.Index(fields=["status", "leave_date"]),
            models.Index(fields=["worker", "leave_date"]),
        ]

    def __str__(self):
        return f"{self.worker} on leave {self.leave_date} ({self.status})"

    @property
    def is_settled(self) -> bool:
        return self.settled_at is not None

    @property
    def needs_resident_response(self) -> bool:
        return self.status == LeaveStatus.APPROVED

    @property
    def can_withdraw(self) -> bool:
        """Withdrawable only while nobody else has committed to the day."""
        return self.status in WITHDRAWABLE_LEAVE_STATUSES and not self.is_settled

    @property
    def is_covered(self) -> bool:
        """Whether somebody is actually coming."""
        return self.status == LeaveStatus.REPLACEMENT_CONFIRMED

    @property
    def summary(self) -> str:
        """One line for a notification or a list row."""
        if self.status == LeaveStatus.REPLACEMENT_CONFIRMED and self.replacement_id:
            return f"{self.replacement.user.get_full_name()} is covering this visit."
        if self.status == LeaveStatus.WAIVED:
            return "No replacement needed."
        if self.status == LeaveStatus.UNFILLED:
            return "No replacement was found for this visit."
        if self.status == LeaveStatus.WITHDRAWN:
            return "The leave was withdrawn."
        if self.status == LeaveStatus.REPLACEMENT_REQUESTED:
            return "Looking for a replacement."
        return "Waiting for the household to say whether they need cover."


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
