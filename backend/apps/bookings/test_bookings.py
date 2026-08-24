"""
Module 5 — One-Day Service Booking: API tests.

Grouped by sub-module. The conflict and cancellation-fee cases carry the most
weight: double-booking a worker costs someone a day's income, and a fee that
differs from the one the resident was shown is the kind of bug that destroys
trust in the app rather than merely annoying someone.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import Role
from apps.core.pricing import MAID_DAY_CATEGORY_SLUG, MAID_DAY_RATE_INR
from apps.bookings.models import (
    Booking,
    BookingStatus,
    DayAvailability,
    ServiceCategory,
)
from apps.hiring.models import Engagement
from apps.societies.models import Flat, Resident, Society, SocietyStatus, Tower
from apps.workers.models import ServiceType, WorkerProfile

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def cleaning_category(db):
    """Seeded by migration 0002; fetched rather than recreated."""
    return ServiceCategory.objects.get(slug="deep-cleaning")


@pytest.fixture
def emergency_category(db):
    return ServiceCategory.objects.get(slug="emergency-assistance")


@pytest.fixture
def maid_day_category(db):
    """Seeded by migration 0005; the one category the platform prices itself."""
    return ServiceCategory.objects.get(slug=MAID_DAY_CATEGORY_SLUG)


@pytest.fixture
def flat(society):
    tower = Tower.objects.create(society=society, name="A", floors=10)
    return Flat.objects.create(tower=tower, number="301", floor=3)


@pytest.fixture
def resident(resident_user, flat):
    return Resident.objects.create(user=resident_user, flat=flat, is_primary=True)


def make_worker(user, **kwargs):
    kwargs.setdefault("photo", "workers/photos/test.jpg")
    return WorkerProfile.objects.create(user=user, **kwargs)


@pytest.fixture
def worker(worker_user):
    return make_worker(worker_user, trust_score=70, average_rating=4.5)


@pytest.fixture
def booking_date():
    """Comfortably beyond any society's notice window."""
    return timezone.localdate() + dt.timedelta(days=7)


@pytest.fixture
def available_worker(worker, booking_date):
    DayAvailability.objects.create(worker=worker, date=booking_date, is_available=True)
    return worker


def make_booking(resident, worker, category, booking_date, **kwargs):
    kwargs.setdefault("start_time", dt.time(10, 0))
    kwargs.setdefault("expected_duration_minutes", 120)
    kwargs.setdefault("quoted_price", 2000)
    return Booking.objects.create(
        society=resident.flat.tower.society,
        resident=resident,
        worker=worker,
        category=category,
        scheduled_date=booking_date,
        **kwargs,
    )


# ---------------------------------------------------------------------------
# 5.1 Catalogue
# ---------------------------------------------------------------------------


class TestServiceCatalogue:
    URL = "v1:bookings:category-list"

    def test_seeded_catalogue_is_listed(self, authenticated_client, resident_user):
        response = authenticated_client(resident_user).get(reverse(self.URL))

        assert response.status_code == 200
        slugs = {row["slug"] for row in response.data}
        assert {"deep-cleaning", "emergency-assistance", "temporary-cooking"} <= slugs

    def test_categories_carry_the_guidance_shown_before_booking(
        self, authenticated_client, resident_user
    ):
        response = authenticated_client(resident_user).get(reverse(self.URL))
        row = next(r for r in response.data if r["slug"] == "deep-cleaning")

        assert row["expected_duration_minutes"] > 0
        assert row["price_min"] > 0
        assert row["price_max"] >= row["price_min"]
        assert row["price_guidance"]

    def test_only_emergency_bypasses_the_notice_window(
        self, authenticated_client, resident_user
    ):
        response = authenticated_client(resident_user).get(reverse(self.URL))
        exempt = {r["slug"] for r in response.data if r["bypasses_notice_period"]}

        assert exempt == {"emergency-assistance"}

    def test_inactive_categories_are_hidden(
        self, authenticated_client, resident_user, cleaning_category
    ):
        cleaning_category.is_active = False
        cleaning_category.save(update_fields=["is_active"])

        response = authenticated_client(resident_user).get(reverse(self.URL))
        assert "deep-cleaning" not in {row["slug"] for row in response.data}

    def test_anonymous_cannot_read_the_catalogue(self, api_client):
        assert api_client.get(reverse(self.URL)).status_code == 401


# ---------------------------------------------------------------------------
# 5.3 Day availability
# ---------------------------------------------------------------------------


