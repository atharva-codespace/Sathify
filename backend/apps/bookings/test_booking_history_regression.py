"""Bug 7 — booking history shows past and recent bookings.

The list endpoint was never at fault: these three cases passed before the fix
too. What was broken was *creation* — ``create_booking`` enforced the same
missing opt-in row as matching did, so no booking could be made and the
history was correctly empty. The second test is the one that regressed.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import Role
from apps.bookings.models import Booking, BookingStatus, ServiceCategory
from apps.societies.models import Flat, Resident, Tower
from apps.workers.models import WorkerProfile


@pytest.fixture
def flat(society):
    tower = Tower.objects.create(society=society, name="A", floors=10)
    return Flat.objects.create(tower=tower, number="301", floor=3)


@pytest.fixture
def resident(resident_user, flat):
    return Resident.objects.create(user=resident_user, flat=flat, is_primary=True)


@pytest.fixture
def maid(db, django_user_model, society):
    user = django_user_model.objects.create_user(
        phone_number="9850000031", password="test-pass-12345",
        role=Role.WORKER, society=society, first_name="Sunita", last_name="D",
        is_approved=True,
    )
    return WorkerProfile.objects.create(
        user=user, photo="workers/x.jpg", is_available=True,
    )


def _booking(society, resident, maid, on_date, status, start=dt.time(10, 0)):
    return Booking.objects.create(
        society=society, resident=resident, worker=maid,
        category=ServiceCategory.objects.get(slug="deep-cleaning"),
        scheduled_date=on_date, start_time=start,
        expected_duration_minutes=120, quoted_price=1500, status=status,
    )


@pytest.mark.django_db
def test_history_returns_past_and_recent_bookings(
    authenticated_client, resident, resident_user, maid, society
):
    """The app calls GET /bookings/ with no status filter and expects
    everything the resident is party to, past included."""
    today = timezone.localdate()
    _booking(society, resident, maid, today - dt.timedelta(days=30),
             BookingStatus.COMPLETED)
    _booking(society, resident, maid, today - dt.timedelta(days=5),
             BookingStatus.CANCELLED, start=dt.time(11, 0))
    _booking(society, resident, maid, today + dt.timedelta(days=3),
             BookingStatus.CONFIRMED, start=dt.time(12, 0))

    response = authenticated_client(resident_user).get(
        reverse("v1:bookings:booking-list"), {"page_size": 100}
    )

    assert response.status_code == 200
    body = response.data
    print("\n  response keys:", list(body.keys()) if hasattr(body, "keys") else type(body))
    print("  count:", body.get("count") if hasattr(body, "get") else "n/a")

    assert "results" in body, "the app reads response['results']"
    assert len(body["results"]) == 3, body


@pytest.mark.django_db
def test_a_single_one_day_booking_is_visible_right_after_it_is_made(
    authenticated_client, resident, resident_user, maid, society
):
    """The exact reported flow: make one booking, open history, see it."""
    client = authenticated_client(resident_user)
    on_date = timezone.localdate() + dt.timedelta(days=7)

    created = client.post(
        reverse("v1:bookings:booking-list"),
        {
            "worker": maid.pk,
            "category": ServiceCategory.objects.get(slug="deep-cleaning").pk,
            "scheduled_date": on_date.isoformat(),
            "start_time": "10:00",
            "expected_duration_minutes": 120,
            "quoted_price": 1500,
        },
        format="json",
    )
    assert created.status_code == 201, created.data

    listed = client.get(reverse("v1:bookings:booking-list"), {"page_size": 100})
    print("\n  after booking, count:", listed.data.get("count"))
    assert len(listed.data["results"]) == 1, listed.data


@pytest.mark.django_db
def test_page_size_is_honoured_or_at_least_not_fatal(
    authenticated_client, resident, resident_user, maid, society
):
    """The client always sends page_size=100. If the server ignores it the
    list silently truncates, which on a long history looks like missing rows."""
    today = timezone.localdate()
    for i in range(25):
        _booking(
            society, resident, maid, today - dt.timedelta(days=i + 1),
            BookingStatus.COMPLETED,
            start=dt.time(8 + i % 10, (i * 5) % 60),
        )

    response = authenticated_client(resident_user).get(
        reverse("v1:bookings:booking-list"), {"page_size": 100}
    )

    print("\n  25 bookings -> returned:", len(response.data["results"]),
          "of count", response.data.get("count"))
    assert response.data["count"] == 25
    assert len(response.data["results"]) == 25, (
        "page_size=100 was ignored, so history truncates at the default page"
    )
