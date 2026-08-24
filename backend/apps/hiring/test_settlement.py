"""
Module 4.6 — the pro-rata owed before notice takes effect.

The arithmetic here decides somebody's final pay packet, so the denominator gets
its own tests. It is calendar days in the month, as specified — correct for a
helper who comes daily, and deliberately below a scheduled-days share for a
part-week one. That consequence has a test of its own rather than being left to
be discovered.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.attendance.models import AttendanceEvent, Decision, Direction
from apps.hiring.models import Engagement, EngagementStatus, weekday_of
from apps.hiring.settlement import (
    days_worked_in,
    outstanding_settlement,
    scheduled_days_in,
    settlement_due,
)
from apps.payments.models import Payment, PaymentKind, PaymentStatus
from apps.scheduling.models import LeaveRequest, LeaveStatus, TaskCompletion
from apps.societies.models import Flat, Resident, Tower
from apps.workers.models import ServiceType, WorkerProfile

pytestmark = pytest.mark.django_db


@pytest.fixture
def flat(society):
    tower = Tower.objects.create(society=society, name="A", floors=10)
    return Flat.objects.create(tower=tower, number="301", floor=3)


@pytest.fixture
def resident(resident_user, flat):
    return Resident.objects.create(user=resident_user, flat=flat, is_primary=True)


@pytest.fixture
def worker(worker_user):
    return WorkerProfile.objects.create(
        user=worker_user, photo="workers/x.jpg", is_available=True,
        trust_score=70, average_rating=4.4,
    )


@pytest.fixture
def engagement(society, resident, worker):
    """Every weekday, so "scheduled days" is predictable in any month."""
    service_type, _ = ServiceType.objects.get_or_create(name="Maid", slug="maid")
    return Engagement.objects.create(
        society=society,
        resident=resident,
        worker=worker,
        service_type=service_type,
        days_of_week=[0, 1, 2, 3, 4],
        start_time=dt.time(9, 0),
        expected_duration_minutes=60,
        monthly_rate=8000,
        status=EngagementStatus.ACTIVE,
        started_on=timezone.localdate().replace(day=1) - dt.timedelta(days=60),
    )


def _work(engagement, *dates, via="gate"):
    """Record days worked, through whichever record the test is exercising."""
    for day in dates:
        if via == "gate":
            AttendanceEvent.objects.create(
                society=engagement.society,
                worker=engagement.worker,
                engagement=engagement,
                direction=Direction.ENTRY,
                decision=Decision.ALLOWED,
                occurred_at=timezone.make_aware(
                    dt.datetime.combine(day, dt.time(9, 5))
                ),
            )
        else:
            TaskCompletion.objects.create(
                society=engagement.society,
                engagement=engagement,
                worker=engagement.worker,
                visit_date=day,
                completed_at=timezone.now(),
            )


def _month_end() -> dt.date:
    """Last day of the current month.

    Used rather than a hard-coded 28th: February would pass either way, but a
    31-day month has weekdays past the 28th, and counting the numerator over a
    shorter window than the denominator quietly understates what was worked.
    """
    import calendar

    today = timezone.localdate()
    return today.replace(day=calendar.monthrange(today.year, today.month)[1])


def _every_day_this_month(count: int) -> list[dt.date]:
    """The first ``count`` calendar days of the current month.

    The denominator is calendar days, so the "full month" case needs a helper
    that does not skip weekends.
    """
    first = timezone.localdate().replace(day=1)
    return [first + dt.timedelta(days=offset) for offset in range(count)]


def _weekdays_this_month(count: int) -> list[dt.date]:
    """The first ``count`` weekdays of the current month."""
    today = timezone.localdate()
    day = today.replace(day=1)
    out: list[dt.date] = []
    while len(out) < count:
        if day.weekday() < 5:
            out.append(day)
        day += dt.timedelta(days=1)
    return out


def _weekdays_so_far() -> list[dt.date]:
    """Weekdays from the 1st up to and including today.

    The presumption window. Computed rather than hard-coded because these tests
    must give the same answer whichever day of the month they are run on — a
    suite that passes on the 3rd and fails on the 27th is worse than no suite.
    """
    today = timezone.localdate()
    day = today.replace(day=1)
    out: list[dt.date] = []
    while day <= today:
        if day.weekday() < 5:
            out.append(day)
        day += dt.timedelta(days=1)
    return out


def _take_leave(engagement, *dates, status=LeaveStatus.APPROVED):
    """Put leave on record for these dates — the way a day stops counting."""
    for day in dates:
        LeaveRequest.objects.create(
            society=engagement.society,
            engagement=engagement,
            worker=engagement.worker,
            leave_date=day,
            status=status,
        )


class TestTheDenominator:
    """``days_worked / calendar_days_in_month * monthly_rate``, as specified.

    The denominator is calendar days — 31 in August — not the number of visits
    the terms called for. That is the right arithmetic for a helper who comes
    every day, and it under-pays a part-week helper; the last test here pins
    that consequence rather than hiding it, so the behaviour is deliberate and
    visible if the policy is ever revisited.
    """

    # ``on=`` is passed on every call below, and that is the point rather than
    # boilerplate.
    #
    # `worked_days_in` presumes a scheduled day was worked when no record
    # contradicts it, bounded by `min(month_end, today)` — deliberate behaviour,
    # documented in `settlement.py`, and what makes a mid-month payslip honest.
    # A test that works the first N days and then asks for the settlement *as of
    # the real today* is therefore asserting on N worked days plus however many
    # scheduled days happen to sit between N and whenever the suite is run.
    #
    # That made two of these three tests pass only on certain dates: the
    # ten-day case failed from the 11th of any month, and the half-month case
    # from the 16th. Pinning the date the production function already accepts
    # removes the dependency without weakening a single assertion — each test
    # now asserts exactly what its name claims, on every day of the year.

    def test_a_full_calendar_month_worked_pays_the_full_rate(self, engagement):
        """Every day of the month worked settles the whole month."""
        days_in_month = _month_end().day
        worked = _every_day_this_month(days_in_month)
        _work(engagement, *worked)

        breakdown = settlement_due(engagement, on=worked[-1])

        assert breakdown.days_worked == days_in_month
        assert breakdown.days_in_month == days_in_month
        assert breakdown.amount_paise == breakdown.monthly_rate_paise

    def test_half_the_month_pays_half_the_rate(self, engagement):
        days_in_month = _month_end().day
        half = days_in_month // 2
        worked = _every_day_this_month(half)
        _work(engagement, *worked)

        breakdown = settlement_due(engagement, on=worked[-1])

        assert breakdown.days_worked == half
        assert breakdown.amount_paise == (
            breakdown.monthly_rate_paise * half // days_in_month
        )

    def test_the_denominator_is_calendar_days(self, engagement):
        """Stated as its own assertion because it is the rule.

        ``scheduled_days`` is still reported — it is what makes the figure
        legible in the breakdown — but it divides nothing.
        """
        worked = _every_day_this_month(10)
        _work(engagement, *worked)
        breakdown = settlement_due(engagement, on=worked[-1])

        assert breakdown.days_worked == 10
        assert breakdown.days_in_month == _month_end().day
        assert breakdown.amount_paise == (
            breakdown.monthly_rate_paise * 10 // breakdown.days_in_month
        )

    def test_presumption_still_fills_the_gap_to_today(self, engagement):
        """The behaviour the three tests above deliberately pin away from.

        Without this, pinning ``on=`` would look like a way of hiding the
        presumption rather than isolating it — and the next person to read the
        class would have no way to tell which it was.
        """
        worked = _every_day_this_month(10)
        _work(engagement, *worked)

        later = worked[-1] + dt.timedelta(days=4)
        breakdown = settlement_due(engagement, on=later)

        assert breakdown.attended_days == 10
        assert breakdown.presumed_days > 0
        assert breakdown.days_worked > 10
        # Present, and different — the two are not interchangeable.
        assert breakdown.scheduled_days != breakdown.days_in_month

    def test_a_part_week_helper_settles_below_her_scheduled_share(
        self, engagement
    ):
        """The consequence of the specified formula, pinned deliberately.

        A weekday-only helper works about 22 of a 31-day month. Dividing by
        calendar days settles her at roughly 22/31 rather than 22/22, so a month
        in which she missed nothing pays about 71% of her rate. That is what the
        formula says; this test exists so nobody discovers it by accident.
        """
        scheduled = settlement_due(engagement).scheduled_days
        _work(engagement, *_weekdays_this_month(scheduled))

        breakdown = settlement_due(engagement)
        full_month = breakdown.monthly_rate_paise

        assert breakdown.days_worked == scheduled
        assert breakdown.amount_paise < full_month
        assert breakdown.amount_paise == full_month * scheduled // breakdown.days_in_month

    def test_a_month_with_no_records_at_all_still_owes_the_scheduled_days(
        self, engagement
    ):
        """The bug this feature was reported for.

        A gate event is only linked to an engagement when the scan lands inside
        the visit window, so a helper who came every day can easily have no
        linked record at all. Settling her at zero on that basis is wage theft
        dressed up as arithmetic — the days her terms called for count unless
        leave says she was away.
        """
        breakdown = settlement_due(engagement)

        assert breakdown.days_worked == len(_weekdays_so_far())
        assert breakdown.presumed_days == breakdown.days_worked
        assert breakdown.attended_days == 0
        assert breakdown.amount_paise > 0

    def test_leave_on_every_scheduled_day_owes_nothing(self, engagement):
        """The way a day is taken off the bill is to record the absence."""
        _take_leave(engagement, *_weekdays_so_far())

        breakdown = settlement_due(engagement)

        assert breakdown.days_worked == 0
        assert breakdown.amount_paise == 0

    def test_more_days_than_the_month_has_is_capped(self, engagement):
        """A data oddity is not a pay rise."""
        breakdown = settlement_due(engagement)
        assert breakdown.amount_paise <= breakdown.monthly_rate_paise

    def test_an_engagement_with_no_days_still_settles_what_was_worked(
        self, engagement
    ):
        """The denominator no longer depends on the terms, so an engagement with
        no weekdays set still owes for days somebody actually turned up."""
        engagement.days_of_week = []
        engagement.save(update_fields=["days_of_week"])
        _work(engagement, *_every_day_this_month(5))

        breakdown = settlement_due(engagement)

        assert breakdown.scheduled_days == 0
        assert breakdown.amount_paise > 0


class TestWhatCountsAsADayWorked:
    def test_a_gate_entry_counts(self, engagement):
        _work(engagement, *_weekdays_this_month(2), via="gate")

        total, attended, completed = days_worked_in(
            engagement,
            start=timezone.localdate().replace(day=1),
            end=_month_end(),
        )
        assert (total, attended, completed) == (2, 2, 0)

    def test_a_completion_mark_counts_on_its_own(self, engagement):
        """A society with no guard on duty logs no entry. That is not her fault
        and must not cost her the day."""
        _work(engagement, *_weekdays_this_month(3), via="completion")

        total, attended, completed = days_worked_in(
            engagement,
            start=timezone.localdate().replace(day=1),
            end=_month_end(),
        )
        assert (total, attended, completed) == (3, 0, 3)

    def test_both_records_on_one_day_count_once(self, engagement):
        days = _weekdays_this_month(2)
        _work(engagement, *days, via="gate")
        _work(engagement, *days, via="completion")

        total, _, _ = days_worked_in(
            engagement,
            start=timezone.localdate().replace(day=1),
            end=_month_end(),
        )
        assert total == 2

    def test_passing_the_gate_twice_in_a_day_is_one_day(self, engagement):
        day = _weekdays_this_month(1)[0]
        _work(engagement, day)
        _work(engagement, day)

        total, _, _ = days_worked_in(
            engagement,
            start=timezone.localdate().replace(day=1),
            end=_month_end(),
        )
        assert total == 1

    def test_a_denied_gate_entry_is_not_attendance(self, engagement):
        AttendanceEvent.objects.create(
            society=engagement.society,
            worker=engagement.worker,
            engagement=engagement,
            direction=Direction.ENTRY,
            decision=Decision.DENIED,
            occurred_at=timezone.now(),
        )

        total, _, _ = days_worked_in(
            engagement,
            start=timezone.localdate().replace(day=1),
            end=_month_end(),
        )
        assert total == 0

    def test_another_engagements_work_does_not_count(
        self, engagement, society, resident, worker
    ):
        """She may work three households. Ending one settles that one only."""
        other_type, _ = ServiceType.objects.get_or_create(name="Cook", slug="cook")
        other = Engagement.objects.create(
            society=society, resident=resident, worker=worker,
            service_type=other_type, days_of_week=[0, 1, 2, 3, 4],
            start_time=dt.time(18, 0), expected_duration_minutes=60,
            monthly_rate=5000, status=EngagementStatus.ACTIVE,
        )
        _work(other, *_weekdays_this_month(4))

        # Records are what must not leak. Both engagements are scheduled on the
        # same weekdays, so both presume the same days independently — that is
        # correct, and it is why this asserts on the recorded view rather than
        # on the settled total.
        total, attended, completed = days_worked_in(
            engagement,
            start=timezone.localdate().replace(day=1),
            end=_month_end(),
        )
        assert (total, attended, completed) == (0, 0, 0)


class TestTheScheduleCountsAsEvidence:
    """A scheduled day is presumed worked; recorded leave is what removes it.

    This is the half of the rule that fixes the reported bug. Attendance rows
    are attached to an engagement only when the scan lands inside the visit
    window, so "count the linked records" settles most helpers at zero.
    """

    def test_an_unlinked_gate_entry_on_a_scheduled_day_counts(self, engagement):
        """The exact shape of the production data that settled at ₹0.

        A real arrival, allowed through the gate, with ``engagement_id`` NULL
        because the scan was hours outside the expected window.
        """
        day = _weekdays_so_far()[0]
        AttendanceEvent.objects.create(
            society=engagement.society,
            worker=engagement.worker,
            engagement=None,
            direction=Direction.ENTRY,
            decision=Decision.ALLOWED,
            occurred_at=timezone.make_aware(dt.datetime.combine(day, dt.time(23, 30))),
        )

        _, attended, _ = days_worked_in(
            engagement,
            start=timezone.localdate().replace(day=1),
            end=_month_end(),
        )
        assert attended == 1

    def test_an_unlinked_entry_on_an_unscheduled_day_is_not_attributed(
        self, engagement
    ):
        """A Sunday entry belongs to somebody else's arrangement, not this one."""
        first = timezone.localdate().replace(day=1)
        sunday = next(
            first + dt.timedelta(days=offset)
            for offset in range(31)
            if (first + dt.timedelta(days=offset)).weekday() == 6
        )
        AttendanceEvent.objects.create(
            society=engagement.society,
            worker=engagement.worker,
            engagement=None,
            direction=Direction.ENTRY,
            decision=Decision.ALLOWED,
            occurred_at=timezone.make_aware(dt.datetime.combine(sunday, dt.time(9, 5))),
        )

        _, attended, _ = days_worked_in(
            engagement,
            start=timezone.localdate().replace(day=1),
            end=_month_end(),
        )
        assert attended == 0

    def test_a_gate_entry_overrides_recorded_leave(self, engagement):
        """She took the day off on paper and came anyway. She is owed for it."""
        day = _weekdays_so_far()[0]
        _take_leave(engagement, day)
        _work(engagement, day)

        breakdown = settlement_due(engagement)

        assert day in {*_weekdays_so_far()}
        assert breakdown.days_worked == len(_weekdays_so_far())

    def test_withdrawn_leave_puts_the_day_back(self, engagement):
        """Withdrawing leave means she came after all."""
        days = _weekdays_so_far()
        _take_leave(engagement, *days, status=LeaveStatus.WITHDRAWN)

        assert settlement_due(engagement).days_worked == len(days)

    def test_nothing_is_presumed_after_today(self, engagement):
        """The month's remaining weekdays have not happened yet."""
        breakdown = settlement_due(engagement)

        assert breakdown.days_worked == len(_weekdays_so_far())
        assert breakdown.days_worked < breakdown.scheduled_days

    def test_nothing_is_presumed_while_paused(self, engagement):
        """Otherwise pausing an engagement would quietly bill for the rest of
        the month, which would make "pause" a charge."""
        engagement.pause(reason="Away for a fortnight")
        engagement.paused_at = timezone.now() - dt.timedelta(days=400)
        engagement.save(update_fields=["paused_at"])

        assert settlement_due(engagement).days_worked == 0

    def test_nothing_is_presumed_before_the_engagement_started(
        self, engagement
    ):
        """A hire made today owes nothing for the first three weeks of the month."""
        engagement.started_on = timezone.localdate()
        engagement.save(update_fields=["started_on"])

        breakdown = settlement_due(engagement)

        assert breakdown.days_worked <= 1