class TestMyAvailability:
    URL = "v1:bookings:my-availability"

    def test_worker_opts_into_a_date(
        self, authenticated_client, worker_user, worker, booking_date
    ):
        response = authenticated_client(worker_user).put(
            reverse(self.URL),
            {"date": booking_date.isoformat(), "is_available": True},
            format="json",
        )

        assert response.status_code == 201
        assert DayAvailability.objects.filter(worker=worker, date=booking_date).exists()

    def test_setting_the_same_date_twice_updates_rather_than_duplicates(
        self, authenticated_client, worker_user, worker, booking_date
    ):
        """A double tap on a flaky connection must not create two answers."""
        client = authenticated_client(worker_user)
        payload = {"date": booking_date.isoformat(), "is_available": True}

        assert client.put(reverse(self.URL), payload, format="json").status_code == 201
        second = client.put(
            reverse(self.URL), {**payload, "is_available": False}, format="json"
        )

        assert second.status_code == 200
        rows = DayAvailability.objects.filter(worker=worker, date=booking_date)
        assert rows.count() == 1
        assert rows.first().is_available is False

    def test_half_a_window_is_rejected(
        self, authenticated_client, worker_user, worker, booking_date
    ):
        response = authenticated_client(worker_user).put(
            reverse(self.URL),
            {"date": booking_date.isoformat(), "start_time": "09:00"},
            format="json",
        )
        assert response.status_code == 400

    def test_inverted_window_is_rejected(
        self, authenticated_client, worker_user, worker, booking_date
    ):
        response = authenticated_client(worker_user).put(
            reverse(self.URL),
            {
                "date": booking_date.isoformat(),
                "start_time": "18:00",
                "end_time": "09:00",
            },
            format="json",
        )
        assert response.status_code == 400

    def test_listing_hides_past_dates_by_default(
        self, authenticated_client, worker_user, worker, booking_date
    ):
        DayAvailability.objects.create(
            worker=worker, date=timezone.localdate() - dt.timedelta(days=3)
        )
        DayAvailability.objects.create(worker=worker, date=booking_date)

        response = authenticated_client(worker_user).get(reverse(self.URL))
        assert [row["date"] for row in response.data] == [booking_date.isoformat()]

    def test_resident_cannot_set_availability(
        self, authenticated_client, resident_user, booking_date
    ):
        response = authenticated_client(resident_user).put(
            reverse(self.URL), {"date": booking_date.isoformat()}, format="json"
        )
        assert response.status_code == 403


# ---------------------------------------------------------------------------
# 5.3 Matching
# ---------------------------------------------------------------------------


