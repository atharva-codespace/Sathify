"""
Module 13 — conformance tests for the offline and resilience conventions.

Module 13 is not a feature, so it does not get a feature's test file. It gets
this: a set of tests that fail when one of its four conventions stops holding
somewhere in the codebase.

That is the whole point. Conventions written only in prose decay — the next
person adds a sync endpoint that mutates on retry, or an AI feature with no
fallback, and nothing objects until an outage. These tests object.

Each group states the rule it is defending and why breaking it would hurt
somebody, because a conformance test whose reason is lost gets deleted the first
time it is inconvenient.
"""

from __future__ import annotations

import datetime as dt
import uuid

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.core import resilience
from apps.societies.models import Flat, Resident, Tower
from apps.workers.models import ServiceType, WorkerProfile

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def geo_society(society):
    """A society with coordinates, so the geofence has a centre to measure from."""
    society.latitude = 18.559000
    society.longitude = 73.780000
    society.allow_resident_self_checkin = True
    society.save(
        update_fields=["latitude", "longitude", "allow_resident_self_checkin"]
    )
    return society


@pytest.fixture
def maid_service(db):
    return ServiceType.objects.create(name="Maid", slug="maid")


@pytest.fixture
def worker(worker_user, maid_service):
    profile = WorkerProfile.objects.create(
        user=worker_user, photo="workers/photos/test.jpg"
    )
    profile.service_types.add(maid_service)
    return profile


@pytest.fixture
def resident(resident_user, society):
    tower = Tower.objects.create(society=society, name="A", floors=10)
    flat = Flat.objects.create(tower=tower, number="301", floor=3)
    return Resident.objects.create(user=resident_user, flat=flat, is_primary=True)


@pytest.fixture
def scheduled_engagement(geo_society, resident, worker, maid_service):
    """A live engagement whose visit is due right now.

    Built around the current time so the "expected visit" branch is exercised
    without freezing the clock — the same approach Module 5's tests settled on
    after two date-pinned tests started failing after 22:00.
    """
    from apps.hiring.models import Engagement

    now = timezone.localtime()
    return Engagement.objects.create(
        society=geo_society,
        resident=resident,
        worker=worker,
        service_type=maid_service,
        days_of_week=[now.weekday()],
        start_time=now.time().replace(second=0, microsecond=0),
        monthly_rate=4000,
    )


# ---------------------------------------------------------------------------
# 13.1 Client-generated identifiers
# ---------------------------------------------------------------------------


class TestClientGeneratedIds:
    """The rule: anything a device can record offline is addressed by a UUID it
    minted itself.

    A server-assigned id cannot work. The device has to name the record in order
    to retry it safely, and with a server-assigned id it has nothing to retry
    *with* until the request it is retrying has already succeeded.
    """

    def test_attendance_events_use_uuid_primary_keys(self):
        from apps.attendance.models import AttendanceEvent

        field = AttendanceEvent._meta.pk
        assert field.get_internal_type() == "UUIDField"
        # Not editable=False-and-auto: the *client* supplies it.
        assert field.default is not None

    def test_the_sync_serializer_requires_the_device_to_supply_an_id(self):
        from apps.attendance.serializers import RecordEventSerializer

        serializer = RecordEventSerializer(
            data={"worker": 1, "occurred_at": timezone.now().isoformat()}
        )
        assert serializer.is_valid(), serializer.errors
        # Defaulted rather than absent, so a device that forgets still gets a
        # stable id for that submission.
        assert serializer.validated_data["id"] is not None

    def test_self_check_in_takes_a_client_id_too(self):
        from apps.attendance.serializers import SelfCheckInSerializer

        given = uuid.uuid4()
        serializer = SelfCheckInSerializer(
            data={"id": str(given), "occurred_at": timezone.now().isoformat()}
        )
        assert serializer.is_valid(), serializer.errors
        assert serializer.validated_data["id"] == given


# ---------------------------------------------------------------------------
# 13.2 Idempotent sync
# ---------------------------------------------------------------------------


