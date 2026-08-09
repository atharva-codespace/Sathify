"""
Module 5.5 — emergency booking, end to end.

Two of these are regressions for reported bugs and are marked as such. The rest
cover the flow that was built around them. The race tests carry the most weight:
a broadcast deliberately creates a thundering herd, so "two workers both get the
job" is not an exotic edge case here, it is what happens on an ordinary Tuesday
if the claim is not atomic.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.bookings import emergency as emergency_service
from apps.bookings.models import (
    Booking,
    BookingOffer,
    BookingStatus,
    OfferState,
    ServiceCategory,
)
from apps.bookings.policy import emergency_surcharge
from apps.notifications.models import Notification, NotificationCategory
from apps.payments.models import Payment, PaymentKind, PaymentStatus
from apps.societies.models import Flat, Resident, Tower
from apps.workers.models import WorkerProfile

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def emergency_category(db):
    return ServiceCategory.objects.get(slug="emergency-assistance")


@pytest.fixture
def flat(society):
    tower = Tower.objects.create(society=society, name="A", floors=10)
    return Flat.objects.create(tower=tower, number="301", floor=3)


@pytest.fixture
def resident(resident_user, flat):
    return Resident.objects.create(user=resident_user, flat=flat, is_primary=True)


def make_maid(django_user_model, society, phone, first_name):
    user = django_user_model.objects.create_user(
        phone_number=phone,
        password="test-pass-12345",
        role="worker",
        society=society,
        first_name=first_name,
        last_name="K",
        is_approved=True,
    )
    return WorkerProfile.objects.create(
        user=user,
        photo="workers/photos/test.jpg",
        is_available=True,
        trust_score=70,
        average_rating=4.4,
    )


@pytest.fixture
def maids(db, django_user_model, society):
    """Three approved, searchable workers in the same society."""
    return [
        make_maid(django_user_model, society, "9870000001", "Sunita"),
        make_maid(django_user_model, society, "9870000002", "Lakshmi"),
        make_maid(django_user_model, society, "9870000003", "Meena"),
    ]


def raise_request(resident, society, category, *, when=None, **kwargs):
    moment = when or (timezone.localtime() + dt.timedelta(minutes=20))
    booking, payment = emergency_service.raise_emergency(
        resident=resident,
        society=society,
        category=category,
        scheduled_date=moment.date(),
        start_time=moment.time().replace(microsecond=0),
        **kwargs,
    )
    return booking, payment


def settle(payment):
    """Settle a payment the way Razorpay's webhook would, hook included."""
    from apps.payments.services import on_payment_settled

    payment.mark_paid(razorpay_payment_id="pay_test_123", signature="webhook")
    on_payment_settled(payment)
    payment.refresh_from_db()
    return payment


@pytest.fixture
def run_on_commit(django_capture_on_commit_callbacks):
    """Run the ``transaction.on_commit`` work a real request would run.

    Every notification in this module is deliberately deferred to commit, so
    that nobody is told about a row a subsequent request cannot yet read. A test
    runs inside a transaction that never commits, so without this the assertions
    about who was told would all pass vacuously — which is worse than failing.

    Usage: ``with run_on_commit(): ...``.
    """

    def _capture():
        return django_capture_on_commit_callbacks(execute=True)

    return _capture


# ---------------------------------------------------------------------------
# Regressions for the two reported bugs
# ---------------------------------------------------------------------------


def test_regression_mark_as_done_works_on_an_emergency_booking(
    authenticated_client, resident, society, emergency_category, maids
):
    """Bug 1. "Mark as Done" failed for emergency bookings.

    Root cause: ``complete_booking`` required ``has_started`` — the clock past
    the booking's start time. An emergency is raised minutes before it is
    served, so the worker routinely finishes on the wrong side of that boundary
    and the job could not be closed. Here she finishes *before* the nominal
    start time, which is the exact case that used to 409.
    """
    starts_soon = timezone.localtime() + dt.timedelta(minutes=30)
    booking, payment = raise_request(
        resident, society, emergency_category, when=starts_soon
    )
    settle(payment)
    booking.refresh_from_db()

    winner = maids[0]
    emergency_service.accept_offer(booking_id=booking.pk, worker=winner)

    # She is done at, say, 20 minutes before the slot even opens.
    response = authenticated_client(winner.user).post(
        reverse("v1:scheduling:mark-task-complete"),
        {"booking": booking.pk},
        format="json",
    )

    assert response.status_code == 201, response.data
    booking.refresh_from_db()
    assert booking.status == BookingStatus.COMPLETED
    assert booking.completed_at is not None


