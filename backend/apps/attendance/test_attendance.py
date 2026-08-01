"""
Module 7 — Attendance & Gate Verification: tests.

Two groups carry the most weight.

``TestOfflineSync`` pins the property the whole offline design rests on: a
replayed queue must never double-log anyone. If that breaks, a guard with a bad
connection silently doubles a day's attendance, which Module 8 then bills and
Module 9 scores on.

``TestFaceVerification`` pins that a failed or unavailable face check produces a
guard review and never an automatic denial. That is a decision about people's
livelihoods, not a tuning parameter, and it should fail loudly if anyone changes
it.
"""

from __future__ import annotations

import datetime as dt
import uuid
from unittest.mock import patch

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import Role
from apps.attendance.face import FaceResult
from apps.attendance.models import (
    AttendanceEvent,
    Decision,
    Direction,
    GatePass,
    VerificationMethod,
)
from apps.attendance.services import ensure_gate_pass, gate_roster, look_up_pass
from apps.hiring.models import Engagement
from apps.societies.models import Flat, Gate, Resident, Society, SocietyStatus, Tower
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
def gate(society):
    return Gate.objects.create(society=society, name="Main Gate")


@pytest.fixture
def worker_pass(worker):
    return ensure_gate_pass(worker)


@pytest.fixture
def tiny_image():
    """A real 2x2 PNG.

    ImageField validates that an upload is genuinely an image, so a stub of
    arbitrary bytes fails before the code under test is ever reached.
    """
    from io import BytesIO

    from django.core.files.uploadedfile import SimpleUploadedFile
    from PIL import Image

    buffer = BytesIO()
    Image.new("RGB", (2, 2), color="white").save(buffer, format="PNG")
    return SimpleUploadedFile("face.png", buffer.getvalue(), content_type="image/png")


@pytest.fixture
def todays_engagement(society, resident, worker, maid_service):
    """An engagement that falls on today, whatever weekday today is."""
    return Engagement.objects.create(
        society=society,
        resident=resident,
        worker=worker,
        service_type=maid_service,
        days_of_week=[timezone.localdate().weekday()],
        start_time=timezone.localtime().time().replace(second=0, microsecond=0),
        expected_duration_minutes=90,
        monthly_rate=4000,
    )


def event_payload(worker, **overrides):
    payload = {
        "id": str(uuid.uuid4()),
        "worker": worker.pk,
        "direction": Direction.ENTRY,
        "method": VerificationMethod.QR,
        "decision": Decision.ALLOWED,
        "occurred_at": timezone.now().isoformat(),
        "device_id": "guard-tablet-01",
    }
    payload.update(overrides)
    return payload


# ---------------------------------------------------------------------------
# 7.1 Gate passes
# ---------------------------------------------------------------------------


