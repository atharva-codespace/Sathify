"""Bug 1 — every booking category must find available maids, not just urgent.

Before the fix, ``candidate_workers`` required an explicit per-date
``DayAvailability`` opt-in row for every category except the one carrying
``bypasses_notice_period``. Almost no such rows exist in practice, so four of
the five seeded categories matched nobody while emergency assistance found the
very same workers.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import Role
from apps.bookings.models import Booking, BookingStatus, DayAvailability, ServiceCategory
from apps.societies.models import Flat, Resident, Tower
from apps.workers.models import WorkerProfile


@pytest.fixture
def flat(society):
    tower = Tower.objects.create(society=society, name="A", floors=10)
    return Flat.objects.create(tower=tower, number="301", floor=3)


@pytest.fixture
def resident(resident_user, flat):
    return Resident.objects.create(user=resident_user, flat=flat, is_primary=True)


def _worker(user, **kw):
    profile = WorkerProfile.objects.create(
        user=user, photo="workers/x.jpg", is_available=True, **kw
    )
    return profile


@pytest.fixture
def maids(db, django_user_model, society):
    out = []
    for i, name in enumerate(["Sunita", "Kavita", "Meena"]):
        user = django_user_model.objects.create_user(
            phone_number=f"988000000{i}", password="test-pass-12345",
            role=Role.WORKER, society=society, first_name=name, last_name="D",
            is_approved=True,
        )
        out.append(_worker(user, trust_score=70, average_rating=4.2))
    return out


@pytest.mark.django_db
def test_e2e_every_category_finds_maids_and_booking_persists(
    authenticated_client, resident, resident_user, maids
):
    """UI action -> API -> backend logic -> DB state -> what the UI re-reads."""
    client = authenticated_client(resident_user)
    on_date = timezone.localdate() + dt.timedelta(days=7)

    # No maid has ever opened the availability screen: zero opt-in rows.
    assert DayAvailability.objects.count() == 0

    # --- Step 1: the resident opens each category and taps "Find workers" ---
    found = {}
    for category in ServiceCategory.objects.filter(is_active=True):
        response = client.get(
            reverse("v1:bookings:match"),
            {
                "category": category.pk,
                "date": on_date.isoformat(),
                "start_time": "10:00",
            },
        )
        assert response.status_code == 200, category.slug
        found[category.slug] = response.data["count"]

    print("\n  match counts by category:", found)
    assert all(n == 3 for n in found.values()), found

    # --- Step 2: resident books the first maid for deep cleaning -----------
    category = ServiceCategory.objects.get(slug="deep-cleaning")
    match = client.get(
        reverse("v1:bookings:match"),
        {"category": category.pk, "date": on_date.isoformat(), "start_time": "10:00"},
    )
    chosen = match.data["results"][0]["id"]

    created = client.post(
        reverse("v1:bookings:booking-list"),
        {
            "worker": chosen,
            "category": category.pk,
            "scheduled_date": on_date.isoformat(),
            "start_time": "10:00",
            "expected_duration_minutes": 240,
            "quoted_price": 1500,
        },
        format="json",
    )
    assert created.status_code == 201, created.data

    # --- Step 3: DB state ---------------------------------------------------
    booking = Booking.objects.get(pk=created.data["booking"]["id"])
    assert booking.status == BookingStatus.PENDING
    assert booking.worker_id == chosen
    assert booking.resident_id == resident.pk
    assert booking.scheduled_date == on_date

    # --- Step 4: the slot is now taken, so that maid drops out of a -------
    #             re-search for the same window, and only that maid does.
    again = client.get(
        reverse("v1:bookings:match"),
        {"category": category.pk, "date": on_date.isoformat(), "start_time": "10:00"},
    )
    ids = [row["id"] for row in again.data["results"]]
    assert chosen not in ids
    assert len(ids) == 2

    # ...but a non-overlapping window still offers all three.
    later = client.get(
        reverse("v1:bookings:match"),
        {"category": category.pk, "date": on_date.isoformat(), "start_time": "16:00"},
    )
    assert later.data["count"] == 3


@pytest.mark.django_db
def test_e2e_blocked_date_excludes_only_that_maid(
    authenticated_client, resident, resident_user, maids
):
    client = authenticated_client(resident_user)
    on_date = timezone.localdate() + dt.timedelta(days=7)
    category = ServiceCategory.objects.get(slug="event-preparation")

    DayAvailability.objects.create(
        worker=maids[0], date=on_date, is_available=False
    )

    response = client.get(
        reverse("v1:bookings:match"),
        {"category": category.pk, "date": on_date.isoformat(), "start_time": "10:00"},
    )

    ids = [row["id"] for row in response.data["results"]]
    assert maids[0].pk not in ids
    assert sorted(ids) == sorted([maids[1].pk, maids[2].pk])
