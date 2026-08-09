"""
Module 6 — Scheduling & Task Management: tests.

The assembly tests carry the most weight. A schedule that silently drops a
visit, or shows one that was cancelled, sends a worker to the wrong place —
and because the schedule is derived rather than stored, a mistake in the
expansion is invisible in the database and only shows up in someone's day.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import Role
from apps.bookings.models import Booking, BookingStatus, ServiceCategory
from apps.hiring.models import Engagement, EngagementStatus
from apps.scheduling.models import Reminder, ReminderStatus, TaskTiming
from apps.scheduling.schedule import (
    MAX_SCHEDULE_DAYS,
    ScheduleRangeTooWide,
    find_overlaps,
    worker_day,
    worker_schedule,
)
from apps.scheduling.services import (
    check_conflict,
    conflicted_worker_ids,
    due_reminders,
    effective_timing,
    ensure_reminders_for_worker,
)
from apps.societies.models import Flat, Resident, Tower
from apps.workers.models import ServiceType, WorkerProfile

pytestmark = pytest.mark.django_db

#: 3 August 2026 is a Monday — pinned so weekday arithmetic is not a guess.
MONDAY = dt.date(2026, 8, 3)
TUESDAY = dt.date(2026, 8, 4)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


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


def make_engagement(society, resident, worker, service, **kwargs):
    kwargs.setdefault("days_of_week", [0, 2, 4])  # Mon, Wed, Fri
    kwargs.setdefault("start_time", dt.time(9, 0))
    kwargs.setdefault("expected_duration_minutes", 90)
    kwargs.setdefault("monthly_rate", 4000)
    return Engagement.objects.create(
        society=society, resident=resident, worker=worker, service_type=service, **kwargs
    )


def make_booking(society, resident, worker, day, **kwargs):
    kwargs.setdefault("start_time", dt.time(14, 0))
    kwargs.setdefault("expected_duration_minutes", 120)
    kwargs.setdefault("quoted_price", 2000)
    kwargs.setdefault("status", BookingStatus.CONFIRMED)
    return Booking.objects.create(
        society=society,
        resident=resident,
        worker=worker,
        category=ServiceCategory.objects.get(slug="deep-cleaning"),
        scheduled_date=day,
        **kwargs,
    )


# ---------------------------------------------------------------------------
# 6.1 Calendar assembly
# ---------------------------------------------------------------------------


class TestWorkerSchedule:
    def test_a_recurring_engagement_expands_onto_its_weekdays(
        self, society, resident, worker, maid_service
    ):
        make_engagement(society, resident, worker, maid_service, days_of_week=[0, 2, 4])

        items = worker_schedule(worker.pk, MONDAY, MONDAY + dt.timedelta(days=6))

        assert [item.date for item in items] == [
            MONDAY,
            MONDAY + dt.timedelta(days=2),
            MONDAY + dt.timedelta(days=4),
        ]

    def test_an_engagement_does_not_appear_on_other_weekdays(
        self, society, resident, worker, maid_service
    ):
        make_engagement(society, resident, worker, maid_service, days_of_week=[0])

        assert worker_day(worker.pk, TUESDAY) == []

    def test_a_paused_engagement_disappears_from_the_calendar(
        self, society, resident, worker, maid_service
    ):
        """The whole reason the schedule is derived rather than stored."""
        engagement = make_engagement(society, resident, worker, maid_service)
        assert worker_day(worker.pk, MONDAY)

        engagement.pause("Away for a month")
        assert worker_day(worker.pk, MONDAY) == []

    def test_a_terminated_engagement_disappears_too(
        self, society, resident, worker, maid_service
    ):
        engagement = make_engagement(society, resident, worker, maid_service)
        engagement.terminate(reason="resident_ended")

        assert worker_day(worker.pk, MONDAY) == []

    def test_bookings_and_engagements_appear_in_one_list(
        self, society, resident, worker, maid_service
    ):
        """Module 6.1's entire point: one schedule, not two systems."""
        make_engagement(society, resident, worker, maid_service, days_of_week=[0])
        make_booking(society, resident, worker, MONDAY)

        items = worker_day(worker.pk, MONDAY)

        assert len(items) == 2
        assert {item.source for item in items} == {"engagement", "booking"}

    def test_the_day_is_ordered_by_start_time(
        self, society, resident, worker, maid_service
    ):
        make_engagement(
            society, resident, worker, maid_service,
            days_of_week=[0], start_time=dt.time(16, 0),
        )
        make_booking(society, resident, worker, MONDAY, start_time=dt.time(8, 0))

        items = worker_day(worker.pk, MONDAY)
        assert [item.start_time for item in items] == [dt.time(8, 0), dt.time(16, 0)]

    def test_a_pending_booking_is_on_the_calendar_but_flagged_unconfirmed(
        self, society, resident, worker
    ):
        """It still blocks the slot, and the worker still needs to answer it."""
        make_booking(society, resident, worker, MONDAY, status=BookingStatus.PENDING)

        item = worker_day(worker.pk, MONDAY)[0]
        assert item.is_confirmed is False

    def test_a_cancelled_booking_is_not_on_the_calendar(
        self, society, resident, worker
    ):
        make_booking(society, resident, worker, MONDAY, status=BookingStatus.CANCELLED)
        assert worker_day(worker.pk, MONDAY) == []

    def test_items_carry_where_they_came_from(
        self, society, resident, worker, maid_service
    ):
        engagement = make_engagement(society, resident, worker, maid_service, days_of_week=[0])

        item = worker_day(worker.pk, MONDAY)[0]
        assert item.source == "engagement"
        assert item.source_id == engagement.pk
        assert item.flat_label == str(resident.flat)
        assert item.is_recurring is True

    def test_end_time_is_derived_from_the_duration(
        self, society, resident, worker, maid_service
    ):
        make_engagement(
            society, resident, worker, maid_service,
            days_of_week=[0], start_time=dt.time(9, 0), expected_duration_minutes=90,
        )

        assert worker_day(worker.pk, MONDAY)[0].end_time == dt.time(10, 30)

    def test_a_range_wider_than_the_cap_is_refused(self, worker):
        """Recurring visits expand per day, so an unbounded range is unbounded work."""
        with pytest.raises(ScheduleRangeTooWide):
            worker_schedule(
                worker.pk, MONDAY, MONDAY + dt.timedelta(days=MAX_SCHEDULE_DAYS)
            )

    def test_an_inverted_range_is_refused(self, worker):
        with pytest.raises(ScheduleRangeTooWide):
            worker_schedule(worker.pk, MONDAY, MONDAY - dt.timedelta(days=1))

    def test_query_count_does_not_grow_with_the_range(
        self, society, resident, worker, maid_service
    ):
        """The property that matters: cost is independent of how many days.

        Compared rather than hardcoded, which is what this test always claimed
        to do. A fixed number fails the moment a legitimate constant query is
        added — Module 6.5's leave lookup added two — while saying nothing about
        the thing actually worth protecting. An N+1 regression scales with the
        range and still fails here, which is the point.

        The ceiling is a second, weaker guard: constant is not the same as
        cheap, and a dozen constant queries per schedule read would be its own
        problem on a free-tier instance.
        """
        make_engagement(society, resident, worker, maid_service, days_of_week=[0, 1, 2, 3, 4])
        make_booking(society, resident, worker, MONDAY)

        with CaptureQueriesContext(connection) as one_day:
            worker_schedule(worker.pk, MONDAY, MONDAY)

        with CaptureQueriesContext(connection) as fortnight:
            worker_schedule(worker.pk, MONDAY, MONDAY + dt.timedelta(days=13))

        assert len(fortnight) == len(one_day)
        assert len(one_day) <= 8, f"{len(one_day)} queries for one day is too many"