class TestGatePass:
    URL = "v1:attendance:my-pass"

    def test_worker_gets_a_pass_on_first_look(
        self, authenticated_client, worker_user, worker
    ):
        response = authenticated_client(worker_user).get(reverse(self.URL))

        assert response.status_code == 200
        assert response.data["is_usable"] is True
        assert GatePass.objects.filter(worker=worker).count() == 1

    def test_reading_it_twice_does_not_create_a_second_pass(
        self, authenticated_client, worker_user, worker
    ):
        client = authenticated_client(worker_user)
        first = client.get(reverse(self.URL)).data["code"]
        second = client.get(reverse(self.URL)).data["code"]

        assert first == second
        assert GatePass.objects.filter(worker=worker).count() == 1

    def test_the_code_is_not_the_worker_id(self, worker_pass, worker):
        """A guessable code on a laminated card is everyone else's card too."""
        assert str(worker_pass.code) != str(worker.pk)
        assert len(str(worker_pass.code)) == 36

    def test_rotating_invalidates_the_old_code(
        self, authenticated_client, worker_user, worker_pass
    ):
        old_code = worker_pass.code

        response = authenticated_client(worker_user).post(
            reverse("v1:attendance:rotate-pass")
        )

        assert response.status_code == 200
        worker_pass.refresh_from_db()
        assert worker_pass.code != old_code
        assert worker_pass.rotation_count == 1

    def test_a_revoked_pass_is_unusable(self, worker_pass):
        assert worker_pass.revoke(reason="Card lost") is True
        assert worker_pass.is_usable is False

    def test_revoking_twice_is_idempotent(self, worker_pass):
        worker_pass.revoke(reason="Card lost")
        first_revoked_at = worker_pass.revoked_at

        assert worker_pass.revoke(reason="Again") is False
        assert worker_pass.revoked_at == first_revoked_at

    def test_an_unapproved_worker_cannot_use_a_live_pass(
        self, worker_pass, worker_user
    ):
        """Withdrawing approval must lock the gate without anyone remembering
        to revoke the card too."""
        worker_user.is_approved = False
        worker_user.save(update_fields=["is_approved"])
        worker_pass.refresh_from_db()

        assert worker_pass.is_active is True
        assert worker_pass.is_usable is False

    def test_a_resident_has_no_gate_pass(self, authenticated_client, resident_user):
        response = authenticated_client(resident_user).get(reverse(self.URL))
        assert response.status_code == 403


# ---------------------------------------------------------------------------
# 7.2 Scanning
# ---------------------------------------------------------------------------


class TestScanning:
    URL = "v1:attendance:scan"

    def test_a_scan_resolves_the_worker(
        self, authenticated_client, guard_user, worker, worker_pass
    ):
        response = authenticated_client(guard_user).post(
            reverse(self.URL), {"code": str(worker_pass.code)}, format="json"
        )

        assert response.status_code == 200
        assert response.data["worker_id"] == worker.pk
        assert response.data["is_usable"] is True

    def test_a_scan_creates_no_event(
        self, authenticated_client, guard_user, worker_pass
    ):
        """Lookup and record are separate so the offline path is identical."""
        authenticated_client(guard_user).post(
            reverse(self.URL), {"code": str(worker_pass.code)}, format="json"
        )
        assert AttendanceEvent.objects.count() == 0

    def test_an_expected_worker_is_recommended_for_entry(
        self, authenticated_client, guard_user, worker_pass, todays_engagement
    ):
        response = authenticated_client(guard_user).post(
            reverse(self.URL), {"code": str(worker_pass.code)}, format="json"
        )

        assert response.data["is_expected"] is True
        assert response.data["recommendation"] == Decision.ALLOWED
        assert len(response.data["expected_visits"]) == 1

    def test_an_unscheduled_worker_goes_to_the_guard_rather_than_being_refused(
        self, authenticated_client, guard_user, worker_pass
    ):
        """Not scheduled is not the same as not permitted."""
        response = authenticated_client(guard_user).post(
            reverse(self.URL), {"code": str(worker_pass.code)}, format="json"
        )

        assert response.data["is_expected"] is False
        assert response.data["recommendation"] == Decision.PENDING_REVIEW

    def test_a_revoked_pass_is_recommended_for_denial_with_a_reason(
        self, authenticated_client, guard_user, worker_pass
    ):
        worker_pass.revoke(reason="Card reported lost.")

        response = authenticated_client(guard_user).post(
            reverse(self.URL), {"code": str(worker_pass.code)}, format="json"
        )

        assert response.data["is_usable"] is False
        assert response.data["recommendation"] == Decision.DENIED
        assert "cancelled" in response.data["reason"]

    def test_an_unknown_code_is_a_404(self, authenticated_client, guard_user):
        response = authenticated_client(guard_user).post(
            reverse(self.URL), {"code": str(uuid.uuid4())}, format="json"
        )
        assert response.status_code == 404

    def test_another_societys_pass_is_refused_distinctly(
        self, authenticated_client, guard_user, django_user_model, maid_service
    ):
        """A neighbouring society's card is a different problem from a fake one."""
        other = Society.objects.create(
            name="Blue Ridge", address_line="X", city="Pune",
            state="Maharashtra", pincode="411006", status=SocietyStatus.ACTIVE,
        )
        outsider = django_user_model.objects.create_user(
            phone_number="9800000061", password="test-pass-12345",
            role=Role.WORKER, society=other, is_approved=True,
        )
        outside_pass = ensure_gate_pass(
            WorkerProfile.objects.create(user=outsider, photo="p.jpg")
        )

        response = authenticated_client(guard_user).post(
            reverse(self.URL), {"code": str(outside_pass.code)}, format="json"
        )
        assert response.status_code == 403

    def test_a_resident_cannot_scan(
        self, authenticated_client, resident_user, worker_pass
    ):
        response = authenticated_client(resident_user).post(
            reverse(self.URL), {"code": str(worker_pass.code)}, format="json"
        )
        assert response.status_code == 403