class TestIdempotentSync:
    """The rule: the same event may be sent twice with no second effect, and a
    duplicate is success rather than error.

    A device that synced, lost its connection before reading the response, and
    retried has done nothing wrong. Treating its retry as a failure makes it
    retry forever.
    """

    def _row(self, worker, event_id=None):
        return {
            "id": event_id or uuid.uuid4(),
            "worker": worker.pk,
            "direction": "entry",
            "method": "qr",
            "decision": "allowed",
            "decision_reason": "",
            "occurred_at": timezone.now(),
            "gate": None,
            "device_id": "guard-phone-1",
            "was_offline": True,
        }

    def test_replaying_a_batch_creates_nothing_new(self, society, guard_user, worker):
        from apps.attendance.models import AttendanceEvent
        from apps.attendance.services import sync_events

        rows = [self._row(worker), self._row(worker)]

        first = sync_events(rows, guard=guard_user, society=society)
        second = sync_events(rows, guard=guard_user, society=society)

        assert len(first.created) == 2
        assert len(second.created) == 0
        assert len(second.duplicates) == 2
        assert AttendanceEvent.objects.count() == 2

    def test_a_duplicate_counts_as_accepted(self, society, guard_user, worker):
        """So the device can clear the row rather than queueing it forever."""
        from apps.attendance.services import sync_events

        rows = [self._row(worker)]
        sync_events(rows, guard=guard_user, society=society)
        outcome = sync_events(rows, guard=guard_user, society=society)

        assert outcome.as_dict()["accepted_count"] == 1

    def test_a_retry_cannot_rewrite_an_existing_decision(
        self, society, guard_user, worker
    ):
        """The one that would actually hurt somebody.

        Without this, a device replaying an old queue could overwrite a denial
        an administrator had already reviewed — or turn an administrator's
        correction back into the original mistake.
        """
        from apps.attendance.models import AttendanceEvent
        from apps.attendance.services import sync_events

        event_id = uuid.uuid4()
        sync_events([self._row(worker, event_id)], guard=guard_user, society=society)

        tampered = self._row(worker, event_id)
        tampered["decision"] = "denied"
        tampered["decision_reason"] = "changed my mind"
        sync_events([tampered], guard=guard_user, society=society)

        assert AttendanceEvent.objects.get(pk=event_id).decision == "allowed"

    def test_one_bad_row_does_not_reject_the_batch(self, society, guard_user, worker):
        """Otherwise the device retries the whole batch forever and a day of
        attendance never lands."""
        from apps.attendance.services import sync_events

        bad = self._row(worker)
        bad["worker"] = 999999  # no such worker

        outcome = sync_events(
            [self._row(worker), bad, self._row(worker)],
            guard=guard_user,
            society=society,
        )

        assert len(outcome.created) == 2
        assert len(outcome.rejected) == 1
        # Named, so the device drops exactly that one and clears the rest.
        assert outcome.rejected[0].get("id")


# ---------------------------------------------------------------------------
# 13.3 Tiered attendance fallback
# ---------------------------------------------------------------------------


class TestGeofenceArithmetic:
    """Pure, so it is tested without a database or a request."""

    def test_the_same_point_is_zero_metres_away(self):
        assert resilience.haversine_metres(18.5, 73.8, 18.5, 73.8) == pytest.approx(0)

    def test_a_known_short_distance_is_about_right(self):
        # 0.001° of latitude is ~111 m anywhere on Earth.
        distance = resilience.haversine_metres(18.500, 73.800, 18.501, 73.800)
        assert 105 < distance < 118

    def test_a_position_inside_the_radius_passes(self):
        check = resilience.check_geofence(
            latitude=18.5591,
            longitude=73.7801,
            centre_latitude=18.559,
            centre_longitude=73.780,
        )
        assert check.available is True
        assert check.inside is True
        assert check.needs_human_confirmation is False

    def test_another_city_does_not(self):
        check = resilience.check_geofence(
            latitude=19.076,  # Mumbai
            longitude=72.877,
            centre_latitude=18.559,  # Pune
            centre_longitude=73.780,
        )
        assert check.inside is False
        assert check.needs_human_confirmation is True
        assert "outside" in check.reason

    def test_a_poor_fix_widens_the_allowance_rather_than_failing(self):
        """A phone admitting to a 400 m fix is being truthful.

        Holding that against the person carrying it would punish them for
        standing next to a tall building — which is most of a housing society.
        """
        far = dict(
            latitude=18.5615,
            longitude=73.780,
            centre_latitude=18.559,
            centre_longitude=73.780,
        )

        assert resilience.check_geofence(**far).inside is False
        assert resilience.check_geofence(**far, accuracy_metres=400).inside is True

    def test_a_society_with_no_coordinates_is_unmeasured_not_outside(self):
        """A third state. Silence is not a refusal."""
        check = resilience.check_geofence(
            latitude=18.5,
            longitude=73.8,
            centre_latitude=None,
            centre_longitude=None,
        )

        assert check.available is False
        assert check.inside is False
        assert check.needs_human_confirmation is True

    def test_no_reported_position_is_also_unmeasured(self):
        check = resilience.check_geofence(
            latitude=None,
            longitude=None,
            centre_latitude=18.559,
            centre_longitude=73.780,
        )
        assert check.available is False


