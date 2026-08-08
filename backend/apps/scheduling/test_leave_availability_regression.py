"""Bug 4 — leave removes the absent maid, and only her.

``conflicted_worker_ids`` is the one place every module asks "is this worker
busy", and it did not consult ``LeaveRequest`` at all.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import Role
from apps.bookings.models import ServiceCategory, minutes_of
from apps.hiring.models import Engagement, EngagementStatus, weekday_of
from apps.scheduling.models import LeaveRequest, LeaveStatus
from apps.scheduling.services import conflicted_worker_ids
from apps.societies.models import Flat, Resident, Tower
from apps.workers.models import ServiceType, WorkerProfile


@pytest.fixture
def flat(society):
    tower = Tower.objects.create(society=society, name="A", floors=10)
    return Flat.objects.create(tower=tower, number="301", floor=3)


@pytest.fixture
def resident(resident_user, flat):
    return Resident.objects.create(user=resident_user, flat=flat, is_primary=True)


@pytest.fixture
def maids(db, django_user_model, society):
    out = []
    for i, name in enumerate(["Sunita", "Kavita", "Meena"]):
        user = django_user_model.objects.create_user(
            phone_number=f"987000000{i}", password="test-pass-12345",
            role=Role.WORKER, society=society, first_name=name, last_name="D",
            is_approved=True,
        )
        out.append(
            WorkerProfile.objects.create(
                user=user, photo="workers/x.jpg", is_available=True,
                trust_score=70, average_rating=4.2,
            )
        )
    return out


@pytest.fixture
def leave_date():
    """A date inside the 14-day leave window and past any notice period."""
    return timezone.localdate() + dt.timedelta(days=7)


def _engagement(society, resident, worker, on_date):
    service_type, _ = ServiceType.objects.get_or_create(name="Maid", slug="maid")
    return Engagement.objects.create(
        society=society, resident=resident, worker=worker,
        service_type=service_type,
        days_of_week=[weekday_of(on_date)],
        start_time=dt.time(9, 0),
        expected_duration_minutes=60,
        monthly_rate=4000,
    )


@pytest.mark.django_db
def test_e2e_leave_removes_only_the_absent_maid(
    authenticated_client, resident, resident_user, maids, society, leave_date
):
    """The reported bug: searching a date one maid is away on returned
    'nobody is free' instead of the other two."""
    absent, *others = maids
    engagement = _engagement(society, resident, absent, leave_date)
    client = authenticated_client(resident_user)
    category = ServiceCategory.objects.get(slug="emergency-assistance")

    def search(start_time, duration=30):
        response = client.get(
            reverse("v1:bookings:match"),
            {
                "category": category.pk,
                "date": leave_date.isoformat(),
                "start_time": start_time,
                "duration_minutes": duration,
            },
        )
        assert response.status_code == 200
        return [row["id"] for row in response.data["results"]]

    # The engagement runs 09:00–10:00, so before any leave the absent maid is
    # already committed then and free later.
    assert absent.pk not in search("09:15")
    assert sorted(search("14:00")) == sorted(m.pk for m in maids)

    # --- The maid applies for leave through the real endpoint --------------
    applied = authenticated_client(absent.user).post(
        reverse("v1:scheduling:leave-list"),
        {
            "engagement": engagement.pk,
            "leave_date": leave_date.isoformat(),
            "reason": "Child unwell",
        },
        format="json",
    )
    assert applied.status_code == 201, applied.data

    leave = LeaveRequest.objects.get(engagement=engagement, leave_date=leave_date)
    assert leave.status == LeaveStatus.APPROVED

    # --- During the hours she is away ---------------------------------------
    during = search("09:15")

    assert absent.pk not in during, "the maid on leave should not be offered"
    assert sorted(during) == sorted(m.pk for m in others), (
        "the other maids must still be available — this is the bug"
    )

    # --- ...and the rest of her day is untouched ----------------------------
    # Leave is per engagement, not per day: she took one morning visit off, so
    # she is still bookable that afternoon and still working her other
    # households. Blocking the whole day would cost her the rest of it.
    after = search("14:00")
    assert absent.pk in after, "leave must not block hours it does not cover"
    assert sorted(after) == sorted(m.pk for m in maids)


@pytest.mark.django_db
def test_withdrawn_leave_stops_holding_the_visit_window(
    resident, maids, society, leave_date
):
    """Withdrawing releases the hours the leave was holding.

    Tested against a *paused* engagement on purpose. An active engagement
    occupies its own visit hours whether or not leave was taken, so it would
    mask the thing under test; pausing it leaves the leave row as the only
    reason those hours could be held. That is also the one case where the
    leave check is not merely agreeing with the engagement check — the
    worker's statement that she cannot work then outlives the engagement being
    suspended.
    """
    absent = maids[0]
    engagement = _engagement(society, resident, absent, leave_date)
    leave = LeaveRequest.objects.create(
        society=society, engagement=engagement, worker=absent,
        leave_date=leave_date,
    )
    engagement.status = EngagementStatus.PAUSED
    engagement.save(update_fields=["status"])

    def busy_at(hour, minute=0, duration=30):
        return absent.pk in conflicted_worker_ids(
            [absent.pk],
            on_date=leave_date,
            start_minutes=minutes_of(dt.time(hour, minute)),
            duration_minutes=duration,
        )

    # The visit was 09:00–10:00.
    assert busy_at(9, 15) is True
    assert busy_at(14) is False, "leave never covered the afternoon"

    # She can come after all.
    leave.status = LeaveStatus.WITHDRAWN
    leave.save(update_fields=["status"])

    assert busy_at(9, 15) is False, "withdrawn leave must not keep blocking her"


@pytest.mark.django_db
def test_e2e_a_maid_on_leave_cannot_be_booked_over_the_visit_she_missed(
    authenticated_client, resident, resident_user, maids, society, leave_date
):
    """Search and creation must agree — otherwise a resident who deep-links to
    a worker can still book somebody who is away.

    And they must agree on the *narrow* rule: refused for the hours she is
    away, allowed for the rest of her day.
    """
    absent = maids[0]
    engagement = _engagement(society, resident, absent, leave_date)
    LeaveRequest.objects.create(
        society=society, engagement=engagement, worker=absent,
        leave_date=leave_date,
    )
    category = ServiceCategory.objects.get(slug="emergency-assistance")

    def book(start_time):
        return authenticated_client(resident_user).post(
            reverse("v1:bookings:booking-list"),
            {
                "worker": absent.pk,
                "category": category.pk,
                "scheduled_date": leave_date.isoformat(),
                "start_time": start_time,
                "expected_duration_minutes": 30,
                "quoted_price": 500,
            },
            format="json",
        )

    over_the_visit = book("09:15")
    assert over_the_visit.status_code == 409
    assert over_the_visit.data["error"]["code"] == "slot_conflict"

    later_the_same_day = book("14:00")
    assert later_the_same_day.status_code == 201, later_the_same_day.data


@pytest.mark.django_db
def test_e2e_the_replacement_is_busy_only_for_the_covered_hours(
    authenticated_client, resident, resident_user, maids, society, leave_date
):
    """A maid covering somebody else's 09:00 visit is committed then, and free
    the rest of the day."""
    absent, cover, spare = maids
    engagement = _engagement(society, resident, absent, leave_date)
    leave = LeaveRequest.objects.create(
        society=society, engagement=engagement, worker=absent,
        leave_date=leave_date, status=LeaveStatus.REPLACEMENT_CONFIRMED,
        replacement=cover, replacement_confirmed_at=timezone.now(),
    )
    assert leave.replacement_id == cover.pk

    category = ServiceCategory.objects.get(slug="emergency-assistance")

    def search(start_time, duration):
        response = authenticated_client(resident_user).get(
            reverse("v1:bookings:match"),
            {
                "category": category.pk,
                "date": leave_date.isoformat(),
                "start_time": start_time,
                "duration_minutes": duration,
            },
        )
        return [row["id"] for row in response.data["results"]]

    # 09:00-10:00 is the covered visit.
    during = search("09:15", 30)
    assert cover.pk not in during
    assert absent.pk not in during
    assert spare.pk in during

    # 14:00 is not — for either of them. The absent worker took one morning
    # visit off, not the day.
    after = search("14:00", 60)
    assert cover.pk in after
    assert absent.pk in after