class TestRoster:
    URL = "v1:attendance:roster"

    def test_the_roster_lists_expected_workers_with_their_codes(
        self, authenticated_client, guard_user, worker, worker_pass, todays_engagement
    ):
        response = authenticated_client(guard_user).get(reverse(self.URL))

        assert response.status_code == 200
        assert response.data["count"] == 1
        entry = response.data["results"][0]
        assert entry["worker_id"] == worker.pk
        assert entry["pass_code"] == str(worker_pass.code)
        assert len(entry["visits"]) == 1

    def test_a_worker_with_nothing_scheduled_is_not_on_the_roster(
        self, authenticated_client, guard_user, worker_pass
    ):
        response = authenticated_client(guard_user).get(reverse(self.URL))
        assert response.data["count"] == 0

    def test_a_revoked_pass_is_omitted_from_the_roster(
        self, society, worker, worker_pass, todays_engagement
    ):
        worker_pass.revoke(reason="Lost")

        entry = gate_roster(society.pk, timezone.localdate())[0]
        assert entry["pass_code"] is None

    def test_only_gate_staff_may_read_it(
        self, authenticated_client, resident_user, worker_user
    ):
        """It contains the codes that open gates."""
        assert authenticated_client(resident_user).get(reverse(self.URL)).status_code == 403
        assert authenticated_client(worker_user).get(reverse(self.URL)).status_code == 403

    def test_an_administrator_may_read_it(self, authenticated_client, admin_user):
        assert authenticated_client(admin_user).get(reverse(self.URL)).status_code == 200


# ---------------------------------------------------------------------------
# 7.2 / 7.5 / 7.6 Recording
# ---------------------------------------------------------------------------


