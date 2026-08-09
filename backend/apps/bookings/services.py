"""
Module 5 — the database side of one-day bookings.

Two things here are worth reading before changing:

**Ranking is Module 4's, not a second copy.** Module 5.3 is specified as
"reuses Module 4's recommendation scoring, filtered specifically to workers who
have opted into today's availability rather than the full worker pool". So this
module narrows the *pool* and delegates the *ordering* — it imports
``hiring.services`` rather than reimplementing a formula that would then have to
be kept in step with it.

**Conflict detection runs in Python, deliberately.** An engagement's recurring
days live in a ``JSONField``, and the ``__contains`` lookup that would filter
them in SQL is unsupported on SQLite — which is what dev and the whole test
suite run on, while production is Postgres. A query that passed CI and failed in
production is a worse trade than iterating a society-sized candidate list, so
the weekday match happens in Python. The queries it iterates are bounded and
batched: one for bookings, one for engagements, regardless of pool size.
"""

from __future__ import annotations

import datetime as dt
import logging

from django.db import transaction
from django.db.models import F, Prefetch
from django.utils import timezone

from apps.hiring.scoring import MatchScore
from apps.hiring.services import annotate_hiring_stats, rank_workers, searchable_workers

# Module 6 owns conflict detection (modspec 6.3) — the calendar layer is the one
# place that knows what a worker is already committed to. Imported rather than
# reimplemented: two answers to "is this worker busy" is how someone ends up
# booked in two places at once.
from apps.scheduling.services import conflicted_worker_ids
from apps.workers.models import WorkerProfile

from .models import Booking, BookingStatus, DayAvailability, ServiceCategory, minutes_of
from .policy import CancellationOutcome, cancellation_outcome, check_notice_period

logger = logging.getLogger(__name__)


def _notify(*, recipient, title: str, body: str) -> None:
    """Tell someone about a booking. Imported lazily; never raises.

    See the equivalent in ``apps.hiring.services`` — Module 10 is a leaf that
    other modules call, and a delivery failure must not roll back a confirmed
    booking.
    """
    from apps.notifications.models import NotificationCategory
    from apps.notifications.services import notify

    notify(
        recipient=recipient,
        category=NotificationCategory.BOOKING,
        title=title,
        body=body,
        data={"route": "/bookings"},
    )


class BookingError(Exception):
    """Base for refusals that are business rules, not bugs."""

    code = "booking_error"


class NoticeTooShort(BookingError):
    code = "notice_too_short"


class WorkerUnavailable(BookingError):
    code = "worker_unavailable"


class SlotConflict(BookingError):
    code = "slot_conflict"


class BookingNotActionable(BookingError):
    code = "booking_not_actionable"


class EmergencyMustBroadcast(BookingError):
    """A notice-exempt category may not be booked at one named worker."""

    code = "emergency_must_broadcast"


# ---------------------------------------------------------------------------
# 5.3 Same-day availability matching
# ---------------------------------------------------------------------------


def candidate_workers(
    society_id,
    *,
    category: ServiceCategory,
    on_date: dt.date,
):
    """Workers who could in principle take this category on this date.

    Approved and searchable (Module 4's rule), qualified for the category's
    service type, and not blocked out for the date.

    ---------------------------------------------------------------------
    WHY A MISSING DAY ROW MEANS "YES", FOR EVERY CATEGORY
    ---------------------------------------------------------------------
    This used to require an explicit ``DayAvailability`` opt-in row for every
    category except ``bypasses_notice_period`` ones, on the reading that
    "silence is not consent". In practice that made four of the five seeded
    categories permanently unmatchable: setting the opt-in is a per-date
    toggle buried behind the overflow menu on the worker's schedule, so
    almost no rows exist, and a resident booking deep cleaning or event
    preparation got "nobody is free" while emergency assistance — the one
    seeded category carrying the exemption — found the very same workers.
    A filter that only ever passes when a rarely-used screen has been visited
    is not a consent check, it is an outage.

    So the default is inverted: a worker who is approved, carries a photo and
    has *globally* marked themselves available (``WorkerProfile.is_available``,
    enforced by ``searchable_workers``) is bookable on any date they have not
    blocked. That global flag is the real, maintained statement of "I am
    working"; the per-date row is now purely an **override** on top of it,
    which is what the model's own docstring already says it "also expresses".

    An explicit ``is_available=False`` row still blocks the date, and a
    narrower declared window is still honoured — see
    :func:`_covers_requested_slot`. Only the *absence* of a row changed
    meaning, and it now means the same thing for every category, so there is
    no longer a per-category branch here.
    """
    queryset = searchable_workers(society_id)

    if category.service_type_id:
        queryset = queryset.filter(service_types=category.service_type_id)

    queryset = queryset.exclude(
        day_availability__date=on_date, day_availability__is_available=False
    )

    return (
        annotate_hiring_stats(queryset)
        .prefetch_related(
            # The row is needed again in Python to honour any narrower window
            # the worker set for the day; fetching it here avoids a query per
            # candidate.
            Prefetch(
                "day_availability",
                queryset=DayAvailability.objects.filter(date=on_date),
                to_attr="requested_day_rows",
            )
        )
        .distinct()
    )


