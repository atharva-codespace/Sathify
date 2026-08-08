"""Bug 6 — applying for leave raises the household's unread count.

The server half. The notification was always created and counted correctly;
the badge never refreshed. See
``mobile/test/widget/notification_badge_refresher_test.dart`` for the half that
was actually broken.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import Role
from apps.hiring.models import Engagement, weekday_of
from apps.notifications.models import Notification, NotificationCategory
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
def maid(db, django_user_model, society):
    user = django_user_model.objects.create_user(
        phone_number="9860000021", password="test-pass-12345",
        role=Role.WORKER, society=society, first_name="Sunita", last_name="D",
        is_approved=True,
    )
    return WorkerProfile.objects.create(
        user=user, photo="workers/x.jpg", is_available=True,
    )


@pytest.mark.django_db
def test_applying_for_leave_raises_the_residents_unread_count(
    authenticated_client, resident, resident_user, maid, society
):
    """Bug 6, server half: is the notification created, and does the count
    endpoint the badge reads actually move?"""
    leave_date = timezone.localdate() + dt.timedelta(days=3)
    service_type, _ = ServiceType.objects.get_or_create(name="Maid", slug="maid")
    engagement = Engagement.objects.create(
        society=society, resident=resident, worker=maid,
        service_type=service_type,
        days_of_week=[weekday_of(leave_date)], start_time=dt.time(9, 0),
        expected_duration_minutes=60, monthly_rate=4000,
    )

    resident_client = authenticated_client(resident_user)
    count_url = reverse("v1:notifications:unread-count")

    before = resident_client.get(count_url)
    assert before.status_code == 200
    assert before.data["unread"] == 0

    # --- The maid applies for leave -----------------------------------------
    applied = authenticated_client(maid.user).post(
        reverse("v1:scheduling:leave-list"),
        {
            "engagement": engagement.pk,
            "leave_date": leave_date.isoformat(),
            "reason": "Child unwell",
        },
        format="json",
    )
    assert applied.status_code == 201, applied.data

    # --- The row exists, addressed to the resident --------------------------
    notifications = Notification.objects.filter(
        recipient=resident_user, category=NotificationCategory.URGENT_LEAVE
    )
    assert notifications.count() == 1
    assert "Sunita" in notifications.get().title

    # --- ...and the endpoint the badge polls reflects it --------------------
    after = resident_client.get(count_url)
    assert after.data["unread"] == 1, (
        "the badge endpoint must move without the resident opening anything"
    )

    # A second leave stacks, so the badge can read 2.
    second_date = leave_date + dt.timedelta(days=7)
    authenticated_client(maid.user).post(
        reverse("v1:scheduling:leave-list"),
        {"engagement": engagement.pk, "leave_date": second_date.isoformat()},
        format="json",
    )
    assert resident_client.get(count_url).data["unread"] == 2
