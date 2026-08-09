"""
Module 4.6 / 8.10 — what a household owes before it can stop a helper coming.

-------------------------------------------------------------------------------
THE RULE
-------------------------------------------------------------------------------
Giving notice ends a wage. Before it takes effect the household settles the part
of this month the helper has already worked::

    amount_due = days_worked_this_month / total_days_in_current_month * monthly_rate

The denominator is **calendar days in the month** — 31 in August, 28 in an
ordinary February — as specified.

-------------------------------------------------------------------------------
WHAT THAT MEANS, AND WHO IT SUITS
-------------------------------------------------------------------------------
This is the right arithmetic for a helper who comes **every day**, which is the
common arrangement: 30 days worked out of 31 settles at 97% of the month, which
is what anybody would expect.

It is worth being explicit that it behaves differently for a **part-week**
helper, because the numerator counts days she actually worked and the
denominator counts days on the calendar. Somebody who comes only on Mondays and
Thursdays works about 8 days in a 31-day month, so a month in which she missed
nothing settles at ``8/31`` — roughly a quarter of her monthly rate.

``scheduled_days`` is therefore still computed and still shown in the breakdown,
even though it no longer divides anything. It is the number that makes the
result legible: a resident looking at "8 days worked, 22 scheduled, 31 in the
month" can see exactly how the figure was reached, and anybody revisiting this
policy has the other denominator already in front of them.

-------------------------------------------------------------------------------
WHAT COUNTS AS A DAY WORKED
-------------------------------------------------------------------------------
Three things count, and a day needs only one of them:

1. The gate logged her arrival (Module 7).
2. She marked the visit done (Module 6.6).
3. Her terms called for a visit that day, the day has passed, and no leave was
   recorded against it.

(1) and (2) fail independently and neither failure is her fault: a society with
no guard on duty logs no entry, and a flat battery records no completion.
Requiring both, or requiring the stricter one, would dock wages for somebody
else's broken equipment.

(3) is why this module does not simply count records, and it is the difference
between a correct figure and a cruel one. A gate event is only attached to an
engagement when the scan lands inside the visit window — ``_link_visit`` in
``attendance/services.py`` matches within ±``VISIT_MATCH_WINDOW_MINUTES``, and
leaves ``engagement_id`` NULL otherwise. Arrivals outside that window, scans at
a society with no guard on the gate, and every event recorded before the
engagement existed therefore carry no engagement at all. Counting only linked
records settles a helper who came every day at **zero**, which is precisely the
wage theft the pro-rata exists to prevent.

So the schedule is treated as evidence of work and the records are treated as
evidence about it: a scheduled day is presumed worked, and the way to say she
was *not* there is to record leave (Module 6.5), which is a first-class object
both sides can see. Presumption stops at today, at ``paused_at`` and at
``ended_at``, so no day is ever presumed after she stopped coming.

Unlinked gate entries are still attributed where the day was scheduled for this
engagement. That adds nothing on an ordinary day — presumption already counted
it — but it correctly overrides recorded leave on a day she came anyway.

Counted as **distinct dates**, so passing the gate twice in a day is one day.
"""

from __future__ import annotations

import calendar
import datetime as dt
import logging
from dataclasses import dataclass

from django.utils import timezone

from .models import EngagementStatus

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SettlementBreakdown:
    """The arithmetic behind a final payment, with every term exposed.

    Every field is shown to the resident before they confirm. A number somebody
    cannot account for is a number they will dispute, and this one is being
    presented at the exact moment the relationship is ending — which is when a
    figure nobody can explain does the most damage.
    """

    days_worked: int
    scheduled_days: int
    month_start: dt.date
    month_end: dt.date
    monthly_rate_paise: int
    amount_paise: int

    #: Days counted from the gate log, and from completion marks. They overlap;
    #: they are reported separately so an operator can see *why* a day counted.
    attended_days: int
    completed_days: int

    #: Days counted from the schedule alone — expected, past, and neither
    #: recorded nor covered by leave. Reported so a resident disputing the
    #: figure can see exactly how many days rest on the roster rather than on a
    #: gate log, and knows that recording leave is what removes them.
    presumed_days: int = 0

    @property
    def is_settled_in_full(self) -> bool:
        """Nothing outstanding — either no work yet, or no rate to pro-rate."""
        return self.amount_paise == 0

    @property
    def days_in_month(self) -> int:
        """The denominator. Calendar days, not scheduled visits."""
        return calendar.monthrange(self.month_start.year, self.month_start.month)[1]

    def explain(self) -> str:
        """The division in words, so the figure can be checked by eye."""
        if self.days_worked == 0:
            return "No days were worked yet this month, so nothing is owed."
        sentence = (
            f"{self.days_worked} day(s) worked out of {self.days_in_month} in "
            f"{self.month_start:%B}"
            + (f", of {self.scheduled_days} scheduled." if self.scheduled_days else ".")
        )
        if self.presumed_days:
            sentence += (
                f" {self.presumed_days} of those are days the schedule expected "
                "her and no leave was recorded."
            )
        return sentence

    def as_dict(self) -> dict:
        from apps.payments.models import format_paise

        return {
            "days_worked": self.days_worked,
            "scheduled_days": self.scheduled_days,
            "attended_days": self.attended_days,
            "completed_days": self.completed_days,
            "presumed_days": self.presumed_days,
            "month_start": self.month_start,
            "month_end": self.month_end,
            "days_in_month": self.days_in_month,
            "monthly_rate_paise": self.monthly_rate_paise,
            "monthly_rate_display": format_paise(self.monthly_rate_paise),
            "amount_paise": self.amount_paise,
            "amount_display": format_paise(self.amount_paise),
            "is_settled_in_full": self.is_settled_in_full,
            "explanation": self.explain(),
        }