class TestRecordingEvents:
    URL = "v1:attendance:event-list"

    def test_a_guard_logs_an_entry(
        self, authenticated_client, guard_user, worker, gate
    ):
        response = authenticated_client(guard_user).post(
            reverse(self.URL),
            event_payload(worker, gate=gate.pk),
            format="json",
        )

        assert response.status_code == 201
        assert response.data["created"] is True
        event = AttendanceEvent.objects.get()
        assert event.recorded_by == guard_user
        assert event.gate == gate

    def test_the_event_links_to_the_visit_it_was_for(
        self, authenticated_client, guard_user, worker, todays_engagement
    ):
        """Module 8 bills from this link, so an orphaned event costs someone."""
        authenticated_client(guard_user).post(
            reverse(self.URL), event_payload(worker), format="json"
        )

        event = AttendanceEvent.objects.get()
        assert event.was_expected is True
        assert event.engagement_id == todays_engagement.pk

    def test_an_unscheduled_entry_is_still_logged(
        self, authenticated_client, guard_user, worker
    ):
        """A worker on the wrong day still walks through a gate, and the audit
        trail has to say so rather than refuse to record it."""
        response = authenticated_client(guard_user).post(
            reverse(self.URL), event_payload(worker), format="json"
        )

        assert response.status_code == 201
        event = AttendanceEvent.objects.get()
        assert event.was_expected is False
        assert event.engagement_id is None

    def test_a_manual_entry_records_its_provenance(
        self, authenticated_client, guard_user, worker
    ):
        """Module 7.5 — scanning failed, so the guard logged it by hand."""
        authenticated_client(guard_user).post(
            reverse(self.URL),
            event_payload(worker, method=VerificationMethod.MANUAL),
            format="json",
        )

        assert AttendanceEvent.objects.get().method == VerificationMethod.MANUAL

    def test_a_denial_must_carry_a_reason(
        self, authenticated_client, guard_user, worker
    ):
        """Unauditable otherwise, and the person turned away deserves to know."""
        response = authenticated_client(guard_user).post(
            reverse(self.URL),
            event_payload(worker, decision=Decision.DENIED),
            format="json",
        )
        assert response.status_code == 400

    def test_a_denial_with_a_reason_is_recorded(
        self, authenticated_client, guard_user, worker
    ):
        response = authenticated_client(guard_user).post(
            reverse(self.URL),
            event_payload(
                worker, decision=Decision.DENIED, decision_reason="Pass was cancelled."
            ),
            format="json",
        )

        assert response.status_code == 201
        assert AttendanceEvent.objects.get().decision == Decision.DENIED

    def test_occurred_at_and_recorded_at_are_kept_apart(
        self, authenticated_client, guard_user, worker
    ):
        """Otherwise a batch synced at 6pm looks like everyone arriving at 6pm."""
        happened = timezone.now() - dt.timedelta(hours=3)

        authenticated_client(guard_user).post(
            reverse(self.URL),
            event_payload(worker, occurred_at=happened.isoformat(), was_offline=True),
            format="json",
        )

        event = AttendanceEvent.objects.get()
        assert event.sync_delay_seconds > 3000
        assert event.recorded_at > event.occurred_at

    def test_a_guard_cannot_log_another_societys_worker(
        self, authenticated_client, guard_user, django_user_model
    ):
        other = Society.objects.create(
            name="Blue Ridge", address_line="X", city="Pune",
            state="Maharashtra", pincode="411006", status=SocietyStatus.ACTIVE,
        )
        outsider = django_user_model.objects.create_user(
            phone_number="9800000062", password="test-pass-12345",
            role=Role.WORKER, society=other, is_approved=True,
        )
        outside_worker = WorkerProfile.objects.create(user=outsider, photo="p.jpg")

        response = authenticated_client(guard_user).post(
            reverse(self.URL), event_payload(outside_worker), format="json"
        )
        assert response.status_code == 404

    def test_a_worker_cannot_log_their_own_entry(
        self, authenticated_client, worker_user, worker
    ):
        response = authenticated_client(worker_user).post(
            reverse(self.URL), event_payload(worker), format="json"
        )
        assert response.status_code == 403


# ---------------------------------------------------------------------------
# 7.4 Offline queue & sync — the property the whole design rests on
# ---------------------------------------------------------------------------


