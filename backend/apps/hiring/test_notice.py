"""
Module 4.6 — the notice period: tests.

Two properties carry the weight, and both are about money or a livelihood.

``TestNoticeIsServed`` pins that an engagement serving notice stays **active**.
If it ever flips early, the worker's remaining visits vanish from the schedule,
the gate stops recognising them, and they lose days they were entitled to work.

``TestFinalPay`` pins the promise the rule actually makes: *paid in full, for
the days worked*. The trap is that the derived schedule only expands active
engagements, so a finished one looks like somebody with nothing scheduled — and
the "nothing scheduled, full rate stands" fallback would then pay a whole month
for a fortnight.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.hiring.models import (
    NOTICE_PERIOD_DAYS,
    Engagement,
    EngagementEndReason,
    EngagementStatus,
)
from apps.hiring.services import (
    NoticeAlreadyGiven,
    NoticeTooShort,
    RequestNotActionable,
    close_engagements_past_notice,
    earliest_last_working_day,
    give_notice,
    withdraw_notice,
)
from apps.payments.services import salary_basis
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
    """Every weekday, 09:00, ₹4,000 a month, started a while ago."""
    return Engagement.objects.create(
        society=society,
        resident=resident,
        worker=worker,
        service_type=maid_service,
        days_of_week=[0, 1, 2, 3, 4],
        start_time=dt.time(9, 0),
        expected_duration_minutes=90,
        monthly_rate=4000,
        started_on=timezone.localdate() - dt.timedelta(days=120),
    )


@pytest.fixture
def dues_settled(engagement):
    """This month's wages already paid, so notice is not gated on money.

    Module 4.6 refuses a *household* notice while the days already worked this
    month are unpaid, and a long-running engagement always has some (see
    ``hiring/settlement.py`` — a scheduled day counts unless leave says
    otherwise). That gate has its own tests in ``test_settlement.py``; the ones
    here are about the notice mechanics, so they start from a household that is
    square rather than re-testing the gate by accident.

    A full month's salary is used rather than the exact pro-rata so the fixture
    cannot drift out of date as the arithmetic changes.
    """
    from apps.payments.models import Payment, PaymentKind, PaymentStatus

    return Payment.objects.create(
        society=engagement.society,
        resident=engagement.resident,
        worker=engagement.worker,
        engagement=engagement,
        kind=PaymentKind.ENGAGEMENT_SALARY,
        amount_paise=engagement.monthly_rate * 100,
        status=PaymentStatus.PAID,
        paid_at=timezone.now(),
    )


class TestNoticeIsServed:
    def test_ten_days_is_the_floor(self, engagement, resident_user):
        leave_on = timezone.localdate() + dt.timedelta(days=3)

        with pytest.raises(NoticeTooShort):
            give_notice(
                engagement,
                by=resident_user,
                reason=EngagementEndReason.RESIDENT_ENDED,
                requested_last_day=leave_on,
            )

    def test_the_default_last_day_is_exactly_ten_days_out(
        self, engagement, worker_user
    ):
        given = give_notice(
            engagement, by=worker_user, reason=EngagementEndReason.WORKER_ENDED
        )

        expected = timezone.localdate() + dt.timedelta(days=NOTICE_PERIOD_DAYS)
        assert given.last_working_day == expected
        assert earliest_last_working_day() == expected

    def test_a_longer_notice_is_allowed(self, engagement, worker_user):
        """Ten days is a floor, not a ceiling. Working a fortnight is fine."""
        chosen = timezone.localdate() + dt.timedelta(days=21)

        given = give_notice(
            engagement,
            by=worker_user,
            reason=EngagementEndReason.WORKER_ENDED,
            requested_last_day=chosen,
        )

        assert given.last_working_day == chosen

    def test_the_engagement_stays_active_while_notice_runs(
        self, engagement, worker_user
    ):
        """The load-bearing one.

        Flipping the status early would drop the worker's remaining visits from
        the schedule, and with them the gate entries that pay for those days.
        """
        given = give_notice(
            engagement, by=worker_user, reason=EngagementEndReason.WORKER_ENDED
        )

        assert given.status == EngagementStatus.ACTIVE
        assert given.is_serving_notice is True
        assert given.notice_days_remaining == NOTICE_PERIOD_DAYS

    def test_remaining_visits_are_counted_not_days(self, engagement, worker_user):
        """Ten days of notice is not ten more visits."""
        engagement.days_of_week = [1]  # Tuesdays only
        engagement.save(update_fields=["days_of_week"])

        given = give_notice(
            engagement, by=worker_user, reason=EngagementEndReason.WORKER_ENDED
        )

        # Ten calendar days spans one or two Tuesdays, never ten.
        assert given.visits_remaining() in {1, 2}
        assert given.notice_days_remaining == 10

    def test_notice_cannot_be_given_twice(self, engagement, worker_user):
        give_notice(engagement, by=worker_user, reason=EngagementEndReason.WORKER_ENDED)

        with pytest.raises(NoticeAlreadyGiven):
            give_notice(
                engagement, by=worker_user, reason=EngagementEndReason.WORKER_ENDED
            )

    def test_both_sides_are_told(self, engagement, worker_user):
        from apps.notifications.models import Notification

        give_notice(engagement, by=worker_user, reason=EngagementEndReason.WORKER_ENDED)

        told = set(
            Notification.objects.filter(
                recipient__in=[engagement.worker.user, engagement.resident.user]
            ).values_list("recipient_id", flat=True)
        )
        # The household needs the warning; the worker needs the date in writing.
        assert told == {engagement.worker.user_id, engagement.resident.user_id}

    def test_a_terminated_engagement_cannot_be_given_notice(
        self, engagement, worker_user
    ):
        engagement.terminate(reason=EngagementEndReason.ADMIN_ENDED)

        with pytest.raises(RequestNotActionable):
            give_notice(
                engagement, by=worker_user, reason=EngagementEndReason.WORKER_ENDED
            )

    def test_notice_can_be_withdrawn_before_the_last_day(
        self, engagement, worker_user
    ):
        given = give_notice(
            engagement, by=worker_user, reason=EngagementEndReason.WORKER_ENDED
        )

        restored = withdraw_notice(given, by=worker_user)

        assert restored.last_working_day is None
        assert restored.is_serving_notice is False
        assert restored.status == EngagementStatus.ACTIVE


class TestClosingOut:
    def test_the_engagement_closes_once_the_last_day_has_passed(
        self, engagement, worker_user
    ):
        give_notice(engagement, by=worker_user, reason=EngagementEndReason.WORKER_ENDED)
        Engagement.objects.filter(pk=engagement.pk).update(
            last_working_day=timezone.localdate() - dt.timedelta(days=1)
        )

        closed = close_engagements_past_notice()
        engagement.refresh_from_db()

        assert closed == 1
        assert engagement.status == EngagementStatus.TERMINATED
        assert engagement.ended_at is not None

    def test_it_does_not_close_early(self, engagement, worker_user):
        give_notice(engagement, by=worker_user, reason=EngagementEndReason.WORKER_ENDED)

        assert close_engagements_past_notice() == 0
        engagement.refresh_from_db()
        assert engagement.status == EngagementStatus.ACTIVE

    def test_the_sweep_is_idempotent(self, engagement, worker_user):
        """It runs off a read path, so it runs constantly."""
        give_notice(engagement, by=worker_user, reason=EngagementEndReason.WORKER_ENDED)
        Engagement.objects.filter(pk=engagement.pk).update(
            last_working_day=timezone.localdate() - dt.timedelta(days=1)
        )

        assert close_engagements_past_notice() == 1
        assert close_engagements_past_notice() == 0

    def test_the_last_working_day_itself_is_still_worked(
        self, engagement, worker_user
    ):
        """Inclusive, not exclusive. Getting this wrong costs a day's wage."""
        give_notice(engagement, by=worker_user, reason=EngagementEndReason.WORKER_ENDED)
        Engagement.objects.filter(pk=engagement.pk).update(
            last_working_day=timezone.localdate()
        )

        assert close_engagements_past_notice() == 0
        engagement.refresh_from_db()
        assert engagement.status == EngagementStatus.ACTIVE


