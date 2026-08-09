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
from apps.bookings.policy import EMERGENCY_SURCHARGE_RUPEES, emergency_surcharge
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


def _soon_but_still_today(minutes: int = 20) -> dt.datetime:
    """A moment shortly from now that never lands on tomorrow.

    An emergency is dated by the moment it is raised for, and almost everything
    about it reads that date: the surcharge tier is lead-days from today, the
    schedule's "today" view filters on it, and ``can_be_completed`` refuses a
    future date. So a fixture that says "20 minutes from now" quietly tests a
    *different scenario* when the suite runs after 23:40 — the visit becomes
    tomorrow's, the surcharge becomes the day-ahead tier, and three assertions
    fail for reasons that have nothing to do with the code.

    Clamped **within** today rather than reflected across it. A booking that has
    already started cannot be cancelled, so simply moving the moment backwards
    would break the cancellation tests instead — the offset is squeezed towards
    the end of the day rather than flipped, which keeps "shortly from now" true
    in both senses.
    """
    now = timezone.localtime()
    wanted = now + dt.timedelta(minutes=minutes)
    if wanted.date() == now.date():
        return wanted

    if minutes >= 0:
        # Latest moment still on today's date, so the visit is future *and*
        # today. Leaves a minute of headroom for the request itself.
        return now.replace(hour=23, minute=59, second=0, microsecond=0)
    return now.replace(hour=0, minute=1, second=0, microsecond=0)


