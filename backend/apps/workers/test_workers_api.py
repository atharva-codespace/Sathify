"""
Module 3 — Worker Onboarding & KYC: API tests.

The OCR pipeline itself is covered stage by stage in ``test_ocr_stages.py``.
These tests cover the layer above it: the API, the consent gate, the age gate,
and the approval gate.

-------------------------------------------------------------------------------
WHY THE PIPELINE IS MOCKED HERE
-------------------------------------------------------------------------------
Running the real pipeline would need PaddleOCR loaded and a genuine Aadhaar
photograph, which is neither available in CI nor something to commit to a
repository. Every test that needs OCR output patches ``run_ocr_pipeline`` and
asserts on what this module does with the result — which is the part that could
regress, and the part ``test_ocr_stages.py`` does not touch.

The one thing worth stating explicitly: nothing here ever writes a real Aadhaar
number, and ``TestAadhaarIsNeverStored`` asserts that the full number cannot be
recovered from the database after a run.
"""

from __future__ import annotations

import datetime as dt
from unittest.mock import patch

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from apps.accounts.models import Role
from apps.societies.models import Society, SocietyStatus
from apps.workers.models import (
    ConsentPurpose,
    ConsentRecord,
    KycDocument,
    KycStatus,
    ServiceType,
    WorkerProfile,
    hash_aadhaar,
)
from apps.workers.ocr import OcrPipelineError
from apps.workers.ocr.extraction import ExtractedField, ExtractedFields
from apps.workers.ocr.pipeline import PipelineResult

pytestmark = pytest.mark.django_db

#: A structurally valid Aadhaar number (passes the Verhoeff checksum).
#: Generated for tests; it belongs to nobody.
VALID_AADHAAR = "234123412346"


# ---------------------------------------------------------------------------
# Fixtures & helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def maid_service(db):
    return ServiceType.objects.create(name="Maid", slug="maid")


@pytest.fixture
def worker_profile(worker_user, maid_service):
    profile = WorkerProfile.objects.create(
        user=worker_user, photo="workers/photos/test.jpg"
    )
    profile.service_types.add(maid_service)
    return profile


def a_document():
    """A tiny stand-in upload. Content is irrelevant — the pipeline is mocked."""
    return SimpleUploadedFile("aadhaar.jpg", b"not-a-real-image", content_type="image/jpeg")


def pipeline_result(
    *,
    name="Rahul Sharma",
    dob="01/01/1990",
    aadhaar=VALID_AADHAAR,
    age=36,
    is_minor=False,
    checksum_valid=True,
    low_confidence=None,
) -> PipelineResult:
    """A PipelineResult shaped like a successful run."""
    fields = ExtractedFields(
        name=ExtractedField(value=name, confidence=0.95),
        dob=ExtractedField(value=dob, confidence=0.93),
        gender=ExtractedField(value="Male", confidence=0.9),
        aadhaar=ExtractedField(value=aadhaar, confidence=0.97),
        low_confidence_fields=low_confidence or [],
        aadhaar_checksum_valid=checksum_valid,
    )
    return PipelineResult(
        fields=fields,
        aadhaar_checksum_valid=checksum_valid,
        age=age,
        is_minor=is_minor,
        engine_used="paddleocr",
    )


def upload(client, **overrides):
    payload = {"document": a_document(), "consent": True}
    payload.update(overrides)
    return client.post(reverse("v1:workers:kyc-upload"), payload, format="multipart")


# ---------------------------------------------------------------------------
# Catalogue & 3.1 profile
# ---------------------------------------------------------------------------


class TestServiceTypes:
    def test_lists_active_types(self, authenticated_client, worker_user, maid_service):
        response = authenticated_client(worker_user).get(
            reverse("v1:workers:service-types")
        )

        assert response.status_code == 200
        assert [row["slug"] for row in response.data] == ["maid"]

    def test_inactive_types_are_hidden(
        self, authenticated_client, worker_user, maid_service
    ):
        maid_service.is_active = False
        maid_service.save(update_fields=["is_active"])

        response = authenticated_client(worker_user).get(
            reverse("v1:workers:service-types")
        )
        assert response.data == []