def test_regression_a_finished_visit_stays_on_the_maids_dashboard(
    authenticated_client, resident, society, emergency_category, maids
):
    """Bug 2. The "Work Done" control was not rendered at all.

    Two causes, both here. The schedule only returned PENDING and CONFIRMED
    bookings, so a completed job vanished from the dashboard the instant it was
    marked — it looked like the button had destroyed the job. And the app was
    re-deriving "may she mark this done" for itself, so the server now sends
    ``can_mark_done`` and there is one rule instead of two that disagreed.
    """
    booking, payment = raise_request(resident, society, emergency_category)
    settle(payment)
    winner = maids[0]
    emergency_service.accept_offer(booking_id=booking.pk, worker=winner)

    client = authenticated_client(winner.user)
    today = client.get(reverse("v1:scheduling:my-today"))

    rows = [row for row in today.data["results"] if row["source"] == "booking"]
    assert len(rows) == 1, "the accepted emergency should be on her day"
    assert rows[0]["can_mark_done"] is True
    assert rows[0]["settlement"] == "cash"

    client.post(
        reverse("v1:scheduling:mark-task-complete"),
        {"booking": booking.pk},
        format="json",
    )

    after = client.get(reverse("v1:scheduling:my-today"))
    rows = [row for row in after.data["results"] if row["source"] == "booking"]
    assert len(rows) == 1, "a finished visit must not disappear from the day"
    assert rows[0]["visit_status"] == "complete"
    assert rows[0]["can_mark_done"] is False


def test_regression_a_future_dated_booking_still_cannot_be_marked_done(
    authenticated_client, resident, society, emergency_category, maids
):
    """The relaxation above must not become "anything can be closed out"."""
    tomorrow = timezone.localtime() + dt.timedelta(days=1)
    booking, payment = raise_request(
        resident, society, emergency_category, when=tomorrow
    )
    settle(payment)
    winner = maids[0]
    emergency_service.accept_offer(booking_id=booking.pk, worker=winner)

    response = authenticated_client(winner.user).post(
        reverse("v1:scheduling:mark-task-complete"),
        {"booking": booking.pk},
        format="json",
    )

    assert response.status_code == 409
    assert response.data["error"]["code"] == "booking_not_actionable"


# ---------------------------------------------------------------------------
# The surcharge — payment A
# ---------------------------------------------------------------------------


