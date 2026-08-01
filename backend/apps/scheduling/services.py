"""
Module 6.3 and 6.4 — conflict detection and reminder scheduling.

-------------------------------------------------------------------------------
CONFLICT DETECTION LIVES HERE, NOT IN MODULE 5
-------------------------------------------------------------------------------
Module 5 shipped with its own overlap check because it was the first module that
needed one. Modspec 6.3 assigns it to the calendar layer, and it now belongs to
exactly one module: ``apps.bookings.services`` delegates to
:func:`conflicted_worker_ids`. Two implementations of "is this worker already
busy" would eventually disagree, and the losing side of that disagreement is a
worker told to be in two places at once.

The batched form is the primitive rather than a convenience wrapper, because the
performance-critical caller is Module 5.3's matching, which asks about a whole
page of candidates at once. The single-worker form is built on top of it.

-------------------------------------------------------------------------------
WHY THE WEEKDAY MATCH IS IN PYTHON
-------------------------------------------------------------------------------
An engagement's recurring days live in a ``JSONField``, and the ``__contains``
lookup that would filter them in SQL is unsupported on SQLite — which is what
development and the whole test suite run on, while production is PostgreSQL. A
query that passes CI and fails in production is the worse trade, so the weekday
match happens in Python over a bounded, batched result set.
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass

from django.db import transaction
from django.utils import timezone

from apps.bookings.models import Booking, combine_local, minutes_of, windows_overlap
from apps.hiring.models import Engagement, weekday_of

from .models import Reminder, ReminderKind, ReminderStatus, TaskTiming
from .schedule import ScheduleItem, worker_day

logger = logging.getLogger(__name__)

#: How far ahead reminders are generated. Two days keeps the table small while
#: comfortably covering the default one-hour lead time.
REMINDER_HORIZON_DAYS = 2


# ---------------------------------------------------------------------------
# 6.3 Conflict detection
# ---------------------------------------------------------------------------


def conflicted_worker_ids(
    worker_ids,
    *,
    on_date: dt.date,
    start_minutes: int,
    duration_minutes: int,
    exclude_booking_id=None,
) -> set:
    """Which of ``worker_ids`` are already committed over the requested window.

    Two sources of conflict, both of which must be honoured or the platform
    will cheerfully double-book someone:

    * another live booking that day, and
    * an active engagement whose recurring visit falls on that weekday.

    Batched into two queries no matter how large the pool is.
    """
    worker_ids = list(worker_ids)
    if not worker_ids:
        return set()

    conflicted: set = set()

    bookings = Booking.objects.live().filter(
        worker_id__in=worker_ids, scheduled_date=on_date
    )
    if exclude_booking_id is not None:
        bookings = bookings.exclude(pk=exclude_booking_id)

    for booking in bookings.only("worker_id", "start_time", "expected_duration_minutes"):
        if windows_overlap(
            start_minutes,
            duration_minutes,
            booking.start_minutes,
            booking.expected_duration_minutes,
        ):
            conflicted.add(booking.worker_id)

    weekday = weekday_of(on_date)
    engagements = Engagement.objects.active().filter(worker_id__in=worker_ids)

    for engagement in engagements.only(
        "worker_id", "days_of_week", "start_time", "expected_duration_minutes"
    ):
        if weekday not in set(engagement.days_of_week):
            continue
        if windows_overlap(
            start_minutes,
            duration_minutes,
            minutes_of(engagement.start_time),
            engagement.expected_duration_minutes,
        ):
            conflicted.add(engagement.worker_id)

    return conflicted


@dataclass(frozen=True)
class ConflictReport:
    """What a proposed visit would collide with.

    Carries the colliding items, not just a boolean, because modspec 6.3 allows
    a conflict to be "flagged for manual resolution" as well as rejected — and
    an administrator cannot resolve what they cannot see.
    """

    has_conflict: bool
    clashes: tuple[ScheduleItem, ...] = ()

    @property
    def summary(self) -> str:
        if not self.has_conflict:
            return "No conflicts."
        parts = [
            f"{item.title} at {item.start_time:%H:%M}–{item.end_time:%H:%M} "
            f"({item.flat_label})"
            for item in self.clashes
        ]
        return "Already committed: " + "; ".join(parts)


def check_conflict(
    worker_id,
    *,
    on_date: dt.date,
    start_time: dt.time,
    duration_minutes: int,
    exclude_booking_id=None,
) -> ConflictReport:
    """Module 6.3 — would this visit collide with anything already scheduled?

    Built on the assembled day schedule rather than on raw queries, so what it
    reports is exactly what the worker sees on their calendar.
    """
    start = minutes_of(start_time)
    end = start + duration_minutes

    clashes = [
        item
        for item in worker_day(worker_id, on_date)
        if not (
            exclude_booking_id is not None
            and item.source == "booking"
            and item.source_id == exclude_booking_id
        )
        and item.start_minutes < end
        and start < item.end_minutes
    ]

    return ConflictReport(has_conflict=bool(clashes), clashes=tuple(clashes))


# ---------------------------------------------------------------------------
# 6.2 Task timing
# ---------------------------------------------------------------------------


def effective_timing(engagement) -> dict:
    """The arrival and departure expectations in force for an engagement.

    Always answers, whether or not the resident has set a :class:`TaskTiming`.
    Every consumer — attendance, reminders, the app — should come through here
    rather than branching on ``None`` themselves, so "no timing set" means the
    same thing everywhere.
    """
    timing = getattr(engagement, "task_timing", None)

    if timing is None:
        base = dt.datetime.combine(dt.date.min, engagement.start_time)
        departure = (
            base + dt.timedelta(minutes=engagement.expected_duration_minutes)
        ).time()
        return {
            "expected_arrival": engagement.start_time,
            "arrival_grace_minutes": 0,
            "expected_departure": departure,
            "departure_grace_minutes": 0,
            "task_notes": "",
            "reminders_enabled": True,
            "reminder_lead_minutes": 60,
            "is_customised": False,
        }

    return {
        "expected_arrival": timing.arrival,
        "arrival_grace_minutes": timing.arrival_grace_minutes,
        "expected_departure": timing.departure,
        "departure_grace_minutes": timing.departure_grace_minutes,
        "task_notes": timing.task_notes,
        "reminders_enabled": timing.reminders_enabled,
        "reminder_lead_minutes": timing.reminder_lead_minutes,
        "is_customised": True,
    }


# ---------------------------------------------------------------------------
# 6.4 Reminders
# ---------------------------------------------------------------------------


def _reminder_lead_minutes(item: ScheduleItem) -> int | None:
    """Lead time for an item, or ``None`` when reminders are switched off."""
    if item.source != "engagement":
        return 60  # Bookings use the default; they have no TaskTiming.

    timing = TaskTiming.objects.filter(engagement_id=item.source_id).first()
    if timing is None:
        return 60
    return timing.reminder_lead_minutes if timing.reminders_enabled else None


@transaction.atomic
def ensure_reminders_for_worker(worker, *, days_ahead: int = REMINDER_HORIZON_DAYS) -> int:
    """Queue reminders for a worker's upcoming visits (Module 6.4).

    Idempotent: ``get_or_create`` on (recipient, kind, event_at) means calling
    this on every schedule read cannot produce duplicates. That is what lets
    generation be lazy, which it has to be — there is no scheduler on the free
    tier to generate them on a timer.

    Returns how many were newly created.
    """
    today = timezone.localdate()
    items = worker_day(worker.pk, today)
    for offset in range(1, days_ahead + 1):
        items += worker_day(worker.pk, today + dt.timedelta(days=offset))

    created = 0
    now = timezone.now()

    for item in items:
        lead = _reminder_lead_minutes(item)
        if lead is None:
            continue

        event_at = combine_local(item.date, item.expected_arrival or item.start_time)
        if event_at <= now:
            # Already happened or happening — a reminder now would only confuse.
            continue

        kind = (
            ReminderKind.UPCOMING_ENGAGEMENT
            if item.source == "engagement"
            else ReminderKind.UPCOMING_BOOKING
        )

        _, was_created = Reminder.objects.get_or_create(
            recipient=worker.user,
            kind=kind,
            event_at=event_at,
            defaults={
                "society_id": worker.user.society_id,
                "engagement_id": item.source_id if item.source == "engagement" else None,
                "booking_id": item.source_id if item.source == "booking" else None,
                "send_after": event_at - dt.timedelta(minutes=lead),
                "title": f"{item.title} at {item.flat_label}",
                "body": (
                    f"You are expected at {item.flat_label} at "
                    f"{item.start_time:%H:%M} today."
                ),
            },
        )
        created += int(was_created)

    if created:
        logger.info("Queued %s reminder(s) for worker %s", created, worker.pk)
    return created


def due_reminders(*, society_id=None, recipient=None):
    """Reminders ready for Module 10 to deliver.

    Stale ones are swept first: a reminder about a visit that has already
    happened is worse than no reminder, so it is cancelled rather than sent late.
    """
    stale = Reminder.objects.stale()
    if society_id is not None:
        stale = stale.filter(society_id=society_id)
    swept = stale.update(status=ReminderStatus.CANCELLED, updated_at=timezone.now())
    if swept:
        logger.info("Cancelled %s stale reminder(s)", swept)

    queryset = Reminder.objects.due().select_related("recipient")
    if society_id is not None:
        queryset = queryset.filter(society_id=society_id)
    if recipient is not None:
        queryset = queryset.filter(recipient=recipient)

    return queryset


__all__ = [
    "REMINDER_HORIZON_DAYS",
    "ConflictReport",
    "check_conflict",
    "conflicted_worker_ids",
    "due_reminders",
    "effective_timing",
    "ensure_reminders_for_worker",
]
