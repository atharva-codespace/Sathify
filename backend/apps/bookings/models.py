"""
Module 5 — One-Day Service Booking.

Entity shape::

    ServiceCategory ──< Booking >── WorkerProfile
                            │            │
                       Resident      DayAvailability

-------------------------------------------------------------------------------
WHY THIS IS NOT A VARIANT OF MODULE 4
-------------------------------------------------------------------------------
A ``Booking`` is a single time-bound job; an ``Engagement`` is a standing
relationship. The modspec keeps them deliberately distinct because urgency,
pricing and cancellation behave differently: a recurring engagement is paused
and resumed over months and has no per-visit fee, while a booking happens once,
is quoted up front, and carries a cancellation fee that depends on how close to
the start time it was called off. Folding bookings into ``Engagement`` would
mean a nullable cancellation-fee column and a status enum that means different
things depending on a type flag.

-------------------------------------------------------------------------------
TWO DIFFERENT "SERVICE" CONCEPTS — DO NOT CONFLATE
-------------------------------------------------------------------------------
* ``workers.ServiceType`` — what a *worker* does: maid, cook, cleaner. A worker
  picks these on their profile, and Module 4 filters on them.
* ``bookings.ServiceCategory`` — what a *resident books*: deep cleaning, event
  preparation, temporary cooking, emergency assistance. Each carries expected
  duration and price guidance, and maps to the ``ServiceType`` qualified to do
  it, which is how Module 5.3 narrows the candidate pool.

They are separate models because the two vocabularies genuinely differ: one
"deep cleaning" job is done by a cleaner, but a cleaner also does recurring
work that is not a bookable one-off category.

-------------------------------------------------------------------------------
TIMEZONES
-------------------------------------------------------------------------------
``scheduled_date`` and ``start_time`` are stored separately, in the society's
local terms, rather than as one aware datetime. The service date is what a
worker opts into (``DayAvailability.date``) and what a resident picks off a
calendar, and both must mean the same local day regardless of how the server is
configured. :attr:`Booking.scheduled_start` derives the aware datetime for
deadline arithmetic; nothing else should recombine the two by hand.
"""

from __future__ import annotations

import datetime as dt

from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.models import SocietyScopedModel, TimeStampedModel


def combine_local(day: dt.date, moment: dt.time) -> dt.datetime:
    """A timezone-aware datetime from a local date and time.

    The single place the two stored halves are recombined, so that the choice
    of timezone is made once rather than at each call site.
    """
    return timezone.make_aware(
        dt.datetime.combine(day, moment), timezone.get_default_timezone()
    )


def minutes_of(moment: dt.time) -> int:
    """Minutes since local midnight — the unit all overlap maths uses."""
    return moment.hour * 60 + moment.minute


def windows_overlap(
    start_a: int, duration_a: int, start_b: int, duration_b: int
) -> bool:
    """Whether two same-day windows, given in minutes since midnight, collide.

    Touching windows do not overlap: a job ending at 12:00 and another starting
    at 12:00 are back-to-back, which is a normal working day, not a conflict.
    """
    return start_a < start_b + duration_b and start_b < start_a + duration_a


class ServiceCategory(TimeStampedModel):
    """Module 5.1 — a bookable one-off job, with duration and price guidance.

    Platform-level and predefined (seeded from the SRS list in a data
    migration) rather than per-society, so that a category means the same thing
    everywhere and Module 11's cross-society reporting can aggregate on it.
    Which categories a given society actually offers is a configuration
    question the modspec assigns to society administration (modspec 2.5); until
    that lands, ``is_active`` is the platform-wide switch.
    """

    name = models.CharField(max_length=80, unique=True)
    slug = models.SlugField(max_length=80, unique=True)
    description = models.CharField(max_length=250, blank=True)
    icon = models.CharField(
        max_length=40,
        blank=True,
        help_text=_("Material icon name; the UI is icon-led for low-literacy users."),
    )

    service_type = models.ForeignKey(
        "workers.ServiceType",
        on_delete=models.PROTECT,
        related_name="booking_categories",
        null=True,
        blank=True,
        help_text=_(
            "Which kind of worker is qualified for this job. Module 5.3 narrows "
            "the candidate pool with it. Null means any approved worker may be "
            "booked, which is the right default for a category no existing "
            "service type cleanly covers."
        ),
    )

    # --- Guidance shown to the resident before they commit (5.1) ------------
    expected_duration_minutes = models.PositiveSmallIntegerField(
        default=120, help_text=_("Prefills the booking form and sets the default quote.")
    )
    price_min = models.PositiveIntegerField(help_text=_("Indicative floor, INR."))
    price_max = models.PositiveIntegerField(help_text=_("Indicative ceiling, INR."))

    bypasses_notice_period = models.BooleanField(
        default=False,
        help_text=_(
            "Exempts this category from the society's minimum booking notice. "
            "Set for emergency assistance: a notice window that blocks an "
            "emergency defeats the purpose of the category."
        ),
    )

    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name_plural = _("service categories")
        ordering = ["name"]

    def __str__(self):
        return self.name

    @property
    def price_guidance(self) -> str:
        if self.price_min == self.price_max:
            return f"₹{self.price_min}"
        return f"₹{self.price_min}–₹{self.price_max}"


