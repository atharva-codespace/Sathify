"""
Module 5.5 — Emergency booking: pay, broadcast, race to accept.

An ordinary booking is *directed*: the resident reads profiles, picks somebody,
and waits for that one person to answer. That is the right shape when there is
time to choose, and the wrong shape at 21:40 with a blocked drain, because the
household's chosen worker may simply not answer and the whole evening is spent
discovering that one worker at a time.

So an emergency is *broadcast*. One request goes to every worker who could take
it, and the first to accept gets it.

-------------------------------------------------------------------------------
TWO PAYMENTS, AND ONLY ONE OF THEM IS SATHIFY'S
-------------------------------------------------------------------------------
**A — the surcharge.** Resident → platform, settled through Razorpay before a
single worker is told the request exists. It pays for running the broadcast, and
it is priced by lead time (``policy.emergency_surcharge``). Collecting it up
front is not a convenience: an unpaid request that had already rung eight phones
would be an eight-person interruption the platform could not undo.

**B — the worker's fee.** Resident → worker, in cash, hand to hand, on the day.
The app never touches it, never opens an order for it, and must never imply it
did. All it does is record that the job was completed and tell both parties the
same figure at the same moment, which is the worker's only protection against a
household that later says it already paid.

Conflating the two would be the single most damaging mistake available here, so
they are different ``PaymentKind`` values, different call paths, and — for B —
no ``Payment`` row at all.

-------------------------------------------------------------------------------
THE RACE IS DECIDED BY ONE CONDITIONAL UPDATE. NOTHING ELSE.
-------------------------------------------------------------------------------
Eight workers can tap Accept inside the same second, and exactly one must win.
The obvious implementation — read the booking, check nobody has it, save the
winner — is a check-then-write, and two requests interleaving between the check
and the write both pass. That is not a rare race: a broadcast deliberately
creates a thundering herd, so this is the *expected* traffic pattern.

:func:`accept_offer` therefore claims the job with a single statement::

    UPDATE bookings_booking
       SET worker_id = %s, status = 'confirmed', ...
     WHERE id = %s AND status = 'broadcast' AND worker_id IS NULL

The database evaluates the WHERE and applies the SET atomically, and returns how
many rows it touched. One winner gets 1, everybody else gets 0, on Postgres and
on SQLite alike, with no row locks to arrange and no isolation level to depend
on. ``BookingOffer`` rows are reconciled to that outcome afterwards; they never
decide it. There is also a partial unique index (``one_accepted_offer_per_
booking``) so a future code path cannot quietly reintroduce two winners.

-------------------------------------------------------------------------------
"REAL TIME", ON A FREE TIER WITH NO WORKER PROCESS
-------------------------------------------------------------------------------
There is no Channels, no Redis and no second dyno (docs/free-tier-constraints.md
§7), so there is no socket to push down. What the dashboards get instead is a
deliberately tiny polling endpoint — ``/bookings/emergency/live/`` — that both
sides hit every few seconds *only while a request is actually in flight*, and
not at all otherwise. That is the honest version of real time here: a claimed
job disappears from the other seven dashboards within a few seconds, and the
cost is bounded because nothing polls unless something is happening.
"""

from __future__ import annotations

import datetime as dt
import logging

from django.db import transaction
from django.utils import timezone

from .models import (
    Booking,
    BookingOffer,
    BookingStatus,
    OfferState,
    ServiceCategory,
)
from .policy import MAX_EMERGENCY_LEAD_DAYS, emergency_surcharge
from .services import BookingError, SlotConflict, match_workers

logger = logging.getLogger(__name__)


#: How many workers one request goes to.
#:
#: Bounded on purpose. Broadcasting to everybody maximises the chance of a fast
#: answer and also guarantees that seven people are interrupted for a job they
#: will not get, every single time. Eight is enough that a request is very
#: unlikely to go unanswered while staying a number of people a coordinator
#: could imagine phoning by hand.
BROADCAST_FAN_OUT = 8

#: How long workers have to claim a request before it lapses.
#:
#: Short because the household is waiting and needs to be told "nobody is
#: coming" while there is still time to make other arrangements. A request that
#: sat open for an hour would be worse than one that failed in ten minutes.
OFFER_WINDOW = dt.timedelta(minutes=10)

