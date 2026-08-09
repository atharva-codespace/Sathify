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
            "Exempts this category from the society's minimum booking notice, "
            "and from requiring workers to have pre-marked the date as one "
            "they can work (see bookings.services.candidate_workers). Set for "
            "emergency assistance: nobody pre-declares availability for an "
            "emergency that has not happened yet, so a category meant to be "
            "bookable at short notice needs both exemptions to actually work."
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
    """Where a booking has got to.

    Two lifecycles share this enum, because they share a row:

    * **Directed** (the ordinary path) — a resident picks one worker, so the
      booking opens at ``PENDING`` and that one worker answers it.
    * **Broadcast** (the emergency path, Module 5.5) — nobody is picked. The
      booking opens at ``PAYMENT_PENDING``, moves to ``BROADCAST`` once the
      surcharge is settled, and reaches ``CONFIRMED`` when the first worker to
      accept claims it. ``UNFULFILLED`` is where it lands if none of them does.

    They converge at ``CONFIRMED``: from that point a booking behaves
    identically whichever way it was created, which is the whole reason this is
    one model and one enum rather than two parallel ones.
    """

    #: Emergency only. The surcharge has not settled, so nobody has been told
    #: about this job yet — see ``bookings/emergency.py``.
    PAYMENT_PENDING = "payment_pending", _("Awaiting the emergency surcharge")
    #: Emergency only. Offered to several workers at once; unclaimed so far.
    BROADCAST = "broadcast", _("Offered to available workers")

    PENDING = "pending", _("Awaiting the worker's confirmation")
    CONFIRMED = "confirmed", _("Confirmed")
    COMPLETED = "completed", _("Completed")
    DECLINED = "declined", _("Declined by the worker")
    CANCELLED = "cancelled", _("Cancelled")
    EXPIRED = "expired", _("Not confirmed before the start time")
    #: Emergency only. Broadcast, and nobody accepted before the deadline.
    UNFULFILLED = "unfulfilled", _("Nobody accepted in time")