class TestSurcharge:
    def test_same_day_costs_a_hundred_and_a_day_ahead_costs_fifty(self):
        today = dt.date(2026, 8, 9)
        assert emergency_surcharge(scheduled_date=today, raised_on=today).rupees == 100
        assert (
            emergency_surcharge(
                scheduled_date=today + dt.timedelta(days=1), raised_on=today
            ).rupees
            == 50
        )

    def test_the_quote_endpoint_says_the_worker_is_paid_in_cash(
        self, authenticated_client, resident, resident_user
    ):
        """The two payments are the thing most likely to be confused, so the
        screen that collects one is explicit about the other."""
        response = authenticated_client(resident_user).get(
            reverse("v1:bookings:emergency-quote")
        )

        assert response.status_code == 200
        assert response.data["surcharge_rupees"] == 100
        assert response.data["worker_fee_settlement"] == "cash"

    def test_raising_opens_a_platform_charge_with_no_worker(
        self, resident, society, emergency_category
    ):
        booking, payment = raise_request(resident, society, emergency_category)

        assert booking.status == BookingStatus.PAYMENT_PENDING
        assert booking.worker_id is None
        assert payment.kind == PaymentKind.EMERGENCY_SURCHARGE
        assert payment.worker_id is None
        assert payment.amount_paise == 100_00
        # The platform's own fee is never itself fee-bearing.
        assert payment.platform_fee_paise == 0

    def test_no_worker_hears_about_it_until_the_surcharge_settles(
        self, authenticated_client, resident, society, emergency_category, maids
    ):
        """The whole reason the fee is collected up front."""
        raise_request(resident, society, emergency_category)

        assert BookingOffer.objects.count() == 0
        offers = authenticated_client(maids[0].user).get(
            reverse("v1:bookings:emergency-offers")
        )
        assert offers.data == []

    def test_an_emergency_more_than_a_day_out_is_refused(
        self, resident, society, emergency_category
    ):
        far = timezone.localtime() + dt.timedelta(days=3)
        with pytest.raises(emergency_service.EmergencyTooFarAhead):
            raise_request(resident, society, emergency_category, when=far)

    def test_an_ordinary_category_cannot_use_the_emergency_flow(
        self, resident, society
    ):
        with pytest.raises(emergency_service.NotAnEmergency):
            raise_request(
                resident, society, ServiceCategory.objects.get(slug="deep-cleaning")
            )

    def test_a_start_time_in_the_next_minute_is_accepted(
        self, resident, society, emergency_category
    ):
        """"Come now" is the commonest emergency there is.

        The directed flow refuses a start time at or before now, correctly — a
        worker cannot confirm a job that has already begun. Applying that rule
        here is what left an emergency bookable for roughly one minute before
        the expiry sweep took it away again.
        """
        booking, _ = raise_request(
            resident,
            society,
            emergency_category,
            when=timezone.localtime() + dt.timedelta(seconds=30),
        )
        assert booking.status == BookingStatus.PAYMENT_PENDING


# ---------------------------------------------------------------------------
# Broadcast
# ---------------------------------------------------------------------------


class TestBroadcast:
    def test_settling_the_surcharge_releases_it_to_every_eligible_maid(
        self, resident, society, emergency_category, maids
    ):
        booking, payment = raise_request(resident, society, emergency_category)
        settle(payment)

        booking.refresh_from_db()
        assert booking.status == BookingStatus.BROADCAST
        assert booking.offer_expires_at is not None
        assert BookingOffer.objects.filter(booking=booking).count() == len(maids)
        assert set(BookingOffer.objects.values_list("state", flat=True)) == {
            OfferState.OFFERED
        }

    def test_every_offered_maid_is_actually_told(
        self, resident, society, emergency_category, maids, run_on_commit
    ):
        booking, payment = raise_request(resident, society, emergency_category)
        with run_on_commit():
            settle(payment)

        told = Notification.objects.filter(
            category=NotificationCategory.BOOKING,
            recipient__in=[maid.user for maid in maids],
        )
        assert told.count() == len(maids)

    def test_broadcasting_twice_does_not_re_notify(
        self, resident, society, emergency_category, maids, run_on_commit
    ):
        """The settle hook fires from both the client confirmation and the
        webhook, and which arrives first is a race. Re-running it must not ring
        every phone a second time."""
        booking, payment = raise_request(resident, society, emergency_category)
        with run_on_commit():
            settle(payment)
        before = Notification.objects.count()

        with run_on_commit():
            emergency_service.broadcast(booking)

        assert Notification.objects.count() == before

    def test_a_request_nobody_can_serve_is_closed_immediately(
        self, resident, society, emergency_category
    ):
        """No maids in this society at all. Making the household wait ten
        minutes for an answer the platform already has is ten minutes they could
        have spent phoning somebody themselves."""
        booking, payment = raise_request(resident, society, emergency_category)
        settle(payment)

        booking.refresh_from_db()
        assert booking.status == BookingStatus.UNFULFILLED

    def test_the_offer_window_never_outlives_the_job_it_is_for(
        self, resident, society, emergency_category, maids
    ):
        soon = timezone.localtime() + dt.timedelta(minutes=2)
        booking, payment = raise_request(
            resident, society, emergency_category, when=soon
        )
        settle(payment)

        booking.refresh_from_db()
        assert booking.offer_expires_at <= booking.scheduled_start + dt.timedelta(
            seconds=1
        )


