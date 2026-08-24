"""
The worker's and resident's session endpoints.

Every test here that concerns a failure asserts the same thing in a different
place: **a failure must not cost her the day**. A bad GPS fix, a double tap, a
race between her phone and the resident's scan — each resolves in her favour,
because the alternative is a wage lost to a bug she cannot see.
"""

from __future__ import annotations

import datetime as dt
import uuid

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.attendance.models import SessionSource, SessionStatus, WorkSession
from apps.hiring.models import Engagement, EngagementStatus, RateBasis
from apps.payments.models import Invoice, InvoiceStatus, SessionQuery
from apps.scheduling.models import TaskTiming
from apps.societies.models import Flat, Resident, Tower
from apps.workers.models import ServiceType, WorkerProfile

pytestmark = pytest.mark.django_db


@pytest.fixture
def flat(society):
    tower = Tower.objects.create(society=society, name="A", floors=10)
    return Flat.objects.create(tower=tower, number="301", floor=3)


@pytest.fixture
def resident(resident_user, flat):
    return Resident.objects.create(user=resident_user, flat=flat, is_primary=True)


@pytest.fixture
def worker(worker_user):
    return WorkerProfile.objects.create(
        user=worker_user, photo="workers/x.jpg", is_available=True,
        trust_score=70, average_rating=4.4,
    )


@pytest.fixture
def engagement(society, resident, worker):
    service_type, _ = ServiceType.objects.get_or_create(name="Maid", slug="maid")
    eng = Engagement.objects.create(
        society=society, resident=resident, worker=worker, service_type=service_type,
        days_of_week=list(range(7)),  # every day, so "today" always has a visit
        start_time=dt.time(9, 0), expected_duration_minutes=180,
        monthly_rate=0, rate_basis=RateBasis.HOURLY, hourly_rate=120, visit_fee=60,
        status=EngagementStatus.ACTIVE,
        started_on=timezone.localdate() - dt.timedelta(days=60),
    )
    TaskTiming.objects.create(
        engagement=eng, expected_arrival=dt.time(9, 0), expected_departure=dt.time(12, 0),
        arrival_grace_minutes=10, departure_grace_minutes=10,
    )
    return eng


@pytest.fixture
def worker_client(authenticated_client, worker):
    return authenticated_client(worker.user)


@pytest.fixture
def resident_client(authenticated_client, resident):
    return authenticated_client(resident.user)


class TestStartingIsNeverRefused:
    def test_a_good_fix_is_tier_one(self, worker_client, engagement, society):
        society.latitude, society.longitude = 18.5204, 73.8567
        society.save(update_fields=["latitude", "longitude"])

        response = worker_client.post(
            reverse("v1:attendance:session-start"),
            {"engagement": engagement.id, "latitude": 18.5205, "longitude": 73.8568},
            format="json",
        )
        assert response.status_code == 201
        assert response.json()["source"] == SessionSource.SELF
        assert response.json()["needs_review"] is False

    def test_a_distant_fix_still_starts_the_day(self, worker_client, engagement, society):
        """Lower tier and a flag — never a refusal. A GPS glitch is not absence."""
        society.latitude, society.longitude = 18.5204, 73.8567
        society.save(update_fields=["latitude", "longitude"])

        response = worker_client.post(
            reverse("v1:attendance:session-start"),
            {"engagement": engagement.id, "latitude": 19.9, "longitude": 75.3},
            format="json",
        )
        assert response.status_code == 201
        body = response.json()
        assert body["source"] == SessionSource.RESIDENT_CONFIRM
        assert body["needs_review"] is True
        assert WorkSession.objects.get().status == SessionStatus.OPEN

    def test_no_location_at_all_still_starts_the_day(self, worker_client, engagement):
        response = worker_client.post(
            reverse("v1:attendance:session-start"),
            {"engagement": engagement.id},
            format="json",
        )
        assert response.status_code == 201
        assert response.json()["needs_review"] is True

    def test_a_society_with_no_coordinates_does_not_penalise_her(
        self, worker_client, engagement, society
    ):
        """Our missing data, not hers."""
        assert society.latitude is None
        response = worker_client.post(
            reverse("v1:attendance:session-start"),
            {"engagement": engagement.id, "latitude": 18.5, "longitude": 73.8},
            format="json",
        )
        assert response.json()["source"] == SessionSource.SELF
        assert response.json()["needs_review"] is False

    def test_a_replayed_start_does_not_open_a_second_session(
        self, worker_client, engagement
    ):
        """Offline queue replay. The client-generated id makes this safe."""
        session_id = str(uuid.uuid4())
        payload = {"id": session_id, "engagement": engagement.id}

        first = worker_client.post(reverse("v1:attendance:session-start"), payload, format="json")
        second = worker_client.post(reverse("v1:attendance:session-start"), payload, format="json")

        assert first.status_code == 201
        assert second.status_code == 200
        assert WorkSession.objects.count() == 1

    def test_another_workers_engagement_is_not_startable(
        self, authenticated_client, engagement, society, db, django_user_model
    ):
        intruder = django_user_model.objects.create_user(
            phone_number="9800000077", password="x-12345", role="worker",
            society=society, is_approved=True,
        )
        WorkerProfile.objects.create(
            user=intruder, photo="workers/y.jpg", is_available=True,
            trust_score=50, average_rating=4.0,
        )
        response = authenticated_client(intruder).post(
            reverse("v1:attendance:session-start"),
            {"engagement": engagement.id},
            format="json",
        )
        assert response.status_code == 404
        assert not WorkSession.objects.exists()


