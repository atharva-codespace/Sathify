"""
Module 6.5 — urgent leave ("chutti"): tests.

Three properties carry the weight here, and each of them is somebody's money or
somebody's day:

``TestInstantApproval`` pins that leave is never pending. If a review step ever
creeps in, the notice this whole workflow exists to buy disappears — a worker
who has to wait for an answer stops asking and simply does not turn up.

``TestSettlement`` pins that a day of leave is deducted **once**. Salary is
pro-rated from attendance, so an absence already costs the worker that day;
deducting again in the leave record would dock it twice, and the person it would
be taken from is the least able to spot it.

``TestScheduleIntegration`` pins that a covered visit reaches the gate roster. A
replacement who is not on it arrives to a guard with no record of them and is
turned away from a visit the household is expecting.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.hiring.models import Engagement
from apps.payments.models import PaymentKind, ReplacementSplit
from apps.payments.models import Payment
from apps.payments.services import daily_rate_paise
from apps.scheduling.models import LeaveRequest, LeaveStatus
from apps.scheduling.schedule import society_schedule, worker_day, worker_schedule
from apps.scheduling.services import (
    DuplicateLeave,
    LeaveDateInvalid,
    LeaveNotActionable,
    assign_replacement,
    close_lapsed_leave,
    replacement_candidates,
    request_leave,
    respond_to_leave,
    settle_leave,
    withdraw_leave,
)
from apps.societies.models import Flat, Resident, Tower
from apps.workers.models import ServiceType, WorkerProfile

pytestmark = pytest.mark.django_db


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


@pytest.fixture
def other_worker(db, django_user_model, society, maid_service):
    """A second worker in the same society, available to cover."""
    user = django_user_model.objects.create_user(
        phone_number="9000000123",
        password="pw",
        role="worker",
        society=society,
        first_name="Sunita",
        last_name="Rao",
        is_approved=True,
    )
    profile = WorkerProfile.objects.create(
        user=user, photo="workers/photos/other.jpg", is_available=True
    )
    profile.service_types.add(maid_service)
    return profile


@pytest.fixture
def engagement(society, resident, worker, maid_service):
    """Mon/Wed/Fri, 09:00, 90 minutes, ₹4,000 a month."""
    return Engagement.objects.create(
        society=society,
        resident=resident,
        worker=worker,
        service_type=maid_service,
        days_of_week=[0, 2, 4],
        start_time=dt.time(9, 0),
        expected_duration_minutes=90,
        monthly_rate=4000,
    )


def next_weekday(weekday: int, *, after: dt.date | None = None) -> dt.date:
    """The next date falling on ``weekday``, strictly after ``after``.

    Relative to today rather than a pinned calendar date, because leave in the
    past is refused — a fixed date would start failing the moment it passed.
    """
    day = (after or timezone.localdate()) + dt.timedelta(days=1)
    while day.weekday() != weekday:
        day += dt.timedelta(days=1)
    return day


@pytest.fixture
def leave_date(engagement):
    return next_weekday(0)  # a Monday, which this engagement calls for


# ---------------------------------------------------------------------------


class TestInstantApproval:
    def test_leave_is_approved_the_moment_it_is_asked_for(self, engagement, leave_date):
        """No pending state, ever.

        A worker who must wait for permission to stay home with a sick child
        does not wait — they stop turning up, and the household finds out at 7am.
        Instant approval is what buys the notice.
        """
        leave = request_leave(engagement, leave_date=leave_date, reason="Child unwell")

        assert leave.status == LeaveStatus.APPROVED
        assert leave.needs_resident_response is True
        assert leave.pk is not None

    def test_a_reason_is_never_required(self, engagement, leave_date):
        """A private emergency should not have to be described to be believed."""
        leave = request_leave(engagement, leave_date=leave_date)
        assert leave.reason == ""
        assert leave.status == LeaveStatus.APPROVED

    def test_the_household_is_notified(self, engagement, leave_date):
        from apps.notifications.models import Notification, NotificationCategory

        request_leave(engagement, leave_date=leave_date)

        note = Notification.objects.filter(
            recipient=engagement.resident.user,
            category=NotificationCategory.URGENT_LEAVE,
        ).first()
        assert note is not None
        # Unmutable by policy — this is the one they most need to receive.
        assert note.is_safety_critical is True
        # A route the client can actually match; see Routes in app_router.dart.
        assert note.data.get("route") == "/schedule"

    def test_applying_twice_does_not_take_two_days_off(self, engagement, leave_date):
        """The same request twice is one absence, not two.

        A double tap on a bad connection must not settle the day twice.
        """
        request_leave(engagement, leave_date=leave_date)

        with pytest.raises(DuplicateLeave):
            request_leave(engagement, leave_date=leave_date)

        assert LeaveRequest.objects.filter(engagement=engagement).count() == 1

    def test_a_withdrawn_day_can_be_taken_again(self, engagement, leave_date):
        leave = request_leave(engagement, leave_date=leave_date)
        withdraw_leave(leave)

        again = request_leave(engagement, leave_date=leave_date, reason="Still unwell")

        assert again.pk == leave.pk  # reopened, not duplicated
        assert again.status == LeaveStatus.APPROVED
        assert again.reason == "Still unwell"

    def test_leave_cannot_be_taken_for_a_day_that_has_passed(self, engagement):
        yesterday = timezone.localdate() - dt.timedelta(days=1)
        with pytest.raises(LeaveDateInvalid):
            request_leave(engagement, leave_date=yesterday)

    def test_leave_cannot_be_taken_for_a_day_with_no_visit(self, engagement):
        """The engagement runs Mon/Wed/Fri, so there is nothing to miss on Tuesday."""
        with pytest.raises(LeaveDateInvalid):
            request_leave(engagement, leave_date=next_weekday(1))


class TestHouseholdResponse:
    def test_the_household_is_never_asked_whether_to_allow_it(self):
        """The response serializer offers one question, and it is not that one."""
        from apps.scheduling.serializers import LeaveResponseSerializer

        assert set(LeaveResponseSerializer().fields) == {"needs_replacement"}

    def test_no_cover_needed_settles_the_day(self, engagement, leave_date):
        leave = request_leave(engagement, leave_date=leave_date)

        leave = respond_to_leave(leave, needs_replacement=False)

        assert leave.status == LeaveStatus.WAIVED
        assert leave.is_settled is True
        assert leave.replacement_paise == 0
        assert leave.resident_responded_at is not None

    def test_cover_wanted_opens_the_search(self, engagement, leave_date):
        leave = request_leave(engagement, leave_date=leave_date)

        leave = respond_to_leave(leave, needs_replacement=True)

        assert leave.status == LeaveStatus.REPLACEMENT_REQUESTED
        assert leave.is_settled is False  # nothing is owed until somebody agrees

    def test_waived_and_unfilled_are_not_the_same_thing(self, engagement, leave_date):
        """Both mean nobody came. Only one of them is the platform failing.

        Collapsing them would hide the one worth measuring.
        """
        assert LeaveStatus.WAIVED != LeaveStatus.UNFILLED


class TestReplacementMatching:
    def test_the_absent_worker_is_never_offered_as_their_own_cover(
        self, engagement, leave_date, other_worker
    ):
        leave = request_leave(engagement, leave_date=leave_date)
        candidates = replacement_candidates(leave)

        assert leave.worker_id not in [w.pk for w, _ in candidates]

    def test_a_free_worker_is_offered(self, engagement, leave_date, other_worker):
        leave = request_leave(engagement, leave_date=leave_date)
        candidates = replacement_candidates(leave)

        assert other_worker.pk in [w.pk for w, _ in candidates]

    def test_a_worker_already_booked_at_that_hour_is_not_offered(
        self, society, resident, other_worker, maid_service, engagement, leave_date
    ):
        # Give the candidate their own visit over the same window.
        Engagement.objects.create(
            society=society,
            resident=resident,
            worker=other_worker,
            service_type=maid_service,
            days_of_week=[0, 2, 4],
            start_time=dt.time(9, 0),
            expected_duration_minutes=90,
            monthly_rate=3000,
        )
        leave = request_leave(engagement, leave_date=leave_date)

        assert replacement_candidates(leave) == []

    def test_a_worker_cannot_cover_their_own_absence(self, engagement, leave_date, worker):
        leave = request_leave(engagement, leave_date=leave_date)
        with pytest.raises(LeaveNotActionable):
            assign_replacement(leave, worker)


class TestSettlement:
    def test_the_replacement_is_paid_the_whole_day_by_default(
        self, engagement, leave_date, other_worker
    ):
        """No ReplacementSplit agreed means the whole day goes to whoever worked it.

        Taking a share off the person who actually turned up requires an
        explicit prior agreement, not a default.
        """
        leave = request_leave(engagement, leave_date=leave_date)
        respond_to_leave(leave, needs_replacement=True)

        leave = assign_replacement(leave, other_worker)

        expected = daily_rate_paise(engagement)
        assert leave.status == LeaveStatus.REPLACEMENT_CONFIRMED
        assert leave.day_rate_paise == expected
        assert leave.replacement_paise == expected
        assert leave.forgone_paise == expected  # what she forgoes is what he receives

    def test_a_payment_row_is_created_for_the_replacement(
        self, engagement, leave_date, other_worker
    ):
        leave = request_leave(engagement, leave_date=leave_date)
        respond_to_leave(leave, needs_replacement=True)
        leave = assign_replacement(leave, other_worker)

        payment = Payment.objects.filter(
            worker=other_worker, kind=PaymentKind.REPLACEMENT
        ).first()

        assert payment is not None
        assert payment.amount_paise == leave.replacement_paise
        assert payment.resident_id == engagement.resident_id
        assert payment.engagement_id == engagement.pk

    def test_an_agreed_split_is_honoured(self, engagement, leave_date, other_worker):
        """60/40 — the "regular worker sent a substitute" arrangement."""
        ReplacementSplit.objects.create(
            engagement=engagement, replacement_share_percent=60
        )
        leave = request_leave(engagement, leave_date=leave_date)
        respond_to_leave(leave, needs_replacement=True)

        leave = assign_replacement(leave, other_worker)

        rate = daily_rate_paise(engagement)
        assert leave.replacement_paise == rate * 60 // 100
        # She keeps 40% of the day, so she forgoes only the 60% he received.
        assert leave.forgone_paise == leave.replacement_paise

    def test_settlement_is_idempotent(self, engagement, leave_date, other_worker):
        """Calling it twice must not pay the replacement twice."""
        leave = request_leave(engagement, leave_date=leave_date)
        respond_to_leave(leave, needs_replacement=True)
        leave = assign_replacement(leave, other_worker)

        settle_leave(leave)
        settle_leave(leave)

        assert Payment.objects.filter(kind=PaymentKind.REPLACEMENT).count() == 1

    def test_leave_does_not_deduct_from_the_absent_workers_salary(
        self, engagement, leave_date, other_worker
    ):
        """The one that would cost a worker real money if it broke.

        Salary is pro-rated from attendance: a day not worked is already a day
        not paid. If this model also deducted, the same absence would be docked
        twice. Nothing here may write to a salary payment.
        """
        leave = request_leave(engagement, leave_date=leave_date)
        respond_to_leave(leave, needs_replacement=True)
        assign_replacement(leave, other_worker)

        salary_payments = Payment.objects.filter(
            worker=engagement.worker, kind=PaymentKind.ENGAGEMENT_SALARY
        )
        assert not salary_payments.exists()
        # The only money that moved is the replacement's.
        assert Payment.objects.filter(worker=engagement.worker).count() == 0

    def test_an_unanswered_day_closes_as_unfilled_once_it_has_passed(
        self, engagement, resident
    ):
        """No scheduler exists, so this runs off a read path. It must be safe."""
        leave = LeaveRequest.objects.create(
            society=engagement.society,
            engagement=engagement,
            worker=engagement.worker,
            leave_date=timezone.localdate() - dt.timedelta(days=2),
            status=LeaveStatus.REPLACEMENT_REQUESTED,
        )

        closed = close_lapsed_leave()
        leave.refresh_from_db()

        assert closed == 1
        assert leave.status == LeaveStatus.UNFILLED
        assert leave.is_settled is True
        assert leave.replacement_paise == 0


class TestWithdrawal:
    def test_a_worker_may_change_their_mind_before_cover_is_arranged(
        self, engagement, leave_date
    ):
        leave = request_leave(engagement, leave_date=leave_date)
        leave = withdraw_leave(leave)
        assert leave.status == LeaveStatus.WITHDRAWN

    def test_but_not_once_somebody_has_rearranged_their_day_for_it(
        self, engagement, leave_date, other_worker
    ):
        """The same harm this module prevents, pointed the other way."""
        leave = request_leave(engagement, leave_date=leave_date)
        respond_to_leave(leave, needs_replacement=True)
        assign_replacement(leave, other_worker)

        with pytest.raises(LeaveNotActionable):
            withdraw_leave(leave)


class TestScheduleIntegration:
    def test_the_visit_stays_on_the_schedule_and_is_marked(
        self, engagement, leave_date, worker
    ):
        """Not deleted — payroll counts expected visits from this same list."""
        request_leave(engagement, leave_date=leave_date)

        items = worker_day(worker.pk, leave_date)

        assert len(items) == 1
        assert items[0].on_leave is True
        assert items[0].leave_status == LeaveStatus.APPROVED

    def test_a_withdrawn_leave_leaves_no_mark(self, engagement, leave_date, worker):
        leave = request_leave(engagement, leave_date=leave_date)
        withdraw_leave(leave)

        items = worker_day(worker.pk, leave_date)

        assert items[0].on_leave is False

    def test_the_cover_visit_appears_on_the_replacements_own_schedule(
        self, engagement, leave_date, other_worker
    ):
        leave = request_leave(engagement, leave_date=leave_date)
        respond_to_leave(leave, needs_replacement=True)
        assign_replacement(leave, other_worker)

        items = worker_day(other_worker.pk, leave_date)

        assert len(items) == 1
        assert items[0].is_cover is True
        assert items[0].covering_for_name == engagement.worker.user.get_full_name()
        # The gate matches an arrival against the engagement being served.
        assert items[0].source_id == engagement.pk

    def test_the_cover_visit_reaches_the_gate_roster(
        self, engagement, leave_date, other_worker, society
    ):
        """Otherwise the replacement is turned away at the gate."""
        leave = request_leave(engagement, leave_date=leave_date)
        respond_to_leave(leave, needs_replacement=True)
        assign_replacement(leave, other_worker)

        roster = society_schedule(society.pk, leave_date, leave_date)
        worker_ids = {item.worker_id for item in roster}

        assert other_worker.pk in worker_ids
        # The original visit is still listed, marked, so the guard can see why
        # somebody unfamiliar is arriving for it.
        assert engagement.worker_id in worker_ids

    def test_the_household_sees_who_is_covering(
        self, engagement, leave_date, other_worker, worker
    ):
        leave = request_leave(engagement, leave_date=leave_date)
        respond_to_leave(leave, needs_replacement=True)
        assign_replacement(leave, other_worker)

        item = worker_day(worker.pk, leave_date)[0]

        assert item.cover_worker_name == other_worker.user.get_full_name()


class TestLeaveApi:
    def test_a_worker_applies_and_it_is_approved_in_the_response(
        self, authenticated_client, worker_user, engagement, leave_date
    ):
        response = authenticated_client(worker_user).post(
            reverse("v1:scheduling:leave-list"),
            {
                "engagement": engagement.pk,
                "leave_date": leave_date.isoformat(),
                "reason": "Child unwell",
            },
            format="json",
        )

        assert response.status_code == 201
        assert response.data["leave"]["status"] == LeaveStatus.APPROVED

    def test_a_second_application_conflicts_rather_than_duplicating(
        self, authenticated_client, worker_user, engagement, leave_date
    ):
        client = authenticated_client(worker_user)
        payload = {"engagement": engagement.pk, "leave_date": leave_date.isoformat()}

        client.post(reverse("v1:scheduling:leave-list"), payload, format="json")
        second = client.post(reverse("v1:scheduling:leave-list"), payload, format="json")

        assert second.status_code == 409
        assert second.data["error"]["code"] == "duplicate_leave"

    def test_a_worker_cannot_take_leave_from_somebody_elses_engagement(
        self, authenticated_client, other_worker, engagement, leave_date
    ):
        response = authenticated_client(other_worker.user).post(
            reverse("v1:scheduling:leave-list"),
            {"engagement": engagement.pk, "leave_date": leave_date.isoformat()},
            format="json",
        )

        assert response.status_code == 404

    def test_the_household_answers_and_the_day_is_settled(
        self, authenticated_client, resident_user, engagement, leave_date
    ):
        leave = request_leave(engagement, leave_date=leave_date)

        response = authenticated_client(resident_user).post(
            reverse("v1:scheduling:leave-response", args=[leave.pk]),
            {"needs_replacement": False},
            format="json",
        )

        assert response.status_code == 200
        assert response.data["leave"]["status"] == LeaveStatus.WAIVED
        assert response.data["leave"]["is_settled"] is True

    def test_a_worker_cannot_answer_on_the_households_behalf(
        self, authenticated_client, worker_user, engagement, leave_date
    ):
        leave = request_leave(engagement, leave_date=leave_date)

        response = authenticated_client(worker_user).post(
            reverse("v1:scheduling:leave-response", args=[leave.pk]),
            {"needs_replacement": True},
            format="json",
        )

        assert response.status_code == 403

    def test_candidates_and_assignment_run_end_to_end(
        self, authenticated_client, resident_user, engagement, leave_date, other_worker
    ):
        leave = request_leave(engagement, leave_date=leave_date)
        client = authenticated_client(resident_user)

        client.post(
            reverse("v1:scheduling:leave-response", args=[leave.pk]),
            {"needs_replacement": True},
            format="json",
        )
        candidates = client.get(
            reverse("v1:scheduling:leave-candidates", args=[leave.pk])
        )
        assert candidates.status_code == 200
        assert candidates.data["count"] >= 1

        assigned = client.post(
            reverse("v1:scheduling:leave-replacement", args=[leave.pk]),
            {"replacement": other_worker.pk},
            format="json",
        )

        assert assigned.status_code == 200
        assert assigned.data["leave"]["status"] == LeaveStatus.REPLACEMENT_CONFIRMED
        assert assigned.data["leave"]["replacement_paise"] > 0

    def test_a_worker_sees_both_their_leave_and_what_they_are_covering(
        self, authenticated_client, engagement, leave_date, other_worker
    ):
        leave = request_leave(engagement, leave_date=leave_date)
        respond_to_leave(leave, needs_replacement=True)
        assign_replacement(leave, other_worker)

        response = authenticated_client(other_worker.user).get(
            reverse("v1:scheduling:leave-list")
        )

        assert response.status_code == 200
        assert response.data["count"] == 1
        assert response.data["results"][0]["replacement"] == other_worker.pk

    def test_one_household_cannot_read_anothers_leave(
        self, authenticated_client, guard_user, engagement, leave_date
    ):
        request_leave(engagement, leave_date=leave_date)

        response = authenticated_client(guard_user).get(
            reverse("v1:scheduling:leave-list")
        )

        # A guard is neither party to the engagement nor an administrator.
        assert response.status_code in {200, 403}
        if response.status_code == 200:
            assert response.data["count"] == 0
