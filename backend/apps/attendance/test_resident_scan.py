"""
Module 13.3 tier 2.5 — the resident scans the worker's printed card.

The gap the other two fallbacks leave: no guard on the gate *and* no smartphone
in the worker's pocket. Tier 2 needs her phone; tier 3 needs a guard with a
paper register.

Two properties carry the weight. The engagement constraint is what stops this
becoming "anyone with a camera can write attendance rows". And the never-denies
rule is the same one binding every other tier — a worker turned away by somebody
else's misconfigured phone loses a day's wages for a measurement error.
"""

from __future__ import annotations

import datetime as dt
import uuid

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.attendance.models import Decision, Direction, VerificationMethod
from apps.attendance.services import (
    NoEngagementWithWorker,
    UnknownPass,
    ensure_gate_pass,
    resident_scan,
)
from apps.hiring.models import Engagement, EngagementStatus
from apps.societies.models import Flat, Resident, Tower
from apps.workers.models import ServiceType, WorkerProfile

pytestmark = pytest.mark.django_db


@pytest.fixture
def maid_service(db):
    return ServiceType.objects.create(name="Maid", slug="maid")


@pytest.fixture
def located_society(society):
    """A society with coordinates, so the geofence can actually answer."""
    society.latitude = 18.5204
    society.longitude = 73.8567
    society.save(update_fields=["latitude", "longitude"])
    return society


@pytest.fixture
def flat(located_society):
    tower = Tower.objects.create(society=located_society, name="A", floors=10)
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
def engagement(located_society, resident, worker, maid_service):
    """Runs every day at the current hour, so a scan "now" is expected."""
    now = timezone.localtime()
    return Engagement.objects.create(
        society=located_society,
        resident=resident,
        worker=worker,
        service_type=maid_service,
        days_of_week=[0, 1, 2, 3, 4, 5, 6],
        start_time=now.time().replace(second=0, microsecond=0),
        expected_duration_minutes=90,
        monthly_rate=4000,
    )


@pytest.fixture
def card(worker):
    """The printed card. Same GatePass code, on laminated cardboard."""
    return ensure_gate_pass(worker)


def scan(resident, card, society, **overrides):
    kwargs = {
        "event_id": uuid.uuid4(),
        "code": card.code,
        "resident": resident,
        "society": society,
        "direction": Direction.ENTRY,
        "occurred_at": timezone.now(),
        "latitude": 18.5204,
        "longitude": 73.8567,
        "accuracy_metres": 20.0,
    }
    kwargs.update(overrides)
    return resident_scan(**kwargs)


class TestTheEngagementConstraint:
    """What stops this being a way for anyone with a camera to write rows."""

    def test_a_household_can_scan_a_worker_it_employs(
        self, resident, card, located_society, engagement
    ):
        result = scan(resident, card, located_society)

        assert result.created is True
        assert result.event.method == VerificationMethod.RESIDENT_SCAN
        assert result.event.worker_id == engagement.worker_id

    def test_a_household_cannot_scan_a_worker_it_does_not_employ(
        self, resident, card, located_society
    ):
        """No engagement at all — refused outright rather than logged pending.

        "This resident has nothing to do with this worker" is not a measurement
        that needs reviewing; it is the wrong person scanning.
        """
        with pytest.raises(NoEngagementWithWorker):
            scan(resident, card, located_society)

    def test_a_terminated_engagement_does_not_count(
        self, resident, card, located_society, engagement
    ):
        engagement.terminate(reason="worker_ended")

        with pytest.raises(NoEngagementWithWorker):
            scan(resident, card, located_society)

    def test_a_paused_engagement_still_counts(
        self, resident, card, located_society, engagement
    ):
        """Paused means the worker is expected back, not that she is a stranger."""
        engagement.pause(reason="Family holiday")

        result = scan(resident, card, located_society)

        assert result.created is True

    def test_an_unknown_card_is_refused(self, resident, located_society):
        # A well-formed code that belongs to nobody — otherwise this would be
        # testing Django's UUID validation rather than our lookup.
        class _Fake:
            code = uuid.uuid4()

        with pytest.raises(UnknownPass):
            scan(resident, _Fake(), located_society)

    def test_the_witness_is_recorded(
        self, resident, card, located_society, engagement
    ):
        """The whole difference from tier 2 is that somebody vouched."""
        result = scan(resident, card, located_society)

        assert result.event.recorded_by_id == resident.user_id


