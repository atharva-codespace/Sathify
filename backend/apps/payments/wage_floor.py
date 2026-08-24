"""
Module 8.11 — the statutory minimum, checked on the rate she actually earns.

-------------------------------------------------------------------------------
THE ADVERTISED RATE IS NOT THE RATE
-------------------------------------------------------------------------------
A platform that *computes* wages sits closer to the calculation than one
recording a figure two people agreed out loud, so the floor is enforced in code
rather than in a disclaimer. But enforcing it on ``hourly_rate`` alone would
check the wrong number.

Her real rate is earnings over *committed* time — the visit plus the journey it
required. With ``F = R × T`` (see ``hourly.calibrated_visit_fee``) those are the
same figure at every job length, which is what makes one comparison answer
compliance for a whole state. Let the visit fee drift below ``R × T`` and they
come apart, worst at the shortest visits: a one-hour job with no fee at all pays
₹80/hour against an advertised ₹120.

That gap is exactly where a floor gets breached while the stored number still
looks compliant, so that gap is what this module measures.
"""

from __future__ import annotations

from dataclasses import dataclass

from .hourly import session_paise
from .models import WageFloor, format_paise, rupees_to_paise


class WageFloorViolation(Exception):
    """Proposed terms pay below the statutory minimum. A refusal, not a bug."""

    def __init__(self, finding: "FloorCheck"):
        super().__init__(finding.message)
        self.finding = finding


@dataclass(frozen=True)
class FloorCheck:
    """The comparison, with its working exposed.

    Every field is here so the refusal can be *explained* at the point of
    agreement. "That rate is too low" tells a resident nothing they can act on;
    "₹95/hour once her travel is counted, against a ₹110 floor" tells them what
    to change.
    """

    state: str
    #: None means no figure is recorded for this state. See :func:`check`.
    floor_paise: int | None
    effective_paise: int
    hourly_paise: int
    visit_fee_paise: int
    scheduled_minutes: int
    overhead_minutes: int

    @property
    def is_known(self) -> bool:
        return self.floor_paise is not None

    @property
    def is_compliant(self) -> bool:
        """Unknown floors are *not* compliant and not a breach either.

        Callers must not read a missing figure as permission — see
        :func:`assert_compliant`, which is explicit about which way it fails.
        """
        return self.is_known and self.effective_paise >= self.floor_paise

    @property
    def is_calibrated(self) -> bool:
        """Whether the visit fee matches ``R × T``.

        A fee below the calibrated value is not itself illegal; it just means
        the effective rate falls as visits get shorter, and short visits are
        where a floor is breached first.
        """
        return self.visit_fee_paise >= session_paise(
            self.overhead_minutes, self.hourly_paise
        )

    @property
    def message(self) -> str:
        if not self.is_known:
            return (
                f"No minimum wage is recorded for {self.state}, so these terms "
                "cannot be checked against one."
            )
        if self.is_compliant:
            return (
                f"{format_paise(self.effective_paise)}/hour once travel is "
                f"counted, against a {format_paise(self.floor_paise)} floor."
            )
        return (
            f"These terms pay {format_paise(self.effective_paise)}/hour once her "
            f"travel is counted, below the {format_paise(self.floor_paise)} "
            f"minimum for {self.state}. Raise the hourly rate or the visit fee."
        )


def effective_hourly_paise(
    *, hourly_paise: int, visit_fee_paise: int, scheduled_minutes: int, overhead_minutes: int
) -> int:
    """``(F + R·H) / (H + T)`` — what she earns per hour of committed time."""
    worked = max(0, int(scheduled_minutes))
    committed = worked + max(0, int(overhead_minutes))
    if committed <= 0:
        return int(hourly_paise)
    earned = session_paise(worked, int(hourly_paise)) + int(visit_fee_paise)
    return (earned * 60 + committed // 2) // committed


def check(
    *,
    state: str,
    hourly_rate: int,
    visit_fee: int,
    scheduled_minutes: int,
    overhead_minutes: int = 30,
    on=None,
) -> FloorCheck:
    """Compare proposed terms against the floor in force. Never raises.

    ``hourly_rate`` and ``visit_fee`` arrive in whole rupees, matching how the
    terms are stored and how two people agree them out loud.
    """
    hourly_paise = rupees_to_paise(int(hourly_rate))
    fee_paise = rupees_to_paise(int(visit_fee))
    floor = WageFloor.in_force(state, on=on)

    return FloorCheck(
        state=state,
        floor_paise=floor.min_hourly_paise if floor else None,
        effective_paise=effective_hourly_paise(
            hourly_paise=hourly_paise,
            visit_fee_paise=fee_paise,
            scheduled_minutes=scheduled_minutes,
            overhead_minutes=overhead_minutes,
        ),
        hourly_paise=hourly_paise,
        visit_fee_paise=fee_paise,
        scheduled_minutes=int(scheduled_minutes),
        overhead_minutes=int(overhead_minutes),
    )


def assert_compliant(*, allow_unknown: bool = True, **kwargs) -> FloorCheck:
    """Raise :class:`WageFloorViolation` if the terms pay below the floor.

    ``allow_unknown`` decides what a *missing* state figure means, and it
    defaults to permissive on purpose: refusing every engagement in a state
    nobody has entered a figure for would take the platform offline there, to
    protect workers from a number we do not have. The gap is ours to fill, and
    the console's report of unchecked states is how it gets noticed.

    Set it False where the stakes justify blocking — a bulk migration onto
    hourly terms, say, which is a deliberate act nobody is waiting on.
    """
    finding = check(**kwargs)
    if finding.is_known and not finding.is_compliant:
        raise WageFloorViolation(finding)
    if not finding.is_known and not allow_unknown:
        raise WageFloorViolation(finding)
    return finding


__all__ = [
    "FloorCheck",
    "WageFloorViolation",
    "assert_compliant",
    "check",
    "effective_hourly_paise",
]