def _month_bounds(on: dt.date) -> tuple[dt.date, dt.date]:
    last = calendar.monthrange(on.year, on.month)[1]
    return on.replace(day=1), on.replace(day=last)


def scheduled_dates_in(engagement, *, start: dt.date, end: dt.date) -> set[dt.date]:
    """The dates this engagement's terms call for a visit on, within a window.

    Expanded from ``days_of_week`` rather than read from the derived schedule,
    for the reason ``payments.services._visits_from_terms`` gives: the schedule
    only expands **active** engagements, and this runs at the moment one is
    being ended. Clipped to the engagement's own lifetime so a hire made on the
    20th is never charged for the first three weeks of the month.
    """
    days = set(engagement.days_of_week or [])
    if not days:
        return set()

    if engagement.started_on:
        start = max(start, engagement.started_on)
    if engagement.last_working_day is not None:
        end = min(end, engagement.last_working_day)

    dates = set()
    day = start
    while day <= end:
        if day.weekday() in days:
            dates.add(day)
        day += dt.timedelta(days=1)
    return dates


def scheduled_days_in(engagement, *, start: dt.date, end: dt.date) -> int:
    """How many visits this engagement's terms call for in a window."""
    return len(scheduled_dates_in(engagement, start=start, end=end))


def _recorded_dates(
    engagement, *, start: dt.date, end: dt.date
) -> tuple[set[dt.date], set[dt.date]]:
    """``(attended, completed)`` — dates something on record says she worked.

    Gate entries are matched on the engagement FK **or**, where the FK is NULL,
    on this worker having entered on a day these terms called for a visit. See
    the module docstring: the FK is only populated when the scan lands inside
    the visit window, so trusting it alone silently discards most arrivals.

    A worker with two engagements scheduled on the same day has one gate entry
    attributed to both. That is deliberate and it costs nothing — a scheduled
    day is presumed worked anyway — except on a day with leave recorded, where
    it decides in her favour. One entry cannot be split between households, and
    the alternative is docking her for a day the gate says she was there.

    Two queries, both indexed, regardless of how long the window is.
    """
    from apps.attendance.models import AttendanceEvent, Decision, Direction
    from apps.scheduling.models import TaskCompletion

    scheduled = scheduled_dates_in(engagement, start=start, end=end)

    def attributable(rows, date_key):
        """Dates from ``rows`` that belong to this engagement."""
        return {
            row[date_key]
            for row in rows
            if row["engagement_id"] == engagement.pk
            or (row["engagement_id"] is None and row[date_key] in scheduled)
        }

    attended = attributable(
        AttendanceEvent.objects.filter(
            worker_id=engagement.worker_id,
            direction=Direction.ENTRY,
            decision=Decision.ALLOWED,
            occurred_at__date__gte=start,
            occurred_at__date__lte=end,
        ).values("engagement_id", "occurred_at__date"),
        "occurred_at__date",
    )

    completed = attributable(
        TaskCompletion.objects.filter(
            worker_id=engagement.worker_id,
            visit_date__gte=start,
            visit_date__lte=end,
        ).values("engagement_id", "visit_date"),
        "visit_date",
    )

    return attended, completed


def _leave_dates(engagement, *, start: dt.date, end: dt.date) -> set[dt.date]:
    """Dates this engagement has leave on record for.

    ``live()`` excludes withdrawn leave, which is exactly right: a worker who
    withdrew her request came after all, and the day should count again.
    """
    from apps.scheduling.models import LeaveRequest

    return set(
        LeaveRequest.objects.live()
        .filter(
            engagement_id=engagement.pk,
            leave_date__gte=start,
            leave_date__lte=end,
        )
        .values_list("leave_date", flat=True)
    )