def _covers_requested_slot(rows, start_time: dt.time, duration_minutes: int) -> bool:
    """Whether a worker's declared day-availability window covers this slot.

    A row that exists is the authority — an explicit narrower window (or an
    explicit "not available", already filtered out upstream) is honoured. No
    row at all is the silence that :func:`candidate_workers` reads as "yes",
    so it covers anything; the worker's usual hours still influence *ranking*
    through Module 4.3's availability component, they just do not exclude.
    """
    if not rows:
        return True
    return any(row.covers(start_time, duration_minutes) for row in rows)


def match_workers(
    society_id,
    *,
    category: ServiceCategory,
    on_date: dt.date,
    start_time: dt.time,
    duration_minutes: int,
    resident_society=None,
) -> list[tuple[WorkerProfile, MatchScore]]:
    """Module 5.3 — ranked workers who can actually take this job.

    Ordering is Module 4.3's score, computed over the booking's own time window
    so that availability counts toward the match rather than being a separate,
    invisible filter.
    """
    candidates = list(candidate_workers(society_id, category=category, on_date=on_date))

    start_minutes = minutes_of(start_time)
    end_minutes = start_minutes + duration_minutes

    # Honour a narrower window the worker declared for this specific day. A
    # worker with no row at all said nothing either way, which
    # candidate_workers() reads as "yes" — so a bare "no row" must not be
    # re-filtered out here, or that default would be undone right back.
    covered = [
        worker
        for worker in candidates
        if _covers_requested_slot(
            getattr(worker, "requested_day_rows", []), start_time, duration_minutes
        )
    ]

    conflicted = conflicted_worker_ids(
        [worker.pk for worker in covered],
        on_date=on_date,
        start_minutes=start_minutes,
        duration_minutes=duration_minutes,
    )
    free = [worker for worker in covered if worker.pk not in conflicted]

    return rank_workers(
        free,
        resident_society=resident_society,
        requested_from=start_time,
        # Expressed as a time so it lines up with the worker's stated hours;
        # a job running past midnight is clamped for scoring purposes only.
        requested_until=(
            dt.time(23, 59) if end_minutes >= 24 * 60 else dt.time(end_minutes // 60, end_minutes % 60)
        ),
    )


# ---------------------------------------------------------------------------
# 5.2 Booking creation
# ---------------------------------------------------------------------------


def notice_hours_for(society) -> int:
    """The society's configured minimum notice (Module 2.5)."""
    from .policy import DEFAULT_NOTICE_HOURS

    return getattr(society, "booking_notice_hours", None) or DEFAULT_NOTICE_HOURS


@transaction.atomic
def create_booking(
    *,
    resident,
    worker: WorkerProfile,
    category: ServiceCategory,
    society,
    scheduled_date: dt.date,
    start_time: dt.time,
    duration_minutes: int,
    quoted_price: int,
    notes: str = "",
) -> Booking:
    """Create a pending booking, after checking it is actually placeable.

    Locks the worker row for the duration. That is the right granularity: two
    residents booking the *same worker* for overlapping windows must serialise,
    while bookings for different workers proceed in parallel. Without the lock
    both requests would pass the conflict check before either inserted, and the
    unique constraint only catches an identical start time, not a genuine
    overlap.
    """
    # ---------------------------------------------------------------------
    # An emergency category never comes through here (Module 5.5).
    # ---------------------------------------------------------------------
    # Directing an emergency at one named worker is the flow this endpoint
    # offers, and it is the wrong shape for the problem: the household waits on
    # one person who may simply not answer, and discovers that one person at a
    # time. It is also what produced the request that could not be answered at
    # all — booked minutes ahead, at a single worker, expiring at its own start
    # time on a screen that had no Accept button.
    #
    # Enforced on the server rather than by only routing the app elsewhere,
    # because a booking created this way is not a cosmetic problem: it collects
    # no surcharge, reaches nobody but the one worker, and settles through the
    # in-app charge rather than in cash. A stale build must not be able to make
    # one.
    if category.bypasses_notice_period:
        raise EmergencyMustBroadcast(
            "Emergency requests go to every available worker at once rather "
            "than to one you choose. Raise it from the emergency flow instead."
        )

    # Re-read under lock so the conflict check below sees a stable world.
    locked_worker = WorkerProfile.objects.select_for_update().get(pk=worker.pk)

    booking = Booking(
        society=society,
        resident=resident,
        worker=locked_worker,
        category=category,
        scheduled_date=scheduled_date,
        start_time=start_time,
        expected_duration_minutes=duration_minutes,
        quoted_price=quoted_price,
        notes=notes,
    )

    notice = check_notice_period(
        hours_until_start=booking.hours_until_start,
        notice_hours=notice_hours_for(society),
        bypasses_notice=category.bypasses_notice_period,
    )
    if not notice.allowed:
        raise NoticeTooShort(notice.reason)

    # Mirrors candidate_workers()/_covers_requested_slot() exactly. It has to:
    # this is the second half of the same rule, and if the two disagree the
    # resident is shown a worker by the match endpoint and then refused when
    # they tap Book. No row means the worker never blocked the date, which is
    # bookable; a row is honoured, including a narrower declared window.
    day_row = DayAvailability.objects.filter(
        worker=locked_worker, date=scheduled_date
    ).first()
    if day_row is not None and not day_row.covers(start_time, duration_minutes):
        raise WorkerUnavailable(
            "This worker is not available for that date and time."
        )

    if conflicted_worker_ids(
        [locked_worker.pk],
        on_date=scheduled_date,
        start_minutes=minutes_of(start_time),
        duration_minutes=duration_minutes,
    ):
        raise SlotConflict("This worker already has something booked in that window.")

    booking.save()
    logger.info(
        "Booking %s created: resident=%s worker=%s %s %s",
        booking.pk,
        resident.pk,
        locked_worker.pk,
        scheduled_date,
        start_time,
    )
    return booking


# ---------------------------------------------------------------------------
# 5.4 Confirmation & cancellation
# ---------------------------------------------------------------------------


@transaction.atomic
def confirm_booking(booking: Booking, *, note: str = "") -> Booking:
    """The worker accepts. Re-checked under lock, like acceptance in Module 4."""
    locked = Booking.objects.select_for_update().get(pk=booking.pk)

    if not locked.is_actionable:
        raise BookingNotActionable(
            "This booking can no longer be confirmed — it was already answered, "
            "cancelled, or its start time has passed."
        )

    # The worker may have taken other work since the request arrived.
    if conflicted_worker_ids(
        [locked.worker_id],
        on_date=locked.scheduled_date,
        start_minutes=locked.start_minutes,
        duration_minutes=locked.expected_duration_minutes,
        exclude_booking_id=locked.pk,
    ):
        raise SlotConflict(
            "You already have something booked in that window. Decline this one "
            "or cancel the other first."
        )

    locked.status = BookingStatus.CONFIRMED
    locked.confirmed_at = timezone.now()
    locked.response_note = note
    locked.save(update_fields=["status", "confirmed_at", "response_note", "updated_at"])

    _notify(
        recipient=locked.resident.user,
        title=f"{locked.worker.user.get_full_name()} confirmed",
        body=f"{locked.category.name} on {locked.scheduled_date:%d %b} at "
        f"{locked.start_time:%H:%M}.",
    )

    logger.info("Booking %s confirmed by worker %s", locked.pk, locked.worker_id)
    return locked


@transaction.atomic
def decline_booking(booking: Booking, *, note: str = "") -> Booking:
    locked = Booking.objects.select_for_update().get(pk=booking.pk)

    if not locked.is_actionable:
        raise BookingNotActionable(
            "This booking can no longer be declined — it was already answered, "
            "cancelled, or its start time has passed."
        )

    locked.status = BookingStatus.DECLINED
    locked.declined_at = timezone.now()
    locked.response_note = note
    locked.save(update_fields=["status", "declined_at", "response_note", "updated_at"])

    _notify(
        recipient=locked.resident.user,
        title=f"{locked.worker.user.get_full_name()} declined",
        body=note or "They cannot take this job. You can book someone else.",
    )

    logger.info("Booking %s declined by worker %s", locked.pk, locked.worker_id)
    return locked


@transaction.atomic
def cancel_booking(
    booking: Booking, *, cancelled_by: str, reason: str = ""
) -> tuple[Booking, int]:
    """Cancel a booking and record what it cost (Module 5.4).

    Returns the booking and the fee charged. The fee is computed once, here,
    and stored — see ``policy.py`` on why it is never recomputed later.
    """
    locked = Booking.objects.select_for_update().get(pk=booking.pk)

    if not locked.can_be_cancelled:
        raise BookingNotActionable(
            "Only a booking that has not started yet can be cancelled."
        )

    outcome = _cancellation_outcome_for(locked)

    locked.status = BookingStatus.CANCELLED
    locked.cancelled_at = timezone.now()
    locked.cancelled_by = cancelled_by
    locked.cancellation_reason = reason
    locked.cancellation_fee = outcome.fee
    locked.save(
        update_fields=[
            "status",
            "cancelled_at",
            "cancelled_by",
            "cancellation_reason",
            "cancellation_fee",
            "updated_at",
        ]
    )

    # An emergency that nobody had taken yet has to give the surcharge back and
    # stop ringing phones about a job that is no longer happening. Both are
    # emergency-module concerns, imported lazily to keep Module 5's core free of
    # them — and both are no-ops on an ordinary booking.
    from .emergency import settle_cancelled_emergency

    settle_cancelled_emergency(locked, had_worker=bool(booking.worker_id))

    logger.info(
        "Booking %s cancelled by %s; fee %s (%s)",
        locked.pk,
        cancelled_by,
        outcome.fee,
        outcome.tier,
    )
    return locked, outcome.fee


@transaction.atomic
def complete_booking(booking: Booking) -> Booking:
    """Mark a confirmed booking done.

    Interim: Module 7 records attendance at the gate and should be what closes a
    booking out. Until it exists, either party marking it complete is what lets
    Module 8 bill for it and Module 9 collect a rating.
    """
    locked = Booking.objects.select_for_update().get(pk=booking.pk)

    if locked.status == BookingStatus.COMPLETED:
        # Idempotent. Two routes settle a booking — this one and Module 6.6's
        # completion mark — and both can legitimately arrive for the same job.
        return locked

    if locked.status != BookingStatus.CONFIRMED:
        raise BookingNotActionable("Only a confirmed booking can be completed.")
    if not locked.can_be_completed:
        raise BookingNotActionable(
            "This booking is for a later date, so it cannot be marked complete yet."
        )

    locked.status = BookingStatus.COMPLETED
    locked.completed_at = timezone.now()
    locked.save(update_fields=["status", "completed_at", "updated_at"])

    # Module 9 caps ratings at one per completed job, and Module 4.3 uses this
    # count as the rating count, so it must move when work actually completes.
    WorkerProfile.objects.filter(pk=locked.worker_id).update(
        completed_engagements=F("completed_engagements") + 1
    )

    # Completion is the moment the job becomes payable — Module 8 refuses to
    # open a payment before this status is reached (payments.views
    # .CreateBookingPaymentView). Nothing was telling the resident that, so the
    # money waited until they happened to reopen the booking screen. The worker
    # has finished and is standing there; this is exactly when to ask.
    #
    # Every booking, emergency included. An earlier version settled emergency
    # work in cash and skipped this — see ``_prompt_for_payment`` for why that
    # was reversed.
    _prompt_for_payment(locked)

    # Module 9 builds a "jobs you can still rate" list, but nothing was telling
    # anyone it existed — a rating that nobody is prompted for is a rating
    # nobody leaves, and the trust score every other module ranks on starves.
    # Both sides, because Module 9 rates in both directions.
    _prompt_for_rating(locked)

    logger.info("Booking %s marked complete", locked.pk)
    return locked


def _prompt_for_payment(booking: Booking) -> None:
    """Ask the resident to settle a finished job (Module 10, PAYMENT).

    Only the resident: the worker is owed the money, not asked for it. Skipped
    when a live payment already exists, so re-completing an already-paid job
    does not nag somebody who has already paid.

    ---------------------------------------------------------------------------
    EMERGENCY WORK IS PAID THROUGH THE APP TOO
    ---------------------------------------------------------------------------
    It briefly was not. An earlier design settled the emergency worker's fee in
    cash, hand to hand, with the app recording only that the job was done — so
    this function was skipped for emergencies and a separate notification told
    both sides what was owed in notes.

    That is reversed. Every booking now settles the same way, which removes a
    whole class of divergence: one completion path, one payment path, one answer
    to "has this been paid". It also means the worker is paid through a channel
    with a receipt behind it rather than relying on the household having the
    right cash at the door — which was always the weaker end of that bargain.

    Lazily imported and non-raising, like every other Module 10 call on a write
    path — a push failure must not roll back a completed booking.
    """
    from apps.notifications.models import NotificationCategory
    from apps.notifications.services import notify
    from apps.payments.models import Payment, PaymentKind, PaymentStatus

    already_paying = (
        Payment.objects.filter(booking=booking, kind=PaymentKind.BOOKING)
        .exclude(status__in=[PaymentStatus.FAILED, PaymentStatus.CANCELLED])
        .exists()
    )
    if already_paying:
        return

    notify(
        recipient=booking.resident.user,
        category=NotificationCategory.PAYMENT,
        title=f"Pay ₹{booking.quoted_price} for {booking.category.name}",
        body=(
            f"{booking.worker.user.get_full_name()} has marked the job complete. "
            "Tap to pay."
        ),
        # "/bookings" rather than "/payments": the Pay button lives on the
        # booking card, and a payment that does not exist yet has no row on the
        # payments screen to tap.
        data={"route": "/bookings", "booking": booking.pk},
        society=booking.society,
    )


def _prompt_for_rating(booking: Booking) -> None:
    """Ask both parties to rate a finished job (Module 10, RATING).

    Lazily imported and non-raising: a completed booking must not be rolled back
    because a push failed.
    """
    from apps.notifications.models import NotificationCategory
    from apps.notifications.services import notify

    worker_name = booking.worker.user.get_full_name()
    resident_name = booking.resident.user.get_full_name()

    for recipient, subject in (
        (booking.resident.user, worker_name),
        (booking.worker.user, resident_name),
    ):
        notify(
            recipient=recipient,
            category=NotificationCategory.RATING,
            title="How did it go?",
            body=f"Leave a rating for {subject}.",
            data={"route": "/rate"},
            society=booking.society,
        )


def _cancellation_outcome_for(booking: Booking):
    """The fee for cancelling this specific booking.

    Wraps the pure policy function with the one case it cannot see: a booking
    nobody has accepted. The fee compensates a worker who turned other work away
    for a slot they can no longer fill, and an unclaimed emergency has no such
    worker — charging for it would be charging the household for the platform's
    failure to find anybody.
    """
    if booking.worker_id is None:
        return CancellationOutcome(
            fee=0,
            tier="free",
            rationale="Nobody had accepted this job yet, so there is no fee.",
        )

    return cancellation_outcome(
        hours_until_start=booking.hours_until_start,
        quoted_price=booking.quoted_price,
    )


def cancellation_quote(booking: Booking) -> dict:
    """What cancelling right now would cost, for the confirmation dialog.

    Shown before the resident commits: a fee that appears only after the fact
    is the kind of surprise that makes people stop trusting the app.
    """
    outcome = _cancellation_outcome_for(booking)
    return {
        "fee": outcome.fee,
        "tier": outcome.tier,
        "rationale": outcome.rationale,
        "is_free": outcome.is_free,
    }