def raise_request(resident, society, category, *, when=None, **kwargs):
    moment = when or _soon_but_still_today()
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
    booking, payment = raise_request(
        resident, society, emergency_category, when=_soon_but_still_today(30)
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
    assert rows[0]["settlement"] == "app"

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


def _directed_emergency(resident, society, category, *, minutes_from_now: int):
    """An emergency booked the *ordinary* way: one named worker, status PENDING.

    Still reachable — a resident can pick the emergency category out of the
    catalogue and choose somebody — so it has to work, and it is the shape that
    was reported broken on the worker's dashboard.
    """
    moment = _soon_but_still_today(minutes_from_now)
    return Booking.objects.create(
        society=society,
        resident=resident,
        worker=None,
        category=category,
        scheduled_date=moment.date(),
        start_time=moment.time().replace(microsecond=0),
        expected_duration_minutes=60,
        quoted_price=600,
        notes="come fast",
        status=BookingStatus.PENDING,
    )


def _card_for(client, booking):
    """The schedule card for a booking, read from its own day.

    Deliberately not the "today" endpoint. That returns only visits dated today,
    so a fixture booking 45 minutes ahead lands on *tomorrow* when the suite runs
    late in the evening and the assertion finds no card at all — a test that
    passes for 23 hours a day and fails in the 24th, with the code under test
    entirely innocent. Asking for the booking's own date removes the coupling.
    """
    response = client.get(
        reverse("v1:scheduling:my-agenda"),
        {"from": booking.scheduled_date.isoformat(),
         "to": booking.scheduled_date.isoformat()},
    )
    rows = [
        row for row in response.data["results"]
        if row["source"] == "booking" and row["source_id"] == booking.pk
    ]
    return rows[0] if rows else None


class TestAnsweringFromTheDashboard:
    """The reported bug: "Awaiting your confirmation", and nothing to tap."""

    @pytest.fixture
    def pending(self, resident, society, emergency_category, maids):
        booking = _directed_emergency(
            resident, society, emergency_category, minutes_from_now=45
        )
        booking.worker = maids[0]
        booking.save(update_fields=["worker"])
        return booking

    def test_the_card_carries_the_accept_control(
        self, authenticated_client, pending, maids
    ):
        """Without ``can_respond`` the dashboard has no way to draw a button,
        which is exactly what it was doing: a warning flag and no action."""
        card = _card_for(authenticated_client(maids[0].user), pending)

        assert card is not None, "the pending request should be on her schedule"
        assert card["can_respond"] is True
        assert card["is_confirmed"] is False
        assert card["can_mark_done"] is False

    def test_an_unanswered_request_does_not_claim_to_be_in_progress(
        self, authenticated_client, pending, maids, society
    ):
        """She was at the gate for a different job. Attendance is logged per
        worker per day, so it used to leak onto every visit that day — including
        one she had not accepted, producing a card that read "Awaiting your
        confirmation" and "In progress" at once."""
        from apps.attendance.models import AttendanceEvent, Decision, Direction

        AttendanceEvent.objects.create(
            society=society,
            worker=maids[0],
            direction=Direction.ENTRY,
            decision=Decision.ALLOWED,
            occurred_at=timezone.now() - dt.timedelta(hours=2),
        )

        card = _card_for(authenticated_client(maids[0].user), pending)

        assert card["visit_status"] == "pending"

    def test_accepting_from_the_dashboard_confirms_it(
        self, authenticated_client, pending, maids
    ):
        client = authenticated_client(maids[0].user)

        response = client.post(
            reverse("v1:bookings:booking-respond", args=[pending.pk]),
            {"confirm": True},
            format="json",
        )

        assert response.status_code == 200
        pending.refresh_from_db()
        assert pending.status == BookingStatus.CONFIRMED

        # And the card immediately offers the next action rather than nothing.
        card = _card_for(client, pending)
        assert card["can_respond"] is False

    def test_an_emergency_can_still_be_accepted_just_after_its_start_time(
        self, authenticated_client, resident, society, emergency_category, maids
    ):
        """"Come fast" at 15:00, opened at 15:05.

        The ordinary rule — a job that has begun can no longer be confirmed —
        is right for a booking agreed days ahead and wrong for this one, where
        the start time is a guess at when somebody might arrive. Without the
        grace the request was answerable for only the minutes between being
        raised and its nominal start.
        """
        booking = _directed_emergency(
            resident, society, emergency_category, minutes_from_now=-5
        )
        booking.worker = maids[0]
        booking.save(update_fields=["worker"])

        response = authenticated_client(maids[0].user).post(
            reverse("v1:bookings:booking-respond", args=[booking.pk]),
            {"confirm": True},
            format="json",
        )

        assert response.status_code == 200, response.data
        booking.refresh_from_db()
        assert booking.status == BookingStatus.CONFIRMED

    def test_the_expiry_sweep_honours_the_same_grace(
        self, authenticated_client, resident, society, emergency_category, maids
    ):
        """The sweep and the button have to agree.

        The sweep was the stricter of the two, so a worker looking at a live
        Accept button would tap it a moment after the start time and be told the
        booking had expired — because a read somewhere else had already moved it
        while she was reading the card.
        """
        booking = _directed_emergency(
            resident, society, emergency_category, minutes_from_now=-5
        )
        booking.worker = maids[0]
        booking.save(update_fields=["worker"])

        # Any booking list read runs the sweep.
        authenticated_client(maids[0].user).get(reverse("v1:bookings:booking-list"))

        booking.refresh_from_db()
        assert booking.status == BookingStatus.PENDING
        assert booking.is_actionable

    def test_the_grace_does_run_out(
        self, authenticated_client, resident, society, emergency_category, maids
    ):
        """An hour later nobody should still believe somebody might turn up."""
        booking = _directed_emergency(
            resident, society, emergency_category, minutes_from_now=-90
        )
        booking.worker = maids[0]
        booking.save(update_fields=["worker"])

        authenticated_client(maids[0].user).get(reverse("v1:bookings:booking-list"))

        booking.refresh_from_db()
        assert booking.status == BookingStatus.EXPIRED

    def test_an_ordinary_booking_gets_no_grace(
        self, authenticated_client, resident, society, maids
    ):
        """The relaxation is emergency-only, deliberately. A household with a
        deep clean booked for Tuesday needs to know before Tuesday."""
        booking = _directed_emergency(
            resident,
            society,
            ServiceCategory.objects.get(slug="deep-cleaning"),
            minutes_from_now=-5,
        )
        booking.worker = maids[0]
        booking.save(update_fields=["worker"])

        authenticated_client(maids[0].user).get(reverse("v1:bookings:booking-list"))

        booking.refresh_from_db()
        assert booking.status == BookingStatus.EXPIRED

    def test_a_lapsed_request_stops_offering_the_button(
        self, authenticated_client, resident, society, emergency_category, maids
    ):
        """The other half of the fix. A card that keeps saying "awaiting your
        confirmation" with a button that always fails is no better than one with
        no button at all."""
        booking = _directed_emergency(
            resident, society, emergency_category, minutes_from_now=-90
        )
        booking.worker = maids[0]
        booking.save(update_fields=["worker"])

        card = _card_for(authenticated_client(maids[0].user), booking)
        cards = [card] if card else []

        # Swept to EXPIRED by the schedule read, so it leaves the day entirely.
        # Whichever way it goes, what must never happen is an offered control
        # that the server would refuse.
        assert all(not card["can_respond"] for card in cards)


# ---------------------------------------------------------------------------
# The surcharge — payment A
# ---------------------------------------------------------------------------


class TestSurcharge:
    """Pins the pricing *rule*, never a specific rupee figure.

    ``EMERGENCY_SURCHARGE_RUPEES`` is explicitly the tunable part of this
    feature — an operator is expected to re-price it, and did. A test that
    asserted "same day costs ₹100" would fail on that re-pricing while telling
    nobody anything about whether the flow still worked, so these read the table
    and assert the properties that must hold whatever is in it.
    """

    def test_lead_time_is_measured_in_whole_days_and_priced_from_the_table(self):
        today = dt.date(2026, 8, 9)
        same_day = emergency_surcharge(scheduled_date=today, raised_on=today)
        day_ahead = emergency_surcharge(
            scheduled_date=today + dt.timedelta(days=1), raised_on=today
        )

        assert (same_day.lead_days, same_day.rupees) == (
            0,
            EMERGENCY_SURCHARGE_RUPEES[0],
        )
        assert (day_ahead.lead_days, day_ahead.rupees) == (
            1,
            EMERGENCY_SURCHARGE_RUPEES[1],
        )

    def test_a_date_already_past_is_priced_as_today_rather_than_negatively(self):
        """Clamped at zero. A lead time of -1 would miss the table and come out
        free, which is the one direction a pricing bug must never go."""
        today = dt.date(2026, 8, 9)
        quote = emergency_surcharge(
            scheduled_date=today - dt.timedelta(days=2), raised_on=today
        )

        assert quote.lead_days == 0
        assert quote.rupees == EMERGENCY_SURCHARGE_RUPEES[0]

    def test_beyond_the_table_is_free(self):
        today = dt.date(2026, 8, 9)
        quote = emergency_surcharge(
            scheduled_date=today + dt.timedelta(days=5), raised_on=today
        )

        assert quote.rupees == 0
        assert "no emergency fee" in quote.rationale

    def test_rupees_convert_to_paise_without_drifting(self):
        today = dt.date(2026, 8, 9)
        quote = emergency_surcharge(scheduled_date=today, raised_on=today)
        assert quote.paise == quote.rupees * 100

    def test_the_quote_endpoint_separates_the_fee_from_the_work(
        self, authenticated_client, resident, resident_user
    ):
        """The two payments are the thing most likely to be confused, so the
        screen that collects the surcharge is explicit that the worker's own
        charge is separate and comes later."""
        response = authenticated_client(resident_user).get(
            reverse("v1:bookings:emergency-quote")
        )

        assert response.status_code == 200
        assert response.data["surcharge_rupees"] == EMERGENCY_SURCHARGE_RUPEES[0]
        assert response.data["worker_fee_settlement"] == "app"

    def test_raising_opens_a_platform_charge_with_no_worker(
        self, resident, society, emergency_category
    ):
        booking, payment = raise_request(resident, society, emergency_category)

        assert booking.status == BookingStatus.PAYMENT_PENDING
        assert booking.worker_id is None
        assert payment.kind == PaymentKind.EMERGENCY_SURCHARGE
        assert payment.worker_id is None
        # Whatever the table says today, the charge and the booking must agree.
        assert payment.amount_paise == EMERGENCY_SURCHARGE_RUPEES[0] * 100
        assert booking.emergency_surcharge_paise == payment.amount_paise
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
# Payment B — the worker's fee, settled in the app
# ---------------------------------------------------------------------------


class TestPayingForTheWork:
    """Module 5.5 — the worker's fee, settled through the app like any other.

    This briefly worked the other way: emergency work was paid in cash and the
    app deliberately opened no charge for it. That is reversed, so these pin the
    opposite behaviour — completion must open a payment, exactly as it does for
    an ordinary one-day booking.
    """

    @pytest.fixture
    def finished_job(self, resident, society, emergency_category, maids):
        booking, payment = raise_request(resident, society, emergency_category)
        settle(payment)
        emergency_service.accept_offer(booking_id=booking.pk, worker=maids[0])
        from apps.bookings.services import complete_booking

        booking.refresh_from_db()
        return complete_booking(booking)

    def test_the_household_is_asked_to_pay_in_the_app(
        self, finished_job, resident, run_on_commit
    ):
        """The regression that matters: an emergency used to be skipped here,
        so the worker's fee never appeared as anything the app would collect."""
        prompts = Notification.objects.filter(
            recipient=resident.user, category=NotificationCategory.PAYMENT
        )

        assert prompts.exists()
        assert str(finished_job.quoted_price) in prompts.first().title
        assert prompts.first().data["booking"] == finished_job.pk

    def test_the_payment_endpoint_opens_a_charge(
        self, authenticated_client, finished_job, resident_user
    ):
        """It used to refuse with `settled_in_cash`."""
        response = authenticated_client(resident_user).post(
            reverse("v1:payments:pay-booking"),
            {"booking": finished_job.pk},
            format="json",
        )

        assert response.status_code in (200, 201), response.data
        assert Payment.objects.filter(
            booking=finished_job, kind=PaymentKind.BOOKING
        ).exists()

    def test_the_charge_is_the_quoted_price_and_not_the_surcharge(
        self, authenticated_client, finished_job, resident_user
    ):
        """Two payments, two amounts. Totalling them together, or charging the
        surcharge twice, is the mistake worth a test of its own."""
        authenticated_client(resident_user).post(
            reverse("v1:payments:pay-booking"),
            {"booking": finished_job.pk},
            format="json",
        )

        fee = Payment.objects.get(booking=finished_job, kind=PaymentKind.BOOKING)
        surcharge = Payment.objects.get(
            booking=finished_job, kind=PaymentKind.EMERGENCY_SURCHARGE
        )

        assert fee.amount_paise == finished_job.quoted_price * 100
        assert fee.worker_id == finished_job.worker_id
        # The surcharge is the platform's and has no worker on it.
        assert surcharge.worker_id is None
        assert surcharge.amount_paise != fee.amount_paise

    def test_the_booking_reports_app_settlement(
        self, authenticated_client, finished_job, resident_user
    ):
        response = authenticated_client(resident_user).get(
            reverse("v1:bookings:booking-detail", args=[finished_job.pk])
        )
        assert response.data["settlement"] == "app"
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