class TestMatching:
    URL = "v1:bookings:match"

    def params(self, category, booking_date, **overrides):
        payload = {
            "category": category.pk,
            "date": booking_date.isoformat(),
            "start_time": "10:00",
        }
        payload.update(overrides)
        return payload

    def test_matches_a_worker_who_opted_in(
        self, authenticated_client, resident, resident_user,
        available_worker, cleaning_category, booking_date,
    ):
        response = authenticated_client(resident_user).get(
            reverse(self.URL), self.params(cleaning_category, booking_date)
        )

        assert response.status_code == 200
        assert [row["id"] for row in response.data["results"]] == [available_worker.pk]

    def test_results_carry_module_4s_match_score(
        self, authenticated_client, resident, resident_user,
        available_worker, cleaning_category, booking_date,
    ):
        response = authenticated_client(resident_user).get(
            reverse(self.URL), self.params(cleaning_category, booking_date)
        )
        assert response.data["results"][0]["match_percentage"] is not None

    def test_a_worker_who_never_opted_in_is_still_matched(
        self, authenticated_client, resident, resident_user,
        worker, cleaning_category, booking_date,
    ):
        """A worker who never blocked the date is bookable on it.

        The regression this pins: requiring an explicit per-date opt-in row
        made every category except the ``bypasses_notice_period`` one return
        an empty list, because almost nobody sets those rows.
        """
        response = authenticated_client(resident_user).get(
            reverse(self.URL), self.params(cleaning_category, booking_date)
        )
        assert [row["id"] for row in response.data["results"]] == [worker.pk]

    def test_an_emergency_category_matches_a_worker_who_never_opted_in(
        self, authenticated_client, resident, resident_user,
        worker, emergency_category, booking_date,
    ):
        """The emergency category behaves the same as every other one now."""
        response = authenticated_client(resident_user).get(
            reverse(self.URL), self.params(emergency_category, booking_date)
        )
        assert [row["id"] for row in response.data["results"]] == [worker.pk]

    @pytest.mark.parametrize(
        "slug", ["deep-cleaning", "event-preparation", "temporary-cooking"]
    )
    def test_every_ordinary_category_can_match(
        self, authenticated_client, resident, resident_user,
        worker, booking_date, slug,
    ):
        """Bug 1, stated in the terms it was reported in: it was not one
        category misbehaving, it was every category *but* the urgent one."""
        category = ServiceCategory.objects.get(slug=slug)

        response = authenticated_client(resident_user).get(
            reverse(self.URL), self.params(category, booking_date)
        )

        assert response.data["count"] == 1
        assert [row["id"] for row in response.data["results"]] == [worker.pk]

    def test_a_worker_who_blocked_the_date_is_not_matched(
        self, authenticated_client, resident, resident_user,
        worker, cleaning_category, booking_date,
    ):
        """The override still works in the direction that matters."""
        DayAvailability.objects.create(
            worker=worker, date=booking_date, is_available=False
        )

        response = authenticated_client(resident_user).get(
            reverse(self.URL), self.params(cleaning_category, booking_date)
        )
        assert response.data["results"] == []

    def test_an_emergency_category_still_excludes_an_explicit_no(
        self, authenticated_client, resident, resident_user,
        worker, emergency_category, booking_date,
    ):
        DayAvailability.objects.create(
            worker=worker, date=booking_date, is_available=False
        )

        response = authenticated_client(resident_user).get(
            reverse(self.URL), self.params(emergency_category, booking_date)
        )
        assert response.data["results"] == []

    def test_an_emergency_category_still_honours_a_declared_window(
        self, authenticated_client, resident, resident_user,
        worker, emergency_category, booking_date,
    ):
        DayAvailability.objects.create(
            worker=worker,
            date=booking_date,
            is_available=True,
            start_time=dt.time(14, 0),
            end_time=dt.time(18, 0),
        )

        client = authenticated_client(resident_user)
        outside = client.get(
            reverse(self.URL), self.params(emergency_category, booking_date)
        )
        inside = client.get(
            reverse(self.URL),
            self.params(emergency_category, booking_date, start_time="14:00"),
        )

        assert outside.data["results"] == []
        assert len(inside.data["results"]) == 1

    def test_a_blocked_date_is_not_matched(
        self, authenticated_client, resident, resident_user,
        worker, cleaning_category, booking_date,
    ):
        DayAvailability.objects.create(
            worker=worker, date=booking_date, is_available=False
        )

        response = authenticated_client(resident_user).get(
            reverse(self.URL), self.params(cleaning_category, booking_date)
        )
        assert response.data["results"] == []

    def test_a_narrower_declared_window_is_respected(
        self, authenticated_client, resident, resident_user,
        worker, cleaning_category, booking_date,
    ):
        DayAvailability.objects.create(
            worker=worker,
            date=booking_date,
            is_available=True,
            start_time=dt.time(14, 0),
            end_time=dt.time(18, 0),
        )

        client = authenticated_client(resident_user)
        outside = client.get(
            reverse(self.URL), self.params(cleaning_category, booking_date)
        )
        inside = client.get(
            reverse(self.URL),
            self.params(cleaning_category, booking_date, start_time="14:00"),
        )

        assert outside.data["results"] == []
        assert len(inside.data["results"]) == 1

    def test_an_existing_booking_removes_the_worker_from_the_pool(
        self, authenticated_client, resident, resident_user,
        available_worker, cleaning_category, booking_date,
    ):
        make_booking(
            resident, available_worker, cleaning_category, booking_date,
            start_time=dt.time(10, 0), expected_duration_minutes=120,
        )

        response = authenticated_client(resident_user).get(
            reverse(self.URL),
            self.params(cleaning_category, booking_date, start_time="11:00"),
        )
        assert response.data["results"] == []

    def test_a_non_overlapping_booking_leaves_the_worker_matchable(
        self, authenticated_client, resident, resident_user,
        available_worker, cleaning_category, booking_date,
    ):
        """Back-to-back jobs are a normal working day, not a conflict."""
        make_booking(
            resident, available_worker, cleaning_category, booking_date,
            start_time=dt.time(8, 0), expected_duration_minutes=120,
        )

        response = authenticated_client(resident_user).get(
            reverse(self.URL),
            self.params(cleaning_category, booking_date, start_time="10:00"),
        )
        assert len(response.data["results"]) == 1

    def test_a_recurring_engagement_blocks_the_slot(
        self, authenticated_client, resident, resident_user,
        available_worker, cleaning_category, booking_date, maid_service_type,
    ):
        """A Module 4 engagement occupies the day just as a booking does."""
        Engagement.objects.create(
            society=resident.flat.tower.society,
            resident=resident,
            worker=available_worker,
            service_type=maid_service_type,
            days_of_week=[booking_date.weekday()],
            start_time=dt.time(10, 0),
            expected_duration_minutes=120,
            monthly_rate=4000,
        )

        response = authenticated_client(resident_user).get(
            reverse(self.URL),
            self.params(cleaning_category, booking_date, start_time="11:00"),
        )
        assert response.data["results"] == []

    def test_an_engagement_on_another_weekday_does_not_block(
        self, authenticated_client, resident, resident_user,
        available_worker, cleaning_category, booking_date, maid_service_type,
    ):
        other_weekday = (booking_date.weekday() + 1) % 7
        Engagement.objects.create(
            society=resident.flat.tower.society,
            resident=resident,
            worker=available_worker,
            service_type=maid_service_type,
            days_of_week=[other_weekday],
            start_time=dt.time(10, 0),
            monthly_rate=4000,
        )

        response = authenticated_client(resident_user).get(
            reverse(self.URL), self.params(cleaning_category, booking_date)
        )
        assert len(response.data["results"]) == 1

    def test_category_service_type_narrows_the_pool(
        self, authenticated_client, resident, resident_user,
        available_worker, cleaning_category, booking_date, maid_service_type,
    ):
        cleaning_category.service_type = maid_service_type
        cleaning_category.save(update_fields=["service_type"])

        client = authenticated_client(resident_user)
        without = client.get(
            reverse(self.URL), self.params(cleaning_category, booking_date)
        )
        assert without.data["results"] == []

        available_worker.service_types.add(maid_service_type)
        with_type = client.get(
            reverse(self.URL), self.params(cleaning_category, booking_date)
        )
        assert len(with_type.data["results"]) == 1

    def test_another_societys_worker_is_never_matched(
        self, authenticated_client, resident, resident_user,
        cleaning_category, booking_date, django_user_model,
    ):
        other = Society.objects.create(
            name="Blue Ridge", address_line="Kalyani Nagar", city="Pune",
            state="Maharashtra", pincode="411006", status=SocietyStatus.ACTIVE,
        )
        outsider = django_user_model.objects.create_user(
            phone_number="9800000091", password="test-pass-12345",
            role=Role.WORKER, society=other, is_approved=True,
        )
        outside_worker = make_worker(outsider)
        DayAvailability.objects.create(worker=outside_worker, date=booking_date)

        response = authenticated_client(resident_user).get(
            reverse(self.URL), self.params(cleaning_category, booking_date)
        )
        assert response.data["results"] == []

    def test_worker_cannot_run_the_match_query(
        self, authenticated_client, worker_user, worker, cleaning_category, booking_date
    ):
        response = authenticated_client(worker_user).get(
            reverse(self.URL), self.params(cleaning_category, booking_date)
        )
        assert response.status_code == 403

    def test_missing_parameters_are_rejected(
        self, authenticated_client, resident, resident_user
    ):
        response = authenticated_client(resident_user).get(reverse(self.URL))
        assert response.status_code == 400