# ---------------------------------------------------------------------------
# The race. The part most likely to break silently.
# ---------------------------------------------------------------------------


class TestRaceToAccept:
    @pytest.fixture
    def broadcast_booking(self, resident, society, emergency_category, maids):
        booking, payment = raise_request(resident, society, emergency_category)
        settle(payment)
        booking.refresh_from_db()
        return booking

    def test_the_first_maid_wins_and_the_second_is_told_cleanly(
        self, broadcast_booking, maids
    ):
        first, second = maids[0], maids[1]

        claimed = emergency_service.accept_offer(
            booking_id=broadcast_booking.pk, worker=first
        )
        assert claimed.worker_id == first.pk
        assert claimed.status == BookingStatus.CONFIRMED

        with pytest.raises(emergency_service.OfferGone):
            emergency_service.accept_offer(
                booking_id=broadcast_booking.pk, worker=second
            )

    def test_the_claim_statement_matches_one_row_and_then_none(
        self, broadcast_booking, maids
    ):
        """The mechanism itself, tested as a statement rather than a story.

        This is the single line the whole feature rests on. Whatever else moves
        around it, the guarantee is that the claiming UPDATE's WHERE clause
        matches exactly once: the first caller changes one row, and every caller
        after that changes none, because ``worker_id IS NULL`` is no longer
        true. A read-check-write cannot make that promise — its check and its
        write are two statements with a gap in between — which is why the
        rewrite is built on this and not on ``select_for_update``.
        """
        first, second = maids[0], maids[1]

        def claim(worker):
            return Booking.objects.filter(
                pk=broadcast_booking.pk,
                status=BookingStatus.BROADCAST,
                worker__isnull=True,
            ).update(
                worker=worker,
                status=BookingStatus.CONFIRMED,
                confirmed_at=timezone.now(),
            )

        assert claim(first) == 1
        assert claim(second) == 0

        broadcast_booking.refresh_from_db()
        assert broadcast_booking.worker_id == first.pk

    def test_a_claim_landing_mid_flight_is_refused_rather_than_overwriting(
        self, broadcast_booking, maids, monkeypatch
    ):
        """The same guarantee, exercised through ``accept_offer``.

        The pre-flight conflict check is the last thing that runs before the
        claim, so another worker's claim is injected during it — precisely the
        window in which a read-check-write implementation has already decided
        the job is free and is about to write itself in as the owner.

        The assertion is that the losing caller is *refused*, not that the
        winner's row survives: both calls run on one connection inside one
        transaction here, so the injected claim is rolled back along with the
        refusal. That rollback is an artifact of simulating concurrency in a
        single test process, and it is why the statement-level test above exists
        alongside this one.
        """
        loser, winner = maids[0], maids[1]
        real_check = emergency_service._conflicts_for

        def claim_from_under_us(worker, booking):
            if worker.pk == loser.pk:
                emergency_service.accept_offer(booking_id=booking.pk, worker=winner)
            return real_check(worker, booking)

        monkeypatch.setattr(emergency_service, "_conflicts_for", claim_from_under_us)

        with pytest.raises(emergency_service.OfferGone):
            emergency_service.accept_offer(
                booking_id=broadcast_booking.pk, worker=loser
            )

        broadcast_booking.refresh_from_db()
        assert broadcast_booking.worker_id != loser.pk
        assert not BookingOffer.objects.filter(
            booking=broadcast_booking, worker=loser, state=OfferState.ACCEPTED
        ).exists()

    def test_the_database_itself_refuses_a_second_accepted_offer(
        self, broadcast_booking, maids
    ):
        """Belt and braces: a partial unique index, so a future code path
        cannot quietly reintroduce the race this was rebuilt to remove."""
        from django.db import IntegrityError

        emergency_service.accept_offer(
            booking_id=broadcast_booking.pk, worker=maids[0]
        )

        with pytest.raises(IntegrityError):
            BookingOffer.objects.filter(
                booking=broadcast_booking, worker=maids[1]
            ).update(state=OfferState.ACCEPTED)

    def test_losing_is_recorded_as_lost_and_never_as_declined(
        self, broadcast_booking, maids
    ):
        """Module 9 scores workers partly on how they respond to work. Somebody
        who was beaten to a job did not turn it down, and the two must never be
        stored as the same thing."""
        emergency_service.accept_offer(
            booking_id=broadcast_booking.pk, worker=maids[0]
        )

        states = dict(
            BookingOffer.objects.filter(booking=broadcast_booking).values_list(
                "worker_id", "state"
            )
        )
        assert states[maids[0].pk] == OfferState.ACCEPTED
        assert states[maids[1].pk] == OfferState.LOST
        assert states[maids[2].pk] == OfferState.LOST

    def test_the_job_disappears_from_every_other_dashboard(
        self, authenticated_client, broadcast_booking, maids
    ):
        """The real-time requirement, as the other maids' phones experience it."""
        others = authenticated_client(maids[1].user)
        assert len(others.get(reverse("v1:bookings:emergency-offers")).data) == 1

        emergency_service.accept_offer(
            booking_id=broadcast_booking.pk, worker=maids[0]
        )

        assert others.get(reverse("v1:bookings:emergency-offers")).data == []

    def test_the_household_is_told_who_is_coming(
        self, broadcast_booking, maids, resident, run_on_commit
    ):
        with run_on_commit():
            emergency_service.accept_offer(
                booking_id=broadcast_booking.pk, worker=maids[0]
            )

        told = Notification.objects.filter(
            recipient=resident.user, category=NotificationCategory.BOOKING
        )
        assert told.exists()
        assert "Sunita" in told.first().title

    def test_declining_removes_only_that_maids_card(self, broadcast_booking, maids):
        emergency_service.decline_offer(
            booking_id=broadcast_booking.pk, worker=maids[0]
        )

        broadcast_booking.refresh_from_db()
        assert broadcast_booking.status == BookingStatus.BROADCAST
        assert (
            BookingOffer.objects.filter(
                booking=broadcast_booking, state=OfferState.OFFERED
            ).count()
            == 2
        )

    def test_a_maid_who_was_never_offered_it_cannot_claim_it(
        self, broadcast_booking, django_user_model, society
    ):
        outsider = make_maid(django_user_model, society, "9879999999", "Rekha")
        with pytest.raises(emergency_service.OfferGone):
            emergency_service.accept_offer(
                booking_id=broadcast_booking.pk, worker=outsider
            )

    def test_accepting_over_the_api_returns_the_claimed_booking(
        self, authenticated_client, broadcast_booking, maids
    ):
        response = authenticated_client(maids[0].user).post(
            reverse("v1:bookings:emergency-accept", args=[broadcast_booking.pk])
        )

        assert response.status_code == 200
        assert response.data["booking"]["status"] == BookingStatus.CONFIRMED
        assert response.data["booking"]["worker"] == maids[0].pk

    def test_losing_over_the_api_is_a_409_and_not_a_400(
        self, authenticated_client, broadcast_booking, maids
    ):
        """A 400 renders as "you did something wrong", and she did not."""
        emergency_service.accept_offer(
            booking_id=broadcast_booking.pk, worker=maids[0]
        )

        response = authenticated_client(maids[1].user).post(
            reverse("v1:bookings:emergency-accept", args=[broadcast_booking.pk])
        )

        assert response.status_code == 409
        assert response.data["error"]["code"] == "offer_gone"