class TestAllThreeTiersExist:
    """The rule: three routes to an attendance record, and losing one does not
    stop the day being recorded."""

    def test_every_tier_has_a_verification_method(self):
        from apps.attendance.models import VerificationMethod

        for method in ("QR", "SELF_CHECKIN", "REGISTER"):
            assert hasattr(VerificationMethod, method)

    def test_every_tier_has_a_route(self):
        # Tier 1 scan + record, tier 2 self check-in, tier 3 register scan.
        for name in ("scan", "event-list", "self-checkin", "register-list"):
            assert reverse(f"v1:attendance:{name}")


class TestNoTierCanDenyOnItsOwn:
    """The rule that binds the three tiers together, and the one worth the most.

    A worker turned away by a GPS drift or a blurred photo loses a day's wages
    for a measurement error, and none of these measurements is good enough to
    justify that. So automated tiers produce PENDING_REVIEW at worst; only a
    guard's explicit decision can be a denial.
    """

    def test_a_self_check_in_outside_the_geofence_is_reviewed_not_refused(
        self, geo_society, worker, scheduled_engagement
    ):
        from apps.attendance.models import Decision
        from apps.attendance.services import self_check_in

        result = self_check_in(
            event_id=uuid.uuid4(),
            worker=worker,
            society=geo_society,
            direction="entry",
            occurred_at=timezone.now(),
            latitude=19.076,  # a different city
            longitude=72.877,
        )

        assert result.event.decision == Decision.PENDING_REVIEW
        assert result.event.decision != Decision.DENIED
        assert result.needs_review is True

    def test_a_self_check_in_with_no_position_is_reviewed_not_refused(
        self, geo_society, worker, scheduled_engagement
    ):
        """A phone with location switched off still produces a usable record.

        Refusing it outright would leave a worker who did the job with no
        evidence of it — the exact failure this tier exists to prevent.
        """
        from apps.attendance.models import Decision
        from apps.attendance.services import self_check_in

        result = self_check_in(
            event_id=uuid.uuid4(),
            worker=worker,
            society=geo_society,
            direction="entry",
            occurred_at=timezone.now(),
        )

        assert result.event.decision == Decision.PENDING_REVIEW

    def test_a_face_check_that_fails_never_denies(self, settings):
        from apps.ai_services.face_service import verify

        settings.FACE_SETTINGS = {"ENABLED": True}
        check = verify("/nonexistent/a.jpg", "/nonexistent/b.jpg")

        assert check.requires_human_decision is True
        assert check.outcome in {"unavailable", "below_threshold"}


