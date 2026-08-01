"""
Module 5.4 — tests for the notice and cancellation policy.

Database-free, matching ``policy.py`` itself. These rules decide what someone is
charged, so they get an executable specification independent of any view: a
change in fee behaviour should fail here, loudly, rather than surface as a
surprise on someone's bill.
"""

from __future__ import annotations

import pytest

from apps.bookings.policy import (
    FREE_CANCELLATION_HOURS,
    FULL_FEE_HOURS,
    PARTIAL_FEE_RATE,
    CancellationPolicy,
    cancellation_outcome,
    check_notice_period,
)

PRICE = 2000


class TestCancellationTiers:
    def test_well_ahead_is_free(self):
        outcome = cancellation_outcome(hours_until_start=48, quoted_price=PRICE)

        assert outcome.fee == 0
        assert outcome.tier == "free"
        assert outcome.is_free

    def test_exactly_at_the_free_threshold_is_still_free(self):
        """The boundary belongs to the generous side."""
        outcome = cancellation_outcome(
            hours_until_start=FREE_CANCELLATION_HOURS, quoted_price=PRICE
        )
        assert outcome.fee == 0

    def test_just_inside_the_window_is_partial(self):
        outcome = cancellation_outcome(
            hours_until_start=FREE_CANCELLATION_HOURS - 0.1, quoted_price=PRICE
        )

        assert outcome.tier == "partial"
        assert outcome.fee == int(PRICE * PARTIAL_FEE_RATE)

    def test_exactly_at_the_full_fee_threshold_is_still_partial(self):
        outcome = cancellation_outcome(
            hours_until_start=FULL_FEE_HOURS, quoted_price=PRICE
        )
        assert outcome.tier == "partial"

    def test_close_to_the_start_is_the_full_price(self):
        outcome = cancellation_outcome(hours_until_start=0.5, quoted_price=PRICE)

        assert outcome.tier == "full"
        assert outcome.fee == PRICE

    def test_after_the_start_time_is_the_full_price(self):
        """The case the policy most needs to cover: an effective no-show."""
        outcome = cancellation_outcome(hours_until_start=-3, quoted_price=PRICE)

        assert outcome.tier == "full"
        assert outcome.fee == PRICE
        assert "after the start time" in outcome.rationale

    def test_fee_never_exceeds_the_quoted_price(self):
        for hours in (-10, 0, 1, 3, 5.9, 6, 100):
            outcome = cancellation_outcome(hours_until_start=hours, quoted_price=PRICE)
            assert 0 <= outcome.fee <= PRICE

    def test_rounds_down_so_the_split_favours_the_person_charged(self):
        outcome = cancellation_outcome(hours_until_start=3, quoted_price=999)
        assert outcome.fee == 499  # not 500

    def test_a_zero_price_booking_cannot_produce_a_fee(self):
        outcome = cancellation_outcome(hours_until_start=0, quoted_price=0)
        assert outcome.fee == 0

    def test_every_outcome_explains_itself(self):
        """The fee is shown to a person; a bare number invites a dispute."""
        for hours in (-1, 1, 4, 24):
            outcome = cancellation_outcome(hours_until_start=hours, quoted_price=PRICE)
            assert outcome.rationale.strip()

    def test_thresholds_are_overridable(self):
        lenient = CancellationPolicy(free_hours=1, full_fee_hours=0.5, partial_rate=0.1)
        outcome = cancellation_outcome(
            hours_until_start=2, quoted_price=PRICE, policy=lenient
        )
        assert outcome.fee == 0


class TestNoticePeriod:
    def test_enough_notice_is_allowed(self):
        assert check_notice_period(hours_until_start=24, notice_hours=12).allowed

    def test_exactly_the_required_notice_is_allowed(self):
        assert check_notice_period(hours_until_start=12, notice_hours=12).allowed

    def test_too_little_notice_is_refused_with_a_reason(self):
        check = check_notice_period(hours_until_start=3, notice_hours=12)

        assert not check.allowed
        assert check.required_hours == 12
        assert "12 hours" in check.reason

    def test_the_past_is_always_refused(self):
        check = check_notice_period(hours_until_start=-1, notice_hours=12)

        assert not check.allowed
        assert "past" in check.reason

    def test_emergency_categories_bypass_the_window(self):
        check = check_notice_period(
            hours_until_start=0.5, notice_hours=12, bypasses_notice=True
        )
        assert check.allowed

    def test_emergency_still_cannot_be_booked_in_the_past(self):
        """Bypassing the notice window is not a licence to book backwards."""
        check = check_notice_period(
            hours_until_start=-2, notice_hours=12, bypasses_notice=True
        )
        assert not check.allowed