class TestFinalPay:
    """"Paid in full, for the days worked" — the promise, checked."""

    def test_a_finished_engagement_is_not_paid_a_whole_month(
        self, engagement, worker_user
    ):
        """The trap this feature would otherwise walk into.

        ``worker_schedule`` expands active engagements only. Once notice is
        served and the engagement closes, it returns nothing for the month — and
        ``salary_basis``'s "nothing scheduled, so the full rate stands" fallback
        would hand over a full month's pay for a fortnight of work.
        """
        today = timezone.localdate()
        period_start = today.replace(day=1)
        last_day = period_start + dt.timedelta(days=9)

        give_notice(engagement, by=worker_user, reason=EngagementEndReason.WORKER_ENDED)
        Engagement.objects.filter(pk=engagement.pk).update(
            last_working_day=last_day,
            status=EngagementStatus.TERMINATED,
            ended_at=timezone.now(),
        )
        engagement.refresh_from_db()

        basis = salary_basis(
            engagement,
            period_start=period_start,
            period_end=period_start + dt.timedelta(days=27),
        )

        # Some visits were expected — up to the last working day, and no further.
        assert basis.expected_visits > 0
        assert basis.expected_visits <= 8  # ten calendar days of weekdays
        # And with nothing logged at the gate, nothing is suggested.
        assert basis.suggested_paise == 0

    def test_expected_visits_stop_at_the_last_working_day(
        self, engagement, worker_user
    ):
        today = timezone.localdate()
        period_start = today.replace(day=1)

        give_notice(engagement, by=worker_user, reason=EngagementEndReason.WORKER_ENDED)
        Engagement.objects.filter(pk=engagement.pk).update(
            last_working_day=period_start,  # one day into the month
            status=EngagementStatus.TERMINATED,
            ended_at=timezone.now(),
        )
        engagement.refresh_from_db()

        basis = salary_basis(
            engagement,
            period_start=period_start,
            period_end=period_start + dt.timedelta(days=27),
        )

        # At most the single day, and zero if the 1st fell on a weekend.
        assert basis.expected_visits <= 1

    def test_an_active_engagement_is_unaffected(self, engagement):
        """The existing path must not change — this is additive only."""
        today = timezone.localdate()
        basis = salary_basis(
            engagement,
            period_start=today - dt.timedelta(days=6),
            period_end=today,
        )

        assert basis.expected_visits > 0