class TestStopping:
    def _start(self, client, engagement):
        response = client.post(
            reverse("v1:attendance:session-start"),
            {"engagement": engagement.id},
            format="json",
        )
        return response.json()["id"]

    def test_stopping_prices_the_visit(self, worker_client, engagement):
        session_id = self._start(worker_client, engagement)
        response = worker_client.post(
            reverse("v1:attendance:session-stop", kwargs={"pk": session_id}),
            {"ended_at": timezone.now().isoformat()},
            format="json",
        )
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == SessionStatus.CLOSED
        assert body["priced_at"] is not None
        # The visit fee is owed whatever the hours came to.
        assert body["visit_fee_paise"] == 6_000

    def test_stopping_twice_is_not_an_error(self, worker_client, engagement):
        """A flaky connection makes the double tap normal, not exceptional."""
        session_id = self._start(worker_client, engagement)
        url = reverse("v1:attendance:session-stop", kwargs={"pk": session_id})

        first = worker_client.post(url, {}, format="json")
        second = worker_client.post(url, {}, format="json")

        assert (first.status_code, second.status_code) == (200, 200)
        assert first.json()["total_paise"] == second.json()["total_paise"]

    def test_a_resident_cannot_stop_her_clock(self, resident_client, worker_client, engagement):
        session_id = self._start(worker_client, engagement)
        response = resident_client.post(
            reverse("v1:attendance:session-stop", kwargs={"pk": session_id}), {}, format="json"
        )
        assert response.status_code == 403