@pytest.fixture
def maid_service_type(db):
    return ServiceType.objects.create(name="Maid", slug="maid")


# ---------------------------------------------------------------------------
# 5.2 Booking creation
# ---------------------------------------------------------------------------


def booking_payload(worker, category, booking_date, **overrides):
    payload = {
        "worker": worker.pk,
        "category": category.pk,
        "scheduled_date": booking_date.isoformat(),
        "start_time": "10:00",
        "expected_duration_minutes": 120,
        "quoted_price": 2000,
        "notes": "Third floor, please ring the bell.",
    }
    payload.update(overrides)
    return payload


class TestCreateBooking:
    URL = "v1:bookings:booking-list"

    def test_primary_resident_can_book(
        self, authenticated_client, resident, resident_user,
        available_worker, cleaning_category, booking_date,
    ):
        response = authenticated_client(resident_user).post(
            reverse(self.URL),
            booking_payload(available_worker, cleaning_category, booking_date),
            format="json",
        )

        assert response.status_code == 201
        assert response.data["booking"]["status"] == BookingStatus.PENDING
        assert Booking.objects.count() == 1

    def test_catalogue_defaults_fill_in_when_omitted(
        self, authenticated_client, resident, resident_user,
        available_worker, cleaning_category, booking_date,
    ):
        payload = booking_payload(available_worker, cleaning_category, booking_date)
        payload.pop("expected_duration_minutes")
        payload.pop("quoted_price")

        response = authenticated_client(resident_user).post(
            reverse(self.URL), payload, format="json"
        )

        assert response.status_code == 201
        booking = Booking.objects.get()
        assert booking.expected_duration_minutes == cleaning_category.expected_duration_minutes
        assert booking.quoted_price == cleaning_category.price_min

    def test_maid_day_hire_is_priced_by_the_platform(
        self, authenticated_client, resident, resident_user,
        available_worker, maid_day_category, booking_date,
    ):
        """A day's help costs what the price list says, with no quote sent."""
        payload = booking_payload(available_worker, maid_day_category, booking_date)
        payload.pop("quoted_price")

        response = authenticated_client(resident_user).post(
            reverse(self.URL), payload, format="json"
        )

        assert response.status_code == 201
        assert Booking.objects.get().quoted_price == MAID_DAY_RATE_INR

    def test_maid_day_hire_refuses_a_different_quote(
        self, authenticated_client, resident, resident_user,
        available_worker, maid_day_category, booking_date,
    ):
        """Refused outright, not silently corrected.

        A resident who believes they booked at their own figure has to be told
        they did not; overwriting the number would let them find out at payment.
        """
        response = authenticated_client(resident_user).post(
            reverse(self.URL),
            booking_payload(
                available_worker, maid_day_category, booking_date, quoted_price=90
            ),
            format="json",
        )

        assert response.status_code == 400
        assert "quoted_price" in response.data["error"]["details"]
        assert Booking.objects.count() == 0

    def test_specialist_categories_keep_their_own_prices(
        self, authenticated_client, resident, resident_user,
        available_worker, cleaning_category, booking_date,
    ):
        """The day rate is scoped to maid hire and must not leak elsewhere.

        Deep cleaning is a different job at a different price, and an agreed
        quote for it is still the resident's and worker's to make.
        """
        response = authenticated_client(resident_user).post(
            reverse(self.URL),
            booking_payload(
                available_worker, cleaning_category, booking_date, quoted_price=2600
            ),
            format="json",
        )

        assert response.status_code == 201
        assert Booking.objects.get().quoted_price == 2600

    def test_non_primary_resident_is_refused(
        self, authenticated_client, resident, resident_user,
        available_worker, cleaning_category, booking_date,
    ):
        resident.is_primary = False
        resident.save(update_fields=["is_primary"])

        response = authenticated_client(resident_user).post(
            reverse(self.URL),
            booking_payload(available_worker, cleaning_category, booking_date),
            format="json",
        )
        assert response.status_code == 403

    def test_booking_a_worker_who_did_not_opt_in_is_allowed(
        self, authenticated_client, resident, resident_user,
        worker, cleaning_category, booking_date,
    ):
        """The other half of bug 1. Creation enforces the same availability
        rule as matching, so relaxing one without the other would list a
        worker and then refuse the booking the resident makes from that list.
        """
        response = authenticated_client(resident_user).post(
            reverse(self.URL),
            booking_payload(worker, cleaning_category, booking_date),
            format="json",
        )

        assert response.status_code == 201

    def test_booking_a_worker_who_blocked_the_date_is_refused(
        self, authenticated_client, resident, resident_user,
        worker, cleaning_category, booking_date,
    ):
        DayAvailability.objects.create(
            worker=worker, date=booking_date, is_available=False
        )

        response = authenticated_client(resident_user).post(
            reverse(self.URL),
            booking_payload(worker, cleaning_category, booking_date),
            format="json",
        )

        assert response.status_code == 409
        assert response.data["error"]["code"] == "worker_unavailable"

    def test_too_little_notice_is_refused(
        self, authenticated_client, resident, resident_user,
        worker, cleaning_category, society,
    ):
        """The society requires 12 hours' notice by default.

        Date and time come from the same helper so the pair stays consistent:
        deriving "two hours from now" but pinning the date to *today* puts the
        booking in the past whenever the suite runs after 22:00, which would
        exercise a different rejection than the one under test.
        """
        day, moment = hours_from_now(2)
        DayAvailability.objects.create(worker=worker, date=day, is_available=True)

        response = authenticated_client(resident_user).post(
            reverse(self.URL),
            booking_payload(
                worker, cleaning_category, day,
                start_time=moment.strftime("%H:%M"),
            ),
            format="json",
        )

        assert response.status_code == 400
        assert response.data["error"]["code"] == "notice_too_short"

    def test_an_emergency_cannot_be_aimed_at_one_chosen_worker(
        self, authenticated_client, resident, resident_user,
        worker, emergency_category,
    ):
        """Module 5.5 — an emergency broadcasts, or it does not happen.

        This endpoint used to accept one: the category was notice-exempt, so it
        sailed through the window check and produced a request aimed at a single
        worker. That booking collected no surcharge, reached nobody else, and
        expired at its own start time — which is precisely the request a worker
        reported being unable to accept.

        Refused here rather than only routed around in the app, because an app
        build that has not been updated must not be able to create one.
        """
        day, moment = hours_from_now(2)
        DayAvailability.objects.create(worker=worker, date=day, is_available=True)

        response = authenticated_client(resident_user).post(
            reverse(self.URL),
            booking_payload(
                worker, emergency_category, day,
                start_time=moment.strftime("%H:%M"),
                expected_duration_minutes=60,
            ),
            format="json",
        )

        assert response.status_code == 409
        assert response.data["error"]["code"] == "emergency_must_broadcast"

    def test_an_ordinary_category_is_unaffected(
        self, authenticated_client, resident, resident_user,
        available_worker, cleaning_category, booking_date,
    ):
        """The refusal is scoped to notice-exempt categories and nothing else."""
        response = authenticated_client(resident_user).post(
            reverse(self.URL),
            booking_payload(available_worker, cleaning_category, booking_date),
            format="json",
        )

        assert response.status_code == 201

    def test_overlapping_booking_is_refused(
        self, authenticated_client, resident, resident_user,
        available_worker, cleaning_category, booking_date,
    ):
        make_booking(
            resident, available_worker, cleaning_category, booking_date,
            start_time=dt.time(10, 0), expected_duration_minutes=120,
        )

        response = authenticated_client(resident_user).post(
            reverse(self.URL),
            booking_payload(
                available_worker, cleaning_category, booking_date, start_time="11:00"
            ),
            format="json",
        )

        assert response.status_code == 409
        assert response.data["error"]["code"] == "slot_conflict"

    def test_another_societys_worker_is_refused(
        self, authenticated_client, resident, resident_user,
        cleaning_category, booking_date, django_user_model,
    ):
        other = Society.objects.create(
            name="Blue Ridge", address_line="Kalyani Nagar", city="Pune",
            state="Maharashtra", pincode="411006", status=SocietyStatus.ACTIVE,
        )
        outsider = django_user_model.objects.create_user(
            phone_number="9800000092", password="test-pass-12345",
            role=Role.WORKER, society=other, is_approved=True,
        )
        outside_worker = make_worker(outsider)
        DayAvailability.objects.create(worker=outside_worker, date=booking_date)

        response = authenticated_client(resident_user).post(
            reverse(self.URL),
            booking_payload(outside_worker, cleaning_category, booking_date),
            format="json",
        )
        assert response.status_code == 400

    def test_worker_cannot_create_a_booking(
        self, authenticated_client, worker_user, available_worker,
        cleaning_category, booking_date,
    ):
        response = authenticated_client(worker_user).post(
            reverse(self.URL),
            booking_payload(available_worker, cleaning_category, booking_date),
            format="json",
        )
        assert response.status_code == 403