class TestSelfCheckIn:
    def test_at_the_society_and_expected_is_allowed(
        self, geo_society, worker, scheduled_engagement
    ):
        from apps.attendance.models import Decision, VerificationMethod
        from apps.attendance.services import self_check_in

        result = self_check_in(
            event_id=uuid.uuid4(),
            worker=worker,
            society=geo_society,
            direction="entry",
            occurred_at=timezone.now(),
            latitude=18.5591,
            longitude=73.7801,
        )

        assert result.event.decision == Decision.ALLOWED
        assert result.event.method == VerificationMethod.SELF_CHECKIN
        assert result.was_expected is True

    def test_at_the_society_but_unscheduled_goes_to_review(
        self, geo_society, worker
    ):
        """Common and innocent — a swapped day, a booking agreed verbally — and
        equally what an unscheduled visit looks like. A person decides which."""
        from apps.attendance.models import Decision
        from apps.attendance.services import self_check_in

        result = self_check_in(
            event_id=uuid.uuid4(),
            worker=worker,
            society=geo_society,
            direction="entry",
            occurred_at=timezone.now(),
            latitude=18.5591,
            longitude=73.7801,
        )

        assert result.event.decision == Decision.PENDING_REVIEW
        assert result.was_expected is False

    def test_it_is_idempotent_like_every_other_queued_event(
        self, geo_society, worker, scheduled_engagement
    ):
        from apps.attendance.models import AttendanceEvent
        from apps.attendance.services import self_check_in

        event_id = uuid.uuid4()
        common = dict(
            event_id=event_id,
            worker=worker,
            society=geo_society,
            direction="entry",
            occurred_at=timezone.now(),
            latitude=18.5591,
            longitude=73.7801,
        )

        first = self_check_in(**common)
        second = self_check_in(**common)

        assert first.created is True
        assert second.created is False
        assert AttendanceEvent.objects.filter(pk=event_id).count() == 1

    def test_a_society_can_switch_the_tier_off(self, geo_society, worker):
        from apps.attendance.services import SelfCheckInDisabled, self_check_in

        geo_society.allow_resident_self_checkin = False
        geo_society.save(update_fields=["allow_resident_self_checkin"])

        with pytest.raises(SelfCheckInDisabled):
            self_check_in(
                event_id=uuid.uuid4(),
                worker=worker,
                society=geo_society,
                direction="entry",
                occurred_at=timezone.now(),
            )

    def test_the_endpoint_records_a_check_in(
        self, authenticated_client, worker_user, worker, geo_society, scheduled_engagement
    ):
        response = authenticated_client(worker_user).post(
            reverse("v1:attendance:self-checkin"),
            {
                "occurred_at": timezone.now().isoformat(),
                "latitude": 18.5591,
                "longitude": 73.7801,
                "accuracy_metres": 20,
            },
            format="json",
        )

        assert response.status_code == 201
        body = response.json()
        assert body["decision"] == "allowed"
        assert body["location_checked"] is True
        assert body["distance_metres"] is not None

    def test_the_endpoint_is_idempotent_over_http(
        self, authenticated_client, worker_user, worker, geo_society, scheduled_engagement
    ):
        client = authenticated_client(worker_user)
        payload = {
            "id": str(uuid.uuid4()),
            "occurred_at": timezone.now().isoformat(),
            "latitude": 18.5591,
            "longitude": 73.7801,
        }

        assert client.post(
            reverse("v1:attendance:self-checkin"), payload, format="json"
        ).status_code == 201
        # 200 rather than 201, and no second row: the device can stop retrying.
        assert client.post(
            reverse("v1:attendance:self-checkin"), payload, format="json"
        ).status_code == 200

    def test_half_a_position_is_refused_as_a_validation_error(
        self, authenticated_client, worker_user, worker, geo_society
    ):
        response = authenticated_client(worker_user).post(
            reverse("v1:attendance:self-checkin"),
            {"occurred_at": timezone.now().isoformat(), "latitude": 18.5591},
            format="json",
        )

        assert response.status_code == 400

    def test_a_resident_cannot_check_a_worker_in(
        self, authenticated_client, resident_user, geo_society
    ):
        """The worker is the one whose presence is in question.

        A resident is at home either way, so geofencing them would measure
        nothing — see the naming note in `attendance/services.py`.
        """
        response = authenticated_client(resident_user).post(
            reverse("v1:attendance:self-checkin"),
            {"occurred_at": timezone.now().isoformat()},
            format="json",
        )

        assert response.status_code == 403

    def test_a_disabled_society_returns_a_clear_refusal(
        self, authenticated_client, worker_user, worker, geo_society
    ):
        geo_society.allow_resident_self_checkin = False
        geo_society.save(update_fields=["allow_resident_self_checkin"])

        response = authenticated_client(worker_user).post(
            reverse("v1:attendance:self-checkin"),
            {"occurred_at": timezone.now().isoformat()},
            format="json",
        )

        assert response.status_code == 403
        assert response.json()["error"]["code"] == "self_checkin_disabled"


# ---------------------------------------------------------------------------
# 13.4 AI fallback conventions
# ---------------------------------------------------------------------------


