"""
The operational loop: nightly close, accrual, review, issue, resolve.

Where ``test_invoicing.py`` pins the models, this pins the sequence — the ten
steps a session actually travels from a tap in a stairwell to a row a Superadmin
reconciles three weeks later.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.utils import timezone

from apps.attendance.models import SessionSource, SessionStatus, WorkSession
from apps.hiring.models import Engagement, EngagementStatus, RateBasis
from apps.payments.invoicing import (
    accrue_session,
    close_period,
    close_stale_sessions,
    issue_after_review,
    mark_no_shows,
    resolve_query,
)
from apps.payments.models import InvoiceStatus, QueryStage, SessionQuery
from apps.scheduling.models import LeaveRequest, LeaveStatus, TaskTiming
from apps.societies.models import Flat, Resident, Tower
from apps.workers.models import ServiceType, WorkerProfile

pytestmark = pytest.mark.django_db

# A Thursday, so the weekday schedule applies.
DAY = dt.date(2026, 8, 13)
PERIOD = (DAY.replace(day=1), DAY.replace(day=28))


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
    service_type, _ = ServiceType.objects.get_or_create(name="Maid", slug="maid")
    eng = Engagement.objects.create(
        society=society, resident=resident, worker=worker, service_type=service_type,
        days_of_week=[0, 1, 2, 3, 4], start_time=dt.time(9, 0),
        expected_duration_minutes=180, monthly_rate=0,
        rate_basis=RateBasis.HOURLY, hourly_rate=120, visit_fee=60,
        status=EngagementStatus.ACTIVE, started_on=DAY - dt.timedelta(days=60),
    )
    TaskTiming.objects.create(
        engagement=eng,
        expected_arrival=dt.time(9, 0),
        expected_departure=dt.time(12, 0),
        arrival_grace_minutes=10,
        departure_grace_minutes=10,
    )
    return eng


def _at(day, hour, minute=0):
    return timezone.make_aware(dt.datetime.combine(day, dt.time(hour, minute)))


def _open_session(engagement, *, day=DAY, start=(9, 0)):
    return WorkSession.objects.create(
        society=engagement.society, engagement=engagement, worker=engagement.worker,
        visit_date=day, started_at=_at(day, *start), source=SessionSource.SELF,
        status=SessionStatus.OPEN,
    )


class TestTheNightlyClose:
    def test_a_forgotten_session_closes_at_the_scheduled_departure(self, engagement):
        """Never at the current time — that would bill until the job ran."""
        session = _open_session(engagement)

        closed = close_stale_sessions(now=_at(DAY, 23, 59))

        session.refresh_from_db()
        assert closed == 1
        assert session.status == SessionStatus.AUTO_CLOSED
        assert session.ended_at == _at(DAY, 12, 0)
        assert session.billable_minutes == 180
        assert session.needs_review is True

    def test_a_session_still_inside_its_grace_is_left_alone(self, engagement):
        """She may simply still be working. 90 minutes by default."""
        _open_session(engagement)
        assert close_stale_sessions(now=_at(DAY, 12, 30)) == 0

    def test_running_twice_changes_nothing(self, engagement):
        session = _open_session(engagement)
        assert close_stale_sessions(now=_at(DAY, 23, 59)) == 1
        assert close_stale_sessions(now=_at(DAY, 23, 59)) == 0

        session.refresh_from_db()
        assert session.total_paise == 36_000 + 6_000  # 3h at ₹120, plus the ₹60 fee


class TestNoShowsAreADifferentClaim:
    def test_a_scheduled_day_with_no_session_and_no_leave_is_a_no_show(self, engagement):
        created = mark_no_shows(day=DAY)

        session = WorkSession.objects.get(engagement=engagement, visit_date=DAY)
        assert created == 1
        assert session.status == SessionStatus.NO_SHOW
        # Flagged, not final: a capture failure must not silently cost her a day.
        assert session.needs_review is True

    def test_leave_is_never_a_no_show(self, engagement):
        LeaveRequest.objects.create(
            society=engagement.society, engagement=engagement, worker=engagement.worker,
            leave_date=DAY, status=LeaveStatus.APPROVED,
        )
        assert mark_no_shows(day=DAY) == 0

    def test_a_day_that_already_has_a_session_is_untouched(self, engagement):
        _open_session(engagement)
        assert mark_no_shows(day=DAY) == 0

    def test_an_unscheduled_day_is_not_an_absence(self, engagement):
        sunday = dt.date(2026, 8, 16)
        assert sunday.weekday() == 6
        assert mark_no_shows(day=sunday) == 0


class TestThePeriodRunsEndToEnd:
    def test_accrue_review_issue(self, engagement):
        session = _open_session(engagement)
        close_stale_sessions(now=_at(DAY, 23, 59))
        session.refresh_from_db()

        invoice = accrue_session(session, period_start=PERIOD[0], period_end=PERIOD[1])
        assert invoice.status == InvoiceStatus.DRAFT
        assert invoice.total_paise == 42_000

        close_period(invoice, hours=48)
        assert invoice.status == InvoiceStatus.REVIEW

        # Nothing is payable while the window is open.
        assert issue_after_review(invoice, now=timezone.now()) is None

        payment = issue_after_review(invoice, now=invoice.review_closes_at + dt.timedelta(hours=1))
        assert payment is not None
        assert payment.amount_paise == 42_000
        assert payment.due_at is not None
        assert invoice.status == InvoiceStatus.ISSUED

    def test_accruing_twice_does_not_double_the_bill(self, engagement):
        session = _open_session(engagement)
        close_stale_sessions(now=_at(DAY, 23, 59))
        session.refresh_from_db()

        accrue_session(session, period_start=PERIOD[0], period_end=PERIOD[1])
        invoice = accrue_session(session, period_start=PERIOD[0], period_end=PERIOD[1])
        assert invoice.total_paise == 42_000

    def test_a_late_session_cannot_slip_into_a_closed_bill(self, engagement):
        first = _open_session(engagement, day=DAY - dt.timedelta(days=1))
        close_stale_sessions(now=_at(DAY, 23, 59))
        first.refresh_from_db()
        invoice = accrue_session(first, period_start=PERIOD[0], period_end=PERIOD[1])
        close_period(invoice, hours=48)
        frozen_total = invoice.total_paise

        late = _open_session(engagement, day=DAY)
        close_stale_sessions(now=_at(DAY + dt.timedelta(days=1), 23, 59))
        late.refresh_from_db()
        accrue_session(late, period_start=PERIOD[0], period_end=PERIOD[1])

        invoice.refresh_from_db()
        assert invoice.total_paise == frozen_total


class TestResolvingAQuery:
    def _queried_invoice(self, engagement, raiser):
        session = _open_session(engagement)
        close_stale_sessions(now=_at(DAY, 23, 59))
        session.refresh_from_db()
        invoice = accrue_session(session, period_start=PERIOD[0], period_end=PERIOD[1])

        query = SessionQuery.objects.create(
            society=engagement.society, session=session, invoice=invoice,
            raised_by=raiser, reason="hours_disputed",
            description="She reached about 11:40, not 11:52.",
        )
        invoice.lines.filter(session=session).update(is_held=True)
        invoice.recalculate()
        return invoice, query

    def test_resolving_before_issue_simply_lifts_the_hold(self, engagement, resident_user):
        invoice, query = self._queried_invoice(engagement, resident_user)
        assert invoice.held_paise == 42_000
        assert invoice.payable_paise == 0

        resolve_query(query, resolution="The gate log agrees with her.", by=resident_user)

        invoice.refresh_from_db()
        assert invoice.held_paise == 0
        assert invoice.payable_paise == 42_000
        query.refresh_from_db()
        assert query.stage == QueryStage.RESOLVED

    def test_a_correction_after_issue_lands_on_the_next_invoice(self, engagement, resident_user):
        invoice, query = self._queried_invoice(engagement, resident_user)
        invoice.issue()
        issued_total = invoice.total_paise

        following = resolve_query(
            query, resolution="Twelve minutes credited.", by=resident_user,
            adjustment_paise=-2_400,
        )

        invoice.refresh_from_db()
        assert invoice.total_paise == issued_total  # history is never edited
        assert following is not None
        assert following.pk != invoice.pk
        assert following.adjustment_paise == -2_400
        assert following.lines.filter(query=query).exists()

    def test_resolving_a_closed_query_is_a_no_op(self, engagement, resident_user):
        _invoice, query = self._queried_invoice(engagement, resident_user)
        resolve_query(query, resolution="done", by=resident_user)
        assert resolve_query(query, resolution="again", by=resident_user) is None