#: Never let the claim window run past the job it is for.
def _offer_deadline(booking: Booking) -> dt.datetime:
    """When this request gives up. Never later than the job's own start."""
    now = timezone.now()
    by_window = now + OFFER_WINDOW
    # A request raised for 30 minutes' time should not still be claimable an
    # hour after the household expected somebody at the door.
    return min(by_window, max(booking.scheduled_start, now + dt.timedelta(minutes=1)))


# ---------------------------------------------------------------------------
# Refusals
# ---------------------------------------------------------------------------


class NotAnEmergency(BookingError):
    code = "not_an_emergency"


class EmergencyTooFarAhead(BookingError):
    code = "emergency_too_far_ahead"


class SurchargeNotSettled(BookingError):
    code = "surcharge_not_settled"


class OfferGone(BookingError):
    """Somebody else got there first, or the window closed."""

    code = "offer_gone"


class NoWorkersAvailable(BookingError):
    code = "no_workers_available"


# ---------------------------------------------------------------------------
# Raising a request
# ---------------------------------------------------------------------------


@transaction.atomic
def raise_emergency(
    *,
    resident,
    society,
    category: ServiceCategory,
    scheduled_date: dt.date,
    start_time: dt.time,
    duration_minutes: int | None = None,
    quoted_price: int | None = None,
    notes: str = "",
) -> tuple[Booking, "object"]:
    """Open an emergency request and the surcharge that unlocks it.

    Returns ``(booking, surcharge_payment)``. The booking is **not** visible to
    any worker yet: it sits at ``PAYMENT_PENDING`` until Payment A settles, at
    which point :func:`broadcast` runs from the payment-settled hook.

    Deliberately does no notice-period check. ``check_notice_period`` refuses a
    start time at or before now even for a notice-exempt category, which is
    correct for a directed booking (a worker cannot confirm a job that has
    already begun) and exactly backwards for this one: "come now" is the most
    common emergency there is. What replaces it is the lead-time ceiling — an
    emergency raised for next week is an ordinary booking wearing a costume, and
    the household is better served by the directed flow where they get to
    choose who comes.
    """
    from apps.payments.models import PaymentKind
    from apps.payments.services import create_payment

    if not category.bypasses_notice_period:
        raise NotAnEmergency(
            "That service is not an emergency category. Book it in the usual way."
        )

    today = timezone.localdate()
    lead_days = (scheduled_date - today).days
    if lead_days > MAX_EMERGENCY_LEAD_DAYS:
        raise EmergencyTooFarAhead(
            f"An emergency can be raised at most {MAX_EMERGENCY_LEAD_DAYS} day(s) "
            "ahead. For anything further out, book a worker directly."
        )
    if lead_days < 0:
        raise EmergencyTooFarAhead("An emergency cannot be raised for a past date.")

    quote = emergency_surcharge(scheduled_date=scheduled_date, raised_on=today)

    booking = Booking.objects.create(
        society=society,
        resident=resident,
        worker=None,
        category=category,
        scheduled_date=scheduled_date,
        start_time=start_time,
        expected_duration_minutes=duration_minutes or category.expected_duration_minutes,
        quoted_price=quoted_price or category.price_min,
        notes=notes,
        status=BookingStatus.PAYMENT_PENDING,
        emergency_surcharge_paise=quote.paise,
    )

    payment = create_payment(
        resident=resident,
        worker=None,
        society=society,
        kind=PaymentKind.EMERGENCY_SURCHARGE,
        amount_paise=quote.paise,
        booking=booking,
        note=quote.rationale,
        # Due immediately: nothing happens until it is settled, so a due date in
        # the future would describe a request that is simply stuck.
        due_at=timezone.now(),
    )

    logger.info(
        "Emergency %s raised by resident %s for %s %s (surcharge %s paise)",
        booking.pk, resident.pk, scheduled_date, start_time, quote.paise,
    )
    return booking, payment


# ---------------------------------------------------------------------------
# Broadcasting
# ---------------------------------------------------------------------------


def eligible_workers(booking: Booking) -> list:
    """Who this request should go to, best match first.

    Reuses Module 5.3's ``match_workers`` wholesale rather than inventing a
    second definition of "available". That function already encodes every rule
    that matters here — approved, searchable, qualified for the category's
    service type, has not blocked the date, honours a narrower declared window,
    and free of conflicting bookings, engagements and leave — and it is the same
    definition the directed flow uses, so a worker cannot be bookable one way
    and invisible the other.

    The ranking is Module 4.3's score. It does not decide who gets the job — the
    race does — but it decides who is asked first when the fan-out is capped,
    and asking the best matches first is what makes a capped fan-out defensible.
    """
    ranked = match_workers(
        booking.society_id,
        category=booking.category,
        on_date=booking.scheduled_date,
        start_time=booking.start_time,
        duration_minutes=booking.expected_duration_minutes,
        resident_society=booking.society,
    )
    return [worker for worker, _score in ranked[:BROADCAST_FAN_OUT]]