# ---------------------------------------------------------------------------
# 5.4 Confirmation
# ---------------------------------------------------------------------------


class TestRespondToBooking:
    def url(self, pk):
        return reverse("v1:bookings:booking-respond", args=[pk])

    def test_worker_confirms(
        self, authenticated_client, resident, worker_user,
        available_worker, cleaning_category, booking_date,
    ):
        booking = make_booking(resident, available_worker, cleaning_category, booking_date)

        response = authenticated_client(worker_user).post(
            self.url(booking.pk), {"confirm": True}, format="json"
        )

        assert response.status_code == 200
        booking.refresh_from_db()
        assert booking.status == BookingStatus.CONFIRMED
        assert booking.confirmed_at is not None

    def test_worker_declines(
        self, authenticated_client, resident, worker_user,
        available_worker, cleaning_category, booking_date,
    ):
        booking = make_booking(resident, available_worker, cleaning_category, booking_date)

        response = authenticated_client(worker_user).post(
            self.url(booking.pk), {"confirm": False, "note": "Away that day."},
            format="json",
        )

        assert response.status_code == 200
        booking.refresh_from_db()
        assert booking.status == BookingStatus.DECLINED
        assert booking.response_note == "Away that day."

    def test_answering_twice_is_refused(
        self, authenticated_client, resident, worker_user,
        available_worker, cleaning_category, booking_date,
    ):
        booking = make_booking(resident, available_worker, cleaning_category, booking_date)
        client = authenticated_client(worker_user)

        assert client.post(self.url(booking.pk), {"confirm": True}, format="json").status_code == 200
        second = client.post(self.url(booking.pk), {"confirm": True}, format="json")
        assert second.status_code == 409

    def test_confirming_a_slot_taken_since_the_request_is_refused(
        self, authenticated_client, resident, worker_user,
        available_worker, cleaning_category, booking_date,
    ):
        """The worker took other work while this request sat unanswered."""
        booking = make_booking(
            resident, available_worker, cleaning_category, booking_date,
            start_time=dt.time(10, 0),
        )
        make_booking(
            resident, available_worker, cleaning_category, booking_date,
            start_time=dt.time(11, 0), status=BookingStatus.CONFIRMED,
        )

        response = authenticated_client(worker_user).post(
            self.url(booking.pk), {"confirm": True}, format="json"
        )

        assert response.status_code == 409
        assert response.data["error"]["code"] == "slot_conflict"

    def test_resident_cannot_confirm_on_the_workers_behalf(
        self, authenticated_client, resident, resident_user,
        available_worker, cleaning_category, booking_date,
    ):
        booking = make_booking(resident, available_worker, cleaning_category, booking_date)

        response = authenticated_client(resident_user).post(
            self.url(booking.pk), {"confirm": True}, format="json"
        )
        assert response.status_code == 403

    def test_another_worker_cannot_answer(
        self, authenticated_client, resident, available_worker,
        cleaning_category, booking_date, society, django_user_model,
    ):
        booking = make_booking(resident, available_worker, cleaning_category, booking_date)
        intruder = django_user_model.objects.create_user(
            phone_number="9800000093", password="test-pass-12345",
            role=Role.WORKER, society=society, is_approved=True,
        )
        make_worker(intruder)

        response = authenticated_client(intruder).post(
            self.url(booking.pk), {"confirm": True}, format="json"
        )
        assert response.status_code == 404


