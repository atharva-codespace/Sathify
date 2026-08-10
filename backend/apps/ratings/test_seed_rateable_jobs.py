"""Module 9 — the seed that makes the rating flow testable at all.

A freshly seeded system has no completed booking and no ended engagement, so
both sides see an empty Rate Work screen and there is nothing to exercise. This
command supplies that work, and these tests pin the two properties that make it
safe to run against a database somebody is already using: it adds nothing on a
second run, and it never ends an arrangement it did not create.
"""

from __future__ import annotations

import datetime as dt
import io

import pytest
from django.core.management import call_command
from django.urls import reverse

from apps.bookings.models import Booking, BookingStatus
from apps.hiring.models import Engagement, EngagementStatus
from apps.ratings.management.commands.seed_rateable_jobs import SEED_NOTE
from apps.ratings.models import Rating, RatingDirection
from apps.societies.models import Flat, Resident, Tower
from apps.workers.models import ServiceType, WorkerProfile

pytestmark = pytest.mark.django_db


@pytest.fixture
def maid_service(db):
    return ServiceType.objects.create(name="Maid", slug="maid")


@pytest.fixture
def resident(resident_user, society):
    tower = Tower.objects.create(society=society, name="A", floors=10)
    flat = Flat.objects.create(tower=tower, number="301", floor=3)
    return Resident.objects.create(user=resident_user, flat=flat, is_primary=True)


@pytest.fixture
def worker(worker_user, maid_service):
    profile = WorkerProfile.objects.create(
        user=worker_user, photo="workers/photos/test.jpg"
    )
    profile.service_types.add(maid_service)
    return profile


PENDING_URL = "v1:ratings:pending"


def test_it_gives_both_sides_something_to_rate(
    authenticated_client, resident_user, worker_user, resident, worker
):
    """A rating runs in both directions, so both sides need work waiting."""
    call_command("seed_rateable_jobs", verbosity=0)

    resident_pending = authenticated_client(resident_user).get(reverse(PENDING_URL))
    worker_pending = authenticated_client(worker_user).get(reverse(PENDING_URL))

    assert resident_pending.data["count"] == 2
    assert worker_pending.data["count"] == 2
    assert {job["kind"] for job in resident_pending.data["results"]} == {
        "booking",
        "engagement",
    }


def test_the_seeded_work_is_actually_finished(resident, worker):
    """Module 9 only offers finished work. Anything less is not rateable."""
    call_command("seed_rateable_jobs", verbosity=0)

    assert Booking.objects.get(notes=SEED_NOTE).status == BookingStatus.COMPLETED
    assert (
        Engagement.objects.get(end_note=SEED_NOTE).status
        == EngagementStatus.TERMINATED
    )


def test_running_it_twice_adds_nothing(resident, worker):
    call_command("seed_rateable_jobs", verbosity=0)
    call_command("seed_rateable_jobs", verbosity=0)

    assert Booking.objects.filter(notes=SEED_NOTE).count() == 1
    assert Engagement.objects.filter(end_note=SEED_NOTE).count() == 1


def test_it_never_ends_an_engagement_it_did_not_create(
    society, resident, worker, maid_service
):
    """The safety property.

    Matching a pair rather than the marker note would find a live arrangement
    somebody was in the middle of testing — and terminating is terminal, so
    there would be no way back from it.
    """
    live = Engagement.objects.create(
        society=society,
        resident=resident,
        worker=worker,
        service_type=maid_service,
        days_of_week=[1, 3],
        start_time=dt.time(8, 0),
        monthly_rate=3000,
    )

    call_command("seed_rateable_jobs", verbosity=0)

    live.refresh_from_db()
    assert live.status == EngagementStatus.ACTIVE


def test_the_report_survives_a_windows_console(resident, worker):
    """This is run from a cp1252 terminal, where a glyph is not cosmetic.

    An arrow in a summary line raised UnicodeEncodeError straight out of the
    command. Every string that reaches stdout or stderr has to be encodable
    there, so the console is simulated rather than trusted.
    """
    console = io.TextIOWrapper(io.BytesIO(), encoding="cp1252", errors="strict")

    call_command("seed_rateable_jobs", stdout=console, stderr=console)

    console.flush()
    assert Booking.objects.filter(notes=SEED_NOTE).exists()


def test_a_failure_while_reporting_does_not_undo_the_seed(
    monkeypatch, resident, worker
):
    """Reporting sat inside the transaction, so a console that could not print
    a summary line rolled back work that had already succeeded."""
    monkeypatch.setattr(
        "apps.ratings.management.commands.seed_rateable_jobs.Command._report",
        lambda self: (_ for _ in ()).throw(UnicodeEncodeError(
            "charmap", "⇄", 0, 1, "unprintable"
        )),
    )

    with pytest.raises(UnicodeEncodeError):
        call_command("seed_rateable_jobs", verbosity=0)

    assert Booking.objects.filter(notes=SEED_NOTE).exists()
    assert Engagement.objects.filter(end_note=SEED_NOTE).exists()


def test_reset_makes_the_jobs_rateable_again(
    authenticated_client, resident_user, resident, worker
):
    call_command("seed_rateable_jobs", verbosity=0)
    client = authenticated_client(resident_user)

    booking = Booking.objects.get(notes=SEED_NOTE)
    client.post(
        reverse("v1:ratings:rating-list"),
        {"booking": booking.pk, "stars": 5, "review": "Thorough and on time."},
        format="json",
    )
    assert client.get(reverse(PENDING_URL)).data["count"] == 1

    call_command("seed_rateable_jobs", "--reset", verbosity=0)

    assert client.get(reverse(PENDING_URL)).data["count"] == 2
    assert not Rating.objects.filter(booking=booking).exists()


def test_reset_puts_the_trust_score_back(
    authenticated_client, resident_user, resident, worker
):
    """Deleting the ratings alone would leave an average computed from ratings
    that no longer exist — the exact inconsistency Module 9 exists to avoid."""
    call_command("seed_rateable_jobs", verbosity=0)

    booking = Booking.objects.get(notes=SEED_NOTE)
    authenticated_client(resident_user).post(
        reverse("v1:ratings:rating-list"),
        {"booking": booking.pk, "stars": 5},
        format="json",
    )
    worker.refresh_from_db()
    assert worker.rating_count == 1

    call_command("seed_rateable_jobs", "--reset", verbosity=0)

    worker.refresh_from_db()
    assert worker.rating_count == 0
    assert not Rating.objects.filter(
        direction=RatingDirection.RESIDENT_TO_WORKER
    ).exists()
