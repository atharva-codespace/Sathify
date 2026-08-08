"""
Module 8.8 — payment due dates, and the sample_payment command.

The due-date tests are mostly about *not* guessing. A blank due date is honest;
a wrong one is a demand for money on a day nobody agreed to.

The command tests are about the two things that make a hand-test trustworthy: it
goes through the real payment path rather than inserting a row, and it converges
on re-runs instead of filling a shared database with junk.
"""

from __future__ import annotations

import datetime as dt
from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import override_settings
from django.utils import timezone

from apps.bookings.models import Booking, BookingStatus, ServiceCategory
from apps.hiring.models import Engagement
from apps.payments.models import Payment, PaymentKind, PaymentStatus
from apps.payments.services import create_payment, payment_due_at
from apps.societies.models import Flat, Resident, Tower
from apps.workers.models import ServiceType, WorkerProfile

pytestmark = pytest.mark.django_db


@pytest.fixture
def maid_service(db):
    return ServiceType.objects.create(name="Maid", slug="maid")


@pytest.fixture
def flat(society):
    tower = Tower.objects.create(society=society, name="A", floors=10)
    return Flat.objects.create(tower=tower, number="301", floor=3)


@pytest.fixture
def resident(resident_user, flat):
    return Resident.objects.create(user=resident_user, flat=flat, is_primary=True)


@pytest.fixture
def worker(worker_user, maid_service):
    profile = WorkerProfile.objects.create(
        user=worker_user, photo="workers/photos/test.jpg"
    )
    profile.service_types.add(maid_service)
    return profile


@pytest.fixture
def engagement(society, resident, worker, maid_service):
    return Engagement.objects.create(
        society=society,
        resident=resident,
        worker=worker,
        service_type=maid_service,
        days_of_week=[0, 1, 2, 3, 4],
        start_time=dt.time(9, 0),
        expected_duration_minutes=90,
        monthly_rate=4000,
        started_on=dt.date(2026, 8, 3),
    )


class TestDueDateDerivation:
    def test_a_booking_is_due_on_the_day_it_is_served(
        self, society, resident, worker
    ):
        booking = Booking(
            scheduled_date=dt.date(2026, 8, 12), start_time=dt.time(14, 0)
        )

        due = payment_due_at(kind=PaymentKind.BOOKING, booking=booking)

        assert due.date() == dt.date(2026, 8, 12)
        assert due.hour == 14

    def test_a_salary_for_a_period_is_due_at_the_end_of_it(self, engagement):
        """Paid for work done. Asking up front bills for visits nobody made."""
        due = payment_due_at(
            kind=PaymentKind.ENGAGEMENT_SALARY,
            engagement=engagement,
            period_end=dt.date(2026, 8, 31),
        )

        assert due.date() == dt.date(2026, 8, 31)
        # End of the day, not its midnight start — a salary for a month ending
        # on the 31st is not overdue at 00:00 on the 31st.
        assert (due.hour, due.minute) == (23, 59)

    def test_an_engagement_with_no_period_falls_back_to_its_start_date(
        self, engagement
    ):
        """The case the brief names: resident books a service.

        `daily_rate_paise` divides by `len(days_of_week) * 4` — a rate
        calculation with no cycle anchor — so there is no billing cycle hiding
        in it to reuse. This is the documented simplest default.
        """
        due = payment_due_at(
            kind=PaymentKind.ENGAGEMENT_SALARY, engagement=engagement
        )

        assert due.date() == engagement.started_on
        assert due.hour == 9  # the engagement's own start time

    def test_nothing_implied_means_no_due_date_rather_than_a_guess(self):
        assert payment_due_at(kind=PaymentKind.TIP) is None

    def test_a_period_end_wins_over_the_engagement_start(self, engagement):
        """The more specific fact is the better answer."""
        due = payment_due_at(
            kind=PaymentKind.ENGAGEMENT_SALARY,
            engagement=engagement,
            period_end=dt.date(2026, 9, 30),
        )
        assert due.date() == dt.date(2026, 9, 30)