class DayAvailability(TimeStampedModel):
    """A worker's answer for one specific date (Module 5.3).

    Module 5 matches against "workers who have opted into that day's
    availability rather than the full worker pool", which needs a per-date
    record — ``WorkerProfile.is_available`` is a single global toggle and
    ``available_from``/``available_until`` are the worker's usual hours, neither
    of which can say "yes to Saturday, no to Sunday".

    The same row also expresses the opposite: ``is_available=False`` blocks a
    date the worker cannot work, which is what a resident's search must respect
    even for a worker who is generally available.

    SCOPE NOTE: this is deliberately the thin slice Module 5 needs. Module 6 is
    specified as the calendar layer underneath both engagements and bookings,
    and is expected to absorb or generalise this model — so treat it as Module
    5's minimum, not as the finished availability system.
    """

    worker = models.ForeignKey(
        "workers.WorkerProfile", on_delete=models.CASCADE, related_name="day_availability"
    )
    date = models.DateField(db_index=True)

    is_available = models.BooleanField(
        default=True,
        help_text=_("True opts into this date; False blocks it out."),
    )
    # Optional narrower window than the worker's usual hours, for a day they can
    # only work part of.
    start_time = models.TimeField(null=True, blank=True)
    end_time = models.TimeField(null=True, blank=True)
    note = models.CharField(max_length=200, blank=True)

    class Meta:
        verbose_name_plural = _("day availability")
        ordering = ["-date"]
        constraints = [
            models.UniqueConstraint(
                fields=["worker", "date"], name="one_availability_row_per_worker_day"
            )
        ]
        indexes = [models.Index(fields=["date", "is_available"])]

    def __str__(self):
        state = "available" if self.is_available else "unavailable"
        return f"{self.worker} — {self.date} ({state})"

    def covers(self, start: dt.time, duration_minutes: int) -> bool:
        """Whether this day's declared window can accommodate a job.

        A row with no times is an open "yes to this date" and covers anything;
        narrowing the window is opt-in, so an unset field must not be read as a
        restriction.
        """
        if not self.is_available:
            return False
        if self.start_time is None or self.end_time is None:
            return True

        job_start = minutes_of(start)
        return (
            minutes_of(self.start_time) <= job_start
            and job_start + duration_minutes <= minutes_of(self.end_time)
        )


class BookingStatus(models.TextChoices):
    PENDING = "pending", _("Awaiting the worker's confirmation")
    CONFIRMED = "confirmed", _("Confirmed")
    COMPLETED = "completed", _("Completed")
    DECLINED = "declined", _("Declined by the worker")
    CANCELLED = "cancelled", _("Cancelled")
    EXPIRED = "expired", _("Not confirmed before the start time")


class CancelledBy(models.TextChoices):
    RESIDENT = "resident", _("Resident")
    WORKER = "worker", _("Worker")
    ADMIN = "admin", _("Society administrator")


class BookingQuerySet(models.QuerySet):
    """Deadline-aware queries. Prefer these over raw ``status=`` filters."""

    def live(self):
        """Not yet resolved — these are the ones that occupy a worker's day."""
        return self.filter(
            status__in=[BookingStatus.PENDING, BookingStatus.CONFIRMED]
        )

    def stale(self):
        """Still pending although the start time has passed.

        Expired in fact, whether or not the row has been swept yet — see
        :meth:`expire_stale`.
        """
        now = timezone.now()
        return self.filter(
            status=BookingStatus.PENDING,
            scheduled_date__lte=timezone.localdate(now),
        ).filter(
            models.Q(scheduled_date__lt=timezone.localdate(now))
            | models.Q(start_time__lte=now.astimezone(timezone.get_default_timezone()).time())
        )

    def expire_stale(self) -> int:
        """Flip un-confirmed past bookings to EXPIRED. Returns how many.

        Same lazy-expiry approach as Module 4's hire requests: there is no
        scheduled worker on the free tier, so rows are swept on read. Idempotent
        and safe to call on every request.
        """
        return self.stale().update(
            status=BookingStatus.EXPIRED, updated_at=timezone.now()
        )