class TestOfflineSync:
    URL = "v1:attendance:sync"

    def test_a_queue_syncs(self, authenticated_client, guard_user, worker):
        rows = [event_payload(worker) for _ in range(3)]

        response = authenticated_client(guard_user).post(
            reverse(self.URL), {"events": rows}, format="json"
        )

        assert response.status_code == 200
        assert len(response.data["created"]) == 3
        assert AttendanceEvent.objects.count() == 3

    def test_replaying_the_same_batch_creates_nothing_new(
        self, authenticated_client, guard_user, worker
    ):
        """The device synced, lost its connection before recording the reply,
        and retried. This must not double-log anyone."""
        rows = [event_payload(worker) for _ in range(3)]
        client = authenticated_client(guard_user)

        client.post(reverse(self.URL), {"events": rows}, format="json")
        second = client.post(reverse(self.URL), {"events": rows}, format="json")

        assert len(second.data["created"]) == 0
        assert len(second.data["duplicates"]) == 3
        assert AttendanceEvent.objects.count() == 3

    def test_a_duplicate_is_reported_as_accepted_not_rejected(
        self, authenticated_client, guard_user, worker
    ):
        """The device must clear a duplicate from its queue, not retry forever."""
        rows = [event_payload(worker)]
        client = authenticated_client(guard_user)

        client.post(reverse(self.URL), {"events": rows}, format="json")
        second = client.post(reverse(self.URL), {"events": rows}, format="json")

        assert second.data["accepted_count"] == 1
        assert second.data["rejected"] == []

    def test_a_replay_never_rewrites_an_existing_decision(
        self, authenticated_client, guard_user, worker
    ):
        """A retrying device must not overwrite a decision already reviewed."""
        row = event_payload(worker, decision=Decision.ALLOWED)
        client = authenticated_client(guard_user)
        client.post(reverse(self.URL), {"events": [row]}, format="json")

        tampered = {**row, "decision": Decision.DENIED, "decision_reason": "changed"}
        client.post(reverse(self.URL), {"events": [tampered]}, format="json")

        assert AttendanceEvent.objects.get().decision == Decision.ALLOWED

    def test_one_bad_row_does_not_reject_the_whole_batch(
        self, authenticated_client, guard_user, worker
    ):
        """Otherwise the device retries forever and the day never lands."""
        good = [event_payload(worker) for _ in range(2)]
        bad = {**event_payload(worker), "worker": 999999}

        response = authenticated_client(guard_user).post(
            reverse(self.URL), {"events": good + [bad]}, format="json"
        )

        assert len(response.data["created"]) == 2
        assert len(response.data["rejected"]) == 1
        assert AttendanceEvent.objects.count() == 2

    def test_a_rejected_row_is_named_so_the_device_can_drop_it(
        self, authenticated_client, guard_user, worker
    ):
        bad = {**event_payload(worker), "worker": 999999}

        response = authenticated_client(guard_user).post(
            reverse(self.URL), {"events": [bad]}, format="json"
        )

        assert response.data["rejected"][0]["id"] == bad["id"]
        assert response.data["rejected"][0]["reason"]

    def test_synced_events_are_flagged_as_offline(
        self, authenticated_client, guard_user, worker
    ):
        authenticated_client(guard_user).post(
            reverse(self.URL), {"events": [event_payload(worker)]}, format="json"
        )

        assert AttendanceEvent.objects.get().was_offline is True

    def test_an_empty_batch_is_rejected(self, authenticated_client, guard_user):
        response = authenticated_client(guard_user).post(
            reverse(self.URL), {"events": []}, format="json"
        )
        assert response.status_code == 400

    def test_an_oversized_batch_is_rejected(
        self, authenticated_client, guard_user, worker
    ):
        """Bounded so a runaway queue cannot exhaust a free-tier instance."""
        rows = [event_payload(worker) for _ in range(501)]

        response = authenticated_client(guard_user).post(
            reverse(self.URL), {"events": rows}, format="json"
        )
        assert response.status_code == 400


# ---------------------------------------------------------------------------
# 7.3 Face verification — a model gets a vote, not a veto
# ---------------------------------------------------------------------------