class TestOvertimeMustBeApprovedBeforeItIsPaid:
    def _open_session(self, engagement, worker):
        return WorkSession.objects.create(
            society=engagement.society, engagement=engagement, worker=worker,
            visit_date=timezone.localdate(),
            started_at=timezone.now() - dt.timedelta(hours=3),
            source=SessionSource.SELF, status=SessionStatus.OPEN,
        )

    def test_requesting_does_not_approve(self, worker_client, engagement, worker):
        session = self._open_session(engagement, worker)
        response = worker_client.post(
            reverse("v1:attendance:session-request-ot", kwargs={"pk": session.pk}),
            {"minutes": 30},
            format="json",
        )
        assert response.status_code == 200
        assert response.json()["approved_minutes"] == 0
        session.refresh_from_db()
        assert session.approved_ot_minutes == 0

    def test_the_response_says_it_is_unpaid_until_approved(
        self, worker_client, engagement, worker
    ):
        session = self._open_session(engagement, worker)
        response = worker_client.post(
            reverse("v1:attendance:session-request-ot", kwargs={"pk": session.pk}),
            {"minutes": 30},
            format="json",
        )
        assert "only paid once the resident approves" in response.json()["note"]

    def test_the_resident_approving_is_what_makes_it_billable(
        self, resident_client, engagement, worker
    ):
        session = self._open_session(engagement, worker)
        response = resident_client.post(
            reverse("v1:attendance:session-approve-ot", kwargs={"pk": session.pk}),
            {"minutes": 30},
            format="json",
        )
        assert response.status_code == 200
        session.refresh_from_db()
        assert session.approved_ot_minutes == 30

    def test_a_worker_cannot_approve_her_own_overtime(
        self, worker_client, engagement, worker
    ):
        session = self._open_session(engagement, worker)
        response = worker_client.post(
            reverse("v1:attendance:session-approve-ot", kwargs={"pk": session.pk}),
            {"minutes": 60},
            format="json",
        )
        assert response.status_code == 403

    def test_approving_after_pricing_is_refused(self, resident_client, engagement, worker):
        """The arithmetic is frozen. Changing it later would rewrite history."""
        from apps.payments.hourly import price_session

        session = self._open_session(engagement, worker)
        session.close(at=timezone.now())
        price_session(session)

        response = resident_client.post(
            reverse("v1:attendance:session-approve-ot", kwargs={"pk": session.pk}),
            {"minutes": 30},
            format="json",
        )
        assert response.status_code == 409


class TestTheTodayScreen:
    def test_it_returns_one_card_per_flat_scheduled_today(
        self, worker_client, engagement
    ):
        body = worker_client.get(reverse("v1:attendance:session-today")).json()
        assert body["flats_total"] == 1
        assert body["cards"][0]["flat"]
        assert body["cards"][0]["session"] is None
        assert body["cards"][0]["visit_fee"] == 60

    def test_earnings_accumulate_across_the_day(self, worker_client, engagement, worker):
        from apps.payments.hourly import price_session

        session = WorkSession.objects.create(
            society=engagement.society, engagement=engagement, worker=worker,
            visit_date=timezone.localdate(),
            started_at=timezone.make_aware(
                dt.datetime.combine(timezone.localdate(), dt.time(9, 0))
            ),
            ended_at=timezone.make_aware(
                dt.datetime.combine(timezone.localdate(), dt.time(12, 0))
            ),
            source=SessionSource.SELF, status=SessionStatus.CLOSED,
        )
        price_session(session)

        body = worker_client.get(reverse("v1:attendance:session-today")).json()
        assert body["earned_paise"] == 42_000  # 3h at ₹120, plus the ₹60 fee
        assert body["flats_done"] == 1

    def test_a_resident_cannot_open_the_worker_screen(self, resident_client):
        assert resident_client.get(reverse("v1:attendance:session-today")).status_code == 403


class TestConfirmingAnAutoClosedDay:
    def test_yes_clears_the_flag(self, worker_client, engagement, worker):
        session = WorkSession.objects.create(
            society=engagement.society, engagement=engagement, worker=worker,
            visit_date=timezone.localdate(), source=SessionSource.SELF,
            status=SessionStatus.AUTO_CLOSED, needs_review=True,
            review_note="Nobody tapped Stop.",
        )
        response = worker_client.post(
            reverse("v1:attendance:session-confirm", kwargs={"pk": session.pk}),
            {"correct": True},
            format="json",
        )
        assert response.status_code == 200
        session.refresh_from_db()
        assert session.needs_review is False

    def test_no_keeps_it_raised_without_asking_her_to_do_the_maths(
        self, worker_client, engagement, worker
    ):
        session = WorkSession.objects.create(
            society=engagement.society, engagement=engagement, worker=worker,
            visit_date=timezone.localdate(), source=SessionSource.SELF,
            status=SessionStatus.AUTO_CLOSED, needs_review=True,
        )
        response = worker_client.post(
            reverse("v1:attendance:session-confirm", kwargs={"pk": session.pk}),
            {"correct": False, "note": "I left at 1, not 12"},
            format="json",
        )
        assert response.status_code == 200
        session.refresh_from_db()
        assert session.needs_review is True
        assert "I left at 1" in session.review_note