@transaction.atomic
def broadcast(booking: Booking) -> int:
    """Release a paid request to the eligible workers. Returns how many.

    Idempotent: re-running it on an already-broadcast booking is a no-op rather
    than a second round of notifications. That matters because the trigger is a
    payment settling, and a payment can settle twice as far as this code is
    concerned — once from the client's signed checkout response and once from
    the webhook that follows it.
    """
    locked = Booking.objects.select_for_update().get(pk=booking.pk)

    if locked.status != BookingStatus.PAYMENT_PENDING:
        # Already broadcast, already claimed, or cancelled while the payment was
        # in flight. All three are legitimate; none should re-notify anybody.
        return 0

    workers = eligible_workers(locked)
    deadline = _offer_deadline(locked)

    locked.status = BookingStatus.BROADCAST
    locked.broadcast_at = timezone.now()
    locked.offer_expires_at = deadline
    locked.save(
        update_fields=["status", "broadcast_at", "offer_expires_at", "updated_at"]
    )

    if not workers:
        # Nobody to ask. Closed immediately rather than left to time out: making
        # a household wait ten minutes for an answer the platform already has is
        # ten minutes they could have spent phoning somebody themselves.
        logger.warning("Emergency %s found no eligible workers", locked.pk)
        _close_unfulfilled(locked, reason="nobody_available")
        return 0

    BookingOffer.objects.bulk_create(
        [
            BookingOffer(booking=locked, worker=worker, rank=rank)
            for rank, worker in enumerate(workers)
        ],
        ignore_conflicts=True,
    )

    # After the transaction commits, not inside it. A notification that fired
    # while this transaction was still open could send a worker to an endpoint
    # that cannot yet see the offer row she was told about.
    transaction.on_commit(lambda: _notify_offered(locked, workers))

    logger.info("Emergency %s broadcast to %s worker(s)", locked.pk, len(workers))
    return len(workers)


def _notify_offered(booking: Booking, workers) -> None:
    """Ring every phone the request went to. Never raises."""
    from apps.notifications.models import NotificationCategory
    from apps.notifications.services import notify

    flat = str(booking.resident.flat)
    for worker in workers:
        try:
            notify(
                recipient=worker.user,
                category=NotificationCategory.BOOKING,
                title=f"Urgent: {booking.category.name} at {flat}",
                body=(
                    f"₹{booking.quoted_price}, "
                    f"{booking.expected_duration_minutes} min, starting "
                    f"{booking.start_time:%H:%M}. First to accept gets it."
                ),
                data={
                    "route": "/emergency",
                    "booking": booking.pk,
                    "expires_at": booking.offer_expires_at.isoformat()
                    if booking.offer_expires_at
                    else "",
                },
                society=booking.society,
            )
        except Exception:  # noqa: BLE001 — one unreachable phone is not a failure
            logger.exception("Could not notify worker %s of emergency %s",
                             worker.pk, booking.pk)


# ---------------------------------------------------------------------------
# The race
# ---------------------------------------------------------------------------


