"""
Module 6.6 — hire → work → completion → gate exit.

The state a visit is in is *composed*, not stored. Two of the three signals
already exist — the gate log says whether somebody arrived and whether they
left — and only "the work was marked done" is new. These tests pin that
composition, and in particular the two places where copying a signal instead of
reading it would have been easier and wrong.
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
from apps.scheduling.models import TaskCompletion, VisitStatus
from apps.scheduling.schedule import worker_day
from apps.scheduling.services import VisitNotFound, mark_task_complete
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
    """Runs every day, so "today" always has a visit whenever the suite runs."""
    return Engagement.objects.create(
        society=society,
        resident=resident,
        worker=worker,
        service_type=maid_service,
        days_of_week=[0, 1, 2, 3, 4, 5, 6],
        start_time=dt.time(9, 0),
        expected_duration_minutes=90,
        monthly_rate=4000,
    )


def gate_event(worker, society, *, direction, when=None, decision=Decision.ALLOWED):
    return AttendanceEvent.objects.create(
        id=uuid.uuid4(),
        worker=worker,
        society=society,
        direction=direction,
        method=VerificationMethod.QR,
        decision=decision,
        occurred_at=when or timezone.now(),
    )


def today_item(worker):
    items = worker_day(worker.pk, timezone.localdate())
    assert items, "expected exactly one visit today"
    return items[0]


class TestTheStateMachine:
    def test_a_visit_starts_pending(self, engagement, worker):
        item = today_item(worker)

        assert item.visit_status == VisitStatus.PENDING
        assert item.checked_in_at is None
        assert item.is_complete is False

    def test_a_gate_entry_makes_it_in_progress(self, engagement, worker, society):
        """Read from the gate log, not copied onto the visit."""
        gate_event(worker, society, direction=Direction.ENTRY)

        item = today_item(worker)

        assert item.visit_status == VisitStatus.IN_PROGRESS
        assert item.checked_in_at is not None

    def test_marking_the_task_done_makes_it_complete(
        self, engagement, worker, society
    ):
        gate_event(worker, society, direction=Direction.ENTRY)
        mark_task_complete(
            worker=worker, visit_date=timezone.localdate(), engagement=engagement
        )

        item = today_item(worker)

        assert item.visit_status == VisitStatus.COMPLETE
        assert item.is_complete is True

    def test_a_confirmed_exit_is_recorded_without_changing_the_status(
        self, engagement, worker, society
    ):
        """Finishing and leaving are different facts.

        A worker who finished and stayed for a cup of tea has still finished; a
        worker who left without marking anything has not. Folding departure into
        the status would make one imply the other.
        """
        gate_event(worker, society, direction=Direction.ENTRY)
        mark_task_complete(
            worker=worker, visit_date=timezone.localdate(), engagement=engagement
        )
        gate_event(worker, society, direction=Direction.EXIT)

        item = today_item(worker)

        assert item.visit_status == VisitStatus.COMPLETE
        assert item.has_left is True
        assert item.exit_confirmed_at is not None

    def test_leaving_without_marking_complete_is_visible_as_exactly_that(
        self, engagement, worker, society
    ):
        """The household should be able to see the mismatch and ask about it."""
        gate_event(worker, society, direction=Direction.ENTRY)
        gate_event(worker, society, direction=Direction.EXIT)

        item = today_item(worker)

        assert item.visit_status == VisitStatus.IN_PROGRESS
        assert item.has_left is True
        assert item.is_complete is False


class TestWhatTheGateContributes:
    def test_a_denied_entry_is_not_an_arrival(self, engagement, worker, society):
        gate_event(
            worker, society, direction=Direction.ENTRY, decision=Decision.DENIED
        )

        assert today_item(worker).visit_status == VisitStatus.PENDING

    def test_a_pending_review_is_not_an_arrival_either(
        self, engagement, worker, society
    ):
        gate_event(
            worker,
            society,
            direction=Direction.ENTRY,
            decision=Decision.PENDING_REVIEW,
        )

        assert today_item(worker).visit_status == VisitStatus.PENDING

    def test_passing_the_gate_twice_reports_the_earliest_arrival(
        self, engagement, worker, society
    ):
        """Somebody who stepped out and back arrived once."""
        first = timezone.now() - dt.timedelta(hours=2)
        gate_event(worker, society, direction=Direction.ENTRY, when=first)
        gate_event(worker, society, direction=Direction.ENTRY)

        assert today_item(worker).checked_in_at == first

    def test_stepping_out_and_back_reports_the_latest_departure(
        self, engagement, worker, society
    ):
        gate_event(
            worker,
            society,
            direction=Direction.EXIT,
            when=timezone.now() - dt.timedelta(hours=2),
        )
        last = timezone.now()
        gate_event(worker, society, direction=Direction.EXIT, when=last)

        assert today_item(worker).exit_confirmed_at == last


class TestMarkingComplete:
    def test_it_does_not_require_a_check_in(self, engagement, worker):
        """The one that protects a worker from somebody else's broken hardware.

        A gate scanner that was down, a guard on a break, a GPS fix that would
        not settle — none of those are her fault, and none should stop her
        saying she finished the job.
        """
        completion = mark_task_complete(
            worker=worker, visit_date=timezone.localdate(), engagement=engagement
        )

        assert completion.pk is not None
        assert today_item(worker).visit_status == VisitStatus.COMPLETE

    def test_marking_twice_does_not_move_the_timestamp(self, engagement, worker):
        """The completion time is evidence. A double tap must not rewrite it."""
        first = mark_task_complete(
            worker=worker, visit_date=timezone.localdate(), engagement=engagement
        )
        second = mark_task_complete(
            worker=worker, visit_date=timezone.localdate(), engagement=engagement
        )

        assert first.pk == second.pk
        assert first.completed_at == second.completed_at
        assert TaskCompletion.objects.count() == 1

    def test_a_day_the_engagement_does_not_run_is_refused(
        self, engagement, worker
    ):
        engagement.days_of_week = [0]  # Mondays only
        engagement.save(update_fields=["days_of_week"])

        not_monday = timezone.localdate()
        while not_monday.weekday() == 0:
            not_monday += dt.timedelta(days=1)

        with pytest.raises(VisitNotFound):
            mark_task_complete(
                worker=worker, visit_date=not_monday, engagement=engagement
            )

    def test_exactly_one_of_engagement_or_booking(self, worker, engagement):
        with pytest.raises(VisitNotFound):
            mark_task_complete(
                worker=worker, visit_date=timezone.localdate()
            )

    def test_the_household_is_told(self, engagement, worker):
        """Close to real-time is the point of this step."""
        from apps.notifications.models import Notification

        mark_task_complete(
            worker=worker, visit_date=timezone.localdate(), engagement=engagement
        )

        assert Notification.objects.filter(
            recipient=engagement.resident.user
        ).exists()


class TestCompletionApi:
    def url(self):
        return reverse("v1:scheduling:mark-task-complete")

    def test_a_worker_marks_their_own_visit(
        self, authenticated_client, worker_user, engagement
    ):
        response = authenticated_client(worker_user).post(
            self.url(),
            {"engagement": engagement.pk, "note": "Kitchen and both bathrooms"},
            format="json",
        )

        assert response.status_code == 201
        assert response.data["completion"]["note"] == "Kitchen and both bathrooms"

    def test_visit_date_defaults_to_today(
        self, authenticated_client, worker_user, engagement
    ):
        response = authenticated_client(worker_user).post(
            self.url(), {"engagement": engagement.pk}, format="json"
        )

        assert response.data["completion"]["visit_date"] == str(timezone.localdate())

    def test_a_worker_cannot_mark_somebody_elses_visit(
        self, authenticated_client, engagement, society, django_user_model
    ):
        other = django_user_model.objects.create_user(
            phone_number="9000000123",
            password="pw",
            role="worker",
            society=society,
            is_approved=True,
        )
        WorkerProfile.objects.create(user=other, photo="workers/photos/x.jpg")

        response = authenticated_client(other).post(
            self.url(), {"engagement": engagement.pk}, format="json"
        )

        assert response.status_code == 404

    def test_a_resident_cannot_mark_the_work_done(
        self, authenticated_client, resident_user, engagement
    ):
        """Only the person who did the work says it was done."""
        response = authenticated_client(resident_user).post(
            self.url(), {"engagement": engagement.pk}, format="json"
        )

        assert response.status_code == 403

    def test_sending_both_engagement_and_booking_is_refused(
        self, authenticated_client, worker_user, engagement
    ):
        response = authenticated_client(worker_user).post(
            self.url(),
            {"engagement": engagement.pk, "booking": 1},
            format="json",
        )

        assert response.status_code == 400

    def test_the_household_sees_the_status_on_their_own_schedule(
        self, authenticated_client, resident_user, worker_user, engagement
    ):
        authenticated_client(worker_user).post(
            self.url(), {"engagement": engagement.pk}, format="json"
        )

        response = authenticated_client(resident_user).get(
            reverse("v1:scheduling:my-today")
        )

        assert response.status_code == 200
        item = response.data["results"][0]
        assert item["visit_status"] == VisitStatus.COMPLETE
        assert item["is_complete"] is True