class TestFindOverlaps:
    def test_back_to_back_visits_are_not_a_conflict(
        self, society, resident, worker, maid_service
    ):
        make_engagement(
            society, resident, worker, maid_service,
            days_of_week=[0], start_time=dt.time(9, 0), expected_duration_minutes=60,
        )
        make_booking(
            society, resident, worker, MONDAY,
            start_time=dt.time(10, 0), expected_duration_minutes=60,
        )

        assert find_overlaps(worker_day(worker.pk, MONDAY)) == []

    def test_an_existing_double_booking_is_surfaced(
        self, society, resident, worker, maid_service
    ):
        """Data predating the check, or created via admin, must still be visible."""
        make_engagement(
            society, resident, worker, maid_service,
            days_of_week=[0], start_time=dt.time(9, 0), expected_duration_minutes=120,
        )
        make_booking(
            society, resident, worker, MONDAY,
            start_time=dt.time(10, 0), expected_duration_minutes=60,
        )

        clashes = find_overlaps(worker_day(worker.pk, MONDAY))
        assert len(clashes) == 1


# ---------------------------------------------------------------------------
# 6.3 Conflict detection
# ---------------------------------------------------------------------------


class TestConflictDetection:
    def test_an_engagement_blocks_an_overlapping_slot(
        self, society, resident, worker, maid_service
    ):
        make_engagement(
            society, resident, worker, maid_service,
            days_of_week=[0], start_time=dt.time(9, 0), expected_duration_minutes=120,
        )

        report = check_conflict(
            worker.pk, on_date=MONDAY, start_time=dt.time(10, 0), duration_minutes=60
        )

        assert report.has_conflict
        assert len(report.clashes) == 1
        assert "Already committed" in report.summary

    def test_a_free_slot_reports_no_conflict(
        self, society, resident, worker, maid_service
    ):
        make_engagement(
            society, resident, worker, maid_service,
            days_of_week=[0], start_time=dt.time(9, 0), expected_duration_minutes=60,
        )

        report = check_conflict(
            worker.pk, on_date=MONDAY, start_time=dt.time(14, 0), duration_minutes=60
        )

        assert not report.has_conflict
        assert report.summary == "No conflicts."

    def test_excluding_a_booking_ignores_its_own_slot(
        self, society, resident, worker
    ):
        """Confirming a booking must not see itself as the conflict."""
        booking = make_booking(
            society, resident, worker, MONDAY,
            start_time=dt.time(10, 0), expected_duration_minutes=60,
        )

        with_it = check_conflict(
            worker.pk, on_date=MONDAY, start_time=dt.time(10, 0), duration_minutes=60
        )
        without_it = check_conflict(
            worker.pk,
            on_date=MONDAY,
            start_time=dt.time(10, 0),
            duration_minutes=60,
            exclude_booking_id=booking.pk,
        )

        assert with_it.has_conflict
        assert not without_it.has_conflict

    def test_the_batched_engine_agrees_with_the_single_worker_check(
        self, society, resident, worker, maid_service
    ):
        """Module 5's matching and Module 6's check must never disagree."""
        make_engagement(
            society, resident, worker, maid_service,
            days_of_week=[0], start_time=dt.time(9, 0), expected_duration_minutes=120,
        )

        batched = conflicted_worker_ids(
            [worker.pk], on_date=MONDAY, start_minutes=10 * 60, duration_minutes=60
        )
        single = check_conflict(
            worker.pk, on_date=MONDAY, start_time=dt.time(10, 0), duration_minutes=60
        )

        assert (worker.pk in batched) is single.has_conflict

    def test_module_5_still_refuses_a_conflicting_booking(
        self, authenticated_client, society, resident, resident_user, worker, maid_service
    ):
        """The delegation to Module 6 must not have loosened Module 5's guard."""
        from apps.bookings.models import DayAvailability

        # A Monday at least a week out, never merely "the next one".
        #
        # This used to take the nearest future Monday, which on a Sunday is
        # tomorrow — and after about 22:00 that is inside the society's 12-hour
        # notice window, so the booking was refused with `notice_too_short`
        # before it ever reached the conflict check this test is about. It
        # passed all day and failed late on Sundays, which is the worst kind of
        # flake to debug because the code under test is innocent.
        today = timezone.localdate()
        future_monday = today + dt.timedelta(days=((7 - today.weekday()) % 7) + 7)
        DayAvailability.objects.create(worker=worker, date=future_monday, is_available=True)
        make_engagement(
            society, resident, worker, maid_service,
            days_of_week=[0], start_time=dt.time(9, 0), expected_duration_minutes=120,
        )

        response = authenticated_client(resident_user).post(
            reverse("v1:bookings:booking-list"),
            {
                "worker": worker.pk,
                "category": ServiceCategory.objects.get(slug="deep-cleaning").pk,
                "scheduled_date": future_monday.isoformat(),
                "start_time": "10:00",
                "expected_duration_minutes": 60,
                "quoted_price": 1500,
            },
            format="json",
        )

        assert response.status_code == 409
        assert response.data["error"]["code"] == "slot_conflict"