# ---------------------------------------------------------------------------
# 5.4 Cancellation
# ---------------------------------------------------------------------------


def hours_from_now(hours: float):
    """A (date, time) pair that many hours ahead, in local terms."""
    moment = timezone.localtime() + dt.timedelta(hours=hours)
    return moment.date(), moment.time().replace(second=0, microsecond=0)


class TestCancellation:
    def cancel_url(self, pk):
        return reverse("v1:bookings:booking-cancel", args=[pk])

    def quote_url(self, pk):
        return reverse("v1:bookings:cancellation-quote", args=[pk])

    def test_cancelling_well_ahead_is_free(
        self, authenticated_client, resident, resident_user,
        available_worker, cleaning_category, booking_date,
    ):
        booking = make_booking(
            resident, available_worker, cleaning_category, booking_date,
            status=BookingStatus.CONFIRMED,
        )

        response = authenticated_client(resident_user).post(
            self.cancel_url(booking.pk), {"reason": "Plans changed."}, format="json"
        )

        assert response.status_code == 200
        assert response.data["cancellation_fee"] == 0
        booking.refresh_from_db()
        assert booking.status == BookingStatus.CANCELLED
        assert booking.cancelled_by == "resident"

    def test_cancelling_close_to_the_start_charges_a_fee(
        self, authenticated_client, resident, resident_user,
        worker, cleaning_category,
    ):
        day, moment = hours_from_now(1)
        DayAvailability.objects.create(worker=worker, date=day, is_available=True)
        booking = make_booking(
            resident, worker, cleaning_category, day,
            start_time=moment, quoted_price=2000, status=BookingStatus.CONFIRMED,
        )

        response = authenticated_client(resident_user).post(
            self.cancel_url(booking.pk), {}, format="json"
        )

        assert response.status_code == 200
        assert response.data["cancellation_fee"] == 2000
        booking.refresh_from_db()
        assert booking.cancellation_fee == 2000

    def test_the_fee_is_stored_not_recomputed_later(
        self, authenticated_client, resident, resident_user,
        worker, cleaning_category,
    ):
        """What someone was charged must not drift when the policy changes."""
        day, moment = hours_from_now(1)
        DayAvailability.objects.create(worker=worker, date=day, is_available=True)
        booking = make_booking(
            resident, worker, cleaning_category, day,
            start_time=moment, quoted_price=1500, status=BookingStatus.CONFIRMED,
        )

        authenticated_client(resident_user).post(
            self.cancel_url(booking.pk), {}, format="json"
        )

        booking.refresh_from_db()
        assert booking.cancellation_fee == 1500
        assert booking.cancelled_at is not None

    def test_quote_matches_what_cancelling_actually_charges(
        self, authenticated_client, resident, resident_user,
        worker, cleaning_category,
    ):
        day, moment = hours_from_now(4)
        DayAvailability.objects.create(worker=worker, date=day, is_available=True)
        booking = make_booking(
            resident, worker, cleaning_category, day,
            start_time=moment, quoted_price=2000, status=BookingStatus.CONFIRMED,
        )
        client = authenticated_client(resident_user)

        quoted = client.get(self.quote_url(booking.pk)).data
        charged = client.post(self.cancel_url(booking.pk), {}, format="json").data

        assert quoted["fee"] == charged["cancellation_fee"] == 1000
        assert quoted["tier"] == "partial"

    def test_a_stale_acknowledged_fee_is_refused(
        self, authenticated_client, resident, resident_user,
        worker, cleaning_category,
    ):
        """Never charge more than the number the person actually saw."""
        day, moment = hours_from_now(1)
        DayAvailability.objects.create(worker=worker, date=day, is_available=True)
        booking = make_booking(
            resident, worker, cleaning_category, day,
            start_time=moment, quoted_price=2000, status=BookingStatus.CONFIRMED,
        )

        response = authenticated_client(resident_user).post(
            self.cancel_url(booking.pk), {"acknowledged_fee": 0}, format="json"
        )

        assert response.status_code == 409
        assert response.data["error"]["code"] == "fee_changed"
        booking.refresh_from_db()
        assert booking.status == BookingStatus.CONFIRMED

    def test_a_matching_acknowledged_fee_goes_through(
        self, authenticated_client, resident, resident_user,
        available_worker, cleaning_category, booking_date,
    ):
        booking = make_booking(
            resident, available_worker, cleaning_category, booking_date,
            status=BookingStatus.CONFIRMED,
        )

        response = authenticated_client(resident_user).post(
            self.cancel_url(booking.pk), {"acknowledged_fee": 0}, format="json"
        )
        assert response.status_code == 200

    def test_worker_can_cancel_too(
        self, authenticated_client, resident, worker_user,
        available_worker, cleaning_category, booking_date,
    ):
        """The policy is symmetric — a worker dropping out is the same problem."""
        booking = make_booking(
            resident, available_worker, cleaning_category, booking_date,
            status=BookingStatus.CONFIRMED,
        )

        response = authenticated_client(worker_user).post(
            self.cancel_url(booking.pk), {}, format="json"
        )

        assert response.status_code == 200
        booking.refresh_from_db()
        assert booking.cancelled_by == "worker"

    def test_a_started_booking_cannot_be_cancelled(
        self, authenticated_client, resident, resident_user,
        worker, cleaning_category,
    ):
        day, moment = hours_from_now(-2)
        booking = make_booking(
            resident, worker, cleaning_category, day,
            start_time=moment, status=BookingStatus.CONFIRMED,
        )

        response = authenticated_client(resident_user).post(
            self.cancel_url(booking.pk), {}, format="json"
        )
        assert response.status_code == 409

    def test_quote_is_refused_for_a_booking_that_cannot_be_cancelled(
        self, authenticated_client, resident, resident_user,
        worker, cleaning_category,
    ):
        day, moment = hours_from_now(-2)
        booking = make_booking(
            resident, worker, cleaning_category, day,
            start_time=moment, status=BookingStatus.CONFIRMED,
        )

        response = authenticated_client(resident_user).get(self.quote_url(booking.pk))
        assert response.status_code == 409


