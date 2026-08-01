"""
Module 11.3 — the SLA clock.

Pure functions over datetimes. No database access, no Django models, nothing to
mock: given when a complaint was raised and how many hours it is allowed, these
say when it is due and whether it has run over.

-------------------------------------------------------------------------------
THE CLOCK ONLY RUNS DURING WAKING HOURS
-------------------------------------------------------------------------------
A society administrator is a volunteer resident, not a support desk. A complaint
raised at 23:40 with a four-hour SLA would be "breached" at 03:40, and the
escalation would fire at a time when nobody could possibly have acted on it.

Escalations that fire for reasons nobody could have prevented are the fastest
way to teach people to ignore escalations. So elapsed time is counted only
inside :data:`ACTIVE_START`–:data:`ACTIVE_END`; overnight does not burn the
clock. That 23:40 complaint is due at 12:00 the next day, which is a promise a
real person can actually keep.

Weekends are *not* excluded. A domestic worker who was refused entry on a
Saturday cannot wait until Monday, and unlike office hours, someone in the
society is around.

-------------------------------------------------------------------------------
THE DUE TIME IS COMPUTED ONCE AND THEN FROZEN
-------------------------------------------------------------------------------
:func:`due_at` is called when the complaint is created and the answer is stored.
Escalation may raise a complaint's priority — that reorders the queue, which is
the point of escalating — but it must not move the deadline. A deadline that
recedes whenever the thing gets more urgent is not a deadline.
"""

from __future__ import annotations

import datetime as dt

from django.utils import timezone

#: The window in which the SLA clock advances, in the society's local time.
#: Deliberately generous at both ends: this is "when could a reasonable person
#: have seen this", not office hours.
ACTIVE_START = dt.time(8, 0)
ACTIVE_END = dt.time(21, 0)

ACTIVE_HOURS_PER_DAY = (
    dt.datetime.combine(dt.date.min, ACTIVE_END)
    - dt.datetime.combine(dt.date.min, ACTIVE_START)
).total_seconds() / 3600

#: How long each priority gets, in active hours.
#:
#: URGENT is deliberately shorter than one active day, so an urgent complaint
#: raised in the morning is due the same afternoon rather than tomorrow.
SLA_HOURS = {
    "urgent": 4.0,
    "high": 24.0,
    "normal": 72.0,
}

DEFAULT_SLA_HOURS = SLA_HOURS["normal"]


def _localise(moment: dt.datetime) -> dt.datetime:
    """Move an aware datetime into the configured local timezone.

    Everything here reasons about wall-clock hours — "was it the middle of the
    night?" — which is only meaningful locally. Storage stays UTC.
    """
    return timezone.localtime(moment) if timezone.is_aware(moment) else moment


def _active_start_of(day: dt.date, reference: dt.datetime) -> dt.datetime:
    return reference.replace(
        year=day.year,
        month=day.month,
        day=day.day,
        hour=ACTIVE_START.hour,
        minute=ACTIVE_START.minute,
        second=0,
        microsecond=0,
    )


def _active_end_of(day: dt.date, reference: dt.datetime) -> dt.datetime:
    return reference.replace(
        year=day.year,
        month=day.month,
        day=day.day,
        hour=ACTIVE_END.hour,
        minute=ACTIVE_END.minute,
        second=0,
        microsecond=0,
    )


def _clamp_into_window(moment: dt.datetime) -> dt.datetime:
    """Move a moment forward to the next instant the clock is running.

    Before the window opens, the clock starts when it opens. After it closes,
    it starts tomorrow morning. Inside it, nothing changes.
    """
    if moment.time() < ACTIVE_START:
        return _active_start_of(moment.date(), moment)
    if moment.time() >= ACTIVE_END:
        return _active_start_of(moment.date() + dt.timedelta(days=1), moment)
    return moment


def due_at(raised_at: dt.datetime, hours: float) -> dt.datetime:
    """When a complaint raised at ``raised_at`` must be answered by.

    Advances ``hours`` of *active* time from the raise point, skipping the
    overnight gap. Returns an aware datetime in the same timezone Django is
    configured with, ready to store.
    """
    if hours <= 0:
        return raised_at

    cursor = _clamp_into_window(_localise(raised_at))
    remaining = dt.timedelta(hours=hours)

    # One iteration per calendar day the SLA spans. A 72-hour SLA is under a
    # week of active days, so this is a handful of passes at worst.
    while True:
        window_end = _active_end_of(cursor.date(), cursor)
        available = window_end - cursor

        if remaining <= available:
            return cursor + remaining

        remaining -= available
        cursor = _active_start_of(cursor.date() + dt.timedelta(days=1), cursor)


def active_hours_between(start: dt.datetime, end: dt.datetime) -> float:
    """How much of the clock actually ran between two moments.

    Used to report "open for 6 working hours" rather than "open for 19 hours",
    which is the figure an administrator can be held to.
    """
    if end <= start:
        return 0.0

    cursor = _clamp_into_window(_localise(start))
    finish = _localise(end)
    total = 0.0

    while cursor < finish:
        window_end = min(_active_end_of(cursor.date(), cursor), finish)
        if window_end > cursor:
            total += (window_end - cursor).total_seconds() / 3600
        cursor = _active_start_of(cursor.date() + dt.timedelta(days=1), cursor)

    return round(total, 2)


def hours_for(priority: str) -> float:
    """The SLA window for a priority, falling back to the normal one.

    An unknown priority gets the *longest* window rather than the shortest: a
    typo should not manufacture an urgent deadline and an escalation with it.
    """
    return SLA_HOURS.get(priority, DEFAULT_SLA_HOURS)


def is_breached(due: dt.datetime | None, *, now: dt.datetime | None = None) -> bool:
    if due is None:
        return False
    return (now or timezone.now()) > due


def hours_remaining(due: dt.datetime | None, *, now: dt.datetime | None = None) -> float:
    """Active hours left before the deadline. Negative once it has passed.

    The negative form is deliberate — "12 hours over" is the number an
    administrator triaging a queue actually wants to sort by.
    """
    if due is None:
        return 0.0

    moment = now or timezone.now()
    if moment <= due:
        return active_hours_between(moment, due)
    return -active_hours_between(due, moment)


__all__ = [
    "ACTIVE_END",
    "ACTIVE_HOURS_PER_DAY",
    "ACTIVE_START",
    "DEFAULT_SLA_HOURS",
    "SLA_HOURS",
    "active_hours_between",
    "due_at",
    "hours_for",
    "hours_remaining",
    "is_breached",
]
