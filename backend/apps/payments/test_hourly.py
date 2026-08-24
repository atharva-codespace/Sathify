"""
Tests for the hourly billing engine.

The first class is the most important one in this file. ``F = R x T`` is the
claim that a visit fee removes the short-visit penalty entirely rather than
merely softening it, and that claim is the whole justification for charging a
resident ₹180 instead of ₹120 for an hour of work. If it is only approximately
true the design is a fudge, so it is asserted exactly, across the whole range of
job lengths the platform expects to see.
"""

import datetime as dt

import pytest
from django.utils import timezone

from apps.attendance.models import SessionStatus
from apps.payments.hourly import (
    BP_PER_UNIT,
    BillingConfig,
    SessionTiming,
    billable,
    calibrated_visit_fee,
    effective_hourly_paise,
    round_minutes,
    session_paise,
)

RATE_120 = 12_000  # ₹120/hour in paise


class _Session:
    """A WorkSession-shaped stub.

    The engine is duck-typed on purpose, so its arithmetic can be argued with
    here without a database, migrations or a society fixture in the way.
    """

    def __init__(self, *, day, start, end, approved_ot=0, status=SessionStatus.CLOSED):
        self.visit_date = day
        self.started_at = start
        self.ended_at = end
        self.approved_ot_minutes = approved_ot
        self.status = status


def _at(day: dt.date, hour: int, minute: int = 0) -> dt.datetime:
    return timezone.make_aware(dt.datetime.combine(day, dt.time(hour, minute)))


DAY = dt.date(2026, 8, 13)
TIMING = SessionTiming(
    arrival=dt.time(9, 0),
    departure=dt.time(12, 0),
    arrival_grace_minutes=10,
    departure_grace_minutes=10,
)
CONFIG = BillingConfig(round_step_minutes=15)


# ---------------------------------------------------------------------------
# The claim the visit fee rests on
# ---------------------------------------------------------------------------


class TestTheVisitFeeRemovesTheShortVisitPenalty:
    """``F = R x T`` makes her effective rate independent of job length."""

    @pytest.mark.parametrize("hours", [1, 2, 3, 4, 6, 8])
    def test_effective_rate_is_exactly_the_hourly_rate(self, hours):
        overhead = 30
        fee = calibrated_visit_fee(RATE_120, overhead)
        worked = hours * 60

        total = session_paise(worked, RATE_120) + fee
        effective = effective_hourly_paise(total, worked, overhead)

        assert effective == RATE_120, (
            f"a {hours}h job pays {effective} per committed hour, not {RATE_120}"
        )

    def test_the_fee_is_rate_times_overhead(self):
        # ₹120/hr x 30 min = ₹60. The number quoted throughout the PRD.
        assert calibrated_visit_fee(RATE_120, 30) == 6_000

    def test_without_the_fee_short_jobs_are_underpaid(self):
        """The failure this whole mechanism exists to prevent.

        Pinned as a test rather than left in prose: if somebody later "simplifies"
        the engine by dropping the visit fee, this is the assertion that explains
        what they broke.
        """
        overhead = 30
        one_hour = effective_hourly_paise(session_paise(60, RATE_120), 60, overhead)
        four_hours = effective_hourly_paise(session_paise(240, RATE_120), 240, overhead)

        assert one_hour < four_hours
        assert one_hour == 8_000  # ₹80/hr — a third less, at the same rate
        assert four_hours == 10_667

    def test_zero_overhead_means_no_fee(self):
        """A society whose staff live on site owes nothing for the journey."""
        assert calibrated_visit_fee(RATE_120, 0) == 0


# ---------------------------------------------------------------------------
# Primitives
# ---------------------------------------------------------------------------


