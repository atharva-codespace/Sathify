"""
Module 6.7 — the dashboard: status, pay, next visit, counts.

The pay tests carry the weight. ``pay_paise`` says what a day is worth;
``pay_state`` says whether it has been earned — and it has three values, not
two, because "the day ended and nobody marked it complete" is a question for a
person, not something this module gets to answer.
"""

from __future__ import annotations

import datetime as dt
import uuid

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.attendance.models import (
    AttendanceEvent,
    Decision,
    Direction,
    VerificationMethod,
)
from apps.hiring.models import Engagement
from apps.payments.services import daily_rate_paise
from apps.scheduling.models import TaskCompletion, VisitStatus
from apps.scheduling.schedule import (
    PAY_EARNED,
    PAY_NOT_YET,
    PAY_UNRESOLVED,
    worker_schedule,
)
from apps.scheduling.services import mark_task_complete
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
        days_of_week=[0, 1, 2, 3, 4, 5, 6],
        start_time=dt.time(9, 0),
        expected_duration_minutes=90,
        monthly_rate=6000,
    )


def today_item(worker):
    return worker_schedule(worker.pk, timezone.localdate(), timezone.localdate())[0]


class TestTodaysPay:
    def test_the_amount_reuses_the_existing_day_rate(self, engagement, worker):
        """No second way to value a day's work."""
        assert today_item(worker).pay_paise == daily_rate_paise(engagement)

    def test_an_unstarted_day_is_not_yet_earned(self, engagement, worker):
        item = today_item(worker)

        assert item.pay_state == PAY_NOT_YET
        # The amount is still shown — it is what the day is worth, not a debt.
        assert item.pay_paise > 0

    def test_completing_the_day_earns_it(self, engagement, worker):
        mark_task_complete(
            worker=worker, visit_date=timezone.localdate(), engagement=engagement
        )

        assert today_item(worker).pay_state == PAY_EARNED

    def test_a_past_day_nobody_marked_is_flagged_not_decided(
        self, engagement, worker
    ):
        """The case the brief said to flag rather than invent a default for.

        A worker may have done the whole job and forgotten to press a button, or
        her phone may have been flat, or she may genuinely not have come. Paying
        in full, pro-rating and paying nothing are all defensible; picking one
        here would be deciding somebody's wages by accident.
        """
        yesterday = timezone.localdate() - dt.timedelta(days=1)

        items = worker_schedule(worker.pk, yesterday, yesterday)

        assert items[0].pay_state == PAY_UNRESOLVED

    def test_a_past_day_that_was_completed_is_earned_not_flagged(
        self, engagement, worker
    ):
        yesterday = timezone.localdate() - dt.timedelta(days=1)
        mark_task_complete(
            worker=worker, visit_date=yesterday, engagement=engagement
        )

        items = worker_schedule(worker.pk, yesterday, yesterday)

        assert items[0].pay_state == PAY_EARNED


class TestTimeUntilNextVisit:
    def test_it_reports_the_gap_to_the_following_visit(self, engagement, worker):
        today = timezone.localdate()
        items = worker_schedule(worker.pk, today, today + dt.timedelta(days=1))

        # 09:00 + 90 min today, next visit 09:00 tomorrow.
        assert items[0].minutes_to_next == pytest.approx(22 * 60 + 30, abs=1)
        assert items[0].next_visit_at.date() == today + dt.timedelta(days=1)

    def test_the_last_visit_in_the_window_reports_minus_one(
        self, engagement, worker
    ):
        """Not zero. "Nothing next in this window" and "the next one is now"
        are different things to show somebody."""
        today = timezone.localdate()
        items = worker_schedule(worker.pk, today, today)

        assert items[-1].minutes_to_next == -1
        assert items[-1].next_visit_at is None

    def test_another_worker_s_visit_does_not_count_as_yours(
        self, society, resident, maid_service, engagement, worker, django_user_model
    ):
        other_user = django_user_model.objects.create_user(
            phone_number="9000000123", password="pw", role="worker",
            society=society, is_approved=True,
        )
        other = WorkerProfile.objects.create(
            user=other_user, photo="workers/photos/o.jpg"
        )
        Engagement.objects.create(
            society=society, resident=resident, worker=other,
            service_type=maid_service, days_of_week=[0, 1, 2, 3, 4, 5, 6],
            start_time=dt.time(14, 0), expected_duration_minutes=60,
            monthly_rate=3000,
        )

        today = timezone.localdate()
        mine = worker_schedule(worker.pk, today, today)

        # My only visit today is still my last, even though somebody else has
        # one at 14:00.
        assert mine[0].minutes_to_next == -1


class TestDashboardSummary:
    def url(self):
        return reverse("v1:scheduling:my-today")

    def test_the_counts_add_up(
        self, authenticated_client, resident_user, engagement, worker, society
    ):
        AttendanceEvent.objects.create(
            id=uuid.uuid4(), worker=worker, society=society,
            direction=Direction.ENTRY, method=VerificationMethod.QR,
            decision=Decision.ALLOWED, occurred_at=timezone.now(),
        )

        response = authenticated_client(resident_user).get(self.url())
        summary = response.data["summary"]

        assert summary["total"] == 1
        assert summary["in_progress"] == 1
        assert summary["completed"] == 0
        assert summary["pending"] == 0

    def test_completed_count_and_earned_total(
        self, authenticated_client, resident_user, engagement, worker
    ):
        mark_task_complete(
            worker=worker, visit_date=timezone.localdate(), engagement=engagement
        )

        summary = authenticated_client(resident_user).get(self.url()).data["summary"]

        assert summary["completed"] == 1
        assert summary["earned_paise"] == daily_rate_paise(engagement)
        assert summary["earned_display"]

    def test_only_earned_pay_is_totalled(
        self, authenticated_client, resident_user, engagement, worker
    ):
        """A day in progress is not money owed.

        Totalling it would have a household believing they are behind on a
        payment nobody is asking for yet.
        """
        summary = authenticated_client(resident_user).get(self.url()).data["summary"]

        assert summary["completed"] == 0
        assert summary["earned_paise"] == 0

    def test_the_next_visit_is_reported(
        self, authenticated_client, resident_user, engagement, worker
    ):
        summary = authenticated_client(resident_user).get(self.url()).data["summary"]

        # Today's visit has tomorrow's to point at, inside the look-ahead window.
        assert summary["minutes_to_next"] > 0
        assert summary["next_visit_at"] is not None

    def test_only_todays_items_are_returned(
        self, authenticated_client, resident_user, engagement
    ):
        """The look-ahead window feeds `next_visit_at`; it must not leak."""
        response = authenticated_client(resident_user).get(self.url())

        assert response.data["count"] == 1
        assert all(
            row["date"] == str(timezone.localdate())
            for row in response.data["results"]
        )

    def test_each_entry_carries_its_own_status_and_pay(
        self, authenticated_client, resident_user, engagement, worker
    ):
        """The dashboard bug: entries had no status at all."""
        mark_task_complete(
            worker=worker, visit_date=timezone.localdate(), engagement=engagement
        )

        row = authenticated_client(resident_user).get(self.url()).data["results"][0]

        assert row["visit_status"] == VisitStatus.COMPLETE
        assert row["pay_state"] == PAY_EARNED
        assert row["pay_paise"] > 0
        assert row["pay_display"]
        assert row["is_complete"] is True

    def test_a_worker_sees_their_own_dashboard_too(
        self, authenticated_client, worker_user, engagement
    ):
        response = authenticated_client(worker_user).get(self.url())

        assert response.status_code == 200
        assert response.data["summary"]["total"] == 1