class Booking(SocietyScopedModel, TimeStampedModel):
    """Module 5.2 / 5.4 — one time-bound job for one worker on one date."""

    resident = models.ForeignKey(
        "societies.Resident", on_delete=models.PROTECT, related_name="bookings"
    )
    worker = models.ForeignKey(
        "workers.WorkerProfile", on_delete=models.PROTECT, related_name="bookings"
    )
    category = models.ForeignKey(
        ServiceCategory, on_delete=models.PROTECT, related_name="bookings"
    )

    # --- When (local to the society; see the module docstring) --------------
    scheduled_date = models.DateField(db_index=True)
    start_time = models.TimeField()
    expected_duration_minutes = models.PositiveSmallIntegerField(default=120)

    quoted_price = models.PositiveIntegerField(
        help_text=_("Agreed price for the job in INR, fixed when the booking is made.")
    )
    notes = models.TextField(blank=True, max_length=500)

    status = models.CharField(
        max_length=20,
        choices=BookingStatus.choices,
        default=BookingStatus.PENDING,
        db_index=True,
    )

    confirmed_at = models.DateTimeField(null=True, blank=True)
    declined_at = models.DateTimeField(null=True, blank=True)
    response_note = models.TextField(blank=True, max_length=500)
    completed_at = models.DateTimeField(null=True, blank=True)

    # --- 5.4 cancellation ---------------------------------------------------
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancelled_by = models.CharField(
        max_length=20, choices=CancelledBy.choices, blank=True
    )
    cancellation_reason = models.TextField(blank=True, max_length=500)
    cancellation_fee = models.PositiveIntegerField(
        default=0,
        help_text=_(
            "Charged at cancellation, in INR. Stored rather than recomputed: "
            "the policy may change later, and what was charged at the time must "
            "not silently change with it. Module 8 settles this amount."
        ),
    )

    objects = BookingQuerySet.as_manager()

    class Meta:
        ordering = ["-scheduled_date", "-start_time"]
        constraints = [
            # Catches an exact duplicate from a double-tapped Book button.
            # Genuine overlap (different start times, colliding windows) cannot
            # be expressed as a constraint and is enforced in services.py.
            models.UniqueConstraint(
                fields=["worker", "scheduled_date", "start_time"],
                condition=models.Q(
                    status__in=[BookingStatus.PENDING, BookingStatus.CONFIRMED]
                ),
                name="one_live_booking_per_worker_slot",
            ),
        ]
        indexes = [
            models.Index(fields=["worker", "scheduled_date", "status"]),
            models.Index(fields=["resident", "-scheduled_date"]),
            models.Index(fields=["status", "scheduled_date"]),
        ]

    def __str__(self):
        return f"{self.category} for {self.resident} on {self.scheduled_date}"

    # --- Derived time ------------------------------------------------------

    @property
    def scheduled_start(self) -> dt.datetime:
        """Timezone-aware start of the job."""
        return combine_local(self.scheduled_date, self.start_time)

    @property
    def scheduled_end(self) -> dt.datetime:
        return self.scheduled_start + dt.timedelta(minutes=self.expected_duration_minutes)

    @property
    def end_time(self) -> dt.time:
        """Local end time. Wraps past midnight rather than clamping."""
        return self.scheduled_end.astimezone(timezone.get_default_timezone()).time()

    @property
    def start_minutes(self) -> int:
        return minutes_of(self.start_time)

    @property
    def hours_until_start(self) -> float:
        """Negative once the start time has passed."""
        return (self.scheduled_start - timezone.now()).total_seconds() / 3600

    # --- State -------------------------------------------------------------

    @property
    def is_live(self) -> bool:
        """Occupies the worker's day: pending or confirmed."""
        return self.status in {BookingStatus.PENDING, BookingStatus.CONFIRMED}

    @property
    def has_started(self) -> bool:
        return timezone.now() >= self.scheduled_start

    @property
    def is_stale(self) -> bool:
        """Still unconfirmed although the start time has passed."""
        return self.status == BookingStatus.PENDING and self.has_started

    @property
    def effective_status(self) -> str:
        """Status as the user should see it, accounting for un-swept expiry."""
        return BookingStatus.EXPIRED if self.is_stale else self.status

    @property
    def is_actionable(self) -> bool:
        """Whether the worker may still confirm or decline."""
        return self.status == BookingStatus.PENDING and not self.has_started

    @property
    def can_be_cancelled(self) -> bool:
        """Cancellable right up to the start time — with a fee near it."""
        return self.is_live and not self.has_started