class TestWorkerProfile:
    URL = "v1:workers:my-profile"

    def test_worker_creates_their_profile(
        self, authenticated_client, worker_user, maid_service
    ):
        response = authenticated_client(worker_user).post(
            reverse(self.URL),
            {
                "service_types": [maid_service.pk],
                "years_of_experience": 5,
                "bio": "Ten years in this area.",
                "languages_spoken": "Hindi, Marathi",
                "expected_monthly_rate": 4000,
            },
            format="multipart",
        )

        assert response.status_code == 201
        profile = WorkerProfile.objects.get(user=worker_user)
        assert profile.years_of_experience == 5
        assert list(profile.service_types.all()) == [maid_service]

    def test_an_unapproved_worker_can_still_build_their_profile(
        self, authenticated_client, worker_user, maid_service
    ):
        """Onboarding would deadlock if this required approval."""
        worker_user.is_approved = False
        worker_user.save(update_fields=["is_approved"])

        response = authenticated_client(worker_user).post(
            reverse(self.URL), {"years_of_experience": 2}, format="multipart"
        )
        assert response.status_code == 201

    def test_creating_twice_is_refused(
        self, authenticated_client, worker_user, worker_profile
    ):
        response = authenticated_client(worker_user).post(
            reverse(self.URL), {"years_of_experience": 1}, format="multipart"
        )
        assert response.status_code == 400

    def test_worker_reads_their_own_profile(
        self, authenticated_client, worker_user, worker_profile
    ):
        response = authenticated_client(worker_user).get(reverse(self.URL))

        assert response.status_code == 200
        assert response.data["id"] == worker_profile.pk
        assert response.data["service_types"][0]["slug"] == "maid"

    def test_profile_reports_searchability(
        self, authenticated_client, worker_user, worker_profile
    ):
        response = authenticated_client(worker_user).get(reverse(self.URL))
        assert response.data["is_searchable"] is True

    def test_scores_are_json_numbers_not_strings(
        self, authenticated_client, worker_user, worker_profile
    ):
        """Same DecimalField trap as Module 4's search rows."""
        body = authenticated_client(worker_user).get(reverse(self.URL)).json()

        assert isinstance(body["trust_score"], (int, float))
        assert isinstance(body["average_rating"], (int, float))

    def test_worker_updates_their_profile(
        self, authenticated_client, worker_user, worker_profile
    ):
        response = authenticated_client(worker_user).patch(
            reverse(self.URL), {"is_available": False}, format="multipart"
        )

        assert response.status_code == 200
        worker_profile.refresh_from_db()
        assert worker_profile.is_available is False

    def test_worker_cannot_set_their_own_trust_score(
        self, authenticated_client, worker_user, worker_profile
    ):
        """A self-settable trust score would make the whole rating system a lie."""
        authenticated_client(worker_user).patch(
            reverse(self.URL), {"trust_score": 99}, format="multipart"
        )

        worker_profile.refresh_from_db()
        assert float(worker_profile.trust_score) == 0

    def test_half_an_availability_window_is_rejected(
        self, authenticated_client, worker_user, worker_profile
    ):
        response = authenticated_client(worker_user).patch(
            reverse(self.URL), {"available_from": "09:00"}, format="multipart"
        )
        assert response.status_code == 400

    def test_resident_cannot_create_a_worker_profile(
        self, authenticated_client, resident_user
    ):
        response = authenticated_client(resident_user).post(
            reverse(self.URL), {"years_of_experience": 1}, format="multipart"
        )
        assert response.status_code == 403


# ---------------------------------------------------------------------------
# 3.2 / 3.6 Upload, OCR and consent
# ---------------------------------------------------------------------------