class TestConflictCheckEndpoint:
    URL = "v1:scheduling:conflict-check"

    def test_reports_the_colliding_item(
        self, authenticated_client, resident_user, society, resident, worker, maid_service
    ):
        make_engagement(
            society, resident, worker, maid_service,
            days_of_week=[0], start_time=dt.time(9, 0), expected_duration_minutes=120,
        )

        response = authenticated_client(resident_user).get(
            reverse(self.URL),
            {
                "worker": worker.pk,
                "date": MONDAY.isoformat(),
                "start_time": "10:00",
                "duration_minutes": 60,
            },
        )

        assert response.status_code == 200
        assert response.data["has_conflict"] is True
        assert len(response.data["clashes"]) == 1

    def test_another_societys_worker_is_not_inspectable(
        self, authenticated_client, resident_user, worker, society, django_user_model
    ):
        from apps.societies.models import Society, SocietyStatus

        other = Society.objects.create(
            name="Blue Ridge", address_line="X", city="Pune",
            state="Maharashtra", pincode="411006", status=SocietyStatus.ACTIVE,
        )
        outsider = django_user_model.objects.create_user(
            phone_number="9800000071", password="test-pass-12345",
            role=Role.WORKER, society=other, is_approved=True,
        )
        outside_worker = WorkerProfile.objects.create(
            user=outsider, photo="workers/photos/x.jpg"
        )

        response = authenticated_client(resident_user).get(
            reverse(self.URL),
            {
                "worker": outside_worker.pk,
                "date": MONDAY.isoformat(),
                "start_time": "10:00",
                "duration_minutes": 60,
            },
        )
        assert response.status_code == 404