class TestNoticeProtectsTheTrustScore:
    """The notice period's *only* enforcement mechanism.

    Deliberately not wage withholding — that is legally exposed under the
    Payment of Wages Act, lands hardest on the workers this platform serves,
    and backfires: somebody who knows leaving costs a week's pay does not give
    notice, they stop turning up, and the household gets less warning. So the
    cost of walking out is a mark that decays, and the cost of doing it
    properly is nothing.
    """

    def abandoned_count(self, worker):
        from apps.ratings.services import worker_trust_inputs

        return worker_trust_inputs(worker).abandoned_jobs

    def test_walking_out_counts_as_abandonment(self, engagement, worker):
        engagement.terminate(reason=EngagementEndReason.WORKER_ENDED)

        assert self.abandoned_count(worker) == 1

    def test_serving_notice_costs_the_worker_nothing(
        self, engagement, worker, worker_user
    ):
        """The one that makes the rule work.

        Before this, a worker who gave ten days' notice and worked every one of
        them was scored identically to one who vanished overnight — which
        removes any reason to give notice at all.
        """
        give_notice(engagement, by=worker_user, reason=EngagementEndReason.WORKER_ENDED)
        Engagement.objects.filter(pk=engagement.pk).update(
            last_working_day=timezone.localdate() - dt.timedelta(days=1)
        )
        close_engagements_past_notice()

        engagement.refresh_from_db()
        assert engagement.status == EngagementStatus.TERMINATED
        assert self.abandoned_count(worker) == 0

    def test_the_household_ending_it_never_marks_the_worker(
        self, engagement, worker, resident_user, dues_settled
    ):
        """A resident letting somebody go says nothing about the worker."""
        give_notice(
            engagement, by=resident_user, reason=EngagementEndReason.RESIDENT_ENDED
        )
        Engagement.objects.filter(pk=engagement.pk).update(
            last_working_day=timezone.localdate() - dt.timedelta(days=1)
        )
        close_engagements_past_notice()

        assert self.abandoned_count(worker) == 0

    def test_no_wages_are_withheld_from_somebody_who_walked_out(
        self, engagement, worker
    ):
        """The mark is the whole penalty. Pay is untouched.

        If a deduction ever appears on this path, the rule has become a fine on
        the poorest people using the platform.
        """
        from apps.payments.models import Payment

        engagement.terminate(reason=EngagementEndReason.WORKER_ENDED)

        assert self.abandoned_count(worker) == 1
        assert not Payment.objects.filter(worker=worker).exists()