# ---------------------------------------------------------------------------
# Nobody accepts, and cancellation
# ---------------------------------------------------------------------------


class TestGivingUp:
    def test_an_unclaimed_request_lapses_and_the_fee_comes_back(
        self, resident, society, emergency_category, maids
    ):
        booking, payment = raise_request(resident, society, emergency_category)
        settle(payment)

        Booking.objects.filter(pk=booking.pk).update(
            offer_expires_at=timezone.now() - dt.timedelta(seconds=1)
        )
        closed = emergency_service.expire_unclaimed()

        booking.refresh_from_db()
        payment.refresh_from_db()
        assert closed == 1
        assert booking.status == BookingStatus.UNFULFILLED
        assert payment.status == PaymentStatus.REFUNDED
        assert BookingOffer.objects.filter(state=OfferState.OFFERED).count() == 0

    def test_the_household_is_told_it_failed_and_that_they_were_refunded(
        self, resident, society, emergency_category, maids, run_on_commit
    ):
        booking, payment = raise_request(resident, society, emergency_category)
        settle(payment)
        Booking.objects.filter(pk=booking.pk).update(
            offer_expires_at=timezone.now() - dt.timedelta(seconds=1)
        )
        with run_on_commit():
            emergency_service.expire_unclaimed()

        told = Notification.objects.filter(
            recipient=resident.user, title__icontains="Nobody could take"
        )
        assert told.exists()
        assert "refunded" in told.first().body

    def test_the_sweep_is_idempotent(
        self, resident, society, emergency_category, maids
    ):
        booking, payment = raise_request(resident, society, emergency_category)
        settle(payment)
        Booking.objects.filter(pk=booking.pk).update(
            offer_expires_at=timezone.now() - dt.timedelta(seconds=1)
        )

        assert emergency_service.expire_unclaimed() == 1
        assert emergency_service.expire_unclaimed() == 0

    def test_cancelling_before_anyone_accepts_refunds_in_full_and_free(
        self, authenticated_client, resident, resident_user, society,
        emergency_category, maids,
    ):
        booking, payment = raise_request(resident, society, emergency_category)
        settle(payment)

        response = authenticated_client(resident_user).post(
            reverse("v1:bookings:booking-cancel", args=[booking.pk]),
            {"acknowledged_fee": 0},
            format="json",
        )

        payment.refresh_from_db()
        assert response.status_code == 200
        assert response.data["cancellation_fee"] == 0
        assert payment.status == PaymentStatus.REFUNDED

    def test_cancelling_after_a_maid_accepts_keeps_the_platform_fee(
        self, authenticated_client, resident, resident_user, society,
        emergency_category, maids,
    ):
        """The broadcast did what it was paid to do, and somebody rearranged
        their evening on the strength of it."""
        booking, payment = raise_request(resident, society, emergency_category)
        settle(payment)
        emergency_service.accept_offer(booking_id=booking.pk, worker=maids[0])

        quote = authenticated_client(resident_user).get(
            reverse("v1:bookings:cancellation-quote", args=[booking.pk])
        )
        authenticated_client(resident_user).post(
            reverse("v1:bookings:booking-cancel", args=[booking.pk]),
            {"acknowledged_fee": quote.data["fee"]},
            format="json",
        )

        payment.refresh_from_db()
        assert payment.status == PaymentStatus.PAID

    def test_an_unaccepted_request_is_free_to_cancel_close_to_the_start(
        self, resident, society, emergency_category, maids
    ):
        from apps.bookings.services import cancellation_quote

        booking, payment = raise_request(
            resident,
            society,
            emergency_category,
            when=timezone.localtime() + dt.timedelta(minutes=5),
        )
        settle(payment)
        booking.refresh_from_db()

        assert cancellation_quote(booking)["fee"] == 0