class TestAiFallbacks:
    """The rule: every Module 12 feature answers with no provider configured.

    Which is the state of a fresh clone, and of a free tier that has spent its
    quota. If these pass with no keys set, they pass in the deployment this
    project actually has.
    """

    @pytest.fixture(autouse=True)
    def _no_providers(self, settings):
        settings.AI_SETTINGS = {
            "ENABLED": True,
            "TIMEOUT_SECONDS": 5,
            "TIERS": [
                {"name": "gemini", "api_key": "", "model": "m", "endpoint": "https://e"}
            ],
        }

    def test_every_declared_feature_is_reachable(self):
        """A feature named in the enum but wired to nothing would be a hole the
        conventions could not cover."""
        from apps.ai_services.models import AiFeature

        assert set(AiFeature.values) == {
            "chat",
            "review_summary",
            "sentiment",
            "complaint_classify",
            "recommendation",
            "ocr",
            "face",
        }

    def test_sentiment_answers_without_a_provider(self):
        from apps.ai_services.analysis import analyse_sentiment

        result = analyse_sentiment("kaam accha hai")
        assert result.is_available is True
        assert result.from_ai is False

    def test_review_summary_answers_without_a_provider(self):
        from apps.ai_services.analysis import summarise_reviews

        result = summarise_reviews(["always on time"])
        assert result.is_available is True
        assert result.value.review_count == 1

    def test_complaint_classification_answers_without_a_provider(self):
        from apps.ai_services.analysis import classify_complaint

        result = classify_complaint("Salary", "The payment never arrived.")
        assert result.is_available is True
        assert result.value.category == "payment"

    def test_the_chatbot_answers_without_a_provider(self, resident, resident_user):
        from apps.ai_services.chatbot import Intent, answer

        reply = answer(resident_user, "have I paid anything")

        assert reply.intent == Intent.PAYMENTS
        assert reply.intent_source == "keywords"

    def test_recommendation_needs_no_provider_at_all(self, worker):
        """It is a local computation, which is why it is not wrapped."""
        from apps.hiring.services import score_worker

        assert 0.0 <= score_worker(worker).total <= 1.0

    def test_ocr_degrades_to_manual_entry(self):
        from apps.ai_services.ocr_service import extract

        result = extract(b"not an image", filename="x.jpg")
        assert result.needs_manual_entry is True

    def test_face_degrades_to_a_guard_decision(self, settings):
        from apps.ai_services.face_service import verify

        settings.FACE_SETTINGS = {"ENABLED": False}
        assert verify("a.jpg", "b.jpg").requires_human_decision is True

    def test_the_wrapper_cannot_be_called_without_a_fallback(self):
        """The convention is enforced by the signature, not by review.

        A developer adding a sixth AI feature has to answer "and what happens
        when this is unavailable?" before the code runs.
        """
        from apps.ai_services.degradation import with_fallback

        with pytest.raises(TypeError):
            with_fallback("sentiment", ai=lambda: (None, "", ""))  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# Free-tier sweeps (constraints §7)
# ---------------------------------------------------------------------------


class TestSweepsAreIdempotent:
    """The rule: everything that would have been a cron job is a sweep that is
    safe to run twice.

    That property is what makes "whoever happens to load the screen" an
    acceptable trigger — and with no scheduler on the free tier, it is the only
    trigger there is.
    """

    def test_hire_request_expiry_is_safe_to_repeat(
        self, society, resident, worker, maid_service
    ):
        from apps.hiring.models import HireRequest, HireRequestStatus

        HireRequest.objects.create(
            society=society,
            resident=resident,
            worker=worker,
            service_type=maid_service,
            days_of_week=[0],
            start_time=dt.time(9, 0),
            monthly_rate=4000,
            expires_at=timezone.now() - dt.timedelta(hours=1),
        )

        assert HireRequest.objects.expire_lapsed() == 1
        assert HireRequest.objects.expire_lapsed() == 0
        assert HireRequest.objects.filter(
            status=HireRequestStatus.EXPIRED
        ).count() == 1

    def test_complaint_escalation_is_safe_to_repeat(self, society, resident_user):
        from apps.administration.models import Complaint, ComplaintCategory
        from apps.administration.services import escalate_overdue, raise_complaint

        complaint = raise_complaint(
            raised_by=resident_user,
            society=society,
            category=ComplaintCategory.QUALITY,
            subject="x",
            description="y",
        )
        Complaint.objects.filter(pk=complaint.pk).update(
            sla_due_at=timezone.now() - dt.timedelta(hours=1)
        )

        assert escalate_overdue(society_id=society.pk) == 1
        assert escalate_overdue(society_id=society.pk) == 0

    def test_the_ai_usage_prune_is_safe_to_repeat(self):
        from apps.ai_services.models import AiUsageCounter, UsageWindow

        AiUsageCounter.objects.create(
            tier="t",
            window=UsageWindow.DAY,
            bucket=(timezone.localdate() - dt.timedelta(days=30)).isoformat(),
        )

        assert AiUsageCounter.prune(keep_days=7) == 1
        assert AiUsageCounter.prune(keep_days=7) == 0