class TestNoticeApi:
    def url(self, engagement_id):
        return reverse("v1:hiring:engagement-notice", args=[engagement_id])

    def test_a_worker_gives_notice(
        self, authenticated_client, worker_user, engagement
    ):
        response = authenticated_client(worker_user).post(
            self.url(engagement.pk),
            {"reason": EngagementEndReason.WORKER_ENDED},
            format="json",
        )

        assert response.status_code == 200
        assert response.data["engagement"]["is_serving_notice"] is True
        assert response.data["engagement"]["status"] == EngagementStatus.ACTIVE

    def test_a_resident_gives_notice(
        self, authenticated_client, resident_user, engagement, dues_settled
    ):
        response = authenticated_client(resident_user).post(
            self.url(engagement.pk),
            {"reason": EngagementEndReason.RESIDENT_ENDED},
            format="json",
        )

        assert response.status_code == 200
        assert response.data["engagement"]["last_working_day"] is not None

    def test_too_short_is_refused_with_the_earliest_permitted_day(
        self, authenticated_client, worker_user, engagement
    ):
        """The client needs the date back, not just a rejection."""
        response = authenticated_client(worker_user).post(
            self.url(engagement.pk),
            {
                "reason": EngagementEndReason.WORKER_ENDED,
                "last_working_day": (
                    timezone.localdate() + dt.timedelta(days=2)
                ).isoformat(),
            },
            format="json",
        )

        assert response.status_code == 400
        assert response.data["error"]["code"] == "notice_too_short"
        assert response.data["error"]["details"]["notice_period_days"] == 10
        assert response.data["error"]["details"]["earliest_last_working_day"]

    def test_a_stranger_cannot_end_somebody_elses_arrangement(
        self, authenticated_client, guard_user, engagement
    ):
        response = authenticated_client(guard_user).post(
            self.url(engagement.pk),
            {"reason": EngagementEndReason.RESIDENT_ENDED},
            format="json",
        )

        assert response.status_code in {403, 404}
        engagement.refresh_from_db()
        assert engagement.last_working_day is None

    def test_notice_can_be_withdrawn_through_the_api(
        self, authenticated_client, worker_user, engagement
    ):
        client = authenticated_client(worker_user)
        client.post(
            self.url(engagement.pk),
            {"reason": EngagementEndReason.WORKER_ENDED},
            format="json",
        )

        response = client.post(
            reverse("v1:hiring:engagement-notice-withdraw", args=[engagement.pk]),
            {},
            format="json",
        )

        assert response.status_code == 200
        assert response.data["engagement"]["is_serving_notice"] is False