# ---------------------------------------------------------------------------
# 6.2 Task timing
# ---------------------------------------------------------------------------


class TestTaskTiming:
    def url(self, engagement_id):
        return reverse("v1:scheduling:task-timing", args=[engagement_id])

    def test_defaults_come_from_the_engagement_when_nothing_is_set(
        self, society, resident, worker, maid_service
    ):
        engagement = make_engagement(
            society, resident, worker, maid_service,
            start_time=dt.time(9, 0), expected_duration_minutes=90,
        )

        timing = effective_timing(engagement)

        assert timing["expected_arrival"] == dt.time(9, 0)
        assert timing["expected_departure"] == dt.time(10, 30)
        assert timing["is_customised"] is False

    def test_resident_sets_the_expected_window(
        self, authenticated_client, resident_user, society, resident, worker, maid_service
    ):
        engagement = make_engagement(society, resident, worker, maid_service)

        response = authenticated_client(resident_user).put(
            self.url(engagement.pk),
            {
                "expected_arrival": "08:30",
                "arrival_grace_minutes": 10,
                "task_notes": "Please start with the kitchen.",
            },
            format="json",
        )

        assert response.status_code == 201
        assert response.data["timing"]["expected_arrival"] == "08:30:00"
        assert response.data["timing"]["is_customised"] is True

    def test_setting_it_twice_updates_rather_than_failing(
        self, authenticated_client, resident_user, society, resident, worker, maid_service
    ):
        engagement = make_engagement(society, resident, worker, maid_service)
        client = authenticated_client(resident_user)

        first = client.put(
            self.url(engagement.pk), {"expected_arrival": "08:30"}, format="json"
        )
        second = client.put(
            self.url(engagement.pk), {"expected_arrival": "09:15"}, format="json"
        )

        assert first.status_code == 201
        assert second.status_code == 200
        assert TaskTiming.objects.filter(engagement=engagement).count() == 1

    def test_the_worker_can_read_what_is_expected_of_them(
        self, authenticated_client, worker_user, society, resident, worker, maid_service
    ):
        engagement = make_engagement(society, resident, worker, maid_service)
        TaskTiming.objects.create(
            engagement=engagement,
            expected_arrival=dt.time(8, 30),
            task_notes="Start with the kitchen.",
        )

        response = authenticated_client(worker_user).get(self.url(engagement.pk))

        assert response.status_code == 200
        assert response.data["task_notes"] == "Start with the kitchen."

    def test_the_worker_cannot_set_it(
        self, authenticated_client, worker_user, society, resident, worker, maid_service
    ):
        """The resident sets expectations; the worker meets them."""
        engagement = make_engagement(society, resident, worker, maid_service)

        response = authenticated_client(worker_user).put(
            self.url(engagement.pk), {"expected_arrival": "11:00"}, format="json"
        )
        assert response.status_code == 403

    def test_a_non_primary_resident_cannot_set_it(
        self, authenticated_client, resident_user, resident, society, worker, maid_service
    ):
        engagement = make_engagement(society, resident, worker, maid_service)
        resident.is_primary = False
        resident.save(update_fields=["is_primary"])

        response = authenticated_client(resident_user).put(
            self.url(engagement.pk), {"expected_arrival": "08:30"}, format="json"
        )
        assert response.status_code == 403

    def test_departure_must_be_after_arrival(
        self, authenticated_client, resident_user, society, resident, worker, maid_service
    ):
        engagement = make_engagement(society, resident, worker, maid_service)

        response = authenticated_client(resident_user).put(
            self.url(engagement.pk),
            {"expected_arrival": "10:00", "expected_departure": "09:00"},
            format="json",
        )
        assert response.status_code == 400

    def test_an_unrelated_resident_cannot_see_the_timing(
        self, authenticated_client, society, resident, worker, maid_service, django_user_model
    ):
        engagement = make_engagement(society, resident, worker, maid_service)
        tower = Tower.objects.create(society=society, name="B", floors=4)
        other_flat = Flat.objects.create(tower=tower, number="101", floor=1)
        other = django_user_model.objects.create_user(
            phone_number="9800000072", password="test-pass-12345",
            role=Role.RESIDENT, society=society, is_approved=True,
        )
        Resident.objects.create(user=other, flat=other_flat, is_primary=True)

        response = authenticated_client(other).get(self.url(engagement.pk))
        assert response.status_code == 404

    def test_lateness_is_measured_past_the_grace_window(
        self, society, resident, worker, maid_service
    ):
        engagement = make_engagement(society, resident, worker, maid_service)
        timing = TaskTiming.objects.create(
            engagement=engagement,
            expected_arrival=dt.time(9, 0),
            arrival_grace_minutes=15,
        )

        assert timing.lateness_minutes(dt.time(9, 10)) == 0
        assert timing.lateness_minutes(dt.time(9, 15)) == 0
        assert timing.lateness_minutes(dt.time(9, 40)) == 25