class TestOutstanding:
    def test_a_salary_already_paid_this_month_clears_it(self, engagement):
        """A household that pays on the 1st and gives notice on the 20th has
        already handed over the money. Asking again charges twice."""
        _work(engagement, *_weekdays_this_month(5))
        breakdown = settlement_due(engagement)
        assert breakdown.amount_paise > 0

        Payment.objects.create(
            society=engagement.society,
            resident=engagement.resident,
            worker=engagement.worker,
            engagement=engagement,
            kind=PaymentKind.ENGAGEMENT_SALARY,
            amount_paise=breakdown.amount_paise,
            status=PaymentStatus.PAID,
            paid_at=timezone.now(),
        )

        assert outstanding_settlement(engagement) is None

    def test_an_unpaid_month_is_outstanding(self, engagement):
        _work(engagement, *_weekdays_this_month(5))
        assert outstanding_settlement(engagement) is not None

    def test_an_unsettled_payment_row_does_not_clear_it(self, engagement):
        """Opening a payment is not paying it."""
        _work(engagement, *_weekdays_this_month(5))
        Payment.objects.create(
            society=engagement.society,
            resident=engagement.resident,
            worker=engagement.worker,
            engagement=engagement,
            kind=PaymentKind.NOTICE_SETTLEMENT,
            amount_paise=settlement_due(engagement).amount_paise,
            status=PaymentStatus.PENDING,
        )

        assert outstanding_settlement(engagement) is not None