# ---------------------------------------------------------------------------
# Lifecycle, visibility, expiry
# ---------------------------------------------------------------------------


class TestCompletion:
    def url(self, pk):
        return reverse("v1:bookings:booking-complete", args=[pk])

    def test_a_started_confirmed_booking_can_be_completed(
        self, authenticated_client, resident, resident_user, worker, cleaning_category
    ):
        day, moment = hours_from_now(-3)
        booking = make_booking(
            resident, worker, cleaning_category, day,
            start_time=moment, status=BookingStatus.CONFIRMED,
        )

        response = authenticated_client(resident_user).post(self.url(booking.pk))

        assert response.status_code == 200
        booking.refresh_from_db()
        assert booking.status == BookingStatus.COMPLETED

    def test_completing_credits_the_workers_job_count(
        self, authenticated_client, resident, resident_user, worker, cleaning_category
    ):
        """Module 4.3 uses this count as the rating count, so it must move."""
        day, moment = hours_from_now(-3)
        booking = make_booking(
            resident, worker, cleaning_category, day,
            start_time=moment, status=BookingStatus.CONFIRMED,
        )
        before = worker.completed_engagements

        authenticated_client(resident_user).post(self.url(booking.pk))

        worker.refresh_from_db()
        assert worker.completed_engagements == before + 1

    def test_a_future_booking_cannot_be_completed(
        self, authenticated_client, resident, resident_user,
        available_worker, cleaning_category, booking_date,
    ):
        booking = make_booking(
            resident, available_worker, cleaning_category, booking_date,
            status=BookingStatus.CONFIRMED,
        )

        response = authenticated_client(resident_user).post(self.url(booking.pk))
        assert response.status_code == 409

    def test_an_unconfirmed_booking_cannot_be_completed(
        self, authenticated_client, resident, resident_user, worker, cleaning_category
    ):
        day, moment = hours_from_now(-3)
        booking = make_booking(resident, worker, cleaning_category, day, start_time=moment)

        response = authenticated_client(resident_user).post(self.url(booking.pk))
        assert response.status_code == 409

    def test_a_freshly_completed_booking_is_not_marked_paid(
        self, authenticated_client, resident, resident_user, worker, cleaning_category
    ):
        day, moment = hours_from_now(-3)
        booking = make_booking(
            resident, worker, cleaning_category, day,
            start_time=moment, status=BookingStatus.CONFIRMED,
        )

        response = authenticated_client(resident_user).post(self.url(booking.pk))
        assert response.data["booking"]["is_paid"] is False

    def test_a_booking_with_a_settled_payment_is_marked_paid(
        self, authenticated_client, resident, resident_user, worker, cleaning_category
    ):
        from apps.payments.models import Payment, PaymentKind, PaymentStatus

        day, moment = hours_from_now(-3)
        booking = make_booking(
            resident, worker, cleaning_category, day,
            start_time=moment, status=BookingStatus.COMPLETED,
        )
        Payment.objects.create(
            society=booking.society,
            resident=resident,
            worker=worker,
            booking=booking,
            kind=PaymentKind.BOOKING,
            amount_paise=booking.quoted_price * 100,
            status=PaymentStatus.PAID,
        )

        response = authenticated_client(resident_user).get(
            reverse("v1:bookings:booking-detail", args=[booking.pk])
        )
        assert response.data["is_paid"] is True


