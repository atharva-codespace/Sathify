"""
Module 4 — Discovery & Hiring: API tests.

Grouped by sub-module. The security-shaped cases (society isolation, role
enforcement, the primary-resident rule) are given as much weight as the happy
paths, because those are the ones a refactor is most likely to quietly break —
a broken hire flow is obvious in the app, a broken tenancy boundary is not.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import Role
from apps.core.pricing import MAID_MONTHLY_RATE_INR
from apps.hiring.models import (
    Engagement,
    EngagementStatus,
    HireRequest,
    HireRequestStatus,
)
from apps.hiring.services import searchable_workers
from apps.societies.models import Flat, Resident, Society, SocietyStatus, Tower
from apps.workers.models import ServiceType, WorkerProfile

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def maid_service():
    return ServiceType.objects.create(name="Maid", slug="maid")


@pytest.fixture
def cook_service():
    return ServiceType.objects.create(name="Cook", slug="cook")


@pytest.fixture
def flat(society):
    tower = Tower.objects.create(society=society, name="A", floors=10)
    return Flat.objects.create(tower=tower, number="301", floor=3)


@pytest.fixture
def resident(resident_user, flat):
    """The primary account holder for their flat — the only one who may hire."""
    return Resident.objects.create(user=resident_user, flat=flat, is_primary=True)


def make_worker(user, service, **kwargs):
    """A searchable worker profile.

    ``photo`` is set to a bare path rather than a real upload: search requires a
    non-empty photo (it is the reference image for gate face verification), but
    these tests are about hiring, not image handling.
    """
    kwargs.setdefault("photo", "workers/photos/test.jpg")
    kwargs.setdefault("expected_monthly_rate", 4000)
    profile = WorkerProfile.objects.create(user=user, **kwargs)
    profile.service_types.add(service)
    return profile


@pytest.fixture
def worker(worker_user, maid_service):
    return make_worker(worker_user, maid_service, trust_score=70, average_rating=4.5)


def make_request(resident, worker, service, **kwargs):
    kwargs.setdefault("days_of_week", [0, 1, 2, 3, 4])
    kwargs.setdefault("start_time", dt.time(9, 0))
    kwargs.setdefault("monthly_rate", 4000)
    return HireRequest.objects.create(
        society=resident.flat.tower.society,
        resident=resident,
        worker=worker,
        service_type=service,
        **kwargs,
    )


# ---------------------------------------------------------------------------
# 4.1 Search & filters
# ---------------------------------------------------------------------------


class TestWorkerSearch:
    URL = "v1:hiring:worker-search"

    def test_resident_sees_searchable_workers(
        self, authenticated_client, resident, resident_user, worker
    ):
        response = authenticated_client(resident_user).get(reverse(self.URL))
        assert response.status_code == 200
        assert [w["id"] for w in response.data["results"]] == [worker.pk]

    def test_results_carry_a_match_percentage(
        self, authenticated_client, resident, resident_user, worker
    ):
        response = authenticated_client(resident_user).get(reverse(self.URL))
        match = response.data["results"][0]["match_percentage"]
        assert isinstance(match, int) and 0 <= match <= 100

    def test_unapproved_worker_is_hidden(
        self, authenticated_client, resident, resident_user, worker
    ):
        worker.user.is_approved = False
        worker.user.save(update_fields=["is_approved"])

        response = authenticated_client(resident_user).get(reverse(self.URL))
        assert response.data["results"] == []

    def test_worker_who_marked_themselves_unavailable_is_hidden(
        self, authenticated_client, resident, resident_user, worker
    ):
        worker.is_available = False
        worker.save(update_fields=["is_available"])

        response = authenticated_client(resident_user).get(reverse(self.URL))
        assert response.data["results"] == []

    def test_worker_without_a_photo_is_hidden(
        self, authenticated_client, resident, resident_user, worker
    ):
        """No photo means no gate face verification, so hiring them is a dead end."""
        worker.photo = ""
        worker.save(update_fields=["photo"])

        response = authenticated_client(resident_user).get(reverse(self.URL))
        assert response.data["results"] == []

    def test_workers_from_another_society_are_hidden(
        self, authenticated_client, resident, resident_user, maid_service, django_user_model
    ):
        other_society = Society.objects.create(
            name="Blue Ridge",
            address_line="Kalyani Nagar",
            city="Pune",
            state="Maharashtra",
            pincode="411006",
            status=SocietyStatus.ACTIVE,
        )
        outsider = django_user_model.objects.create_user(
            phone_number="9800000099",
            password="test-pass-12345",
            role=Role.WORKER,
            society=other_society,
            is_approved=True,
        )
        make_worker(outsider, maid_service)

        response = authenticated_client(resident_user).get(reverse(self.URL))
        assert response.data["results"] == []

    def test_filters_by_service_slug(
        self, authenticated_client, resident, resident_user, worker, cook_service
    ):
        response = authenticated_client(resident_user).get(
            reverse(self.URL), {"service": cook_service.slug}
        )
        assert response.data["results"] == []

    def test_max_rate_keeps_workers_who_have_not_stated_one(
        self, authenticated_client, resident, resident_user, maid_service, django_user_model
    ):
        """An unstated rate is negotiable, not automatically over budget."""
        user = django_user_model.objects.create_user(
            phone_number="9800000098",
            password="test-pass-12345",
            role=Role.WORKER,
            society=resident.flat.tower.society,
            is_approved=True,
        )
        make_worker(user, maid_service, expected_monthly_rate=None)

        response = authenticated_client(resident_user).get(
            reverse(self.URL), {"max_rate": 3000}
        )
        assert len(response.data["results"]) == 1

    def test_min_rating_filters(
        self, authenticated_client, resident, resident_user, worker
    ):
        response = authenticated_client(resident_user).get(
            reverse(self.URL), {"min_rating": 4.9}
        )
        assert response.data["results"] == []

    def test_name_search(self, authenticated_client, resident, resident_user, worker):
        hit = authenticated_client(resident_user).get(reverse(self.URL), {"q": "Rahul"})
        miss = authenticated_client(resident_user).get(reverse(self.URL), {"q": "Zephyr"})
        assert len(hit.data["results"]) == 1
        assert miss.data["results"] == []

    def test_strict_availability_drops_partial_cover(
        self, authenticated_client, resident, resident_user, worker
    ):
        worker.available_from = dt.time(14, 0)
        worker.available_until = dt.time(18, 0)
        worker.save(update_fields=["available_from", "available_until"])

        response = authenticated_client(resident_user).get(
            reverse(self.URL),
            {
                "available_from": "08:00",
                "available_until": "12:00",
                "strict_availability": "true",
            },
        )
        assert response.data["results"] == []

    def test_availability_mismatch_only_lowers_the_score_by_default(
        self, authenticated_client, resident, resident_user, worker
    ):
        """Without strict_availability the worker is ranked down, not removed."""
        worker.available_from = dt.time(14, 0)
        worker.available_until = dt.time(18, 0)
        worker.save(update_fields=["available_from", "available_until"])

        params = {"available_from": "08:00", "available_until": "12:00"}
        client = authenticated_client(resident_user)
        scored = client.get(reverse(self.URL), params).data["results"][0]
        unscored = client.get(reverse(self.URL)).data["results"][0]

        assert scored["match_percentage"] < unscored["match_percentage"]

    def test_sql_sort_path_still_attaches_scores(
        self, authenticated_client, resident, resident_user, worker
    ):
        response = authenticated_client(resident_user).get(
            reverse(self.URL), {"sort": "rating"}
        )
        assert response.status_code == 200
        assert response.data["results"][0]["match_percentage"] is not None

    def test_worker_offering_two_matching_services_appears_once(
        self, authenticated_client, resident, resident_user, worker, cook_service
    ):
        """Filtering on a many-to-many multiplies rows without a distinct()."""
        worker.service_types.add(cook_service)

        response = authenticated_client(resident_user).get(reverse(self.URL))
        assert len(response.data["results"]) == 1

    def test_scores_are_json_numbers_not_strings(
        self, authenticated_client, resident, resident_user, worker
    ):
        """Pins the wire type the Flutter client parses.

        These are model DecimalFields, and DRF renders those as strings unless
        told otherwise. When they went out as "70.00" the Dart numeric parse
        silently produced null, and every worker rendered as unrated with no
        error anywhere. Assert the type, not just the value.
        """
        response = authenticated_client(resident_user).get(reverse(self.URL))
        row = response.json()["results"][0]

        assert isinstance(row["trust_score"], (int, float)), row["trust_score"]
        assert isinstance(row["average_rating"], (int, float)), row["average_rating"]
        assert row["trust_score"] == pytest.approx(70)
        assert row["average_rating"] == pytest.approx(4.5)

    def test_worker_role_cannot_search(self, authenticated_client, worker_user, worker):
        response = authenticated_client(worker_user).get(reverse(self.URL))
        assert response.status_code == 403

    def test_unapproved_resident_cannot_search(
        self, authenticated_client, resident, resident_user, worker
    ):
        resident_user.is_approved = False
        resident_user.save(update_fields=["is_approved"])

        response = authenticated_client(resident_user).get(reverse(self.URL))
        assert response.status_code == 403

    def test_anonymous_cannot_search(self, api_client):
        assert api_client.get(reverse(self.URL)).status_code == 401


class TestSearchableConsistency:
    """``searchable_workers()`` and ``WorkerProfile.is_searchable`` must agree.

    One is the readable statement of the rule and the other its executable form.
    They are asserted together because a divergence would either leak
    unverified workers into search or silently hide legitimate ones.
    """

    def test_queryset_matches_the_property(
        self, society, maid_service, django_user_model
    ):
        profiles = []
        for index, (approved, available, photo) in enumerate(
            [
                (True, True, "p.jpg"),
                (False, True, "p.jpg"),
                (True, False, "p.jpg"),
                (True, True, ""),
                (False, False, ""),
            ]
        ):
            user = django_user_model.objects.create_user(
                phone_number=f"98111000{index:02d}",
                password="test-pass-12345",
                role=Role.WORKER,
                society=society,
                is_approved=approved,
            )
            profile = WorkerProfile.objects.create(
                user=user, is_available=available, photo=photo
            )
            profile.service_types.add(maid_service)
            profiles.append(profile)

        from_sql = set(searchable_workers(society.pk).values_list("pk", flat=True))
        from_python = {p.pk for p in profiles if p.is_searchable}
        assert from_sql == from_python


# ---------------------------------------------------------------------------
# 4.2 Worker profile
# ---------------------------------------------------------------------------


class TestWorkerDetail:
    URL = "v1:hiring:worker-detail"

    def test_returns_the_signal_a_resident_needs(
        self, authenticated_client, resident, resident_user, worker
    ):
        response = authenticated_client(resident_user).get(
            reverse(self.URL, args=[worker.pk])
        )
        assert response.status_code == 200
        assert response.data["verification"]["is_approved"] is True
        assert response.data["engagement_count"] == 0
        assert response.data["match_breakdown"] is not None

    def test_breakdown_explains_the_headline_number(
        self, authenticated_client, resident, resident_user, worker
    ):
        response = authenticated_client(resident_user).get(
            reverse(self.URL, args=[worker.pk])
        )
        total = sum(row["contribution"] for row in response.data["match_breakdown"])
        assert round(total * 100) == response.data["match_percentage"]

    def test_scores_are_json_numbers_not_strings(
        self, authenticated_client, resident, resident_user, worker
    ):
        response = authenticated_client(resident_user).get(
            reverse(self.URL, args=[worker.pk])
        )
        body = response.json()

        assert isinstance(body["trust_score"], (int, float))
        assert isinstance(body["average_rating"], (int, float))

    def test_response_rate_is_none_without_history(
        self, authenticated_client, resident, resident_user, worker
    ):
        response = authenticated_client(resident_user).get(
            reverse(self.URL, args=[worker.pk])
        )
        assert response.data["response_rate"] is None

    def test_response_rate_reflects_answered_requests(
        self, authenticated_client, resident, resident_user, worker, maid_service
    ):
        make_request(resident, worker, maid_service, status=HireRequestStatus.ACCEPTED)
        make_request(resident, worker, maid_service, status=HireRequestStatus.EXPIRED)

        response = authenticated_client(resident_user).get(
            reverse(self.URL, args=[worker.pk])
        )
        assert response.data["response_rate"] == pytest.approx(0.5)

    def test_withdrawn_requests_do_not_count_against_the_worker(
        self, authenticated_client, resident, resident_user, worker, maid_service
    ):
        """The resident took it off the table; that is not a failure to answer."""
        make_request(resident, worker, maid_service, status=HireRequestStatus.ACCEPTED)
        make_request(resident, worker, maid_service, status=HireRequestStatus.WITHDRAWN)

        response = authenticated_client(resident_user).get(
            reverse(self.URL, args=[worker.pk])
        )
        assert response.data["response_rate"] == pytest.approx(1.0)

    def test_unsearchable_worker_is_not_reachable_by_id(
        self, authenticated_client, resident, resident_user, worker
    ):
        worker.is_available = False
        worker.save(update_fields=["is_available"])

        response = authenticated_client(resident_user).get(
            reverse(self.URL, args=[worker.pk])
        )
        assert response.status_code == 404


# ---------------------------------------------------------------------------
# 4.4 Hire requests
# ---------------------------------------------------------------------------


def hire_payload(worker, service, **overrides):
    payload = {
        "worker": worker.pk,
        "service_type": service.pk,
        "days_of_week": [0, 1, 2, 3, 4],
        "start_time": "09:00",
        "expected_duration_minutes": 90,
        "monthly_rate": 4500,
        "message": "Looking for weekday morning help.",
    }
    payload.update(overrides)
    return payload


class TestSendHireRequest:
    URL = "v1:hiring:request-list"

    def test_primary_resident_can_send(
        self, authenticated_client, resident, resident_user, worker, maid_service
    ):
        response = authenticated_client(resident_user).post(
            reverse(self.URL), hire_payload(worker, maid_service), format="json"
        )
        assert response.status_code == 201
        assert HireRequest.objects.count() == 1
        assert response.data["request"]["status"] == HireRequestStatus.PENDING

    def test_rate_comes_from_the_price_list_not_the_payload(
        self, authenticated_client, resident, resident_user, worker, maid_service
    ):
        """Sathify quotes one rate, so a figure in the payload carries no weight.

        Sent as 4500 here and stored as the platform rate. The field is
        read-only on the serializer, so this is ignored rather than rejected —
        an older build of the app that still posts a rate keeps working.
        """
        response = authenticated_client(resident_user).post(
            reverse(self.URL),
            hire_payload(worker, maid_service, monthly_rate=4500),
            format="json",
        )

        assert response.status_code == 201
        assert HireRequest.objects.get().monthly_rate == MAID_MONTHLY_RATE_INR

    def test_accepted_request_carries_its_rate_onto_the_engagement(
        self, authenticated_client, resident, resident_user, worker, worker_user,
        maid_service,
    ):
        """The engagement records what was agreed, not what the file says today.

        Copied from the request rather than re-read from the constant, so that
        changing the price list never re-prices an agreement already made.
        """
        authenticated_client(resident_user).post(
            reverse(self.URL), hire_payload(worker, maid_service), format="json"
        )
        request_row = HireRequest.objects.get()
        request_row.monthly_rate = 4000
        request_row.save(update_fields=["monthly_rate"])

        response = authenticated_client(worker_user).post(
            reverse("v1:hiring:request-respond", args=[request_row.pk]),
            {"accept": True},
            format="json",
        )

        assert response.status_code == 201
        assert Engagement.objects.get().monthly_rate == 4000

    def test_non_primary_resident_is_refused(
        self, authenticated_client, resident, resident_user, worker, maid_service
    ):
        """Module 2.4 — two people in one household must not issue conflicting hires."""
        resident.is_primary = False
        resident.save(update_fields=["is_primary"])

        response = authenticated_client(resident_user).post(
            reverse(self.URL), hire_payload(worker, maid_service), format="json"
        )
        assert response.status_code == 403

    def test_resident_without_a_flat_is_refused(
        self, authenticated_client, resident_user, worker, maid_service
    ):
        response = authenticated_client(resident_user).post(
            reverse(self.URL), hire_payload(worker, maid_service), format="json"
        )
        assert response.status_code == 403

    def test_service_the_worker_does_not_offer_is_rejected(
        self, authenticated_client, resident, resident_user, worker, cook_service
    ):
        response = authenticated_client(resident_user).post(
            reverse(self.URL), hire_payload(worker, cook_service), format="json"
        )
        assert response.status_code == 400
        assert "service_type" in response.data["error"]["details"]

    def test_worker_from_another_society_is_rejected(
        self,
        authenticated_client,
        resident,
        resident_user,
        maid_service,
        django_user_model,
    ):
        """The worker id comes from the client, so search-side scoping is not enough."""
        other = Society.objects.create(
            name="Blue Ridge",
            address_line="Kalyani Nagar",
            city="Pune",
            state="Maharashtra",
            pincode="411006",
            status=SocietyStatus.ACTIVE,
        )
        outsider = django_user_model.objects.create_user(
            phone_number="9800000097",
            password="test-pass-12345",
            role=Role.WORKER,
            society=other,
            is_approved=True,
        )
        outside_worker = make_worker(outsider, maid_service)

        response = authenticated_client(resident_user).post(
            reverse(self.URL), hire_payload(outside_worker, maid_service), format="json"
        )
        assert response.status_code == 400

    def test_unavailable_worker_is_rejected(
        self, authenticated_client, resident, resident_user, worker, maid_service
    ):
        worker.is_available = False
        worker.save(update_fields=["is_available"])

        response = authenticated_client(resident_user).post(
            reverse(self.URL), hire_payload(worker, maid_service), format="json"
        )
        assert response.status_code == 400

    def test_duplicate_pending_request_is_rejected(
        self, authenticated_client, resident, resident_user, worker, maid_service
    ):
        """The double-tap case the partial unique constraint also guards."""
        client = authenticated_client(resident_user)
        payload = hire_payload(worker, maid_service)

        assert client.post(reverse(self.URL), payload, format="json").status_code == 201
        second = client.post(reverse(self.URL), payload, format="json")
        assert second.status_code == 400

    def test_empty_schedule_is_rejected(
        self, authenticated_client, resident, resident_user, worker, maid_service
    ):
        response = authenticated_client(resident_user).post(
            reverse(self.URL),
            hire_payload(worker, maid_service, days_of_week=[]),
            format="json",
        )
        assert response.status_code == 400

    def test_out_of_range_weekday_is_rejected(
        self, authenticated_client, resident, resident_user, worker, maid_service
    ):
        response = authenticated_client(resident_user).post(
            reverse(self.URL),
            hire_payload(worker, maid_service, days_of_week=[0, 7]),
            format="json",
        )
        assert response.status_code == 400

    def test_duplicate_days_are_normalised(
        self, authenticated_client, resident, resident_user, worker, maid_service
    ):
        response = authenticated_client(resident_user).post(
            reverse(self.URL),
            hire_payload(worker, maid_service, days_of_week=[2, 0, 0]),
            format="json",
        )
        assert response.status_code == 400

    def test_worker_cannot_send_a_hire_request(
        self, authenticated_client, worker_user, worker, maid_service
    ):
        response = authenticated_client(worker_user).post(
            reverse(self.URL), hire_payload(worker, maid_service), format="json"
        )
        assert response.status_code == 403


class TestRespondToHireRequest:
    def url(self, pk):
        return reverse("v1:hiring:request-respond", args=[pk])

    def test_accepting_creates_an_engagement(
        self, authenticated_client, resident, worker_user, worker, maid_service
    ):
        request = make_request(resident, worker, maid_service)

        response = authenticated_client(worker_user).post(
            self.url(request.pk), {"accept": True}, format="json"
        )
        assert response.status_code == 201

        engagement = Engagement.objects.get()
        assert engagement.status == EngagementStatus.ACTIVE
        assert engagement.hire_request_id == request.pk
        request.refresh_from_db()
        assert request.status == HireRequestStatus.ACCEPTED
        assert request.responded_at is not None

    def test_agreed_terms_are_copied_verbatim(
        self, authenticated_client, resident, worker_user, worker, maid_service
    ):
        """The engagement must record what the worker actually said yes to."""
        request = make_request(
            resident,
            worker,
            maid_service,
            days_of_week=[1, 3],
            start_time=dt.time(7, 30),
            expected_duration_minutes=45,
            monthly_rate=5200,
        )

        authenticated_client(worker_user).post(
            self.url(request.pk), {"accept": True}, format="json"
        )

        engagement = Engagement.objects.get()
        assert engagement.days_of_week == [1, 3]
        assert engagement.start_time == dt.time(7, 30)
        assert engagement.expected_duration_minutes == 45
        assert engagement.monthly_rate == 5200

    def test_declining_creates_no_engagement(
        self, authenticated_client, resident, worker_user, worker, maid_service
    ):
        request = make_request(resident, worker, maid_service)

        response = authenticated_client(worker_user).post(
            self.url(request.pk), {"accept": False, "note": "Already full."}, format="json"
        )
        assert response.status_code == 200
        assert not Engagement.objects.exists()

        request.refresh_from_db()
        assert request.status == HireRequestStatus.DECLINED
        assert request.response_note == "Already full."

    def test_answering_twice_is_refused(
        self, authenticated_client, resident, worker_user, worker, maid_service
    ):
        request = make_request(resident, worker, maid_service)
        client = authenticated_client(worker_user)

        assert client.post(self.url(request.pk), {"accept": True}, format="json").status_code == 201
        second = client.post(self.url(request.pk), {"accept": True}, format="json")
        assert second.status_code == 409
        assert Engagement.objects.count() == 1

    def test_lapsed_request_cannot_be_accepted(
        self, authenticated_client, resident, worker_user, worker, maid_service
    ):
        request = make_request(
            resident,
            worker,
            maid_service,
            expires_at=timezone.now() - dt.timedelta(hours=1),
        )

        response = authenticated_client(worker_user).post(
            self.url(request.pk), {"accept": True}, format="json"
        )
        assert response.status_code == 409
        assert not Engagement.objects.exists()

    def test_another_worker_cannot_answer_someone_elses_request(
        self,
        authenticated_client,
        resident,
        worker,
        maid_service,
        society,
        django_user_model,
    ):
        request = make_request(resident, worker, maid_service)
        intruder = django_user_model.objects.create_user(
            phone_number="9800000096",
            password="test-pass-12345",
            role=Role.WORKER,
            society=society,
            is_approved=True,
        )
        make_worker(intruder, maid_service)

        response = authenticated_client(intruder).post(
            self.url(request.pk), {"accept": True}, format="json"
        )
        assert response.status_code == 404

    def test_resident_cannot_accept_on_the_workers_behalf(
        self, authenticated_client, resident, resident_user, worker, maid_service
    ):
        request = make_request(resident, worker, maid_service)

        response = authenticated_client(resident_user).post(
            self.url(request.pk), {"accept": True}, format="json"
        )
        assert response.status_code == 403


class TestWithdrawHireRequest:
    def url(self, pk):
        return reverse("v1:hiring:request-withdraw", args=[pk])

    def test_resident_can_withdraw_an_open_request(
        self, authenticated_client, resident, resident_user, worker, maid_service
    ):
        request = make_request(resident, worker, maid_service)

        response = authenticated_client(resident_user).post(
            self.url(request.pk), {"reason": "Sorted it ourselves."}, format="json"
        )
        assert response.status_code == 200

        request.refresh_from_db()
        assert request.status == HireRequestStatus.WITHDRAWN

    def test_answered_request_cannot_be_withdrawn(
        self, authenticated_client, resident, resident_user, worker, maid_service
    ):
        request = make_request(
            resident, worker, maid_service, status=HireRequestStatus.DECLINED
        )

        response = authenticated_client(resident_user).post(
            self.url(request.pk), {}, format="json"
        )
        assert response.status_code == 409


class TestListHireRequests:
    URL = "v1:hiring:request-list"

    def test_resident_sees_their_own(
        self, authenticated_client, resident, resident_user, worker, maid_service
    ):
        make_request(resident, worker, maid_service)

        response = authenticated_client(resident_user).get(reverse(self.URL))
        assert response.data["count"] == 1

    def test_worker_sees_requests_addressed_to_them(
        self, authenticated_client, resident, worker_user, worker, maid_service
    ):
        make_request(resident, worker, maid_service)

        response = authenticated_client(worker_user).get(reverse(self.URL))
        assert response.data["count"] == 1

    def test_another_resident_sees_nothing(
        self, authenticated_client, resident, worker, maid_service, society, django_user_model
    ):
        make_request(resident, worker, maid_service)
        other_tower = Tower.objects.create(society=society, name="B", floors=4)
        other_flat = Flat.objects.create(tower=other_tower, number="101", floor=1)
        other_user = django_user_model.objects.create_user(
            phone_number="9800000095",
            password="test-pass-12345",
            role=Role.RESIDENT,
            society=society,
            is_approved=True,
        )
        Resident.objects.create(user=other_user, flat=other_flat, is_primary=True)

        response = authenticated_client(other_user).get(reverse(self.URL))
        assert response.data["count"] == 0

    def test_lapsed_requests_are_swept_and_reported_as_expired(
        self, authenticated_client, resident, resident_user, worker, maid_service
    ):
        """Expiry is lazy, so listing must not show a lapsed request as pending."""
        request = make_request(
            resident,
            worker,
            maid_service,
            expires_at=timezone.now() - dt.timedelta(hours=1),
        )

        response = authenticated_client(resident_user).get(reverse(self.URL))
        assert response.data["results"][0]["status"] == HireRequestStatus.EXPIRED

        request.refresh_from_db()
        assert request.status == HireRequestStatus.EXPIRED

    def test_pending_filter_excludes_lapsed(
        self, authenticated_client, resident, resident_user, worker, maid_service
    ):
        make_request(
            resident,
            worker,
            maid_service,
            expires_at=timezone.now() - dt.timedelta(hours=1),
        )

        response = authenticated_client(resident_user).get(
            reverse(self.URL), {"status": "pending"}
        )
        assert response.data["count"] == 0


# ---------------------------------------------------------------------------
# 4.5 Engagement lifecycle
# ---------------------------------------------------------------------------


@pytest.fixture
def engagement(resident, worker, maid_service, society):
    return Engagement.objects.create(
        society=society,
        resident=resident,
        worker=worker,
        service_type=maid_service,
        days_of_week=[0, 2, 4],
        start_time=dt.time(9, 0),
        monthly_rate=4000,
    )


class TestEngagementLifecycle:
    def url(self, pk):
        return reverse("v1:hiring:engagement-transition", args=[pk])

    def test_resident_can_pause_and_resume(
        self, authenticated_client, resident_user, engagement
    ):
        client = authenticated_client(resident_user)

        paused = client.post(
            self.url(engagement.pk), {"action": "pause", "note": "Away for a month"},
            format="json",
        )
        assert paused.status_code == 200
        engagement.refresh_from_db()
        assert engagement.status == EngagementStatus.PAUSED
        assert engagement.paused_at is not None

        resumed = client.post(self.url(engagement.pk), {"action": "resume"}, format="json")
        assert resumed.status_code == 200
        engagement.refresh_from_db()
        assert engagement.status == EngagementStatus.ACTIVE

    def test_worker_can_terminate(
        self, authenticated_client, worker_user, engagement
    ):
        response = authenticated_client(worker_user).post(
            self.url(engagement.pk),
            {"action": "terminate", "reason": "worker_left_society"},
            format="json",
        )
        assert response.status_code == 200

        engagement.refresh_from_db()
        assert engagement.status == EngagementStatus.TERMINATED
        assert engagement.ended_at is not None
        assert engagement.ended_by == worker_user

    def test_terminating_requires_a_reason(
        self, authenticated_client, resident_user, engagement
    ):
        response = authenticated_client(resident_user).post(
            self.url(engagement.pk), {"action": "terminate"}, format="json"
        )
        assert response.status_code == 400

    def test_terminated_engagement_cannot_be_resumed(
        self, authenticated_client, resident_user, engagement
    ):
        engagement.terminate(reason="resident_ended")

        response = authenticated_client(resident_user).post(
            self.url(engagement.pk), {"action": "resume"}, format="json"
        )
        assert response.status_code == 409

    def test_pausing_twice_is_idempotent(
        self, authenticated_client, resident_user, engagement
    ):
        client = authenticated_client(resident_user)
        client.post(self.url(engagement.pk), {"action": "pause"}, format="json")
        first_paused_at = Engagement.objects.get(pk=engagement.pk).paused_at

        second = client.post(self.url(engagement.pk), {"action": "pause"}, format="json")
        assert second.status_code == 200
        assert Engagement.objects.get(pk=engagement.pk).paused_at == first_paused_at

    def test_non_primary_resident_cannot_change_an_engagement(
        self, authenticated_client, resident, resident_user, engagement
    ):
        resident.is_primary = False
        resident.save(update_fields=["is_primary"])

        response = authenticated_client(resident_user).post(
            self.url(engagement.pk), {"action": "pause"}, format="json"
        )
        assert response.status_code == 403

    def test_paused_engagement_expects_no_visits(self, engagement):
        monday = dt.date(2026, 8, 3)
        assert engagement.occurs_on(monday) is True

        engagement.pause()
        assert engagement.occurs_on(monday) is False


class TestEngagementVisibility:
    URL = "v1:hiring:engagement-list"

    def test_both_parties_see_it(
        self, authenticated_client, resident_user, worker_user, engagement
    ):
        assert authenticated_client(resident_user).get(reverse(self.URL)).data["count"] == 1
        assert authenticated_client(worker_user).get(reverse(self.URL)).data["count"] == 1

    def test_society_admin_sees_their_societys_engagements(
        self, authenticated_client, admin_user, engagement
    ):
        response = authenticated_client(admin_user).get(reverse(self.URL))
        assert response.data["count"] == 1

    def test_guard_sees_nothing(self, authenticated_client, guard_user, engagement):
        """A guard has no business reading hiring arrangements."""
        response = authenticated_client(guard_user).get(reverse(self.URL))
        assert response.status_code == 403

    def test_live_filter_excludes_terminated(
        self, authenticated_client, resident_user, engagement
    ):
        engagement.terminate(reason="resident_ended")

        response = authenticated_client(resident_user).get(reverse(self.URL), {"live": "true"})
        assert response.data["count"] == 0


class TestEngagementConstraints:
    def test_a_second_live_engagement_for_the_same_pair_is_refused(
        self, authenticated_client, resident, worker_user, worker, maid_service, engagement
    ):
        """The household already has this worker for this service."""
        request = make_request(resident, worker, maid_service)

        response = authenticated_client(worker_user).post(
            reverse("v1:hiring:request-respond", args=[request.pk]),
            {"accept": True},
            format="json",
        )
        assert response.status_code == 409
        assert Engagement.objects.count() == 1

    def test_rehiring_after_termination_is_allowed(
        self, authenticated_client, resident, worker_user, worker, maid_service, engagement
    ):
        """Ending a relationship must not blacklist the pair forever."""
        engagement.terminate(reason="resident_ended")
        request = make_request(resident, worker, maid_service)

        response = authenticated_client(worker_user).post(
            reverse("v1:hiring:request-respond", args=[request.pk]),
            {"accept": True},
            format="json",
        )
        assert response.status_code == 201
        assert Engagement.objects.count() == 2