#: Statuses in which an emergency booking is still looking for somebody.
#:
#: Named because three different places have to agree on it — the sweep, the
#: resident's live view, and the cancellation path — and a list repeated three
#: times is a list that eventually disagrees with itself.
EMERGENCY_OPEN_STATUSES = frozenset(
    {BookingStatus.PAYMENT_PENDING, BookingStatus.BROADCAST}
)


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

        Deliberately keyed on ``status=PENDING`` only. A broadcast emergency has
        no single worker who could have confirmed it, so "not confirmed before
        the start time" is not a thing that can happen to one — it expires on
        its own offer deadline instead, through
        :func:`apps.bookings.emergency.expire_unclaimed`.
        """
        return self.stale().update(
            status=BookingStatus.EXPIRED, updated_at=timezone.now()
        )

    def unclaimed(self):
        """Broadcast emergencies whose offer window has closed."""
        return self.filter(
            status=BookingStatus.BROADCAST,
            offer_expires_at__isnull=False,
            offer_expires_at__lte=timezone.now(),
        )


class Booking(SocietyScopedModel, TimeStampedModel):
    """Module 5.2 / 5.4 — one time-bound job for one worker on one date."""

    resident = models.ForeignKey(
        "societies.Resident", on_delete=models.PROTECT, related_name="bookings"
    )
    #: Null only while an emergency booking is still looking for somebody.
    #:
    #: Every other status implies a worker, and every existing consumer reads
    #: bookings that are PENDING or later, so nothing outside the emergency
    #: module ever sees this null. That is the property that made nullable
    #: cheaper than a parallel "EmergencyRequest" model: a job that has found
    #: its worker is an ordinary booking, and payments, ratings, attendance and
    #: the schedule all keep working on it without knowing how it was created.
    worker = models.ForeignKey(
        "workers.WorkerProfile",
        on_delete=models.PROTECT,
        related_name="bookings",
        null=True,
        blank=True,
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

    # --- 5.5 emergency broadcast -------------------------------------------
    #
    # Frozen at creation, like ``cancellation_fee`` and for the same reason: the
    # surcharge table may be re-priced later, and what a household was actually
    # charged must not change retrospectively when it is.
    emergency_surcharge_paise = models.PositiveIntegerField(
        default=0,
        help_text=_(
            "Module 5.5 — the platform's emergency fee, in paise, fixed when "
            "the request was raised. Zero on every ordinary booking."
        ),
    )
    broadcast_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text=_("When the request was released to workers."),
    )
    offer_expires_at = models.DateTimeField(
        null=True,
        blank=True,
        db_index=True,
        help_text=_(
            "When an unclaimed broadcast gives up. Indexed because the sweep "
            "that closes lapsed requests runs on ordinary read paths."
        ),
    )

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

    # --- Emergency (Module 5.5) --------------------------------------------

    @property
    def is_emergency(self) -> bool:
        """Whether this booking went through the broadcast flow.

        Read off the category's existing ``bypasses_notice_period`` flag rather
        than stored a second time. That flag already *means* "this is an
        emergency category" — its own help text says so — and a second boolean
        that must always equal the first is a second boolean that eventually
        does not.
        """
        return bool(self.category_id and self.category.bypasses_notice_period)

    @property
    def is_awaiting_surcharge(self) -> bool:
        return self.status == BookingStatus.PAYMENT_PENDING

    @property
    def is_broadcast(self) -> bool:
        """Out with the workers, unclaimed."""
        return self.status == BookingStatus.BROADCAST

    @property
    def is_seeking_worker(self) -> bool:
        """Raised, but nobody is coming yet — for either emergency reason."""
        return self.status in EMERGENCY_OPEN_STATUSES

    @property
    def offer_window_closed(self) -> bool:
        if self.offer_expires_at is None:
            return False
        return timezone.now() >= self.offer_expires_at

    @property
    def seconds_left_to_claim(self) -> int:
        """How long a worker still has to accept. Zero once the window shuts."""
        if self.offer_expires_at is None:
            return 0
        return max(0, int((self.offer_expires_at - timezone.now()).total_seconds()))

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
        """Occupies a worker's day: pending or confirmed.

        A broadcast emergency is deliberately **not** live. It occupies nobody's
        day precisely because nobody has taken it, and counting it as a
        commitment would block every worker it was offered to from being
        matched to anything else.
        """
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
        """Status as the user should see it, accounting for un-swept sweeps.

        Both lazy expiries are reflected here, so a row that is over in fact but
        has not been written yet never reads as still open to the person
        looking at it.
        """
        if self.is_stale:
            return BookingStatus.EXPIRED
        if self.is_broadcast and self.offer_window_closed:
            return BookingStatus.UNFULFILLED
        return self.status

    @property
    def is_actionable(self) -> bool:
        """Whether the worker may still confirm or decline.

        Broadcast bookings are answered through the offer endpoints instead —
        there is no single worker for this to be asking about.
        """
        return self.status == BookingStatus.PENDING and not self.has_started

    @property
    def can_be_completed(self) -> bool:
        """Whether "mark as done" would be accepted right now.

        -------------------------------------------------------------------
        WHY THIS IS A DATE COMPARISON AND NOT ``has_started``
        -------------------------------------------------------------------
        It used to be ``has_started`` — the job may only be closed out once the
        clock has passed its start time. That reads as obviously right and is
        the reason "Mark as done" failed on emergency bookings.

        An emergency is booked minutes before it is served, so the gap between
        "she is standing in the flat, finished" and "the start time on the row"
        is routinely on the wrong side of zero: the resident raises it at 14:20
        for 14:30, the worker arrives at 14:22 because that is what an emergency
        *is*, finishes at 14:28, taps the button, and the server tells her the
        job has not started yet. She cannot fix that, and the household cannot
        pay for work that will not close.

        A worker saying she has finished is the authority on whether she has
        finished. The only thing worth guarding against is closing out a job on
        a day that has not arrived, which is what the date comparison does — a
        booking for next Tuesday is still refused, on Monday and on every day
        before it.
        """
        return (
            self.status == BookingStatus.CONFIRMED
            and self.scheduled_date <= timezone.localdate()
        )

    @property
    def can_be_cancelled(self) -> bool:
        """Cancellable right up to the start time — with a fee near it.

        An emergency still hunting for a worker is cancellable too, and always
        free: there is no worker yet who could have turned other work away, so
        there is nobody for a fee to compensate.
        """
        if self.is_seeking_worker:
            return True
        return self.is_live and not self.has_started


class OfferState(models.TextChoices):
    OFFERED = "offered", _("Waiting for an answer")
    ACCEPTED = "accepted", _("Accepted — this worker got the job")
    DECLINED = "declined", _("Declined")
    #: Somebody else got there first. Distinct from DECLINED on purpose: a
    #: worker who was beaten to a job did not turn it down, and Module 9's trust
    #: scoring must never be able to read the two as the same thing.
    LOST = "lost", _("Another worker accepted first")
    EXPIRED = "expired", _("The request lapsed unanswered")


class BookingOfferQuerySet(models.QuerySet):
    def open(self):
        return self.filter(state=OfferState.OFFERED)


class BookingOffer(TimeStampedModel):
    """Module 5.5 — one emergency request, as put to one worker.

    -------------------------------------------------------------------------
    WHY THE OFFER IS A ROW AND NOT JUST A NOTIFICATION
    -------------------------------------------------------------------------
    A broadcast could have been implemented as "notify everyone who matches and
    let whoever answers first hit the accept endpoint". That would work right up
    to the first question anybody asks about it: who was this actually offered
    to, did she ever see it, and why did the request lapse when six workers were
    free? A notification is a delivery attempt, not a record of who was asked.

    So the offer is stored. It also gives the maid's dashboard something cheap
    to query — one indexed lookup on ``(worker, state)`` — rather than
    re-running the matcher on every poll, which is what a five-second refresh
    interval makes unaffordable.

    The row is **not** the arbiter of who won. That is decided by a conditional
    UPDATE on the booking itself; see ``emergency.accept_offer``. Offers are
    reconciled to match the outcome afterwards.
    """

    booking = models.ForeignKey(
        Booking, on_delete=models.CASCADE, related_name="offers"
    )
    worker = models.ForeignKey(
        "workers.WorkerProfile", on_delete=models.CASCADE, related_name="booking_offers"
    )

    state = models.CharField(
        max_length=20, choices=OfferState.choices, default=OfferState.OFFERED,
        db_index=True,
    )
    #: Where this worker ranked when the request went out (0 is the best match).
    #: Kept for the unmet-demand picture: "we asked eight people and the best
    #: three never answered" is a different problem from "we could only find
    #: one".
    rank = models.PositiveSmallIntegerField(default=0)
    responded_at = models.DateTimeField(null=True, blank=True)

    objects = BookingOfferQuerySet.as_manager()

    class Meta:
        ordering = ["rank", "created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["booking", "worker"], name="one_offer_per_worker_per_booking"
            ),
            # At most one accepted offer per booking. The conditional UPDATE in
            # ``emergency.accept_offer`` is what prevents a second winner; this
            # is the database saying so as well, so a future code path cannot
            # quietly reintroduce the race this feature was rebuilt to remove.
            models.UniqueConstraint(
                fields=["booking"],
                condition=models.Q(state=OfferState.ACCEPTED),
                name="one_accepted_offer_per_booking",
            ),
        ]
        indexes = [
            models.Index(fields=["worker", "state"]),
            models.Index(fields=["booking", "state"]),
        ]

    def __str__(self):
        return f"{self.booking_id} → {self.worker_id} ({self.state})"

    @property
    def is_open(self) -> bool:
        return self.state == OfferState.OFFERED