class TestVisibilityAndExpiry:
    URL = "v1:bookings:booking-list"

    def test_both_parties_see_the_booking(
        self, authenticated_client, resident, resident_user, worker_user,
        available_worker, cleaning_category, booking_date,
    ):
        make_booking(resident, available_worker, cleaning_category, booking_date)

        assert authenticated_client(resident_user).get(reverse(self.URL)).data["count"] == 1
        assert authenticated_client(worker_user).get(reverse(self.URL)).data["count"] == 1

    def test_society_admin_sees_their_societys_bookings(
        self, authenticated_client, admin_user, resident,
        available_worker, cleaning_category, booking_date,
    ):
        make_booking(resident, available_worker, cleaning_category, booking_date)

        response = authenticated_client(admin_user).get(reverse(self.URL))
        assert response.data["count"] == 1

    def test_guard_sees_nothing(
        self, authenticated_client, guard_user, resident,
        available_worker, cleaning_category, booking_date,
    ):
        make_booking(resident, available_worker, cleaning_category, booking_date)

        response = authenticated_client(guard_user).get(reverse(self.URL))
        assert response.status_code == 403

    def test_an_unrelated_resident_sees_nothing(
        self, authenticated_client, resident, available_worker,
        cleaning_category, booking_date, society, django_user_model,
    ):
        make_booking(resident, available_worker, cleaning_category, booking_date)
        tower = Tower.objects.create(society=society, name="B", floors=4)
        other_flat = Flat.objects.create(tower=tower, number="101", floor=1)
        other_user = django_user_model.objects.create_user(
            phone_number="9800000094", password="test-pass-12345",
            role=Role.RESIDENT, society=society, is_approved=True,
        )
        Resident.objects.create(user=other_user, flat=other_flat, is_primary=True)

        response = authenticated_client(other_user).get(reverse(self.URL))
        assert response.data["count"] == 0

    def test_an_unconfirmed_past_booking_is_swept_to_expired(
        self, authenticated_client, resident, resident_user, worker, cleaning_category
    ):
        """Expiry is lazy, so listing must not show a stale booking as pending."""
        day, moment = hours_from_now(-5)
        booking = make_booking(resident, worker, cleaning_category, day, start_time=moment)

        response = authenticated_client(resident_user).get(reverse(self.URL))

        assert response.data["results"][0]["status"] == BookingStatus.EXPIRED
        booking.refresh_from_db()
        assert booking.status == BookingStatus.EXPIRED

    def test_a_confirmed_past_booking_is_not_swept(
        self, authenticated_client, resident, resident_user, worker, cleaning_category
    ):
        day, moment = hours_from_now(-5)
        booking = make_booking(
            resident, worker, cleaning_category, day,
            start_time=moment, status=BookingStatus.CONFIRMED,
        )

        authenticated_client(resident_user).get(reverse(self.URL))

        booking.refresh_from_db()
        assert booking.status == BookingStatus.CONFIRMED

    def test_upcoming_filter_excludes_finished_work(
        self, authenticated_client, resident, resident_user,
        available_worker, cleaning_category, booking_date,
    ):
        make_booking(
            resident, available_worker, cleaning_category, booking_date,
            status=BookingStatus.CANCELLED,
        )

        response = authenticated_client(resident_user).get(
            reverse(self.URL), {"upcoming": "true"}
        )
        assert response.data["count"] == 0