def _presumption_horizon(engagement, *, end: dt.date, today: dt.date) -> dt.date:
    """The last date a visit may be *presumed* to have happened.

    Never the future, and never after she stopped coming. Without the pause and
    end clips a household would be billed for the remainder of the month every
    time it paused an engagement, which would turn "pause" into a charge.
    """
    horizon = min(end, today)
    if engagement.paused_at is not None and engagement.status == EngagementStatus.PAUSED:
        horizon = min(horizon, timezone.localdate(engagement.paused_at))
    if engagement.ended_at is not None:
        horizon = min(horizon, timezone.localdate(engagement.ended_at))
    return horizon


@dataclass(frozen=True)
class WorkedDays:
    """Which dates counted, and what made each of them count."""

    dates: frozenset
    attended: int
    completed: int
    presumed: int

    @property
    def total(self) -> int:
        return len(self.dates)


def worked_days_in(
    engagement, *, start: dt.date, end: dt.date, today: dt.date | None = None
) -> WorkedDays:
    """Every date in the window this helper is treated as having worked.

    The union of what the records show and what the schedule expected. See the
    module docstring for why the schedule counts.
    """
    today = today or timezone.localdate()

    attended, completed = _recorded_dates(engagement, start=start, end=end)
    recorded = attended | completed

    horizon = _presumption_horizon(engagement, end=end, today=today)
    presumed: set[dt.date] = set()
    if horizon >= start:
        presumed = (
            scheduled_dates_in(engagement, start=start, end=horizon)
            - _leave_dates(engagement, start=start, end=horizon)
            - recorded
        )

    return WorkedDays(
        dates=frozenset(recorded | presumed),
        attended=len(attended),
        completed=len(completed),
        presumed=len(presumed),
    )


def days_worked_in(engagement, *, start: dt.date, end: dt.date) -> tuple[int, int, int]:
    """Distinct dates on record. ``(total, attended, completed)``.

    Records only — this is the "what can we prove" view, used for reporting and
    for disputes. :func:`worked_days_in` is what the settlement is computed
    from, because a day nobody logged is still a day she worked.
    """
    attended, completed = _recorded_dates(engagement, start=start, end=end)
    return len(attended | completed), len(attended), len(completed)


def settlement_due(engagement, *, on: dt.date | None = None) -> SettlementBreakdown:
    """What the household owes for this month's work so far.

    ``days_worked / calendar_days_in_month * monthly_rate``, as specified.

    Capped at the full monthly rate: a helper who somehow logged more days than
    the month has is not owed more than a month's wage for a month, and the
    overshoot is a data question rather than a pay rise.
    """
    from apps.payments.models import rupees_to_paise

    today = on or timezone.localdate()
    month_start, month_end = _month_bounds(today)
    days_in_month = calendar.monthrange(today.year, today.month)[1]

    # Not the denominator any more, but still computed: it is what makes the
    # figure legible in the breakdown. See the module docstring.
    scheduled = scheduled_days_in(engagement, start=month_start, end=month_end)
    worked = worked_days_in(
        engagement, start=month_start, end=month_end, today=today
    )

    rate = rupees_to_paise(engagement.monthly_rate)

    # Integer arithmetic throughout, rounding down. Sub-paise fractions stay
    # with the payer, consistent with every other split in Module 8.
    amount = rate * min(worked.total, days_in_month) // days_in_month

    return SettlementBreakdown(
        days_worked=worked.total,
        scheduled_days=scheduled,
        month_start=month_start,
        month_end=month_end,
        monthly_rate_paise=rate,
        amount_paise=amount,
        attended_days=worked.attended,
        completed_days=worked.completed,
        presumed_days=worked.presumed,
    )


def outstanding_settlement(engagement, *, on: dt.date | None = None):
    """The unpaid settlement for this engagement, or ``None`` if square.

    "Square" means one of three things, and they are deliberately not
    distinguished to the caller: nothing is owed, a settlement payment for this
    month has already settled, or the resident has already paid enough salary
    this month to cover it. The last matters — a household that pays on the 1st
    and gives notice on the 20th has already handed over the money, and asking
    again would be charging twice for the same work.
    """
    from apps.payments.models import Payment, PaymentKind, PaymentStatus

    breakdown = settlement_due(engagement, on=on)
    if breakdown.amount_paise <= 0:
        return None

    already_paid = sum(
        payment.amount_paise
        for payment in Payment.objects.filter(
            engagement_id=engagement.pk,
            status=PaymentStatus.PAID,
            kind__in=[PaymentKind.ENGAGEMENT_SALARY, PaymentKind.NOTICE_SETTLEMENT],
            paid_at__date__gte=breakdown.month_start,
            paid_at__date__lte=breakdown.month_end,
        )
    )

    if already_paid >= breakdown.amount_paise:
        return None
    return breakdown


__all__ = [
    "SettlementBreakdown",
    "WorkedDays",
    "days_worked_in",
    "outstanding_settlement",
    "scheduled_dates_in",
    "scheduled_days_in",
    "settlement_due",
    "worked_days_in",
]