class TestKycUpload:
    def test_upload_runs_the_pipeline_and_stores_the_result(
        self, authenticated_client, worker_user, worker_profile
    ):
        with patch(
            "apps.workers.services.run_ocr_pipeline", return_value=pipeline_result()
        ):
            response = upload(authenticated_client(worker_user))

        assert response.status_code == 201
        assert response.data["kyc"]["status"] == KycStatus.COMPLETED
        assert response.data["kyc"]["extracted_name"] == "Rahul Sharma"
        assert response.data["kyc"]["aadhaar_checksum_valid"] is True

    def test_upload_records_consent_at_the_point_of_collection(
        self, authenticated_client, worker_user, worker_profile
    ):
        """DPDP 2023 requires consent when the data is collected, not before."""
        with patch(
            "apps.workers.services.run_ocr_pipeline", return_value=pipeline_result()
        ):
            upload(authenticated_client(worker_user))

        assert ConsentRecord.has_consent(worker_user, ConsentPurpose.KYC_AADHAAR)

    def test_refusing_consent_stores_nothing(
        self, authenticated_client, worker_user, worker_profile
    ):
        response = upload(authenticated_client(worker_user), consent=False)

        assert response.status_code == 400
        assert not KycDocument.objects.exists()
        assert not ConsentRecord.objects.filter(user=worker_user).exists()

    def test_repeated_uploads_do_not_stack_consent_records(
        self, authenticated_client, worker_user, worker_profile
    ):
        client = authenticated_client(worker_user)
        with patch(
            "apps.workers.services.run_ocr_pipeline", return_value=pipeline_result()
        ):
            upload(client)
            upload(client)

        assert (
            ConsentRecord.objects.filter(
                user=worker_user, purpose=ConsentPurpose.KYC_AADHAAR
            ).count()
            == 1
        )

    def test_every_attempt_is_kept(
        self, authenticated_client, worker_user, worker_profile
    ):
        """A re-upload after a poor scan must stay auditable (SRS 5.5)."""
        client = authenticated_client(worker_user)
        with patch(
            "apps.workers.services.run_ocr_pipeline", return_value=pipeline_result()
        ):
            upload(client)
            upload(client)

        assert KycDocument.objects.filter(worker=worker_profile).count() == 2

    def test_ocr_failure_is_a_normal_state_not_an_error_response(
        self, authenticated_client, worker_user, worker_profile
    ):
        """SRS 2.5 requires a manual-entry fallback, so this must not 500."""
        with patch(
            "apps.workers.services.run_ocr_pipeline",
            side_effect=OcrPipelineError("No OCR engine available"),
        ):
            response = upload(authenticated_client(worker_user))

        assert response.status_code == 201
        assert response.data["kyc"]["status"] == KycStatus.FAILED
        assert "manually" in response.data["message"]

    def test_an_unexpected_engine_crash_does_not_lose_the_upload(
        self, authenticated_client, worker_user, worker_profile
    ):
        with patch(
            "apps.workers.services.run_ocr_pipeline",
            side_effect=RuntimeError("paddle segfaulted"),
        ):
            response = upload(authenticated_client(worker_user))

        assert response.status_code == 201
        assert response.data["kyc"]["status"] == KycStatus.FAILED
        assert KycDocument.objects.count() == 1

    def test_low_confidence_fields_are_flagged_for_confirmation(
        self, authenticated_client, worker_user, worker_profile
    ):
        with patch(
            "apps.workers.services.run_ocr_pipeline",
            return_value=pipeline_result(low_confidence=["aadhaar"]),
        ):
            response = upload(authenticated_client(worker_user))

        assert response.data["kyc"]["needs_manual_confirmation"] is True
        assert "check the highlighted fields" in response.data["message"]

    def test_upload_without_a_profile_is_refused(
        self, authenticated_client, worker_user
    ):
        response = upload(authenticated_client(worker_user))
        assert response.status_code == 400

    def test_resident_cannot_upload_kyc(self, authenticated_client, resident_user):
        response = upload(authenticated_client(resident_user))
        assert response.status_code == 403


class TestAadhaarIsNeverStored:
    """The single most important privacy property in the codebase."""

    def test_the_full_number_is_not_recoverable_from_the_database(
        self, authenticated_client, worker_user, worker_profile
    ):
        with patch(
            "apps.workers.services.run_ocr_pipeline", return_value=pipeline_result()
        ):
            upload(authenticated_client(worker_user))

        kyc = KycDocument.objects.get()
        stored = " ".join(
            str(value) for value in kyc.__dict__.values() if value is not None
        )

        assert VALID_AADHAAR not in stored
        assert kyc.aadhaar_last4 == VALID_AADHAAR[-4:]
        assert kyc.aadhaar_hash == hash_aadhaar(VALID_AADHAAR)

    def test_the_api_never_returns_the_full_number(
        self, authenticated_client, worker_user, worker_profile
    ):
        with patch(
            "apps.workers.services.run_ocr_pipeline", return_value=pipeline_result()
        ):
            response = upload(authenticated_client(worker_user))

        assert VALID_AADHAAR not in str(response.data)
        assert response.data["kyc"]["masked_aadhaar"].endswith(VALID_AADHAAR[-4:])

    def test_the_hash_is_a_stable_deduplication_key(
        self, authenticated_client, worker_user, worker_profile
    ):
        """The same number written differently must hash the same."""
        assert hash_aadhaar(VALID_AADHAAR) == hash_aadhaar("2341 2341 2346")