class TestDueDateOnPayment:
    def test_it_is_set_automatically(self, society, resident, worker, engagement):
        payment = create_payment(
            resident=resident,
            worker=worker,
            society=society,
            kind=PaymentKind.ENGAGEMENT_SALARY,
            amount_paise=400_000,
            engagement=engagement,
            period_start=dt.date(2026, 8, 1),
            period_end=dt.date(2026, 8, 31),
        )

        assert payment.due_at is not None
        assert payment.due_at.date() == dt.date(2026, 8, 31)

    def test_an_explicit_due_date_wins(self, society, resident, worker, engagement):
        """What sample_payment relies on to make a payment due right now."""
        moment = timezone.now()
        payment = create_payment(
            resident=resident,
            worker=worker,
            society=society,
            kind=PaymentKind.BOOKING,
            amount_paise=50_000,
            engagement=engagement,
            due_at=moment,
        )

        assert payment.due_at == moment

    def test_an_unpaid_payment_past_its_date_is_overdue(
        self, society, resident, worker, engagement
    ):
        payment = create_payment(
            resident=resident,
            worker=worker,
            society=society,
            kind=PaymentKind.BOOKING,
            amount_paise=50_000,
            engagement=engagement,
            due_at=timezone.now() - dt.timedelta(days=3),
        )

        assert payment.is_overdue is True
        assert payment.days_overdue == 3

    def test_a_settled_payment_is_never_overdue(
        self, society, resident, worker, engagement
    ):
        """There is nothing outstanding to be late with."""
        payment = create_payment(
            resident=resident,
            worker=worker,
            society=society,
            kind=PaymentKind.BOOKING,
            amount_paise=50_000,
            engagement=engagement,
            due_at=timezone.now() - dt.timedelta(days=3),
        )
        payment.status = PaymentStatus.PAID
        payment.save(update_fields=["status"])

        assert payment.is_overdue is False

    def test_a_payment_with_no_due_date_is_never_overdue(
        self, society, resident, worker
    ):
        """Rows predating this field have no answer, and must not be invented one."""
        payment = create_payment(
            resident=resident,
            worker=worker,
            society=society,
            kind=PaymentKind.TIP,
            amount_paise=5_000,
        )

        assert payment.due_at is None
        assert payment.is_overdue is False


class TestSamplePaymentCommand:
    def run(self, **kwargs):
        out = StringIO()
        call_command("sample_payment", stdout=out, **kwargs)
        return out.getvalue()

    def test_it_creates_a_payment_due_right_now(self, society):
        before = timezone.now()
        self.run()
        after = timezone.now()

        payment = Payment.objects.latest("created_at")
        assert before <= payment.due_at <= after

    def test_it_prints_the_ids_and_the_due_date(self, society):
        output = self.run()

        for label in ("resident", "worker", "payment", "receipt", "due at"):
            assert label in output

    def test_re_running_does_not_pile_up_duplicates(self, society):
        """A shared database should not fill with sample households."""
        from apps.accounts.models import User

        self.run()
        self.run()
        self.run()

        assert User.objects.filter(phone_number="9000000001").count() == 1
        assert User.objects.filter(phone_number="9000000002").count() == 1
        assert Resident.objects.filter(user__phone_number="9000000001").count() == 1
        # But three payments — that is the point of re-running it.
        assert Payment.objects.count() == 3

    def test_it_goes_through_the_real_payment_path(self, society):
        """Not a hand-inserted row.

        A receipt number and a derived total only exist if `create_payment` ran;
        inserting directly would exercise the database and nothing else.
        """
        self.run(amount=500, tip=50)
        payment = Payment.objects.latest("created_at")

        assert payment.receipt_number  # assigned by the model's own save path
        assert payment.amount_paise == 50_000
        assert payment.tip_paise == 5_000
        assert payment.total_paise == 55_000
        assert payment.kind == PaymentKind.BOOKING

    @override_settings(
        RAZORPAY_SETTINGS={"TEST_MODE": False, "KEY_ID": "", "KEY_SECRET": ""}
    )
    def test_it_refuses_to_run_outside_test_mode(self, society):
        with pytest.raises(CommandError, match="RAZORPAY_TEST_MODE"):
            self.run()

    @override_settings(
        RAZORPAY_SETTINGS={"TEST_MODE": True, "KEY_ID": "rzp_live_abc123"}
    )
    def test_it_refuses_to_run_against_a_live_key(self, society):
        """Belt and braces: TEST_MODE on but a live key pasted in."""
        with pytest.raises(CommandError, match="live Razorpay key"):
            self.run()

    def test_it_refuses_when_there_is_no_society(self, db):
        with pytest.raises(CommandError, match="No active society"):
            self.run()