@transaction.atomic
def accept_offer(*, booking_id: int, worker) -> Booking:
    """Claim an emergency request. Exactly one caller can succeed.

    See the module docstring for why this is a conditional UPDATE and not a
    read-check-write. The short version: a broadcast is a deliberate thundering
    herd, so the race is the normal case rather than the edge case, and the
    correctness of this function is the correctness of the feature.
    """
    offer = (
        BookingOffer.objects.select_related("booking__category", "booking__society")
        .filter(booking_id=booking_id, worker=worker)
        .first()
    )
    if offer is None:
        raise OfferGone("This job was not offered to you.")

    booking = offer.booking

    if booking.status != BookingStatus.BROADCAST:
        _reconcile_losers(booking)
        raise OfferGone("Somebody else has already taken this job.")

    if booking.offer_window_closed:
        expire_unclaimed()
        raise OfferGone("This request has expired.")

    # Checked before the claim as a courtesy — it produces a better message than
    # winning the race and then being told the slot is double-booked. It is not
    # what makes the claim safe; the UPDATE below is.
    if _conflicts_for(worker, booking):
        raise SlotConflict("You already have something booked in that window.")

    now = timezone.now()

    # ---- The whole race, in one statement -------------------------------
    claimed = Booking.objects.filter(
        pk=booking_id,
        status=BookingStatus.BROADCAST,
        worker__isnull=True,
    ).update(
        worker=worker,
        status=BookingStatus.CONFIRMED,
        confirmed_at=now,
        updated_at=now,
    )
    if claimed == 0:
        # Lost. The row moved out from under this request between the read above
        # and the write — which is exactly the case the conditional WHERE exists
        # to catch, and it is caught rather than silently overwriting a winner.
        _reconcile_losers(booking)
        raise OfferGone("Somebody else has already taken this job.")

    BookingOffer.objects.filter(pk=offer.pk).update(
        state=OfferState.ACCEPTED, responded_at=now, updated_at=now
    )
    BookingOffer.objects.filter(booking_id=booking_id, state=OfferState.OFFERED).update(
        state=OfferState.LOST, responded_at=now, updated_at=now
    )

    booking.refresh_from_db()
    transaction.on_commit(lambda: _notify_claimed(booking))

    logger.info("Emergency %s claimed by worker %s", booking_id, worker.pk)
    return booking


def _conflicts_for(worker, booking: Booking) -> bool:
    from apps.scheduling.services import conflicted_worker_ids

    return bool(
        conflicted_worker_ids(
            [worker.pk],
            on_date=booking.scheduled_date,
            start_minutes=booking.start_minutes,
            duration_minutes=booking.expected_duration_minutes,
            exclude_booking_id=booking.pk,
        )
    )


@transaction.atomic
def decline_offer(*, booking_id: int, worker) -> BookingOffer:
    """A worker passes. Never affects anybody else's chance of taking it."""
    offer = BookingOffer.objects.filter(booking_id=booking_id, worker=worker).first()
    if offer is None:
        raise OfferGone("This job was not offered to you.")

    if offer.is_open:
        BookingOffer.objects.filter(pk=offer.pk).update(
            state=OfferState.DECLINED,
            responded_at=timezone.now(),
            updated_at=timezone.now(),
        )
        offer.refresh_from_db()
    return offer


def _reconcile_losers(booking: Booking) -> None:
    """Close every still-open offer on a booking somebody else took.

    Called from the losing branch of the race rather than only from the winning
    one, so a dashboard that polls after a lost claim sees the right state even
    if the winner's own reconciliation has not landed yet.
    """
    BookingOffer.objects.filter(
        booking_id=booking.pk, state=OfferState.OFFERED
    ).update(state=OfferState.LOST, responded_at=timezone.now(), updated_at=timezone.now())


def _notify_claimed(booking: Booking) -> None:
    """Tell the household who is coming. Never raises."""
    from apps.notifications.models import NotificationCategory
    from apps.notifications.services import notify

    try:
        notify(
            recipient=booking.resident.user,
            category=NotificationCategory.BOOKING,
            title=f"{booking.worker.user.get_full_name()} is on the way",
            body=(
                f"They accepted your emergency {booking.category.name.lower()} "
                f"request. ₹{booking.quoted_price} payable in cash when the job "
                "is done."
            ),
            data={"route": "/bookings", "booking": booking.pk},
            society=booking.society,
        )
    except Exception:  # noqa: BLE001
        logger.exception("Could not tell resident about emergency %s", booking.pk)


# ---------------------------------------------------------------------------
# Giving up, and giving the money back
# ---------------------------------------------------------------------------


def expire_unclaimed(*, society_id=None) -> int:
    """Close broadcasts nobody answered, and refund each surcharge.

    Another of the idempotent, bounded sweeps that stand in for the cron this
    deployment does not have (docs/free-tier-constraints.md §7). It is called
    from the two emergency read paths — the resident's live view and the
    worker's offer list — so whoever is watching triggers it, and safe to call
    on every request either way.
    """
    queryset = Booking.objects.unclaimed()
    if society_id is not None:
        queryset = queryset.filter(society_id=society_id)

    closed = 0
    for booking in queryset.select_related("resident__user", "society", "category"):
        _close_unfulfilled(booking, reason="nobody_accepted")
        closed += 1

    if closed:
        logger.info("Closed %s unclaimed emergency request(s)", closed)
    return closed


