"""
Module 9 — Ratings, Reviews & Trust Score: API tests.

The group that matters most is ``TestTrustRecomputation``: it pins that a rating
actually moves the score Module 4 ranks on, and that the movement is logged with
its breakdown. A rating that quietly failed to count would leave the whole
accountability layer decorative.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import Role
from apps.bookings.models import Booking, BookingStatus, ServiceCategory
from apps.hiring.models import Engagement, EngagementEndReason
from apps.ratings.models import (
    FlagStatus,
    Rating,
    RatingDirection,
    ReviewFlag,
    ReviewSentiment,
    TrustScoreLog,
)
from apps.ratings.services import AlreadyRated, submit_rating
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
def unapproved_admin(db, django_user_model, society):
    """An administrator bound to a society but not yet verified.

    societies/serializers.SocietyRegistrationSerializer attaches the society the
    moment it is registered and leaves ``is_approved`` False, so this is a real
    state an account passes through rather than an invented one.
    """
    return django_user_model.objects.create_user(
        phone_number="9800000009",
        password="test-pass-12345",
        role=Role.SOCIETY_ADMIN,
        society=society,
        first_name="Unverified",
        last_name="Admin",
        is_approved=False,
    )


@pytest.fixture
def finished_engagement(society, resident, worker, maid_service):
    """A terminated engagement — the only kind that can be rated."""
    engagement = Engagement.objects.create(
        society=society,
        resident=resident,
        worker=worker,
        service_type=maid_service,
        days_of_week=[0, 2, 4],
        start_time=dt.time(9, 0),
        monthly_rate=4000,
    )
    engagement.terminate(reason=EngagementEndReason.RESIDENT_ENDED)
    return engagement


@pytest.fixture
def finished_booking(society, resident, worker):
    return Booking.objects.create(
        society=society,
        resident=resident,
        worker=worker,
        category=ServiceCategory.objects.get(slug="deep-cleaning"),
        scheduled_date=timezone.localdate() - dt.timedelta(days=1),
        start_time=dt.time(10, 0),
        quoted_price=2000,
        status=BookingStatus.COMPLETED,
    )


RATING_URL = "v1:ratings:rating-list"


# ---------------------------------------------------------------------------
# 9.1 Submitting
# ---------------------------------------------------------------------------


class TestSubmitRating:
    def test_a_duplicate_that_beats_the_check_is_a_conflict_not_a_crash(
        self, monkeypatch, resident_user, finished_booking
    ):
        """Two taps that both pass the "already rated?" check.

        That race is the reason the uniqueness rule is a database constraint as
        well as a query. Before this, the loser of the race raised an
        IntegrityError, which no part of the DRF exception handler recognises —
        so a double tap on a slow connection reached the user as a 500 rather
        than as the "you have already rated this" the winner's check would have
        given them.
        """
        submit_rating(
            rater=resident_user,
            direction=RatingDirection.RESIDENT_TO_WORKER,
            stars=5,
            booking=finished_booking,
        )
        monkeypatch.setattr(
            "apps.ratings.services._already_rated", lambda **kwargs: False
        )

        with pytest.raises(AlreadyRated):
            submit_rating(
                rater=resident_user,
                direction=RatingDirection.RESIDENT_TO_WORKER,
                stars=1,
                booking=finished_booking,
            )

        assert Rating.objects.filter(booking=finished_booking).count() == 1

    def test_a_resident_rates_a_finished_engagement(
        self, authenticated_client, resident_user, finished_engagement
    ):
        response = authenticated_client(resident_user).post(
            reverse(RATING_URL),
            {"engagement": finished_engagement.pk, "stars": 5, "review": "Very good work."},
            format="json",
        )

        assert response.status_code == 201
        rating = Rating.objects.get()
        assert rating.direction == RatingDirection.RESIDENT_TO_WORKER
        assert rating.stars == 5

    def test_a_worker_rates_the_resident_back(
        self, authenticated_client, worker_user, finished_engagement
    ):
        """SRS 3.9 rates both sides, to encourage respectful treatment."""
        response = authenticated_client(worker_user).post(
            reverse(RATING_URL),
            {"engagement": finished_engagement.pk, "stars": 4, "review": "Paid on time."},
            format="json",
        )

        assert response.status_code == 201
        assert Rating.objects.get().direction == RatingDirection.WORKER_TO_RESIDENT

    def test_both_sides_can_rate_the_same_job(
        self, authenticated_client, resident_user, worker_user, finished_engagement
    ):
        payload = {"engagement": finished_engagement.pk, "stars": 5}
        authenticated_client(resident_user).post(reverse(RATING_URL), payload, format="json")
        authenticated_client(worker_user).post(reverse(RATING_URL), payload, format="json")

        assert Rating.objects.count() == 2

    def test_rating_the_same_job_twice_is_refused(
        self, authenticated_client, resident_user, finished_engagement
    ):
        """Modspec 9.1 caps it at one per job per direction, to stop review spam."""
        client = authenticated_client(resident_user)
        payload = {"engagement": finished_engagement.pk, "stars": 5}

        assert client.post(reverse(RATING_URL), payload, format="json").status_code == 201
        second = client.post(reverse(RATING_URL), payload, format="json")

        assert second.status_code == 404
        assert Rating.objects.count() == 1

    def test_a_running_engagement_cannot_be_rated(
        self, authenticated_client, resident_user, society, resident, worker, maid_service
    ):
        """Rating one still running would rate an opinion, not an outcome."""
        running = Engagement.objects.create(
            society=society, resident=resident, worker=worker,
            service_type=maid_service, days_of_week=[0],
            start_time=dt.time(9, 0), monthly_rate=4000,
        )

        response = authenticated_client(resident_user).post(
            reverse(RATING_URL), {"engagement": running.pk, "stars": 5}, format="json"
        )
        assert response.status_code == 404

    def test_an_incomplete_booking_cannot_be_rated(
        self, authenticated_client, resident_user, society, resident, worker
    ):
        pending = Booking.objects.create(
            society=society, resident=resident, worker=worker,
            category=ServiceCategory.objects.get(slug="deep-cleaning"),
            scheduled_date=timezone.localdate() + dt.timedelta(days=2),
            start_time=dt.time(10, 0), quoted_price=2000,
        )

        response = authenticated_client(resident_user).post(
            reverse(RATING_URL), {"booking": pending.pk, "stars": 5}, format="json"
        )
        assert response.status_code == 404

    def test_a_completed_booking_can_be_rated(
        self, authenticated_client, resident_user, finished_booking
    ):
        response = authenticated_client(resident_user).post(
            reverse(RATING_URL), {"booking": finished_booking.pk, "stars": 4}, format="json"
        )
        assert response.status_code == 201

    def test_rating_someone_elses_job_is_refused(
        self, authenticated_client, finished_engagement, society, django_user_model
    ):
        tower = Tower.objects.create(society=society, name="B", floors=2)
        other_flat = Flat.objects.create(tower=tower, number="101", floor=1)
        other = django_user_model.objects.create_user(
            phone_number="9800000041", password="test-pass-12345",
            role=Role.RESIDENT, society=society, is_approved=True,
        )
        Resident.objects.create(user=other, flat=other_flat, is_primary=True)

        response = authenticated_client(other).post(
            reverse(RATING_URL),
            {"engagement": finished_engagement.pk, "stars": 1},
            format="json",
        )
        assert response.status_code == 404

    def test_stars_outside_the_range_are_rejected(
        self, authenticated_client, resident_user, finished_engagement
    ):
        for stars in (0, 6):
            response = authenticated_client(resident_user).post(
                reverse(RATING_URL),
                {"engagement": finished_engagement.pk, "stars": stars},
                format="json",
            )
            assert response.status_code == 400

    def test_rating_neither_a_job_nor_the_other_is_rejected(
        self, authenticated_client, resident_user
    ):
        response = authenticated_client(resident_user).post(
            reverse(RATING_URL), {"stars": 5}, format="json"
        )
        assert response.status_code == 400

    def test_rating_both_at_once_is_rejected(
        self, authenticated_client, resident_user, finished_engagement, finished_booking
    ):
        response = authenticated_client(resident_user).post(
            reverse(RATING_URL),
            {
                "engagement": finished_engagement.pk,
                "booking": finished_booking.pk,
                "stars": 5,
            },
            format="json",
        )
        assert response.status_code == 400


class TestPendingRatings:
    URL = "v1:ratings:pending"

    def test_a_finished_job_appears(
        self, authenticated_client, resident_user, finished_engagement
    ):
        response = authenticated_client(resident_user).get(reverse(self.URL))

        assert response.data["count"] == 1
        assert response.data["results"][0]["kind"] == "engagement"

    def test_a_rated_job_disappears(
        self, authenticated_client, resident_user, finished_engagement
    ):
        client = authenticated_client(resident_user)
        client.post(
            reverse(RATING_URL),
            {"engagement": finished_engagement.pk, "stars": 5},
            format="json",
        )

        assert client.get(reverse(self.URL)).data["count"] == 0

    def test_the_worker_still_sees_it_after_the_resident_rates(
        self, authenticated_client, resident_user, worker_user, finished_engagement
    ):
        """The two directions are independent."""
        authenticated_client(resident_user).post(
            reverse(RATING_URL),
            {"engagement": finished_engagement.pk, "stars": 5},
            format="json",
        )

        assert authenticated_client(worker_user).get(reverse(self.URL)).data["count"] == 1


# ---------------------------------------------------------------------------
# 9.2 Sentiment
# ---------------------------------------------------------------------------


class TestSentimentStorage:
    def test_a_review_is_analysed_and_stored_separately(
        self, authenticated_client, resident_user, finished_engagement
    ):
        """Modspec 9.2 — the text is what a person wrote; this is a model's
        opinion about it, and the two are kept apart."""
        authenticated_client(resident_user).post(
            reverse(RATING_URL),
            {
                "engagement": finished_engagement.pk,
                "stars": 5,
                "review": "Very good work, always punctual",
            },
            format="json",
        )

        rating = Rating.objects.get()
        assert rating.review == "Very good work, always punctual"
        assert ReviewSentiment.objects.filter(rating=rating).exists()
        assert rating.sentiment.label == "positive"

    def test_a_stars_only_rating_gets_no_sentiment_row(
        self, authenticated_client, resident_user, finished_engagement
    ):
        """An empty row would look like a model that found nothing, rather than
        a review that said nothing."""
        authenticated_client(resident_user).post(
            reverse(RATING_URL),
            {"engagement": finished_engagement.pk, "stars": 5},
            format="json",
        )

        assert not ReviewSentiment.objects.exists()

    def test_themes_are_extracted(
        self, authenticated_client, resident_user, finished_engagement
    ):
        authenticated_client(resident_user).post(
            reverse(RATING_URL),
            {
                "engagement": finished_engagement.pk,
                "stars": 2,
                "review": "always late and the kitchen was dirty",
            },
            format="json",
        )

        themes = Rating.objects.get().sentiment.themes
        assert "punctuality" in themes
        assert "hygiene" in themes


# ---------------------------------------------------------------------------
# 9.3 Trust recomputation
# ---------------------------------------------------------------------------


class TestTrustRecomputation:
    def test_a_rating_moves_the_workers_trust_score(
        self, authenticated_client, resident_user, worker, finished_engagement
    ):
        """The score Module 4 ranks on. A rating that did not move it would
        leave the whole accountability layer decorative."""
        before = worker.trust_score

        authenticated_client(resident_user).post(
            reverse(RATING_URL),
            {"engagement": finished_engagement.pk, "stars": 5},
            format="json",
        )

        worker.refresh_from_db()
        assert worker.trust_score != before
        assert worker.trust_score > 0

    def test_the_average_and_count_move_together(
        self, authenticated_client, resident_user, worker, finished_engagement,
        finished_booking,
    ):
        """Module 4.3 shrinks a sparse average toward the prior using the count;
        an average without its count would let one review look like fifty."""
        client = authenticated_client(resident_user)
        client.post(
            reverse(RATING_URL),
            {"engagement": finished_engagement.pk, "stars": 5},
            format="json",
        )
        client.post(
            reverse(RATING_URL),
            {"booking": finished_booking.pk, "stars": 3},
            format="json",
        )

        worker.refresh_from_db()
        assert worker.rating_count == 2
        assert float(worker.average_rating) == pytest.approx(4.0)

    def test_module_4_reads_the_real_count(
        self, authenticated_client, resident_user, worker, finished_engagement
    ):
        """It used to stand in as completed_engagements."""
        from apps.hiring.services import build_scoring_inputs

        authenticated_client(resident_user).post(
            reverse(RATING_URL),
            {"engagement": finished_engagement.pk, "stars": 5},
            format="json",
        )

        worker.refresh_from_db()
        assert build_scoring_inputs(worker).rating_count == 1

    def test_rating_a_resident_moves_their_score(
        self, authenticated_client, worker_user, resident, finished_engagement
    ):
        before = resident.trust_score

        authenticated_client(worker_user).post(
            reverse(RATING_URL),
            {"engagement": finished_engagement.pk, "stars": 5},
            format="json",
        )

        resident.refresh_from_db()
        assert resident.trust_score != before

    def test_every_change_is_logged_with_its_breakdown(
        self, authenticated_client, resident_user, worker, finished_engagement
    ):
        """This is what answers a disputed score months later."""
        authenticated_client(resident_user).post(
            reverse(RATING_URL),
            {"engagement": finished_engagement.pk, "stars": 5},
            format="json",
        )

        log = TrustScoreLog.objects.get(worker=worker)
        assert log.new_score > log.previous_score
        assert log.components
        assert all("detail" in row for row in log.components)
        assert log.trigger == "rating submitted"

    def test_the_logged_breakdown_is_frozen_not_recomputed(
        self, authenticated_client, resident_user, worker, finished_engagement,
        finished_booking,
    ):
        """Recomputing an old score today gives today's answer, not the one
        that was acted on."""
        client = authenticated_client(resident_user)
        client.post(
            reverse(RATING_URL),
            {"engagement": finished_engagement.pk, "stars": 5},
            format="json",
        )
        first_log = TrustScoreLog.objects.get(worker=worker)
        frozen = first_log.components

        client.post(
            reverse(RATING_URL),
            {"booking": finished_booking.pk, "stars": 1},
            format="json",
        )

        first_log.refresh_from_db()
        assert first_log.components == frozen

    def test_an_unchanged_score_is_not_logged(self, worker):
        """A nightly sweep should not bury real changes under no-ops."""
        from apps.ratings.services import recompute_worker_trust

        recompute_worker_trust(worker, trigger="first")
        count_after_first = TrustScoreLog.objects.count()
        recompute_worker_trust(worker, trigger="second")

        assert TrustScoreLog.objects.count() == count_after_first


class TestTrustEndpoints:
    def test_a_worker_reads_their_own_score_with_reasons(
        self, authenticated_client, worker_user, worker
    ):
        response = authenticated_client(worker_user).get(
            reverse("v1:ratings:my-trust")
        )

        assert response.status_code == 200
        assert response.data["score"] > 0
        assert len(response.data["components"]) == 4
        assert response.data["weakest"] is not None

    def test_the_score_never_arrives_without_its_breakdown(
        self, authenticated_client, worker_user, worker
    ):
        """Explainability is the modspec's key requirement, so there is
        deliberately no endpoint returning the bare number."""
        response = authenticated_client(worker_user).get(
            reverse("v1:ratings:my-trust")
        )
        assert "components" in response.data
        assert all(row["detail"] for row in response.data["components"])

    def test_a_resident_reads_a_workers_score(
        self, authenticated_client, resident_user, worker
    ):
        response = authenticated_client(resident_user).get(
            reverse("v1:ratings:worker-trust", args=[worker.pk])
        )

        assert response.status_code == 200
        assert response.data["subject_type"] == "worker"

    def test_another_societys_worker_is_not_readable(
        self, authenticated_client, resident_user, django_user_model
    ):
        from apps.societies.models import Society, SocietyStatus

        other = Society.objects.create(
            name="Blue Ridge", address_line="X", city="Pune",
            state="Maharashtra", pincode="411006", status=SocietyStatus.ACTIVE,
        )
        outsider = django_user_model.objects.create_user(
            phone_number="9800000042", password="test-pass-12345",
            role=Role.WORKER, society=other, is_approved=True,
        )
        outside_worker = WorkerProfile.objects.create(user=outsider, photo="p.jpg")

        response = authenticated_client(resident_user).get(
            reverse("v1:ratings:worker-trust", args=[outside_worker.pk])
        )
        assert response.status_code == 404

    def test_a_worker_reads_their_own_history(
        self, authenticated_client, resident_user, worker_user, worker,
        finished_engagement,
    ):
        authenticated_client(resident_user).post(
            reverse(RATING_URL),
            {"engagement": finished_engagement.pk, "stars": 5},
            format="json",
        )

        response = authenticated_client(worker_user).get(
            reverse("v1:ratings:trust-history")
        )
        assert response.data["count"] == 1


# ---------------------------------------------------------------------------
# 9.4 Fake-review detection
# ---------------------------------------------------------------------------


class TestReviewFlagging:
    def _many_finished_bookings(self, society, resident, worker, count):
        return [
            Booking.objects.create(
                society=society, resident=resident, worker=worker,
                category=ServiceCategory.objects.get(slug="deep-cleaning"),
                scheduled_date=timezone.localdate() - dt.timedelta(days=index + 1),
                start_time=dt.time(10, 0), quoted_price=2000,
                status=BookingStatus.COMPLETED,
            )
            for index in range(count)
        ]

    def test_a_burst_of_ratings_is_flagged_and_withheld(
        self, authenticated_client, resident_user, society, resident, worker
    ):
        bookings = self._many_finished_bookings(society, resident, worker, 6)
        client = authenticated_client(resident_user)

        for booking in bookings:
            client.post(
                reverse(RATING_URL),
                {"booking": booking.pk, "stars": 5},
                format="json",
            )

        assert ReviewFlag.objects.exists()
        assert Rating.objects.filter(is_withheld=True).exists()

    def test_a_flagged_rating_is_not_deleted(
        self, authenticated_client, resident_user, society, resident, worker
    ):
        """Modspec 9.4 escalates rather than auto-deleting — every heuristic
        here has an innocent explanation."""
        bookings = self._many_finished_bookings(society, resident, worker, 6)
        client = authenticated_client(resident_user)

        for booking in bookings:
            client.post(
                reverse(RATING_URL), {"booking": booking.pk, "stars": 5}, format="json"
            )

        assert Rating.objects.count() == 6

    def test_a_withheld_rating_does_not_count_toward_the_score(
        self, authenticated_client, resident_user, society, resident, worker
    ):
        bookings = self._many_finished_bookings(society, resident, worker, 6)
        client = authenticated_client(resident_user)
        for booking in bookings:
            client.post(
                reverse(RATING_URL), {"booking": booking.pk, "stars": 5}, format="json"
            )

        worker.refresh_from_db()
        withheld = Rating.objects.filter(is_withheld=True).count()
        assert worker.rating_count == 6 - withheld

    def test_a_normal_rating_is_not_flagged(
        self, authenticated_client, resident_user, finished_engagement
    ):
        authenticated_client(resident_user).post(
            reverse(RATING_URL),
            {
                "engagement": finished_engagement.pk,
                "stars": 4,
                "review": "She was thorough with the kitchen today.",
            },
            format="json",
        )

        assert not ReviewFlag.objects.exists()
        assert not Rating.objects.filter(is_withheld=True).exists()

    def test_an_admin_dismissing_a_flag_restores_the_rating(
        self, authenticated_client, admin_user, resident_user, society, resident, worker
    ):
        bookings = self._many_finished_bookings(society, resident, worker, 6)
        client = authenticated_client(resident_user)
        for booking in bookings:
            client.post(
                reverse(RATING_URL), {"booking": booking.pk, "stars": 5}, format="json"
            )

        flag = ReviewFlag.objects.filter(status=FlagStatus.OPEN).first()
        response = authenticated_client(admin_user).post(
            reverse("v1:ratings:flag-resolve", args=[flag.pk]),
            {"upheld": False, "note": "Resident was catching up on a month of bookings."},
            format="json",
        )

        assert response.status_code == 200
        flag.rating.refresh_from_db()
        assert flag.rating.is_withheld is False

    def test_dismissing_a_flag_recomputes_the_score(
        self, authenticated_client, admin_user, resident_user, society, resident, worker
    ):
        """Clearing a false positive but leaving the penalty would be the worst
        of both outcomes."""
        bookings = self._many_finished_bookings(society, resident, worker, 6)
        client = authenticated_client(resident_user)
        for booking in bookings:
            client.post(
                reverse(RATING_URL), {"booking": booking.pk, "stars": 5}, format="json"
            )

        worker.refresh_from_db()
        before = worker.rating_count

        for flag in ReviewFlag.objects.filter(status=FlagStatus.OPEN):
            authenticated_client(admin_user).post(
                reverse("v1:ratings:flag-resolve", args=[flag.pk]),
                {"upheld": False, "note": "Genuine."},
                format="json",
            )

        worker.refresh_from_db()
        assert worker.rating_count > before

    def test_upholding_keeps_the_rating_withheld(
        self, authenticated_client, admin_user, resident_user, society, resident, worker
    ):
        bookings = self._many_finished_bookings(society, resident, worker, 6)
        client = authenticated_client(resident_user)
        for booking in bookings:
            client.post(
                reverse(RATING_URL), {"booking": booking.pk, "stars": 5}, format="json"
            )

        flag = ReviewFlag.objects.filter(status=FlagStatus.OPEN).first()
        authenticated_client(admin_user).post(
            reverse("v1:ratings:flag-resolve", args=[flag.pk]),
            {"upheld": True, "note": "Confirmed manufactured."},
            format="json",
        )

        flag.rating.refresh_from_db()
        assert flag.rating.is_withheld is True

    def test_resolving_a_flag_twice_is_refused(
        self, authenticated_client, admin_user, resident_user, society, resident, worker
    ):
        bookings = self._many_finished_bookings(society, resident, worker, 6)
        client = authenticated_client(resident_user)
        for booking in bookings:
            client.post(
                reverse(RATING_URL), {"booking": booking.pk, "stars": 5}, format="json"
            )

        flag = ReviewFlag.objects.filter(status=FlagStatus.OPEN).first()
        url = reverse("v1:ratings:flag-resolve", args=[flag.pk])
        payload = {"upheld": False, "note": "Genuine."}
        admin_client = authenticated_client(admin_user)

        assert admin_client.post(url, payload, format="json").status_code == 200
        assert admin_client.post(url, payload, format="json").status_code == 409

    def test_a_resident_cannot_see_the_flag_queue(
        self, authenticated_client, resident_user
    ):
        response = authenticated_client(resident_user).get(
            reverse("v1:ratings:flag-list")
        )
        assert response.status_code == 403

    def test_an_unapproved_administrator_cannot_see_the_flag_queue(
        self, authenticated_client, unapproved_admin
    ):
        """The role alone is not enough — the account has to be verified.

        This state is reachable, not hypothetical: registering a society binds
        the administrator to it immediately and leaves the account unapproved
        while the society sits PENDING. The queue holds every flagged review in
        the society, in full, with both parties named.
        """
        response = authenticated_client(unapproved_admin).get(
            reverse("v1:ratings:flag-list")
        )
        assert response.status_code == 403

    def test_an_unapproved_administrator_cannot_resolve_a_flag(
        self, authenticated_client, unapproved_admin, resident_user,
        society, resident, worker,
    ):
        """Sharper than reading it: this decides whether a rating counts at all,
        and moves the trust score that decides whether somebody gets hired."""
        bookings = self._many_finished_bookings(society, resident, worker, 6)
        client = authenticated_client(resident_user)
        for booking in bookings:
            client.post(
                reverse(RATING_URL), {"booking": booking.pk, "stars": 5}, format="json"
            )

        flag = ReviewFlag.objects.filter(status=FlagStatus.OPEN).first()
        response = authenticated_client(unapproved_admin).post(
            reverse("v1:ratings:flag-resolve", args=[flag.pk]),
            {"upheld": False, "note": "Looks fine to me."},
            format="json",
        )

        assert response.status_code == 403
        flag.refresh_from_db()
        assert flag.status == FlagStatus.OPEN

    def test_a_resolution_note_is_required(
        self, authenticated_client, admin_user, resident_user, society, resident, worker
    ):
        bookings = self._many_finished_bookings(society, resident, worker, 6)
        client = authenticated_client(resident_user)
        for booking in bookings:
            client.post(
                reverse(RATING_URL), {"booking": booking.pk, "stars": 5}, format="json"
            )

        flag = ReviewFlag.objects.filter(status=FlagStatus.OPEN).first()
        response = authenticated_client(admin_user).post(
            reverse("v1:ratings:flag-resolve", args=[flag.pk]),
            {"upheld": False, "note": "   "},
            format="json",
        )
        assert response.status_code == 400


# ---------------------------------------------------------------------------
# Visibility
# ---------------------------------------------------------------------------


class TestVisibility:
    def test_a_worker_sees_ratings_about_them(
        self, authenticated_client, resident_user, worker_user, finished_engagement
    ):
        """Being rated without being able to see it defeats a two-way system."""
        authenticated_client(resident_user).post(
            reverse(RATING_URL),
            {"engagement": finished_engagement.pk, "stars": 3},
            format="json",
        )

        assert authenticated_client(worker_user).get(reverse(RATING_URL)).data["count"] == 1

    def test_a_withheld_rating_is_hidden_from_a_workers_public_profile(
        self, authenticated_client, resident_user, society, resident, worker
    ):
        bookings = [
            Booking.objects.create(
                society=society, resident=resident, worker=worker,
                category=ServiceCategory.objects.get(slug="deep-cleaning"),
                scheduled_date=timezone.localdate() - dt.timedelta(days=index + 1),
                start_time=dt.time(10, 0), quoted_price=2000,
                status=BookingStatus.COMPLETED,
            )
            for index in range(6)
        ]
        client = authenticated_client(resident_user)
        for booking in bookings:
            client.post(
                reverse(RATING_URL), {"booking": booking.pk, "stars": 5}, format="json"
            )

        response = client.get(
            reverse("v1:ratings:worker-ratings", args=[worker.pk])
        )
        withheld = Rating.objects.filter(is_withheld=True).count()
        assert response.data["count"] == 6 - withheld

    def test_a_guard_sees_no_ratings(self, authenticated_client, guard_user):
        response = authenticated_client(guard_user).get(reverse(RATING_URL))
        assert response.status_code == 403