# ---------------------------------------------------------------------------
# 3.4 Age gate
# ---------------------------------------------------------------------------


class TestAgeGate:
    def test_a_minor_is_rejected_automatically_on_upload(
        self, authenticated_client, worker_user, worker_profile
    ):
        """Modspec 3.4 — a hard block, not a flag for admin discretion."""
        with patch(
            "apps.workers.services.run_ocr_pipeline",
            return_value=pipeline_result(dob="01/01/2012", age=14, is_minor=True),
        ):
            response = upload(authenticated_client(worker_user))

        assert response.data["auto_rejected"] is True
        worker_user.refresh_from_db()
        assert worker_user.is_approved is False
        worker_profile.refresh_from_db()
        assert "under 18" in worker_profile.rejection_reason

    def test_an_administrator_cannot_override_the_age_gate(
        self, authenticated_client, worker_user, worker_profile, admin_user
    ):
        with patch(
            "apps.workers.services.run_ocr_pipeline",
            return_value=pipeline_result(dob="01/01/2012", age=14, is_minor=True),
        ):
            upload(authenticated_client(worker_user))

        response = authenticated_client(admin_user).post(
            reverse("v1:workers:review-decide", args=[worker_profile.pk]),
            {"approve": True},
            format="json",
        )

        assert response.status_code == 409
        assert response.data["error"]["code"] == "minor_rejected"
        worker_user.refresh_from_db()
        assert worker_user.is_approved is False

    def test_a_minor_cannot_edit_their_way_past_the_gate(
        self, authenticated_client, worker_user, worker_profile
    ):
        """Otherwise the hard block would be one correction away from bypass."""
        with patch(
            "apps.workers.services.run_ocr_pipeline",
            return_value=pipeline_result(dob="01/01/2012", age=14, is_minor=True),
        ):
            upload(authenticated_client(worker_user))

        kyc = KycDocument.objects.get()
        response = authenticated_client(worker_user).post(
            reverse("v1:workers:kyc-confirm", args=[kyc.pk]),
            {"dob": "01/01/1990"},
            format="json",
        )

        assert response.status_code == 409
        kyc.refresh_from_db()
        assert kyc.is_minor is True

    def test_an_unreadable_date_is_not_treated_as_a_minor(
        self, authenticated_client, worker_user, worker_profile
    ):
        """Rejecting adults over an OCR failure would be worse than reviewing."""
        with patch(
            "apps.workers.services.run_ocr_pipeline",
            return_value=pipeline_result(dob="", age=None, is_minor=False),
        ):
            response = upload(authenticated_client(worker_user))

        assert response.data["auto_rejected"] is False
        worker_user.refresh_from_db()
        assert worker_user.is_approved is True


# ---------------------------------------------------------------------------
# 3.2 / 3.3 Confirmation and manual entry
# ---------------------------------------------------------------------------