class TestNoticeIsGated:
    def url(self, engagement):
        return reverse("v1:hiring:engagement-notice", args=[engagement.pk])

    def settlement_url(self, engagement):
        return reverse("v1:hiring:engagement-settlement", args=[engagement.pk])

    def test_a_resident_cannot_give_notice_with_wages_outstanding(
        self, authenticated_client, resident_user, engagement
    ):
        _work(engagement, *_weekdays_this_month(5))

        response = authenticated_client(resident_user).post(
            self.url(engagement), {"reason": "resident_ended"}, format="json"
        )

        assert response.status_code == 409
        assert response.data["error"]["code"] == "dues_outstanding"
        # The breakdown travels with the refusal so the app can show it without
        # a second round trip.
        assert response.data["error"]["details"]["days_worked"] == len(
            _weekdays_so_far()
        )
        engagement.refresh_from_db()
        assert engagement.last_working_day is None

    def test_paying_the_settlement_unblocks_notice(
        self, authenticated_client, resident_user, engagement
    ):
        _work(engagement, *_weekdays_this_month(5))
        client = authenticated_client(resident_user)

        opened = client.post(self.settlement_url(engagement))
        assert opened.status_code == 201, opened.data

        payment = Payment.objects.get(pk=opened.data["payment"]["id"])
        payment.mark_paid(razorpay_payment_id="pay_test", signature="test")

        given = client.post(
            self.url(engagement), {"reason": "resident_ended"}, format="json"
        )

        assert given.status_code == 200, given.data
        engagement.refresh_from_db()
        assert engagement.last_working_day is not None

    def test_a_worker_may_resign_with_wages_outstanding(
        self, authenticated_client, worker_user, engagement
    ):
        """One-sided on purpose.

        Making it expensive or difficult to leave does not produce notice, it
        produces somebody who stops turning up — the exact harm the notice
        period exists to prevent. She is still owed the money.
        """
        _work(engagement, *_weekdays_this_month(5))

        response = authenticated_client(worker_user).post(
            self.url(engagement), {"reason": "worker_ended"}, format="json"
        )

        assert response.status_code == 200, response.data
        engagement.refresh_from_db()
        assert engagement.last_working_day is not None
        # And the debt is unchanged by her having resigned.
        assert outstanding_settlement(engagement) is not None

    def test_notice_is_not_blocked_when_nothing_is_owed(
        self, authenticated_client, resident_user, engagement
    ):
        """Leave on every scheduled day leaves nothing to settle."""
        _take_leave(engagement, *_weekdays_so_far())

        response = authenticated_client(resident_user).post(
            self.url(engagement), {"reason": "resident_ended"}, format="json"
        )
        assert response.status_code == 200, response.data

    def test_the_breakdown_endpoint_shows_its_working(
        self, authenticated_client, resident_user, engagement
    ):
        _work(engagement, *_weekdays_this_month(4))

        response = authenticated_client(resident_user).get(
            self.settlement_url(engagement)
        )

        assert response.status_code == 200
        body = response.data
        assert body["days_worked"] == len(_weekdays_so_far())
        assert body["scheduled_days"] > 0
        assert body["monthly_rate_paise"] == 8000_00
        assert body["blocks_notice"] is True
        # Both are present, and they are different numbers. That difference is
        # the bug this feature was corrected for.
        assert body["days_in_month"] != body["scheduled_days"]

    def test_opening_the_settlement_twice_resumes_one_row(
        self, authenticated_client, resident_user, engagement
    ):
        _work(engagement, *_weekdays_this_month(3))
        client = authenticated_client(resident_user)

        first = client.post(self.settlement_url(engagement))
        second = client.post(self.settlement_url(engagement))

        assert first.status_code == 201
        assert second.status_code == 200
        assert first.data["payment"]["id"] == second.data["payment"]["id"]
        assert Payment.objects.filter(
            engagement=engagement, kind=PaymentKind.NOTICE_SETTLEMENT
        ).count() == 1

    def test_a_worker_cannot_open_the_settlement(
        self, authenticated_client, worker_user, engagement
    ):
        _work(engagement, *_weekdays_this_month(3))

        response = authenticated_client(worker_user).post(
            self.settlement_url(engagement)
        )
        assert response.status_code == 403

    def test_the_settlement_carries_no_platform_fee(
        self, authenticated_client, resident_user, engagement
    ):
        """A fee on somebody's final pay packet is indefensible."""
        _work(engagement, *_weekdays_this_month(3))

        opened = authenticated_client(resident_user).post(
            self.settlement_url(engagement)
        )
        payment = Payment.objects.get(pk=opened.data["payment"]["id"])
        assert payment.platform_fee_paise == 0