class TestFaceVerification:
    def face_url(self, event_id):
        return reverse("v1:attendance:face-check", args=[event_id])

    def make_event(self, client, worker, **overrides):
        client.post(
            reverse("v1:attendance:event-list"),
            event_payload(worker, **overrides),
            format="json",
        )
        return AttendanceEvent.objects.latest("recorded_at")

    def test_a_matching_face_leaves_the_entry_allowed(
        self, authenticated_client, guard_user, worker, tiny_image
    ):
        client = authenticated_client(guard_user)
        event = self.make_event(client, worker)

        with patch(
            "apps.attendance.services.verify_face",
            return_value=FaceResult(
                available=True, verified=True, score=0.91, engine="deepface"
            ),
        ):
            response = client.post(
                self.face_url(event.pk), {"photo": tiny_image}, format="multipart"
            )

        assert response.status_code == 200
        assert response.data["result"]["verified"] is True
        event.refresh_from_db()
        assert event.decision == Decision.ALLOWED
        assert event.face_verified is True

    def test_a_failed_match_asks_the_guard_rather_than_denying(
        self, authenticated_client, guard_user, worker, tiny_image
    ):
        """The single most important behaviour in this module.

        Face recognition is least accurate for exactly the people this platform
        serves. A false rejection costs someone a day's pay, so the model never
        turns anyone away on its own.
        """
        client = authenticated_client(guard_user)
        event = self.make_event(client, worker)

        with patch(
            "apps.attendance.services.verify_face",
            return_value=FaceResult(
                available=True, verified=False, score=0.31, engine="deepface"
            ),
        ):
            client.post(
                self.face_url(event.pk), {"photo": tiny_image}, format="multipart"
            )

        event.refresh_from_db()
        assert event.decision == Decision.PENDING_REVIEW
        assert event.decision != Decision.DENIED

    def test_an_unavailable_engine_also_asks_the_guard(
        self, authenticated_client, guard_user, worker, tiny_image
    ):
        """Nothing was measured, so nothing can be concluded."""
        client = authenticated_client(guard_user)
        event = self.make_event(client, worker)

        with patch(
            "apps.attendance.services.verify_face",
            return_value=FaceResult(
                available=False, reason="No face recognition engine is installed."
            ),
        ):
            response = client.post(
                self.face_url(event.pk), {"photo": tiny_image}, format="multipart"
            )

        assert response.data["result"]["available"] is False
        assert response.data["result"]["needs_guard_review"] is True
        event.refresh_from_db()
        assert event.decision == Decision.PENDING_REVIEW

    def test_a_worker_with_no_registered_photo_reports_unavailable(
        self, authenticated_client, guard_user, worker, tiny_image
    ):
        """Not a failed match — it says nothing about who is at the gate."""
        worker.photo = ""
        worker.save(update_fields=["photo"])

        client = authenticated_client(guard_user)
        event = self.make_event(client, worker)

        response = client.post(
            self.face_url(event.pk), {"photo": tiny_image}, format="multipart"
        )

        assert response.data["result"]["available"] is False
        assert response.data["result"]["verified"] is False

    def test_the_score_is_recorded_for_the_audit_trail(
        self, authenticated_client, guard_user, worker, tiny_image
    ):
        client = authenticated_client(guard_user)
        event = self.make_event(client, worker)

        with patch(
            "apps.attendance.services.verify_face",
            return_value=FaceResult(available=True, verified=False, score=0.42),
        ):
            client.post(
                self.face_url(event.pk), {"photo": tiny_image}, format="multipart"
            )

        event.refresh_from_db()
        assert event.face_checked is True
        assert event.face_match_score == pytest.approx(0.42)


