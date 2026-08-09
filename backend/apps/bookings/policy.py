"""
Module 5.4 — booking notice and cancellation policy.

Pure functions over plain numbers, for the same reason ``hiring/scoring.py`` is:
this is the part most likely to be tuned once the platform sees real behaviour,
and rules that touch money should be testable without a database and auditable
without reading a view.

-------------------------------------------------------------------------------
WHY A FEE AT ALL
-------------------------------------------------------------------------------
The modspec asks for "a cancellation window and fee policy, to discourage
last-minute no-shows from either side given how time-sensitive these bookings
are". A one-day booking is a worker's whole slot for that day: cancelled two
hours out, they have almost certainly turned other work away and cannot refill
it. The fee is compensation for that, not a punishment — which is why it scales
with proximity to the start time, and why it applies symmetrically to a worker
who cancels late as much as to a resident.

The amount is computed here and **stored on the booking** at the moment of
cancellation. The policy may change later, and what someone was actually
charged must not silently change with it.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

#: Cancel this many hours or more before the start: no fee. Roughly the window
#: in which a worker can still pick up other work for the day.
FREE_CANCELLATION_HOURS = 6.0

#: Inside this window the slot is effectively unfillable, so the full amount
#: stands.
FULL_FEE_HOURS = 2.0

#: Charged between the two thresholds, as a share of the quoted price.
PARTIAL_FEE_RATE = 0.5

#: Default minimum notice for a booking, when the society has not set its own.
#: ``Society.booking_notice_hours`` is the real source; this is the fallback.
DEFAULT_NOTICE_HOURS = 12


# ---------------------------------------------------------------------------
# 5.5 Emergency surcharge
# ---------------------------------------------------------------------------
#
# This is the *platform's* fee for running an emergency broadcast, and it is the
# only money Sathify moves for one. The worker's own fee is settled in cash
# between the household and the worker, outside the app entirely — see
# ``bookings/emergency.py``. Keeping the two apart matters: this table must
# never be mistaken for what the worker is owed.
#
# Priced by how much notice the platform actually gets, because that is what the
# fee buys: an emergency raised for today has to interrupt people who had other
# plans, and one raised for tomorrow does not.

#: Days of lead time → surcharge in whole rupees. Any lead time not listed is
#: not an emergency in any meaningful sense and is charged nothing.
EMERGENCY_SURCHARGE_RUPEES: dict[int, int] = {
    0: 100,  # raised on the service day itself
    1: 50,   # raised the day before
}

#: Beyond this many days ahead, the emergency flow is refused outright rather
#: than sold at ₹0 — a "next week emergency" is an ordinary booking, and the
#: directed flow serves the household better because they get to choose who
#: comes.
MAX_EMERGENCY_LEAD_DAYS = max(EMERGENCY_SURCHARGE_RUPEES)


@dataclass(frozen=True)
class SurchargeQuote:
    """What raising this emergency costs the household, and why."""

    rupees: int
    lead_days: int
    rationale: str

    @property
    def paise(self) -> int:
        return self.rupees * 100


def emergency_surcharge(
    *, scheduled_date: dt.date, raised_on: dt.date
) -> SurchargeQuote:
    """Price an emergency request from how far ahead it was raised.

    Measured in whole local days rather than hours. A household raising a job
    for "tonight" at 09:00 and another raising it at 21:00 are both asking the
    platform to fill a slot today, and charging them differently for the hour
    they happened to open the app would be arbitrary in a way they could feel.

    A date in the past reads as same-day: the request is as urgent as it gets,
    and refusing it here would be the wrong place to enforce that.
    """
    lead_days = max(0, (scheduled_date - raised_on).days)
    rupees = EMERGENCY_SURCHARGE_RUPEES.get(lead_days, 0)

    if lead_days == 0:
        why = "Raised for today."
    elif lead_days == 1:
        why = "Raised a day ahead."
    else:
        why = f"Raised {lead_days} days ahead — no emergency fee applies."

    return SurchargeQuote(rupees=rupees, lead_days=lead_days, rationale=why)


@dataclass(frozen=True)
class CancellationPolicy:
    """The thresholds in force. Passed in so the maths never reads settings."""

    free_hours: float = FREE_CANCELLATION_HOURS
    full_fee_hours: float = FULL_FEE_HOURS
    partial_rate: float = PARTIAL_FEE_RATE


@dataclass(frozen=True)
class CancellationOutcome:
    """What cancelling now costs, and why."""

    fee: int
    tier: str
    rationale: str

    @property
    def is_free(self) -> bool:
        return self.fee == 0


def cancellation_outcome(
    *,
    hours_until_start: float,
    quoted_price: int,
    policy: CancellationPolicy | None = None,
) -> CancellationOutcome:
    """The fee for cancelling a booking ``hours_until_start`` from now.

    ``hours_until_start`` is negative once the start time has passed, which
    falls into the full-fee tier — a booking abandoned after it should have
    begun is the case the policy most needs to cover.
    """
    rules = policy or CancellationPolicy()
    price = max(0, int(quoted_price))

    if hours_until_start >= rules.free_hours:
        return CancellationOutcome(
            fee=0,
            tier="free",
            rationale=(
                f"Cancelled {hours_until_start:.0f} hours ahead, at or beyond the "
                f"{rules.free_hours:.0f}-hour free window."
            ),
        )

    if hours_until_start >= rules.full_fee_hours:
        # Rounded down: where the split is not exact, the benefit goes to the
        # party being charged rather than the platform.
        fee = int(price * rules.partial_rate)
        return CancellationOutcome(
            fee=fee,
            tier="partial",
            rationale=(
                f"Cancelled {hours_until_start:.0f} hours ahead, inside the "
                f"{rules.free_hours:.0f}-hour window: "
                f"{int(rules.partial_rate * 100)}% of the quoted price."
            ),
        )

    when = (
        "after the start time"
        if hours_until_start < 0
        else f"{hours_until_start:.0f} hours ahead"
    )
    return CancellationOutcome(
        fee=price,
        tier="full",
        rationale=(
            f"Cancelled {when}, inside the {rules.full_fee_hours:.0f}-hour "
            "window: the full quoted price stands."
        ),
    )


@dataclass(frozen=True)
class NoticeCheck:
    """Whether a proposed booking respects the society's minimum notice."""

    allowed: bool
    required_hours: int
    reason: str = ""


def check_notice_period(
    *,
    hours_until_start: float,
    notice_hours: int,
    bypasses_notice: bool = False,
) -> NoticeCheck:
    """Whether a booking may be made this close to its start time (Module 5.2).

    ``bypasses_notice`` is set by emergency categories. A notice window that
    blocks an emergency defeats the point of having the category, so the check
    is skipped rather than shortened — but the booking still cannot be placed
    in the past.
    """
    if hours_until_start <= 0:
        return NoticeCheck(
            allowed=False,
            required_hours=notice_hours,
            reason="A booking cannot start in the past.",
        )

    if bypasses_notice:
        return NoticeCheck(allowed=True, required_hours=0)

    if hours_until_start < notice_hours:
        return NoticeCheck(
            allowed=False,
            required_hours=notice_hours,
            reason=(
                f"This society needs at least {notice_hours} hours' notice for a "
                f"booking. Choose a later time, or pick an emergency category if "
                f"this genuinely cannot wait."
            ),
        )

    return NoticeCheck(allowed=True, required_hours=notice_hours)