# ---------------------------------------------------------------------------
# 6.1 Calendar endpoints
# ---------------------------------------------------------------------------


def today_engagement(society, resident, worker, service, **kwargs):
    """An engagement that occurs today, whatever weekday today is."""
    kwargs["days_of_week"] = [timezone.localdate().weekday()]
    return make_engagement(society, resident, worker, service, **kwargs)


class TestScheduleEndpoints:
    def test_worker_sees_their_own_day(
        self, authenticated_client, worker_user, society, resident, worker, maid_service
    ):
        today_engagement(society, resident, worker, maid_service)

        response = authenticated_client(worker_user).get(
            reverse("v1:scheduling:my-today")
        )

        assert response.status_code == 200
        assert response.data["count"] == 1
        assert response.data["results"][0]["source"] == "engagement"

    def test_resident_sees_their_households_day(
        self, authenticated_client, resident_user, society, resident, worker, maid_service
    ):
        today_engagement(society, resident, worker, maid_service)

        response = authenticated_client(resident_user).get(
            reverse("v1:scheduling:my-today")
        )
        assert response.data["count"] == 1

    def test_agenda_spans_the_requested_range(
        self, authenticated_client, worker_user, society, resident, worker, maid_service
    ):
        make_engagement(
            society, resident, worker, maid_service, days_of_week=[0, 1, 2, 3, 4, 5, 6]
        )

        response = authenticated_client(worker_user).get(
            reverse("v1:scheduling:my-agenda"),
            {"from": MONDAY.isoformat(), "to": (MONDAY + dt.timedelta(days=6)).isoformat()},
        )

        assert response.data["count"] == 7

    def test_an_over_wide_range_is_refused(
        self, authenticated_client, worker_user, worker
    ):
        response = authenticated_client(worker_user).get(
            reverse("v1:scheduling:my-agenda"),
            {
                "from": MONDAY.isoformat(),
                "to": (MONDAY + dt.timedelta(days=MAX_SCHEDULE_DAYS)).isoformat(),
            },
        )
        assert response.status_code == 400

    def test_a_worker_without_a_profile_gets_an_empty_day(
        self, authenticated_client, worker_user
    ):
        response = authenticated_client(worker_user).get(
            reverse("v1:scheduling:my-today")
        )

        assert response.status_code == 200
        assert response.data["count"] == 0

    def test_admin_reads_a_workers_agenda_with_conflicts(
        self, authenticated_client, admin_user, society, resident, worker, maid_service
    ):
        make_engagement(
            society, resident, worker, maid_service,
            days_of_week=[0], start_time=dt.time(9, 0), expected_duration_minutes=120,
        )
        make_booking(
            society, resident, worker, MONDAY,
            start_time=dt.time(10, 0), expected_duration_minutes=60,
        )

        response = authenticated_client(admin_user).get(
            reverse("v1:scheduling:worker-agenda", args=[worker.pk]),
            {"from": MONDAY.isoformat(), "to": MONDAY.isoformat()},
        )

        assert response.status_code == 200
        assert len(response.data["conflicts"]) == 1

    def test_a_resident_cannot_read_a_workers_full_agenda(
        self, authenticated_client, resident_user, worker
    ):
        response = authenticated_client(resident_user).get(
            reverse("v1:scheduling:worker-agenda", args=[worker.pk])
        )
        assert response.status_code == 403

    def test_admin_sees_the_whole_society(
        self, authenticated_client, admin_user, society, resident, worker, maid_service
    ):
        today_engagement(society, resident, worker, maid_service)

        response = authenticated_client(admin_user).get(
            reverse("v1:scheduling:society-agenda")
        )

        assert response.status_code == 200
        assert response.data["count"] == 1

    def test_guard_has_no_schedule_access(self, authenticated_client, guard_user):
        response = authenticated_client(guard_user).get(
            reverse("v1:scheduling:my-today")
        )
        assert response.status_code == 403