class TestKycConfirmation:
    def make_kyc(self, client, **kwargs):
        with patch(
            "apps.workers.services.run_ocr_pipeline",
            return_value=pipeline_result(**kwargs),
        ):
            upload(client)
        return KycDocument.objects.latest("created_at")

    def url(self, pk):
        return reverse("v1:workers:kyc-confirm", args=[pk])

    def test_worker_corrects_a_misread_name(
        self, authenticated_client, worker_user, worker_profile
    ):
        client = authenticated_client(worker_user)
        kyc = self.make_kyc(client, name="Rahu1 5harma")

        response = client.post(
            self.url(kyc.pk), {"name": "Rahul Sharma"}, format="json"
        )

        assert response.status_code == 200
        kyc.refresh_from_db()
        assert kyc.extracted_name == "Rahul Sharma"

    def test_confirming_clears_the_low_confidence_flag(
        self, authenticated_client, worker_user, worker_profile
    ):
        """A human has now looked at it, which is what the flag was asking for."""
        client = authenticated_client(worker_user)
        kyc = self.make_kyc(client, low_confidence=["name"])

        client.post(self.url(kyc.pk), {"name": "Rahul Sharma"}, format="json")

        kyc.refresh_from_db()
        assert "name" not in kyc.low_confidence_fields

    def test_a_corrected_aadhaar_is_revalidated_and_rehashed(
        self, authenticated_client, worker_user, worker_profile
    ):
        client = authenticated_client(worker_user)
        kyc = self.make_kyc(client, aadhaar="999999999999", checksum_valid=False)

        response = client.post(
            self.url(kyc.pk), {"aadhaar_number": VALID_AADHAAR}, format="json"
        )

        assert response.status_code == 200
        kyc.refresh_from_db()
        assert kyc.aadhaar_checksum_valid is True
        assert kyc.aadhaar_hash == hash_aadhaar(VALID_AADHAAR)
        assert kyc.aadhaar_last4 == VALID_AADHAAR[-4:]

    def test_an_invalid_aadhaar_is_rejected_by_the_checksum(
        self, authenticated_client, worker_user, worker_profile
    ):
        client = authenticated_client(worker_user)
        kyc = self.make_kyc(client)

        response = client.post(
            self.url(kyc.pk), {"aadhaar_number": "234123412341"}, format="json"
        )

        assert response.status_code == 400

    def test_a_short_aadhaar_is_rejected(
        self, authenticated_client, worker_user, worker_profile
    ):
        client = authenticated_client(worker_user)
        kyc = self.make_kyc(client)

        response = client.post(
            self.url(kyc.pk), {"aadhaar_number": "1234"}, format="json"
        )
        assert response.status_code == 400

    def test_the_confirm_response_never_echoes_the_number_back(
        self, authenticated_client, worker_user, worker_profile
    ):
        client = authenticated_client(worker_user)
        kyc = self.make_kyc(client)

        response = client.post(
            self.url(kyc.pk), {"aadhaar_number": VALID_AADHAAR}, format="json"
        )

        assert VALID_AADHAAR not in str(response.data)

    def test_manual_entry_completes_a_failed_document(
        self, authenticated_client, worker_user, worker_profile
    ):
        """The SRS 2.5 fallback: OCR unavailable, worker types it in."""
        client = authenticated_client(worker_user)
        with patch(
            "apps.workers.services.run_ocr_pipeline",
            side_effect=OcrPipelineError("No engine"),
        ):
            upload(client)
        kyc = KycDocument.objects.latest("created_at")
        assert kyc.status == KycStatus.FAILED

        response = client.post(
            self.url(kyc.pk),
            {"name": "Rahul Sharma", "dob": "01/01/1990", "aadhaar_number": VALID_AADHAAR},
            format="json",
        )

        assert response.status_code == 200
        kyc.refresh_from_db()
        assert kyc.status == KycStatus.COMPLETED
        assert kyc.extracted_name == "Rahul Sharma"

    def test_an_empty_confirmation_is_rejected(
        self, authenticated_client, worker_user, worker_profile
    ):
        client = authenticated_client(worker_user)
        kyc = self.make_kyc(client)

        assert client.post(self.url(kyc.pk), {}, format="json").status_code == 400

    def test_another_worker_cannot_touch_someone_elses_document(
        self, authenticated_client, worker_user, worker_profile, society, django_user_model
    ):
        kyc = self.make_kyc(authenticated_client(worker_user))
        intruder = django_user_model.objects.create_user(
            phone_number="9800000081",
            password="test-pass-12345",
            role=Role.WORKER,
            society=society,
            is_approved=True,
        )

        response = authenticated_client(intruder).post(
            self.url(kyc.pk), {"name": "Someone Else"}, format="json"
        )
        assert response.status_code == 404


# ---------------------------------------------------------------------------
# 3.6 Consent records
# ---------------------------------------------------------------------------