# ---------------------------------------------------------------------------
# Payment B — the cash one the app must not touch
# ---------------------------------------------------------------------------


class TestCashSettlement:
    @pytest.fixture
    def finished_job(self, resident, society, emergency_category, maids):
        booking, payment = raise_request(resident, society, emergency_category)
        settle(payment)
        emergency_service.accept_offer(booking_id=booking.pk, worker=maids[0])
        from apps.bookings.services import complete_booking

        booking.refresh_from_db()
        return complete_booking(booking)

    def test_completing_an_emergency_opens_no_in_app_charge(self, finished_job):
        """Payment B is cash. A Razorpay order for it would be a second,
        phantom charge for money that is about to change hands in notes."""
        assert not Payment.objects.filter(
            booking=finished_job, kind=PaymentKind.BOOKING
        ).exists()

    def test_both_sides_are_told_the_same_figure_at_the_same_moment(
        self, finished_job, resident, maids
    ):
        """The worker's only protection against "I already paid you"."""
        for user in (resident.user, maids[0].user):
            told = Notification.objects.filter(
                recipient=user, category=NotificationCategory.PAYMENT
            )
            assert told.exists()
            assert str(finished_job.quoted_price) in told.first().title
            assert told.first().data["settlement"] == "cash"

    def test_the_server_refuses_an_in_app_charge_for_a_cash_job(
        self, authenticated_client, finished_job, resident_user
    ):
        """Refused server-side, not merely hidden in the app.

        A stale build, a retried request or a tapped-twice button must not be
        able to open a Razorpay order for money that is about to be handed over
        in notes.
        """
        response = authenticated_client(resident_user).post(
            reverse("v1:payments:pay-booking"),
            {"booking": finished_job.pk},
            format="json",
        )

        assert response.status_code == 409
        assert response.data["error"]["code"] == "settled_in_cash"

    def test_the_booking_reports_cash_settlement_to_the_app(
        self, authenticated_client, finished_job, resident_user
    ):
        response = authenticated_client(resident_user).get(
            reverse("v1:bookings:booking-detail", args=[finished_job.pk])
        )
        assert response.data["settlement"] == "cash"
        assert response.data["is_emergency"] is True