class TestRounding:
    @pytest.mark.parametrize(
        "minutes,expected",
        [(0, 0), (7, 0), (8, 15), (166, 165), (172, 165), (173, 180), (180, 180)],
    )
    def test_nearest_is_half_up(self, minutes, expected):
        assert round_minutes(minutes, step=15) == expected

    @pytest.mark.parametrize("minutes,expected", [(1, 15), (15, 15), (16, 30), (166, 180)])
    def test_up_always_favours_the_worker(self, minutes, expected):
        assert round_minutes(minutes, step=15, up=True) == expected

    def test_nearest_is_the_default_because_up_hides_lateness(self):
        """Why `up` is not the default, stated as an assertion.

        Against a 180-minute schedule, a 14-minute late arrival leaves 166
        minutes worked. Rounding up returns the full 180 — the deduction
        vanishes entirely. Nearest keeps it visible at 165.
        """
        assert round_minutes(166, step=15, up=True) == 180  # deduction gone
        assert round_minutes(166, step=15) == 165  # deduction survives

    def test_a_zero_step_is_rejected(self):
        with pytest.raises(ValueError):
            round_minutes(60, step=0)


class TestSessionPaise:
    def test_whole_hours_are_exact(self):
        assert session_paise(60, RATE_120) == 12_000
        assert session_paise(165, RATE_120) == 33_000  # 2.75h x ₹120 = ₹330

    def test_division_rounds_half_up_not_down(self):
        """Flooring every fraction would shortchange the worker every time.

        ₹100/hr for 7 minutes is 11.666… paise per minute x 7 = 1166.67 paise.
        Floor gives 1166; half-up gives 1167. Small, but always in the same
        direction, which is what makes it corrosive.
        """
        assert session_paise(7, 10_000) == 1_167

    def test_overtime_multiplier_applies(self):
        assert session_paise(60, RATE_120, multiplier_bp=12_500) == 15_000  # 1.25x
        assert session_paise(60, RATE_120, multiplier_bp=BP_PER_UNIT) == 12_000

    def test_negative_minutes_cannot_produce_a_credit(self):
        assert session_paise(-30, RATE_120) == 0


# ---------------------------------------------------------------------------
# The nine rules
# ---------------------------------------------------------------------------


class TestTheRules:
    def test_rule_1_grace_absorbs_the_gate(self):
        """Arriving inside the grace window bills from the scheduled start."""
        session = _Session(day=DAY, start=_at(DAY, 9, 8), end=_at(DAY, 12, 0))
        result = billable(session, TIMING, CONFIG)
        assert result.regular_minutes == 180  # not 172

    def test_rule_2_past_grace_the_shortfall_is_the_deduction(self):
        """14 minutes late costs exactly the quarter hour not worked."""
        session = _Session(day=DAY, start=_at(DAY, 9, 14), end=_at(DAY, 12, 0))
        result = billable(session, TIMING, CONFIG)
        assert result.regular_minutes == 165

        full = session_paise(180, RATE_120)
        short = session_paise(result.regular_minutes, RATE_120)
        assert full - short == 3_000  # ₹30, and no penalty on top

    def test_rule_3_arriving_early_does_not_start_the_clock(self):
        session = _Session(day=DAY, start=_at(DAY, 8, 30), end=_at(DAY, 12, 0))
        result = billable(session, TIMING, CONFIG)
        assert result.regular_minutes == 180

    def test_rule_4_leaving_early_is_symmetric(self):
        session = _Session(day=DAY, start=_at(DAY, 9, 0), end=_at(DAY, 11, 30))
        result = billable(session, TIMING, CONFIG)
        assert result.regular_minutes == 150

    def test_rule_5_unapproved_overtime_is_recorded_but_not_billed(self):
        session = _Session(day=DAY, start=_at(DAY, 9, 0), end=_at(DAY, 12, 41))
        result = billable(session, TIMING, CONFIG)
        assert result.overtime_minutes == 0
        assert result.unbilled_extra_minutes == 41

    def test_rule_5_approved_overtime_is_billed_and_the_rest_is_not(self):
        session = _Session(day=DAY, start=_at(DAY, 9, 0), end=_at(DAY, 12, 41), approved_ot=30)
        result = billable(session, TIMING, CONFIG)
        assert result.overtime_minutes == 30
        assert result.unbilled_extra_minutes == 11

    def test_rule_6_regular_and_overtime_round_separately(self):
        """So a rounding gain on one cannot quietly offset a loss on the other."""
        session = _Session(day=DAY, start=_at(DAY, 9, 14), end=_at(DAY, 12, 22), approved_ot=22)
        result = billable(session, TIMING, CONFIG)
        assert result.regular_minutes == 165
        assert result.overtime_minutes == 15

    def test_rule_7_a_door_cancellation_owes_the_fee_and_no_hours(self):
        session = _Session(
            day=DAY, start=_at(DAY, 9, 0), end=_at(DAY, 9, 0),
            status=SessionStatus.CANCELLED_AT_DOOR,
        )
        result = billable(session, TIMING, CONFIG)
        assert result.regular_minutes == 0
        assert result.charge_visit_fee is True

    def test_rule_9_a_no_show_owes_nothing_at_all(self):
        session = _Session(day=DAY, start=None, end=None, status=SessionStatus.NO_SHOW)
        result = billable(session, TIMING, CONFIG)
        assert result.total_minutes == 0
        assert result.charge_visit_fee is False

    def test_lateness_never_reduces_the_visit_fee(self):
        """She travelled regardless. The hourly shortfall is the whole correction."""
        very_late = _Session(day=DAY, start=_at(DAY, 11, 30), end=_at(DAY, 12, 0))
        result = billable(very_late, TIMING, CONFIG)
        assert result.regular_minutes == 30
        assert result.charge_visit_fee is True