class TestItNeverDenies:
    """The rule binding every attendance tier (apps/core/resilience.py §13.3)."""

    def test_a_scan_from_far_away_is_reviewed_not_denied(
        self, resident, card, located_society, engagement
    ):
        """The failure guarded against is a well-meaning resident tapping the
        button from the office because the worker rang to say she arrived."""
        result = scan(
            resident, card, located_society, latitude=19.0760, longitude=72.8777
        )

        assert result.decision == Decision.PENDING_REVIEW
        assert result.decision != Decision.DENIED

    def test_no_position_at_all_is_reviewed_not_denied(
        self, resident, card, located_society, engagement
    ):
        result = scan(
            resident, card, located_society, latitude=None, longitude=None
        )

        assert result.decision == Decision.PENDING_REVIEW

    def test_an_unscheduled_visit_is_reviewed_not_denied(
        self, resident, card, located_society, engagement
    ):
        """A swapped day or a job arranged on paper is common and innocent."""
        engagement.days_of_week = []
        engagement.save(update_fields=["days_of_week"])

        result = scan(resident, card, located_society)

        assert result.decision == Decision.PENDING_REVIEW

    def test_everything_lining_up_allows_it(
        self, resident, card, located_society, engagement
    ):
        result = scan(resident, card, located_society)

        assert result.decision == Decision.ALLOWED
        assert result.needs_review is False


class TestIdempotency:
    def test_a_replayed_scan_records_once(
        self, resident, card, located_society, engagement
    ):
        """13.1 — the device generates the id before the server sees it."""
        event_id = uuid.uuid4()

        first = scan(resident, card, located_society, event_id=event_id)
        second = scan(resident, card, located_society, event_id=event_id)

        assert first.created is True
        assert second.created is False
        assert first.event.pk == second.event.pk


class TestResidentScanApi:
    def url(self):
        return reverse("v1:attendance:resident-scan")

    def payload(self, card, **overrides):
        return {
            "id": str(uuid.uuid4()),
            "code": card.code,
            "occurred_at": timezone.now().isoformat(),
            "latitude": 18.5204,
            "longitude": 73.8567,
            "accuracy_metres": 20.0,
            **overrides,
        }

    def test_a_resident_scans_a_worker_in(
        self, authenticated_client, resident_user, resident, card, engagement
    ):
        response = authenticated_client(resident_user).post(
            self.url(), self.payload(card), format="json"
        )

        assert response.status_code == 201
        assert response.data["event"]["method"] == VerificationMethod.RESIDENT_SCAN

    def test_scanning_out_works_the_same_way(
        self, authenticated_client, resident_user, resident, card, engagement
    ):
        """Symmetric with entry, like the guard's own scan screen."""
        response = authenticated_client(resident_user).post(
            self.url(),
            self.payload(card, direction=Direction.EXIT),
            format="json",
        )

        assert response.status_code == 201
        assert response.data["event"]["direction"] == Direction.EXIT

    def test_scanning_a_worker_you_do_not_employ_is_forbidden(
        self, authenticated_client, resident_user, resident, card
    ):
        response = authenticated_client(resident_user).post(
            self.url(), self.payload(card), format="json"
        )

        assert response.status_code == 403
        assert response.data["error"]["code"] == "no_engagement_with_worker"

    def test_an_unknown_code_is_a_404(
        self, authenticated_client, resident_user, resident
    ):
        class _Fake:
            code = uuid.uuid4()

        response = authenticated_client(resident_user).post(
            self.url(), self.payload(_Fake()), format="json"
        )

        assert response.status_code == 404

    def test_a_worker_cannot_use_this_endpoint(
        self, authenticated_client, worker_user, card
    ):
        """Tier 2 is the worker's own path. This one needs a witness."""
        response = authenticated_client(worker_user).post(
            self.url(), self.payload(card), format="json"
        )

        assert response.status_code == 403