# ---------------------------------------------------------------------------
# 6.4 Reminders
# ---------------------------------------------------------------------------


class TestReminders:
    def test_reminders_are_queued_for_upcoming_visits(
        self, society, resident, worker, maid_service
    ):
        make_engagement(
            society, resident, worker, maid_service,
            days_of_week=[(timezone.localdate() + dt.timedelta(days=1)).weekday()],
        )

        created = ensure_reminders_for_worker(worker)

        assert created >= 1
        assert Reminder.objects.filter(recipient=worker.user).exists()

    def test_generation_is_idempotent(
        self, society, resident, worker, maid_service
    ):
        """It runs on every schedule read, so duplicates would pile up fast."""
        make_engagement(
            society, resident, worker, maid_service,
            days_of_week=[(timezone.localdate() + dt.timedelta(days=1)).weekday()],
        )

        ensure_reminders_for_worker(worker)
        before = Reminder.objects.count()
        ensure_reminders_for_worker(worker)

        assert Reminder.objects.count() == before

    def test_no_reminder_is_queued_for_a_visit_already_past(
        self, society, resident, worker, maid_service
    ):
        make_engagement(
            society, resident, worker, maid_service,
            days_of_week=[timezone.localdate().weekday()],
            start_time=dt.time(0, 1),
        )

        ensure_reminders_for_worker(worker)

        assert not Reminder.objects.filter(
            event_at__lte=timezone.now(), status=ReminderStatus.SCHEDULED
        ).exists()

    def test_reminders_can_be_switched_off_per_engagement(
        self, society, resident, worker, maid_service
    ):
        engagement = make_engagement(
            society, resident, worker, maid_service,
            days_of_week=[(timezone.localdate() + dt.timedelta(days=1)).weekday()],
        )
        TaskTiming.objects.create(engagement=engagement, reminders_enabled=False)

        assert ensure_reminders_for_worker(worker) == 0

    def test_due_reminders_exclude_ones_not_yet_ready(
        self, society, resident, worker
    ):
        Reminder.objects.create(
            society=society,
            recipient=worker.user,
            kind="upcoming_engagement",
            event_at=timezone.now() + dt.timedelta(hours=10),
            send_after=timezone.now() + dt.timedelta(hours=9),
            title="Later",
            body="Not yet",
        )

        assert due_reminders(recipient=worker.user).count() == 0

    def test_a_stale_reminder_is_cancelled_rather_than_sent_late(
        self, society, resident, worker
    ):
        """A reminder about a visit that already happened is worse than none."""
        stale = Reminder.objects.create(
            society=society,
            recipient=worker.user,
            kind="upcoming_engagement",
            event_at=timezone.now() - dt.timedelta(hours=1),
            send_after=timezone.now() - dt.timedelta(hours=2),
            title="Past",
            body="Already happened",
        )

        assert due_reminders(recipient=worker.user).count() == 0
        stale.refresh_from_db()
        assert stale.status == ReminderStatus.CANCELLED

    def test_due_endpoint_returns_ready_reminders(
        self, authenticated_client, worker_user, society, worker
    ):
        Reminder.objects.create(
            society=society,
            recipient=worker.user,
            kind="upcoming_engagement",
            event_at=timezone.now() + dt.timedelta(hours=1),
            send_after=timezone.now() - dt.timedelta(minutes=5),
            title="Soon",
            body="Coming up",
        )

        response = authenticated_client(worker_user).get(
            reverse("v1:scheduling:reminders-due")
        )

        assert response.status_code == 200
        assert len(response.data) == 1

    def test_marking_delivered_is_idempotent(
        self, authenticated_client, worker_user, society, worker
    ):
        reminder = Reminder.objects.create(
            society=society,
            recipient=worker.user,
            kind="upcoming_engagement",
            event_at=timezone.now() + dt.timedelta(hours=1),
            send_after=timezone.now(),
            title="Soon",
            body="Coming up",
        )
        client = authenticated_client(worker_user)
        url = reverse("v1:scheduling:reminder-delivered", args=[reminder.pk])

        client.post(url, {"delivered": True}, format="json")
        first_sent_at = Reminder.objects.get(pk=reminder.pk).sent_at
        client.post(url, {"delivered": True}, format="json")

        assert Reminder.objects.get(pk=reminder.pk).sent_at == first_sent_at

    def test_a_failed_delivery_records_why(
        self, authenticated_client, worker_user, society, worker
    ):
        reminder = Reminder.objects.create(
            society=society,
            recipient=worker.user,
            kind="upcoming_engagement",
            event_at=timezone.now() + dt.timedelta(hours=1),
            send_after=timezone.now(),
            title="Soon",
            body="Coming up",
        )

        authenticated_client(worker_user).post(
            reverse("v1:scheduling:reminder-delivered", args=[reminder.pk]),
            {"delivered": False, "failure_reason": "No FCM token"},
            format="json",
        )

        reminder.refresh_from_db()
        assert reminder.status == ReminderStatus.FAILED
        assert reminder.failure_reason == "No FCM token"

    def test_a_user_cannot_touch_someone_elses_reminder(
        self, authenticated_client, resident_user, society, worker
    ):
        reminder = Reminder.objects.create(
            society=society,
            recipient=worker.user,
            kind="upcoming_engagement",
            event_at=timezone.now() + dt.timedelta(hours=1),
            send_after=timezone.now(),
            title="Soon",
            body="Coming up",
        )

        response = authenticated_client(resident_user).post(
            reverse("v1:scheduling:reminder-delivered", args=[reminder.pk]),
            {"delivered": True},
            format="json",
        )
        assert response.status_code == 404