class TestGuardOverride:
    def url(self, event_id):
        return reverse("v1:attendance:resolve-event", args=[event_id])

    @pytest.fixture
    def pending_event(self, society, worker, guard_user):
        return AttendanceEvent.objects.create(
            id=uuid.uuid4(),
            society=society,
            worker=worker,
            recorded_by=guard_user,
            direction=Direction.ENTRY,
            method=VerificationMethod.FACE,
            decision=Decision.PENDING_REVIEW,
            occurred_at=timezone.now(),
            face_checked=True,
            face_verified=False,
            face_match_score=0.35,
        )

    def test_a_guard_can_allow_a_failed_match(
        self, authenticated_client, guard_user, pending_event
    ):
        response = authenticated_client(guard_user).post(
            self.url(pending_event.pk),
            {"allow": True, "reason": "I know her, the camera is poor in this light."},
            format="json",
        )

        assert response.status_code == 200
        pending_event.refresh_from_db()
        assert pending_event.decision == Decision.ALLOWED
        assert pending_event.overridden_by == guard_user

    def test_a_guard_can_also_refuse(
        self, authenticated_client, guard_user, pending_event
    ):
        """The guard is the authority, in both directions."""
        authenticated_client(guard_user).post(
            self.url(pending_event.pk),
            {"allow": False, "reason": "Not the person on the card."},
            format="json",
        )

        pending_event.refresh_from_db()
        assert pending_event.decision == Decision.DENIED

    def test_an_override_always_needs_a_reason(
        self, authenticated_client, guard_user, pending_event
    ):
        """Overriding a biometric check must be answerable for later."""
        response = authenticated_client(guard_user).post(
            self.url(pending_event.pk), {"allow": True, "reason": "   "}, format="json"
        )
        assert response.status_code == 400

    def test_an_override_is_attributed_and_timestamped(
        self, authenticated_client, guard_user, pending_event
    ):
        authenticated_client(guard_user).post(
            self.url(pending_event.pk),
            {"allow": True, "reason": "Verified visually."},
            format="json",
        )

        pending_event.refresh_from_db()
        assert pending_event.was_overridden is True
        assert pending_event.overridden_at is not None
        assert pending_event.override_reason == "Verified visually."

    def test_resolving_twice_is_refused(
        self, authenticated_client, guard_user, pending_event
    ):
        client = authenticated_client(guard_user)
        payload = {"allow": True, "reason": "Verified visually."}

        assert client.post(self.url(pending_event.pk), payload, format="json").status_code == 200
        second = client.post(self.url(pending_event.pk), payload, format="json")
        assert second.status_code == 409

    def test_a_resident_cannot_override(
        self, authenticated_client, resident_user, pending_event
    ):
        response = authenticated_client(resident_user).post(
            self.url(pending_event.pk),
            {"allow": True, "reason": "Let them in."},
            format="json",
        )
        assert response.status_code == 403


# ---------------------------------------------------------------------------
# 7.6 Audit trail
# ---------------------------------------------------------------------------


class TestAuditTrail:
    URL = "v1:attendance:event-list"

    def test_a_worker_sees_only_their_own_events(
        self, authenticated_client, guard_user, worker_user, worker, society,
        django_user_model,
    ):
        other_user = django_user_model.objects.create_user(
            phone_number="9800000063", password="test-pass-12345",
            role=Role.WORKER, society=society, is_approved=True,
        )
        other_worker = WorkerProfile.objects.create(user=other_user, photo="p.jpg")

        client = authenticated_client(guard_user)
        client.post(reverse(self.URL), event_payload(worker), format="json")
        client.post(reverse(self.URL), event_payload(other_worker), format="json")

        response = authenticated_client(worker_user).get(reverse(self.URL))
        assert response.data["count"] == 1

    def test_gate_staff_see_the_whole_society(
        self, authenticated_client, guard_user, worker
    ):
        client = authenticated_client(guard_user)
        client.post(reverse(self.URL), event_payload(worker), format="json")

        response = client.get(reverse(self.URL))
        assert response.data["count"] == 1

    def test_pending_reviews_can_be_filtered(
        self, authenticated_client, guard_user, worker, society
    ):
        AttendanceEvent.objects.create(
            id=uuid.uuid4(), society=society, worker=worker,
            direction=Direction.ENTRY, method=VerificationMethod.FACE,
            decision=Decision.PENDING_REVIEW, occurred_at=timezone.now(),
        )
        AttendanceEvent.objects.create(
            id=uuid.uuid4(), society=society, worker=worker,
            direction=Direction.ENTRY, method=VerificationMethod.QR,
            decision=Decision.ALLOWED, occurred_at=timezone.now(),
        )

        response = authenticated_client(guard_user).get(
            reverse(self.URL), {"needs_review": "true"}
        )
        assert response.data["count"] == 1

    def test_a_resident_cannot_read_the_gate_log(
        self, authenticated_client, resident_user
    ):
        response = authenticated_client(resident_user).get(reverse(self.URL))
        assert response.status_code == 403