class TestConsent:
    URL = "v1:workers:consent-list"

    def test_worker_grants_and_lists_consent(self, authenticated_client, worker_user):
        client = authenticated_client(worker_user)

        created = client.post(
            reverse(self.URL),
            {"purpose": ConsentPurpose.FACE_BIOMETRIC},
            format="json",
        )
        assert created.status_code == 201

        listed = client.get(reverse(self.URL))
        assert listed.data["count"] == 1

    def test_withdrawing_one_purpose_leaves_the_others_intact(
        self, authenticated_client, worker_user
    ):
        """Withdrawing face consent must not revoke the identity verification
        a worker's approval rests on."""
        client = authenticated_client(worker_user)
        client.post(
            reverse(self.URL), {"purpose": ConsentPurpose.KYC_AADHAAR}, format="json"
        )
        face = client.post(
            reverse(self.URL), {"purpose": ConsentPurpose.FACE_BIOMETRIC}, format="json"
        )

        response = client.post(
            reverse("v1:workers:consent-withdraw", args=[face.data["id"]]),
            {},
            format="json",
        )

        assert response.status_code == 200
        assert ConsentRecord.has_consent(worker_user, ConsentPurpose.KYC_AADHAAR)
        assert not ConsentRecord.has_consent(worker_user, ConsentPurpose.FACE_BIOMETRIC)

    def test_a_worker_cannot_see_another_users_consents(
        self, authenticated_client, worker_user, resident_user
    ):
        authenticated_client(worker_user).post(
            reverse(self.URL), {"purpose": ConsentPurpose.KYC_AADHAAR}, format="json"
        )

        response = authenticated_client(resident_user).get(reverse(self.URL))
        assert response.data["count"] == 0


# ---------------------------------------------------------------------------
# 3.5 Admin verification & activation gate
# ---------------------------------------------------------------------------


