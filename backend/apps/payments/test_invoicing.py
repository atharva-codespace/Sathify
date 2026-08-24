"""
Work sessions, invoices, and the rule that keeps a query from freezing a wage.

The class that matters most here is :class:`TestAQueryDoesNotFreezeTheMonth`. A
worker who believes raising a query risks her whole month's pay will never raise
one, and a dispute channel nobody uses is worse than none at all — it produces
a record that looks unchallenged because challenging it was too expensive.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.db import IntegrityError
from django.utils import timezone

from apps.attendance.models import (
    SOURCE_TIER,
    SessionSource,
    SessionStatus,
    WorkSession,
)
from apps.hiring.models import Engagement, EngagementStatus, RateBasis
from apps.payments.hourly import BillingConfig, SessionTiming, price_session
from apps.payments.models import (
    Invoice,
    InvoiceLineKind,
    InvoiceStatus,
    PaymentKind,
    PaymentStatus,
    QueryStage,
    SessionQuery,
    WageFloor,
    rupees_to_paise,
)
from apps.payments.services import daily_rate_paise
from apps.societies.models import Flat, Resident, SocietyBillingConfig, Tower, VisitFeePolicy
from apps.workers.models import ServiceType, WorkerProfile

pytestmark = pytest.mark.django_db

DAY = dt.date(2026, 8, 13)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


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
def hourly_engagement(society, resident, worker):
    """₹120/hour with the calibrated ₹60 visit fee, 09:00-12:00 on weekdays."""
    service_type, _ = ServiceType.objects.get_or_create(name="Maid", slug="maid")
    return Engagement.objects.create(
        society=society,
        resident=resident,
        worker=worker,
        service_type=service_type,
        days_of_week=[0, 1, 2, 3, 4],
        start_time=dt.time(9, 0),
        expected_duration_minutes=180,
        monthly_rate=0,
        rate_basis=RateBasis.HOURLY,
        hourly_rate=120,
        visit_fee=60,
        status=EngagementStatus.ACTIVE,
        started_on=DAY - dt.timedelta(days=60),
    )


@pytest.fixture
def monthly_engagement(society, resident, worker):
    service_type, _ = ServiceType.objects.get_or_create(name="Maid", slug="maid")
    return Engagement.objects.create(
        society=society, resident=resident, worker=worker, service_type=service_type,
        days_of_week=[0, 1, 2, 3, 4], start_time=dt.time(9, 0),
        expected_duration_minutes=180, monthly_rate=8000,
        status=EngagementStatus.ACTIVE, started_on=DAY - dt.timedelta(days=60),
    )


def _at(day, hour, minute=0):
    return timezone.make_aware(dt.datetime.combine(day, dt.time(hour, minute)))


def _session(engagement, *, day=DAY, start=(9, 0), end=(12, 0), source=SessionSource.SELF, **kw):
    return WorkSession.objects.create(
        society=engagement.society,
        engagement=engagement,
        worker=engagement.worker,
        visit_date=day,
        started_at=_at(day, *start) if start else None,
        ended_at=_at(day, *end) if end else None,
        source=source,
        status=kw.pop("status", SessionStatus.CLOSED),
        **kw,
    )


TIMING = SessionTiming(
    arrival=dt.time(9, 0), departure=dt.time(12, 0),
    arrival_grace_minutes=10, departure_grace_minutes=10,
)


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------


class TestWorkSession:
    def test_one_session_per_engagement_per_day(self, hourly_engagement):
        """Two capture tiers must not open the same day from opposite ends.

        Her phone in the stairwell and the resident's scan at the door are both
        legitimate, arrive independently, and would otherwise bill twice.
        """
        _session(hourly_engagement)
        with pytest.raises(IntegrityError):
            _session(hourly_engagement, source=SessionSource.RESIDENT_SCAN)

    @pytest.mark.parametrize(
        "source,tier,trusted",
        [
            (SessionSource.SELF, 1, True),
            (SessionSource.RESIDENT_SCAN, 2, True),
            (SessionSource.RESIDENT_CONFIRM, 3, False),
            (SessionSource.DERIVED, 4, False),
            (SessionSource.MANUAL, 5, False),
        ],
    )
    def test_capture_tiers(self, hourly_engagement, source, tier, trusted):
        session = _session(hourly_engagement, source=source)
        assert session.tier == tier
        assert session.is_trusted_capture is trusted
        assert SOURCE_TIER[source] == tier

    def test_auto_close_flags_for_review_and_never_bills_open_ended(self, hourly_engagement):
        """Forgetting to stop is the commonest failure. It must not bill to midnight."""
        session = _session(hourly_engagement, end=None, status=SessionStatus.OPEN)

        closed = session.close(at=_at(DAY, 12, 0), auto=True)

        assert closed is True
        assert session.status == SessionStatus.AUTO_CLOSED
        assert session.needs_review is True
        assert session.review_note

        price_session(session, timing=TIMING, config=BillingConfig())
        # Scheduled hours, not the time until whenever the job happened to run.
        assert session.billable_minutes == 180

    def test_close_is_idempotent(self, hourly_engagement):
        session = _session(hourly_engagement, end=None, status=SessionStatus.OPEN)
        assert session.close(at=_at(DAY, 12, 0)) is True
        assert session.close(at=_at(DAY, 13, 0)) is False

    def test_a_no_show_is_not_billable_but_a_door_cancellation_is(self, hourly_engagement):
        _session(hourly_engagement, day=DAY, status=SessionStatus.NO_SHOW, start=None, end=None)
        _session(
            hourly_engagement, day=DAY - dt.timedelta(days=1),
            status=SessionStatus.CANCELLED_AT_DOOR,
        )
        assert WorkSession.objects.billable().count() == 1


class TestPricingIsFrozen:
    def test_pricing_writes_the_arithmetic_onto_the_session(self, hourly_engagement):
        session = _session(hourly_engagement, start=(9, 14), end=(12, 41), approved_ot_minutes=30)

        price_session(session, timing=TIMING, config=BillingConfig())

        assert session.billable_minutes == 165
        assert session.overtime_minutes == 30
        assert session.unbilled_extra_minutes == 11
        assert session.time_paise == 33_000
        assert session.overtime_paise == 6_000
        assert session.visit_fee_paise == 6_000
        assert session.total_paise == 45_000
        assert session.priced_at is not None

    def test_pricing_twice_does_not_double_charge(self, hourly_engagement):
        """Replaying a sync, or re-running the nightly close, must be safe."""
        session = _session(hourly_engagement)
        price_session(session, timing=TIMING, config=BillingConfig())
        first = session.total_paise

        price_session(session, timing=TIMING, config=BillingConfig())
        assert session.total_paise == first

    def test_a_config_change_does_not_rewrite_a_priced_session(self, hourly_engagement):
        """January's wage must not change because March's committee did."""
        session = _session(hourly_engagement, start=(9, 14), end=(12, 0))
        price_session(session, timing=TIMING, config=BillingConfig(round_step_minutes=15))
        original = session.time_paise

        price_session(session, timing=TIMING, config=BillingConfig(round_step_minutes=60))
        assert session.time_paise == original

    def test_monthly_engagements_keep_sessions_but_are_not_priced_here(
        self, monthly_engagement
    ):
        """Phase 2 ships sessions on monthly terms, where they move no money."""
        session = _session(monthly_engagement)
        price_session(session, timing=TIMING, config=BillingConfig())
        assert session.priced_at is None
        assert session.total_paise == 0


# ---------------------------------------------------------------------------
# Invoices
# ---------------------------------------------------------------------------


def _invoice(engagement, sessions=()):
    invoice = Invoice.objects.create(
        society=engagement.society,
        engagement=engagement,
        resident=engagement.resident,
        worker=engagement.worker,
        period_start=DAY.replace(day=1),
        period_end=DAY,
    )
    for session in sessions:
        price_session(session, timing=TIMING, config=BillingConfig())
        invoice.add_session(session)
    return invoice


class TestInvoice:
    def test_totals_are_derived_from_the_lines(self, hourly_engagement):
        session = _session(hourly_engagement, start=(9, 14), end=(12, 41), approved_ot_minutes=30)
        invoice = _invoice(hourly_engagement, [session])

        assert invoice.time_paise == 33_000
        assert invoice.overtime_paise == 6_000
        assert invoice.visit_fee_paise == 6_000
        assert invoice.total_paise == 45_000
        assert invoice.lines.count() == 3

    def test_adding_the_same_session_twice_is_a_no_op(self, hourly_engagement):
        session = _session(hourly_engagement)
        invoice = _invoice(hourly_engagement, [session])
        before = invoice.total_paise

        invoice.add_session(session)
        invoice.recalculate()
        assert invoice.total_paise == before

    def test_the_visit_fee_appears_as_its_own_line(self, hourly_engagement):
        """Never folded into an hourly figure — the resident must see what it is."""
        session = _session(hourly_engagement)
        invoice = _invoice(hourly_engagement, [session])

        fee_lines = invoice.lines.filter(kind=InvoiceLineKind.VISIT_FEE)
        assert fee_lines.count() == 1
        assert fee_lines.first().amount_paise == 6_000

    def test_issuing_creates_one_payment_carrying_the_whole_settlement_path(
        self, hourly_engagement
    ):
        session = _session(hourly_engagement)
        invoice = _invoice(hourly_engagement, [session])

        payment = invoice.issue()

        assert payment is not None
        assert payment.kind == PaymentKind.ENGAGEMENT_SALARY
        assert payment.amount_paise == invoice.total_paise
        assert payment.status == PaymentStatus.CREATED
        assert payment.receipt_number  # the existing ledger apparatus, unchanged
        assert payment.platform_fee_paise == 0
        assert payment.worker_receives_paise == payment.amount_paise
        assert invoice.status == InvoiceStatus.ISSUED

    def test_an_empty_invoice_raises_no_payment(self, hourly_engagement):
        invoice = _invoice(hourly_engagement, [])
        assert invoice.issue() is None
        assert invoice.status == InvoiceStatus.ISSUED

    def test_review_window_opens_once(self, hourly_engagement):
        invoice = _invoice(hourly_engagement, [_session(hourly_engagement)])
        assert invoice.open_review(hours=48) is True
        assert invoice.review_closes_at is not None
        assert invoice.open_review(hours=48) is False

    def test_a_correction_is_an_adjustment_line_not_an_edit(self, hourly_engagement):
        """An issued invoice is never rewritten; the next one carries the fix."""
        july = _invoice(hourly_engagement, [_session(hourly_engagement)])
        july.issue()
        issued_total = july.total_paise

        august = Invoice.objects.create(
            society=hourly_engagement.society, engagement=hourly_engagement,
            resident=hourly_engagement.resident, worker=hourly_engagement.worker,
            period_start=DAY + dt.timedelta(days=1),
            period_end=DAY + dt.timedelta(days=31),
        )
        august.add_adjustment(amount_paise=-7_000, description="Jul session removed")

        july.refresh_from_db()
        assert july.total_paise == issued_total  # history untouched
        assert august.adjustment_paise == -7_000

    def test_one_invoice_per_engagement_period(self, hourly_engagement):
        _invoice(hourly_engagement, [])
        with pytest.raises(IntegrityError):
            _invoice(hourly_engagement, [])


class TestAQueryDoesNotFreezeTheMonth:
    """The rule that makes the escalation ladder safe to use at all."""

    def test_only_the_contested_line_is_held(self, hourly_engagement, resident_user):
        good = _session(hourly_engagement, day=DAY - dt.timedelta(days=1))
        contested = _session(hourly_engagement, day=DAY)
        invoice = _invoice(hourly_engagement, [good, contested])
        full_total = invoice.total_paise

        query = SessionQuery.objects.create(
            society=hourly_engagement.society, session=contested, invoice=invoice,
            raised_by=resident_user, reason="hours_disputed",
            description="She reached about 11:40, not 11:52.",
        )
        held = invoice.lines.filter(session=contested)
        held.update(is_held=True)
        invoice.recalculate()

        payment = invoice.issue()

        assert invoice.total_paise == full_total
        assert invoice.held_paise == sum(line.amount_paise for line in held)
        assert invoice.payable_paise == full_total - invoice.held_paise
        # She is paid the undisputed remainder now, on time.
        assert payment.amount_paise == invoice.payable_paise
        assert payment.amount_paise > 0
        assert query.is_open

    def test_a_query_starts_at_evidence_not_at_the_administrator(
        self, hourly_engagement, resident_user
    ):
        """Most queries die at stage one, where nobody has to decide anything."""
        query = SessionQuery.objects.create(
            society=hourly_engagement.society, session=_session(hourly_engagement),
            raised_by=resident_user, reason="hours_disputed",
        )
        assert query.stage == QueryStage.EVIDENCE

    def test_one_open_query_per_session_per_person(self, hourly_engagement, resident_user):
        session = _session(hourly_engagement)
        SessionQuery.objects.create(
            society=hourly_engagement.society, session=session,
            raised_by=resident_user, reason="hours_disputed",
        )
        with pytest.raises(IntegrityError):
            SessionQuery.objects.create(
                society=hourly_engagement.society, session=session,
                raised_by=resident_user, reason="wrong_amount",
            )

    def test_resolving_is_idempotent(self, hourly_engagement, resident_user):
        query = SessionQuery.objects.create(
            society=hourly_engagement.society, session=_session(hourly_engagement),
            raised_by=resident_user, reason="hours_disputed",
        )
        assert query.resolve(resolution="Gate log agrees with her.", by=resident_user) is True
        assert query.resolve(resolution="again", by=resident_user) is False


# ---------------------------------------------------------------------------
# Keeping the older modules working
# ---------------------------------------------------------------------------


class TestDayRateUnderBothBases:
    def test_monthly_is_unchanged(self, monthly_engagement):
        """The existing arithmetic, untouched: rate / (days_per_week * 4)."""
        expected = rupees_to_paise(8000) // (5 * 4)
        assert daily_rate_paise(monthly_engagement) == expected

    def test_hourly_includes_the_visit_fee(self, hourly_engagement):
        """A replacement covering one day travels as far as the regular worker.

        Paying her only for the hours would hand whoever stood in at short
        notice exactly the unfairness the visit fee exists to remove.
        """
        # 180 scheduled minutes at ₹120 = ₹360, plus the ₹60 visit fee.
        assert daily_rate_paise(hourly_engagement, DAY) == 36_000 + 6_000

    def test_an_unscheduled_day_still_owes_the_journey(self, hourly_engagement):
        sunday = dt.date(2026, 8, 16)
        assert sunday.weekday() == 6
        assert daily_rate_paise(hourly_engagement, sunday) == 6_000

    def test_leave_and_replacement_callers_need_no_change(self, hourly_engagement):
        """Called with one argument, as scheduling/services.py already does."""
        assert daily_rate_paise(hourly_engagement) > 0


class TestScheduleTimesAreReadInTheLocalClock:
    """Regression: a schedule is wall-clock, storage is UTC, and mixing them zeroes wages.

    Django stores datetimes in UTC, so a session read back from the database
    carries ``tzinfo=UTC``. The engine originally built its scheduled window by
    borrowing the tzinfo of whatever datetime was to hand, which turned a 09:00
    arrival into 09:00 *UTC* — 14:30 in Pune. The scheduled window then sat hours
    after the real one, ``min(ended_at, scheduled_end)`` came out before the
    start, and every session priced as zero minutes worked while still charging
    the visit fee.

    The pure-arithmetic tests could not catch it: they never round-trip through
    the database, so their datetimes were already in the local zone. This one
    saves and reloads on purpose.
    """

    def test_a_session_reloaded_from_the_database_prices_the_same(self, hourly_engagement):
        session = _session(hourly_engagement, start=(9, 0), end=(12, 0))
        reloaded = WorkSession.objects.get(pk=session.pk)

        price_session(reloaded, timing=TIMING, config=BillingConfig())

        assert reloaded.billable_minutes == 180
        assert reloaded.time_paise == 36_000
        assert reloaded.total_paise == 42_000

    def test_the_fee_alone_is_not_a_plausible_full_day(self, hourly_engagement):
        """The exact shape of the bug: fee charged, hours silently lost."""
        session = _session(hourly_engagement, start=(9, 0), end=(12, 0))
        reloaded = WorkSession.objects.get(pk=session.pk)
        price_session(reloaded, timing=TIMING, config=BillingConfig())

        assert reloaded.total_paise != reloaded.visit_fee_paise
        assert reloaded.billable_minutes > 0


class TestSocietyBillingConfig:
    def test_a_society_with_no_config_still_bills(self, society):
        """A missing config is a valid, fully functional state."""
        config = SocietyBillingConfig.for_society(society)
        assert config.visit_overhead_minutes == 30
        assert config.round_minutes == 15
        assert config.round_up_in_workers_favour is False
        assert config.visit_fee_policy == VisitFeePolicy.PER_ENGAGEMENT

    def test_for_society_is_idempotent(self, society):
        first = SocietyBillingConfig.for_society(society)
        assert SocietyBillingConfig.for_society(society).pk == first.pk

    def test_config_snapshots_into_the_engine(self, society):
        SocietyBillingConfig.objects.update_or_create(
            society=society,
            defaults={"round_minutes": 30, "round_up_in_workers_favour": True},
        )
        config = BillingConfig.from_society(society)
        assert config.round_step_minutes == 30
        assert config.round_up is True


class TestWageFloor:
    def test_the_latest_floor_in_force_wins(self):
        WageFloor.objects.create(
            state="Maharashtra", min_hourly_paise=8_000, effective_from=dt.date(2025, 1, 1)
        )
        WageFloor.objects.create(
            state="Maharashtra", min_hourly_paise=10_000, effective_from=dt.date(2026, 4, 1)
        )
        assert WageFloor.in_force("Maharashtra", on=DAY).min_hourly_paise == 10_000

    def test_a_future_floor_is_not_yet_in_force(self):
        WageFloor.objects.create(
            state="Maharashtra", min_hourly_paise=8_000, effective_from=dt.date(2025, 1, 1)
        )
        WageFloor.objects.create(
            state="Maharashtra", min_hourly_paise=99_000, effective_from=dt.date(2027, 1, 1)
        )
        assert WageFloor.in_force("Maharashtra", on=DAY).min_hourly_paise == 8_000

    def test_no_recorded_floor_is_not_permission(self):
        """None means "we have no figure", never "there is no floor"."""
        assert WageFloor.in_force("Goa", on=DAY) is None