# ---------------------------------------------------------------------------
# The polling endpoint both dashboards live on
# ---------------------------------------------------------------------------


class TestLiveEndpoint:
    def test_a_maid_sees_her_open_offers(
        self, authenticated_client, resident, society, emergency_category, maids
    ):
        _, payment = raise_request(resident, society, emergency_category)
        settle(payment)

        response = authenticated_client(maids[0].user).get(
            reverse("v1:bookings:emergency-live")
        )

        assert response.data["role"] == "worker"
        assert response.data["count"] == 1
        assert response.data["offers"][0]["seconds_left"] > 0

    def test_a_resident_sees_who_accepted(
        self, authenticated_client, resident, resident_user, society,
        emergency_category, maids,
    ):
        booking, payment = raise_request(resident, society, emergency_category)
        settle(payment)
        emergency_service.accept_offer(booking_id=booking.pk, worker=maids[0])

        response = authenticated_client(resident_user).get(
            reverse("v1:bookings:emergency-live")
        )

        assert response.data["role"] == "resident"
        assert response.data["requests"][0]["worker_name"] == "Sunita K"
        assert response.data["requests"][0]["status"] == BookingStatus.CONFIRMED

    def test_the_version_stamp_moves_when_something_changes(
        self, authenticated_client, resident, resident_user, society,
        emergency_category, maids,
    ):
        booking, payment = raise_request(resident, society, emergency_category)
        settle(payment)
        client = authenticated_client(resident_user)

        before = client.get(reverse("v1:bookings:emergency-live")).data["version"]
        emergency_service.accept_offer(booking_id=booking.pk, worker=maids[0])
        after = client.get(reverse("v1:bookings:emergency-live")).data["version"]

        assert before != after

    def test_polling_sweeps_lapsed_requests(
        self, authenticated_client, resident, resident_user, society,
        emergency_category, maids,
    ):
        """Whoever is watching triggers the sweep — the free tier's substitute
        for a scheduler (docs/free-tier-constraints.md §7)."""
        booking, payment = raise_request(resident, society, emergency_category)
        settle(payment)
        Booking.objects.filter(pk=booking.pk).update(
            offer_expires_at=timezone.now() - dt.timedelta(seconds=1)
        )

        authenticated_client(resident_user).get(reverse("v1:bookings:emergency-live"))

        booking.refresh_from_db()
        assert booking.status == BookingStatus.UNFULFILLED