class TestApprovalGate:
    def decide_url(self, pk):
        return reverse("v1:workers:review-decide", args=[pk])

    def complete_kyc(self, authenticated_client, worker_user, **kwargs):
        with patch(
            "apps.workers.services.run_ocr_pipeline",
            return_value=pipeline_result(**kwargs),
        ):
            upload(authenticated_client(worker_user))

    def test_pending_queue_lists_unapproved_workers(
        self, authenticated_client, admin_user, worker_user, worker_profile
    ):
        worker_user.is_approved = False
        worker_user.save(update_fields=["is_approved"])

        response = authenticated_client(admin_user).get(
            reverse("v1:workers:review-pending")
        )

        assert response.status_code == 200
        assert response.data["count"] == 1
        assert response.data["results"][0]["id"] == worker_profile.pk

    def test_the_queue_payload_carries_everything_needed_to_decide(
        self, authenticated_client, admin_user, worker_user, worker_profile
    ):
        worker_user.is_approved = False
        worker_user.save(update_fields=["is_approved"])
        self.complete_kyc(authenticated_client, worker_user)

        response = authenticated_client(admin_user).get(
            reverse("v1:workers:review-detail", args=[worker_profile.pk])
        )

        assert response.data["latest_kyc"]["extracted_name"] == "Rahul Sharma"
        assert response.data["approval_blockers"] == []
        assert response.data["duplicate_of"] is None

    def test_admin_approves_a_complete_worker(
        self, authenticated_client, admin_user, worker_user, worker_profile
    ):
        worker_user.is_approved = False
        worker_user.save(update_fields=["is_approved"])
        self.complete_kyc(authenticated_client, worker_user)

        response = authenticated_client(admin_user).post(
            self.decide_url(worker_profile.pk), {"approve": True}, format="json"
        )

        assert response.status_code == 200
        worker_user.refresh_from_db()
        assert worker_user.is_approved is True
        worker_profile.refresh_from_db()
        assert worker_profile.reviewed_by == admin_user

    def test_approval_makes_the_worker_searchable(
        self, authenticated_client, admin_user, worker_user, worker_profile
    ):
        """Modspec 3.5 — only an approved worker reaches Module 4's search."""
        worker_user.is_approved = False
        worker_user.save(update_fields=["is_approved"])
        self.complete_kyc(authenticated_client, worker_user)

        from apps.hiring.services import searchable_workers

        assert worker_profile.pk not in set(
            searchable_workers(worker_user.society_id).values_list("pk", flat=True)
        )

        authenticated_client(admin_user).post(
            self.decide_url(worker_profile.pk), {"approve": True}, format="json"
        )

        assert worker_profile.pk in set(
            searchable_workers(worker_user.society_id).values_list("pk", flat=True)
        )

    def test_a_worker_without_a_photo_cannot_be_approved(
        self, authenticated_client, admin_user, worker_user, worker_profile
    ):
        """Approving one produces a worker who can never be found or admitted."""
        worker_profile.photo = ""
        worker_profile.save(update_fields=["photo"])
        self.complete_kyc(authenticated_client, worker_user)

        response = authenticated_client(admin_user).post(
            self.decide_url(worker_profile.pk), {"approve": True}, format="json"
        )

        assert response.status_code == 409
        assert response.data["error"]["code"] == "profile_incomplete"

    def test_a_worker_without_a_document_cannot_be_approved(
        self, authenticated_client, admin_user, worker_profile
    ):
        response = authenticated_client(admin_user).post(
            self.decide_url(worker_profile.pk), {"approve": True}, format="json"
        )

        assert response.status_code == 409
        assert "Aadhaar" in response.data["error"]["message"]

    def test_rejection_requires_a_reason(
        self, authenticated_client, admin_user, worker_profile
    ):
        response = authenticated_client(admin_user).post(
            self.decide_url(worker_profile.pk), {"approve": False}, format="json"
        )
        assert response.status_code == 400

    def test_rejection_keeps_the_account_so_it_can_be_corrected(
        self, authenticated_client, admin_user, worker_user, worker_profile
    ):
        response = authenticated_client(admin_user).post(
            self.decide_url(worker_profile.pk),
            {"approve": False, "rejection_reason": "The photo is unreadable."},
            format="json",
        )

        assert response.status_code == 200
        worker_profile.refresh_from_db()
        assert worker_profile.rejection_reason == "The photo is unreadable."
        assert WorkerProfile.objects.filter(pk=worker_profile.pk).exists()
        worker_user.refresh_from_db()
        assert worker_user.is_approved is False

    def test_a_duplicate_aadhaar_is_surfaced_as_a_warning(
        self, authenticated_client, admin_user, worker_user, worker_profile,
        society, maid_service, django_user_model,
    ):
        """The same person moving societies looks like a double registration."""
        self.complete_kyc(authenticated_client, worker_user)

        other_user = django_user_model.objects.create_user(
            phone_number="9800000082",
            password="test-pass-12345",
            role=Role.WORKER,
            society=society,
            is_approved=False,
        )
        other_profile = WorkerProfile.objects.create(
            user=other_user, photo="workers/photos/other.jpg"
        )
        other_profile.service_types.add(maid_service)
        KycDocument.objects.create(
            worker=other_profile,
            document_image="workers/kyc/other.jpg",
            status=KycStatus.COMPLETED,
            aadhaar_hash=hash_aadhaar(VALID_AADHAAR),
            aadhaar_last4=VALID_AADHAAR[-4:],
        )

        response = authenticated_client(admin_user).get(
            reverse("v1:workers:review-detail", args=[other_profile.pk])
        )

        assert response.data["duplicate_of"] is not None
        assert response.data["duplicate_of"]["worker_id"] == worker_profile.pk

    def test_an_admin_cannot_decide_for_another_society(
        self, authenticated_client, worker_profile, django_user_model
    ):
        other_society = Society.objects.create(
            name="Blue Ridge",
            address_line="Kalyani Nagar",
            city="Pune",
            state="Maharashtra",
            pincode="411006",
            status=SocietyStatus.ACTIVE,
        )
        outside_admin = django_user_model.objects.create_user(
            phone_number="9800000083",
            password="test-pass-12345",
            role=Role.SOCIETY_ADMIN,
            society=other_society,
            is_approved=True,
        )

        response = authenticated_client(outside_admin).post(
            self.decide_url(worker_profile.pk), {"approve": True}, format="json"
        )
        assert response.status_code == 404

    def test_a_worker_cannot_approve_themselves(
        self, authenticated_client, worker_user, worker_profile
    ):
        response = authenticated_client(worker_user).post(
            self.decide_url(worker_profile.pk), {"approve": True}, format="json"
        )
        assert response.status_code == 403

    def test_a_resident_cannot_see_the_approval_queue(
        self, authenticated_client, resident_user
    ):
        response = authenticated_client(resident_user).get(
            reverse("v1:workers:review-pending")
        )
        assert response.status_code == 403