class TestTheWorkedExample:
    """The example carried through the PRD, asserted end to end.

    Sunita, ₹120/hr, ₹60 visit fee, scheduled 09:00-12:00 with 10 minutes'
    grace. Arrives 09:14, leaves 12:41, 30 minutes of overtime approved.
    """

    def test_it_comes_to_450_rupees(self):
        session = _Session(day=DAY, start=_at(DAY, 9, 14), end=_at(DAY, 12, 41), approved_ot=30)
        result = billable(session, TIMING, CONFIG)

        time_paise = session_paise(result.regular_minutes, RATE_120)
        ot_paise = session_paise(result.overtime_minutes, RATE_120)
        fee = calibrated_visit_fee(RATE_120, 30)

        assert (result.regular_minutes, result.overtime_minutes) == (165, 30)
        assert result.unbilled_extra_minutes == 11
        assert (time_paise, ot_paise, fee) == (33_000, 6_000, 6_000)
        assert time_paise + ot_paise + fee == 45_000  # ₹450.00

    def test_the_scheduled_day_would_have_been_420(self):
        """So the 14 minutes of lateness cost ₹30 — and only ₹30."""
        scheduled = session_paise(180, RATE_120) + calibrated_visit_fee(RATE_120, 30)
        assert scheduled == 42_000


class TestOvernightAndBoundaries:
    def test_a_slot_crossing_midnight_does_not_bill_backwards(self):
        """Departure before arrival means the next day, not a negative span."""
        timing = SessionTiming(
            arrival=dt.time(22, 0), departure=dt.time(1, 0),
            arrival_grace_minutes=10, departure_grace_minutes=10,
        )
        session = _Session(
            day=DAY,
            start=_at(DAY, 22, 0),
            end=_at(DAY + dt.timedelta(days=1), 1, 0),
        )
        result = billable(session, timing, CONFIG)
        assert result.regular_minutes == 180

    def test_an_open_session_bills_the_fee_and_no_hours_yet(self):
        session = _Session(day=DAY, start=_at(DAY, 9, 0), end=None, status=SessionStatus.OPEN)
        result = billable(session, TIMING, CONFIG)
        assert result.total_minutes == 0
        assert result.charge_visit_fee is True