@transaction.atomic
def _close_unfulfilled(booking: Booking, *, reason: str) -> None:
    """Mark a request as unfilled, refund the surcharge, tell the household.

    The refund is not a goodwill gesture. The surcharge buys a broadcast that
    finds somebody; a broadcast that finds nobody did not deliver the thing that
    was paid for, and keeping the money would be charging a household for the
    platform's own failure at the exact moment it has let them down.
    """
    updated = Booking.objects.filter(
        pk=booking.pk, status=BookingStatus.BROADCAST
    ).update(status=BookingStatus.UNFULFILLED, updated_at=timezone.now())
    if updated == 0:
        return  # Claimed or cancelled in the meantime. Nothing to close.

    BookingOffer.objects.filter(
        booking_id=booking.pk, state=OfferState.OFFERED
    ).update(state=OfferState.EXPIRED, responded_at=timezone.now(),
             updated_at=timezone.now())

    refunded = refund_surcharge(booking, reason=f"Emergency unfilled ({reason}).")
    transaction.on_commit(lambda: _notify_unfulfilled(booking, refunded=refunded))


def refund_surcharge(booking: Booking, *, reason: str) -> bool:
    """Give back Payment A, if it was ever collected. Returns whether it was.

    Non-raising by design: this runs inside the path that closes a request out,
    and a gateway that cannot be reached must not leave the booking stuck in a
    state the household can neither use nor escape. A settled-but-unrefunded
    payment is visible in the ledger and recoverable by hand; a request wedged
    at BROADCAST forever is not.
    """
    from apps.payments.models import PaymentKind, PaymentStatus
    from apps.payments.services import refund_payment

    payments = booking.payments.filter(
        kind=PaymentKind.EMERGENCY_SURCHARGE, status=PaymentStatus.PAID
    )
    refunded = False
    for payment in payments:
        try:
            refund_payment(payment, reason=reason)
            refunded = True
        except Exception:  # noqa: BLE001 — see the docstring
            logger.exception("Could not refund surcharge on emergency %s", booking.pk)

    # Nothing settled yet: cancel the open order instead, so the household is
    # not left with a live payment request for a job that is over.
    booking.payments.filter(
        kind=PaymentKind.EMERGENCY_SURCHARGE,
        status__in=[PaymentStatus.CREATED, PaymentStatus.PENDING],
    ).update(status=PaymentStatus.CANCELLED, updated_at=timezone.now())

    return refunded


def _notify_unfulfilled(booking: Booking, *, refunded: bool) -> None:
    from apps.notifications.models import NotificationCategory
    from apps.notifications.services import notify

    money = (
        "Your emergency fee has been refunded."
        if refunded
        else "You have not been charged."
    )
    try:
        notify(
            recipient=booking.resident.user,
            category=NotificationCategory.BOOKING,
            title="Nobody could take your emergency request",
            body=f"No worker was free for {booking.category.name.lower()}. {money}",
            data={"route": "/bookings", "booking": booking.pk},
            society=booking.society,
        )
    except Exception:  # noqa: BLE001
        logger.exception("Could not tell resident emergency %s went unfilled", booking.pk)


def settle_cancelled_emergency(booking: Booking, *, had_worker: bool) -> None:
    """Tidy up after an emergency booking is cancelled.

    A no-op on anything that is not an emergency, which is why
    ``services.cancel_booking`` can call it unconditionally.

    The surcharge is refunded only when nobody had accepted yet. Once a worker
    has taken the job the broadcast did what it was paid to do, and she has
    already turned down or rearranged whatever else she was doing — refunding
    the platform fee at that point would mean the household pays nothing at all
    for having occupied somebody's evening.
    """
    if not booking.is_emergency:
        return

    BookingOffer.objects.filter(
        booking_id=booking.pk, state=OfferState.OFFERED
    ).update(state=OfferState.EXPIRED, responded_at=timezone.now(),
             updated_at=timezone.now())

    if not had_worker:
        refund_surcharge(booking, reason="Emergency cancelled before it was accepted.")


__all__ = [
    "BROADCAST_FAN_OUT",
    "EmergencyTooFarAhead",
    "NoWorkersAvailable",
    "NotAnEmergency",
    "OFFER_WINDOW",
    "OfferGone",
    "SurchargeNotSettled",
    "accept_offer",
    "broadcast",
    "decline_offer",
    "eligible_workers",
    "expire_unclaimed",
    "raise_emergency",
    "refund_surcharge",
    "settle_cancelled_emergency",
]
