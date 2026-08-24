"""
Module 8.10 — the hourly billing engine.

-------------------------------------------------------------------------------
TWO RULES GOVERN EVERYTHING BELOW
-------------------------------------------------------------------------------
**Pay tracks time worked, and nothing else.** Lateness reduces pay only by the
minutes not worked. There is no fine, no penalty multiplier, no separate
deduction line anywhere in this module. A platform that levies wage fines on
domestic workers is inventing a liability it does not need, and the arithmetic
of "you are paid for what you did" is both fairer and easier to defend.

**Every visit carries a fixed fee alongside the hourly rate.** Her cost of
turning up does not shrink when the job is short, so a flat hourly rate
underpays short visits — see :func:`calibrated_visit_fee` for the algebra and
for why the fee is derived rather than guessed.

-------------------------------------------------------------------------------
INTEGERS, IN PAISE, ALWAYS
-------------------------------------------------------------------------------
Same rule as the rest of this app: no floats, no Decimal. Floats cannot
represent 0.1 exactly, so a float ledger drifts by fractions of a paisa per row
and eventually fails to reconcile against Razorpay, which counts in paise and is
right to. Minutes are rounded first, then converted, with an explicit half-up on
the final division so no fraction is silently lost.

-------------------------------------------------------------------------------
PRICING HAPPENS ONCE
-------------------------------------------------------------------------------
:func:`price_session` writes its results onto the session and stamps
``priced_at``. Nothing recomputes on read. A society that changes its rounding
rule in March must not silently rewrite what a worker was paid in January, and a
resident querying a six-week-old charge has to be shown the number that actually
happened rather than the number today's config would produce.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from django.conf import settings
from django.utils import timezone

from .models import rupees_to_paise

#: Basis points in 1.0x. Overtime multipliers are stored in basis points so a
#: 1.25x premium is an integer (12500) rather than a float.
BP_PER_UNIT = 10_000


# ---------------------------------------------------------------------------
# Primitives
# ---------------------------------------------------------------------------


def round_minutes(minutes: int, *, step: int = 15, up: bool = False) -> int:
    """Round to the nearest ``step`` minutes, half-up. Applied once, per session.

    ``up=True`` always rounds in the worker's favour instead. Nearest is the
    default because it is symmetric — neither party can claim the app leans
    against them — and because it is the only mode in which a genuine shortfall
    actually registers. Rounding up would swallow most lateness entirely: 166
    minutes against a 180-minute schedule would round back up to 180, and a
    fourteen-minute late arrival would cost nothing at all.

    A society may still choose ``up``; at ₹120/hour it costs a resident at most
    one step per session and removes a whole class of "the app shaved my time"
    arguments. It is surfaced at onboarding as a committee decision rather than
    buried here as a default.
    """
    if step <= 0:
        raise ValueError("Rounding step must be a positive number of minutes.")
    minutes = max(0, int(minutes))
    if up:
        return ((minutes + step - 1) // step) * step
    return ((minutes + step // 2) // step) * step


def session_paise(minutes: int, hourly_rate_paise: int, *, multiplier_bp: int = BP_PER_UNIT) -> int:
    """Minutes at a rate, in paise. Half-up on the division.

    The ``+ denominator // 2`` is the half-up: integer floor division alone would
    round every fractional paisa down, which across a month of sessions is a
    small, systematic, one-directional shortfall against the worker. Small and
    systematic against the same party every time is exactly the kind of error
    that erodes trust in a wage figure.
    """
    minutes = max(0, int(minutes))
    numerator = int(hourly_rate_paise) * minutes * int(multiplier_bp)
    denominator = 60 * BP_PER_UNIT
    return (numerator + denominator // 2) // denominator


def calibrated_visit_fee(hourly_rate_paise: int, overhead_minutes: int) -> int:
    """``F = R × T`` — the fee that makes her effective rate job-length-independent.

    Let R be the hourly rate, T the fixed overhead per visit (travel, the gate,
    the lift, getting in and out), H the hours worked and F the visit fee. Her
    earnings per hour of *committed* time are::

        effective(H) = (F + R·H) / (H + T)

    Requiring that to equal R for every H::

        F + R·H = R·(H + T)
        F + R·H = R·H + R·T
              F = R × T

    Not an approximation — the H terms cancel exactly. At ₹120/hour and 30
    minutes of overhead the fee is ₹60, and she earns ₹120 per committed hour
    whether the job is one hour or four. Without it, the same worker at the same
    advertised rate earns ₹80/hr effective on a one-hour job and ₹107/hr on a
    four-hour one, and rationally starts dropping the short jobs first.

    Returned in paise. The caller stores whole rupees on the terms, so this is
    the figure to *suggest* at agreement time rather than the one to bill from.
    """
    return session_paise(int(overhead_minutes), int(hourly_rate_paise))


# ---------------------------------------------------------------------------
# The nine rules
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BillingConfig:
    """The society's knobs, snapshotted so pricing cannot drift under a session.

    A plain dataclass rather than the model, so the engine can be exercised —
    and its arithmetic argued with — without a database.
    """

    round_step_minutes: int = 15
    round_up: bool = False
    ot_multiplier_bp: int = BP_PER_UNIT
    free_cancellation_hours: int = 12

    @classmethod
    def from_society(cls, society) -> "BillingConfig":
        from apps.societies.models import SocietyBillingConfig

        row = SocietyBillingConfig.for_society(society)
        return cls(
            round_step_minutes=row.round_minutes,
            round_up=row.round_up_in_workers_favour,
            ot_multiplier_bp=row.ot_multiplier_bp,
            free_cancellation_hours=row.free_cancellation_hours,
        )


@dataclass(frozen=True)
class SessionTiming:
    """What the engine needs to know about when a visit was meant to happen.

    ``scheduling.effective_timing`` already answers this and is the single place
    "no TaskTiming set" is resolved, so this adapts its dict rather than
    re-deriving the fallbacks and risking a second, subtly different answer.

    Note the grace values come through unchanged: an engagement with no
    ``TaskTiming`` has *zero* grace by that function's existing definition, not
    the 15-minute default a ``TaskTiming`` row carries. Rule 1 is therefore
    inert until a resident sets expectations, which is the honest reading —
    nobody has said what "on time" means yet.
    """

    arrival: dt.time
    departure: dt.time
    arrival_grace_minutes: int = 0
    departure_grace_minutes: int = 0

    @classmethod
    def for_engagement(cls, engagement) -> "SessionTiming":
        from apps.scheduling.services import effective_timing

        t = effective_timing(engagement)
        return cls(
            arrival=t["expected_arrival"],
            departure=t["expected_departure"],
            arrival_grace_minutes=t["arrival_grace_minutes"],
            departure_grace_minutes=t["departure_grace_minutes"],
        )


@dataclass(frozen=True)
class Billable:
    """What a session is worth, before money is applied."""

    regular_minutes: int
    overtime_minutes: int
    unbilled_extra_minutes: int
    charge_visit_fee: bool

    @property
    def total_minutes(self) -> int:
        return self.regular_minutes + self.overtime_minutes


def _minutes_between(start: dt.datetime, end: dt.datetime) -> int:
    return max(0, int((end - start).total_seconds() // 60))


def _combine(day: dt.date, at: dt.time) -> dt.datetime:
    """A wall-clock time on ``day``, in the *local* timezone.

    Schedules are wall-clock ("she comes at nine"), sessions are instants, and
    this is the one place the two meet.

    The local zone is used deliberately rather than the tzinfo of whatever
    datetime happens to be at hand. Django stores datetimes in UTC, so a session
    read back from the database carries ``tzinfo=UTC``; borrowing that would
    reinterpret a 09:00 arrival as 09:00 UTC — 14:30 in Pune — and every session
    would price as zero minutes worked, because the "scheduled" window would sit
    hours after the real one. The schedule belongs to the society's clock, not
    to the storage format.
    """
    naive = dt.datetime.combine(day, at)
    if settings.USE_TZ and timezone.is_naive(naive):
        return timezone.make_aware(naive, timezone.get_current_timezone())
    return naive


def billable(session, timing, config: BillingConfig) -> Billable:
    """Apply rules 1–9 to one session.

    ``timing`` is a ``scheduling.TaskTiming``-shaped object: anything exposing
    ``arrival``, ``departure``, ``arrival_grace_minutes`` and
    ``departure_grace_minutes``. Duck-typed on purpose so the rules can be tested
    against a stub.
    """
    from apps.attendance.models import SessionStatus

    # Rule 9 — a no-show owes nothing at all, not even the fee.
    if session.status == SessionStatus.NO_SHOW:
        return Billable(0, 0, 0, charge_visit_fee=False)

    # Rule 8 — cancelled once she had already arrived. She travelled, so the fee
    # is owed; no hours were worked, so none are billed.
    if session.status == SessionStatus.CANCELLED_AT_DOOR:
        return Billable(0, 0, 0, charge_visit_fee=True)

    if not (session.started_at and session.ended_at):
        # Still open, or closed without boundaries. The fee stands — she is
        # there — and the hours resolve when the session does.
        return Billable(0, 0, 0, charge_visit_fee=True)

    day = session.visit_date
    scheduled_start = _combine(day, timing.arrival)
    scheduled_end = _combine(day, timing.departure)
    if scheduled_end <= scheduled_start:
        # An overnight slot: departure belongs to the following day.
        scheduled_end += dt.timedelta(days=1)

    grace = dt.timedelta(minutes=timing.arrival_grace_minutes)

    # Rule 1 — grace absorbs the gate. A queue at the gate and a slow lift are
    # not her fault, so an arrival inside the window bills from the scheduled
    # start rather than from when she actually got upstairs.
    start = session.started_at
    if start <= scheduled_start + grace:
        start = scheduled_start

    # Rule 3 — arriving early does not start the clock. She is not paid for it,
    # and equally not penalised; if the resident wants the extra time they
    # approve it through the same flow as overtime.
    start = max(start, scheduled_start)

    # Rule 2 is what remains: past the grace window, billing starts when she
    # did. The shortfall *is* the deduction, and nothing further is applied.

    # Rule 4 — early departure is symmetric. Billing ends when she left.
    end = min(session.ended_at, scheduled_end)
    regular = _minutes_between(start, end)

    # Rule 5 — overtime must have been approved before it was worked. Anything
    # past the approval is recorded so both parties can see it, and not charged.
    over = _minutes_between(scheduled_end, session.ended_at)
    approved = min(over, int(session.approved_ot_minutes or 0))
    unbilled = over - approved

    # Rule 6 — round once, here, and never again. Regular and overtime round
    # separately so a rounding gain on one cannot silently offset a loss on the
    # other.
    regular = round_minutes(regular, step=config.round_step_minutes, up=config.round_up)
    approved = round_minutes(approved, step=config.round_step_minutes, up=config.round_up)

    # Rule 7 — one visit fee per session, flat and unrounded.
    return Billable(regular, approved, unbilled, charge_visit_fee=True)


def price_session(session, *, timing=None, config: BillingConfig | None = None, commit: bool = True):
    """Compute and freeze what a session is worth. Returns the session.

    Idempotent by ``priced_at``: a session already priced is returned untouched,
    so replaying a sync or re-running the nightly close cannot double-charge.
    """
    from apps.hiring.models import RateBasis

    if session.priced_at is not None:
        return session

    engagement = session.engagement
    if engagement.rate_basis != RateBasis.HOURLY:
        # Monthly engagements still keep sessions — that is how attendance
        # transparency ships ahead of hourly billing — but they are priced by
        # `services.salary_basis`, not here.
        return session

    if config is None:
        config = BillingConfig.from_society(session.society)
    if timing is None:
        timing = SessionTiming.for_engagement(engagement)

    result = billable(session, timing, config)
    rate = rupees_to_paise(engagement.hourly_rate)

    session.billable_minutes = result.regular_minutes
    session.overtime_minutes = result.overtime_minutes
    session.unbilled_extra_minutes = result.unbilled_extra_minutes
    session.time_paise = session_paise(result.regular_minutes, rate)
    session.overtime_paise = session_paise(
        result.overtime_minutes, rate, multiplier_bp=config.ot_multiplier_bp
    )
    session.visit_fee_paise = (
        rupees_to_paise(engagement.visit_fee) if result.charge_visit_fee else 0
    )
    session.priced_at = timezone.now()

    if commit:
        session.save(
            update_fields=[
                "billable_minutes", "overtime_minutes", "unbilled_extra_minutes",
                "time_paise", "overtime_paise", "visit_fee_paise", "priced_at",
                "updated_at",
            ]
        )
    return session


def effective_hourly_paise(total_paise: int, worked_minutes: int, overhead_minutes: int) -> int:
    """What she actually earned per hour of committed time, including travel.

    The metric §9.3 of the PRD tracks: if ``visit_fee`` is calibrated correctly
    this is flat across job lengths, and a spread opening up between short and
    long engagements means the society's ``visit_overhead_minutes`` no longer
    matches how far its staff really travel.
    """
    committed = max(1, int(worked_minutes) + int(overhead_minutes))
    return (int(total_paise) * 60 + committed // 2) // committed


# ---------------------------------------------------------------------------
# Switching an engagement onto hourly terms
# ---------------------------------------------------------------------------


def suggest_hourly_terms(society, *, hourly_rate: int) -> dict:
    """What to propose, given a society's overhead. Rupees, as agreed out loud.

    The visit fee is *derived*, not left blank for somebody to guess at: a fee
    picked by feel is how the short-visit penalty creeps back in, and the whole
    point of `F = R x T` is that there is a right answer.
    """
    from apps.societies.models import SocietyBillingConfig

    config = SocietyBillingConfig.for_society(society)
    fee_paise = calibrated_visit_fee(
        rupees_to_paise(int(hourly_rate)), config.visit_overhead_minutes
    )
    return {
        "hourly_rate": int(hourly_rate),
        # Rounded up to the rupee. Rounding down would put her a few paise under
        # the calibrated fee on every single visit, in the same direction.
        "visit_fee": -(-fee_paise // 100),
        "overhead_minutes": config.visit_overhead_minutes,
    }


def set_hourly_terms(engagement, *, hourly_rate: int, visit_fee: int, by=None):
    """Move one engagement onto hourly terms. The only sanctioned way.

    The statutory floor is checked *here* rather than in a serializer, because
    this is the seam every path goes through — an API endpoint, the admin, a
    shell session during a pilot. A check that lives in one view is a check the
    next caller skips without noticing.

    Raises ``wage_floor.WageFloorViolation`` if the terms pay below the minimum
    once travel is counted. Nothing is written when it does.
    """
    from apps.hiring.models import RateBasis
    from apps.societies.models import SocietyBillingConfig

    from . import wage_floor

    config = SocietyBillingConfig.for_society(engagement.society)
    wage_floor.assert_compliant(
        state=engagement.society.state,
        hourly_rate=hourly_rate,
        visit_fee=visit_fee,
        scheduled_minutes=engagement.expected_duration_minutes,
        overhead_minutes=config.visit_overhead_minutes,
    )

    engagement.rate_basis = RateBasis.HOURLY
    engagement.hourly_rate = int(hourly_rate)
    engagement.visit_fee = int(visit_fee)
    engagement.save(update_fields=["rate_basis", "hourly_rate", "visit_fee", "updated_at"])
    return engagement


__all__ = [
    "BP_PER_UNIT",
    "Billable",
    "BillingConfig",
    "SessionTiming",
    "billable",
    "calibrated_visit_fee",
    "effective_hourly_paise",
    "price_session",
    "round_minutes",
    "session_paise",
    "set_hourly_terms",
    "suggest_hourly_terms",
]