class TestSessionsAreScopedToTheParties:
    def test_a_resident_sees_their_own_flats_sessions(
        self, resident_client, engagement, worker
    ):
        WorkSession.objects.create(
            society=engagement.society, engagement=engagement, worker=worker,
            visit_date=timezone.localdate(), source=SessionSource.SELF,
            status=SessionStatus.CLOSED,
        )
        body = resident_client.get(reverse("v1:attendance:session-list")).json()
        assert len(body) == 1

    def test_an_unrelated_resident_sees_nothing(
        self, authenticated_client, engagement, worker, society, django_user_model
    ):
        outsider = django_user_model.objects.create_user(
            phone_number="9800000066", password="x-12345", role="resident",
            society=society, is_approved=True,
        )
        tower = Tower.objects.create(society=society, name="B", floors=4)
        other_flat = Flat.objects.create(tower=tower, number="101", floor=1)
        Resident.objects.create(user=outsider, flat=other_flat, is_primary=True)

        WorkSession.objects.create(
            society=society, engagement=engagement, worker=worker,
            visit_date=timezone.localdate(), source=SessionSource.SELF,
            status=SessionStatus.CLOSED,
        )
        body = authenticated_client(outsider).get(reverse("v1:attendance:session-list")).json()
        assert body == []


class TestTheInvoiceScreen:
    def _invoice(self, engagement, worker, *, status_=InvoiceStatus.REVIEW):
        from apps.payments.hourly import price_session

        day = timezone.localdate()
        session = WorkSession.objects.create(
            society=engagement.society, engagement=engagement, worker=worker,
            visit_date=day,
            started_at=timezone.make_aware(dt.datetime.combine(day, dt.time(9, 0))),
            ended_at=timezone.make_aware(dt.datetime.combine(day, dt.time(12, 0))),
            source=SessionSource.SELF, status=SessionStatus.CLOSED,
        )
        price_session(session)
        invoice = Invoice.objects.create(
            society=engagement.society, engagement=engagement,
            resident=engagement.resident, worker=worker,
            period_start=day.replace(day=1), period_end=day, status=status_,
        )
        invoice.add_session(session)
        return invoice, session

    def test_the_visit_fee_is_its_own_line(self, resident_client, engagement, worker):
        invoice, _session = self._invoice(engagement, worker)
        body = resident_client.get(
            reverse("v1:payments:invoice-detail", kwargs={"pk": invoice.pk})
        ).json()
        kinds = {line["kind"] for line in body["lines"]}
        assert "visit_fee" in kinds
        assert body["visit_fee_paise"] == 6_000

    def test_the_worker_sees_the_same_bill(self, worker_client, engagement, worker):
        invoice, _session = self._invoice(engagement, worker)
        body = worker_client.get(reverse("v1:payments:invoice-list")).json()
        assert [row["number"] for row in body] == [invoice.number]

    def test_raising_a_query_holds_only_that_line(
        self, resident_client, engagement, worker
    ):
        invoice, session = self._invoice(engagement, worker)
        response = resident_client.post(
            reverse("v1:payments:invoice-query", kwargs={"pk": invoice.pk}),
            {"session": str(session.pk), "reason": "hours_disputed",
             "description": "She reached about 11:40."},
            format="json",
        )
        assert response.status_code == 201
        invoice.refresh_from_db()
        assert invoice.held_paise > 0
        assert SessionQuery.objects.count() == 1

    def test_raising_the_same_query_twice_does_not_duplicate_it(
        self, resident_client, engagement, worker
    ):
        invoice, session = self._invoice(engagement, worker)
        payload = {"session": str(session.pk), "reason": "hours_disputed"}
        url = reverse("v1:payments:invoice-query", kwargs={"pk": invoice.pk})

        resident_client.post(url, payload, format="json")
        resident_client.post(url, payload, format="json")

        assert SessionQuery.objects.count() == 1

    def test_the_other_party_can_accept_in_one_tap(
        self, resident_client, worker_client, engagement, worker
    ):
        invoice, session = self._invoice(engagement, worker)
        resident_client.post(
            reverse("v1:payments:invoice-query", kwargs={"pk": invoice.pk}),
            {"session": str(session.pk), "reason": "hours_disputed"},
            format="json",
        )
        query = SessionQuery.objects.get()

        response = worker_client.post(
            reverse("v1:payments:query-accept", kwargs={"pk": query.pk})
        )
        assert response.status_code == 200
        invoice.refresh_from_db()
        assert invoice.held_paise == 0

    def test_an_open_query_is_exposed_so_it_can_be_accepted(
        self, resident_client, worker_client, engagement, worker
    ):
        """Stage two needs a query id on the wire, or it cannot be offered.

        Without this the client has no way to call accept, every query waits out
        its 48 hours, and the ladder delivers every dispute to a volunteer
        committee member — the outcome it was designed to prevent.
        """
        invoice, session = self._invoice(engagement, worker)
        resident_client.post(
            reverse("v1:payments:invoice-query", kwargs={"pk": invoice.pk}),
            {"session": str(session.pk), "reason": "hours_disputed",
             "description": "She reached about 11:40."},
            format="json",
        )

        # The worker sees it and may accept: she did not raise it.
        hers = worker_client.get(
            reverse("v1:payments:invoice-detail", kwargs={"pk": invoice.pk})
        ).json()["open_queries"]
        assert len(hers) == 1
        assert hers[0]["can_accept"] is True
        assert hers[0]["description"] == "She reached about 11:40."

        # The resident sees the same query and may not accept their own.
        theirs = resident_client.get(
            reverse("v1:payments:invoice-detail", kwargs={"pk": invoice.pk})
        ).json()["open_queries"]
        assert theirs[0]["id"] == hers[0]["id"]
        assert theirs[0]["can_accept"] is False

    def test_a_resolved_query_leaves_the_open_list(
        self, resident_client, worker_client, engagement, worker
    ):
        invoice, session = self._invoice(engagement, worker)
        resident_client.post(
            reverse("v1:payments:invoice-query", kwargs={"pk": invoice.pk}),
            {"session": str(session.pk), "reason": "hours_disputed"},
            format="json",
        )
        query = SessionQuery.objects.get()
        worker_client.post(reverse("v1:payments:query-accept", kwargs={"pk": query.pk}))

        body = resident_client.get(
            reverse("v1:payments:invoice-detail", kwargs={"pk": invoice.pk})
        ).json()
        assert body["open_queries"] == []

    def test_you_cannot_accept_your_own_query(
        self, resident_client, engagement, worker
    ):
        invoice, session = self._invoice(engagement, worker)
        resident_client.post(
            reverse("v1:payments:invoice-query", kwargs={"pk": invoice.pk}),
            {"session": str(session.pk), "reason": "hours_disputed"},
            format="json",
        )
        query = SessionQuery.objects.get()

        response = resident_client.post(
            reverse("v1:payments:query-accept", kwargs={"pk": query.pk})
        )
        assert response.status_code == 400

    def test_an_issued_bill_routes_to_a_payment_dispute_instead(
        self, resident_client, engagement, worker
    ):
        invoice, session = self._invoice(engagement, worker, status_=InvoiceStatus.ISSUED)
        response = resident_client.post(
            reverse("v1:payments:invoice-query", kwargs={"pk": invoice.pk}),
            {"session": str(session.pk), "reason": "hours_disputed"},
            format="json",
        )
        assert response.status_code == 409

    def test_unbilled_extra_time_is_shown_not_hidden(
        self, resident_client, engagement, worker
    ):
        invoice, session = self._invoice(engagement, worker)
        WorkSession.objects.filter(pk=session.pk).update(unbilled_extra_minutes=11)
        body = resident_client.get(
            reverse("v1:payments:invoice-detail", kwargs={"pk": invoice.pk})
        ).json()
        assert body["unbilled_extra_minutes"] == 11
