"""Bug 2 — marking a job done must also make it payable.

Two completion mechanisms existed and neither did the whole job: the booking
endpoint moved the status but wrote no ``TaskCompletion``, and Module 6.6 wrote
the completion but left the booking un-completed — and Module 8 refuses to open
a payment until the booking itself reads COMPLETED.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import Role
from apps.bookings.models import Booking, BookingStatus, ServiceCategory
from apps.notifications.models import Notification, NotificationCategory
from apps.scheduling.models import TaskCompletion
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
        phone_number="9880000011", password="test-pass-12345",
        role=Role.WORKER, society=society, first_name="Sunita", last_name="D",
        is_approved=True,
    )
    return WorkerProfile.objects.create(
        user=user, photo="workers/x.jpg", is_available=True,
        trust_score=70, average_rating=4.2,
    )


@pytest.fixture
def started_booking(resident, maid, society):
    """A confirmed booking whose start time has passed — she has just finished."""
    started = timezone.localtime() - dt.timedelta(hours=2)
    return Booking.objects.create(
        society=society,
        resident=resident,
        worker=maid,
        category=ServiceCategory.objects.get(slug="deep-cleaning"),
        scheduled_date=started.date(),
        start_time=started.time().replace(microsecond=0),
        expected_duration_minutes=120,
        quoted_price=1500,
        status=BookingStatus.CONFIRMED,
    )


@pytest.mark.django_db
def test_e2e_maid_marks_done_and_resident_can_pay(
    authenticated_client, resident, resident_user, maid, started_booking
):
    """Maid taps Mark as done -> booking completes -> resident is asked to pay
    -> the payment the server previously refused now opens."""
    maid_client = authenticated_client(maid.user)
    resident_client = authenticated_client(resident_user)

    # --- Before: payment is refused, because nothing is marked done ---------
    too_early = resident_client.post(
        reverse("v1:payments:pay-booking"),
        {"booking": started_booking.pk},
        format="json",
    )
    assert too_early.status_code == 409
    assert too_early.data["error"]["code"] == "not_completed"

    # --- Step 1: the maid taps "Mark as done" -------------------------------
    done = maid_client.post(
        reverse("v1:scheduling:mark-task-complete"),
        {
            "booking": started_booking.pk,
            "visit_date": started_booking.scheduled_date.isoformat(),
            "note": "All rooms cleaned.",
        },
        format="json",
    )
    assert done.status_code == 201, done.data

    # --- Step 2: DB state — BOTH rows moved, not just one -------------------
    started_booking.refresh_from_db()
    assert started_booking.status == BookingStatus.COMPLETED
    assert started_booking.completed_at is not None

    completion = TaskCompletion.objects.get(booking=started_booking)
    assert completion.worker_id == maid.pk
    assert completion.note == "All rooms cleaned."

    # --- Step 3: the resident is actually told to pay -----------------------
    prompts = Notification.objects.filter(
        recipient=resident_user, category=NotificationCategory.PAYMENT
    )
    assert prompts.count() == 1
    prompt = prompts.get()
    assert "1500" in prompt.title
    assert prompt.data["route"] == "/bookings"
    assert prompt.data["booking"] == started_booking.pk

    # --- Step 4: payment now opens ------------------------------------------
    now_allowed = resident_client.post(
        reverse("v1:payments:pay-booking"),
        {"booking": started_booking.pk},
        format="json",
    )
    assert now_allowed.status_code in (200, 201), now_allowed.data

    # --- Step 5: re-marking is idempotent and does not re-nag ---------------
    again = maid_client.post(
        reverse("v1:scheduling:mark-task-complete"),
        {
            "booking": started_booking.pk,
            "visit_date": started_booking.scheduled_date.isoformat(),
        },
        format="json",
    )
    assert again.status_code == 201
    assert TaskCompletion.objects.filter(booking=started_booking).count() == 1
    assert Notification.objects.filter(
        recipient=resident_user, category=NotificationCategory.PAYMENT
    ).count() == 1


@pytest.mark.django_db
def test_e2e_marking_an_unstarted_booking_is_a_clean_refusal(
    authenticated_client, resident, maid, society
):
    """The refusal must read as a business rule, not as a photo-storage
    outage the worker is told to retry."""
    future = timezone.localdate() + dt.timedelta(days=3)
    booking = Booking.objects.create(
        society=society, resident=resident, worker=maid,
        category=ServiceCategory.objects.get(slug="deep-cleaning"),
        scheduled_date=future, start_time=dt.time(10, 0),
        expected_duration_minutes=120, quoted_price=1500,
        status=BookingStatus.CONFIRMED,
    )

    response = authenticated_client(maid.user).post(
        reverse("v1:scheduling:mark-task-complete"),
        {"booking": booking.pk, "visit_date": future.isoformat()},
        format="json",
    )

    assert response.status_code == 409
    assert response.data["error"]["code"] == "booking_not_actionable"
    assert "storage" not in str(response.data).lower()


@pytest.mark.django_db
def test_e2e_engagement_visit_completion_does_not_ask_for_money(
    authenticated_client, resident, resident_user, maid, society
):
    """Recurring work is paid monthly, so a daily visit must not raise a
    per-visit payment prompt."""
    from apps.hiring.models import Engagement, weekday_of
    from apps.workers.models import ServiceType

    today = timezone.localdate()
    service_type = ServiceType.objects.create(name="Maid", slug="maid")
    engagement = Engagement.objects.create(
        society=society, resident=resident, worker=maid,
        service_type=service_type,
        days_of_week=[weekday_of(today)], start_time=dt.time(9, 0),
        expected_duration_minutes=60, monthly_rate=4000,
    )

    response = authenticated_client(maid.user).post(
        reverse("v1:scheduling:mark-task-complete"),
        {"engagement": engagement.pk, "visit_date": today.isoformat()},
        format="json",
    )

    assert response.status_code == 201
    assert not Notification.objects.filter(
        recipient=resident_user, category=NotificationCategory.PAYMENT
    ).exists()
    # ...but the household is still told the work is done.
    assert Notification.objects.filter(
        recipient=resident_user, category=NotificationCategory.SCHEDULE
    ).exists()
