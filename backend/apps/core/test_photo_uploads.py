"""
Section 7 — photo uploads, both of them.

Two places a photo is attached: proof that a visit was completed (Module 6.6)
and evidence on a complaint (Module 11.3). Both were already modelled; what
these tests pin is the part that is easy to leave out and expensive to
discover — that the bytes actually reach storage, that an oversized upload is
refused with a message somebody can act on, and that a storage outage does not
swallow the thing being recorded.
"""

from __future__ import annotations

import datetime as dt
from io import BytesIO
from unittest.mock import patch

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.administration.models import Complaint, ComplaintCategory
from apps.core.files import MAX_PHOTO_BYTES
from apps.hiring.models import Engagement
from apps.scheduling.models import TaskCompletion
from apps.societies.models import Flat, Resident, Tower
from apps.workers.models import ServiceType, WorkerProfile

pytestmark = pytest.mark.django_db


def photo(name="proof.png", *, size_bytes=None):
    """A real PNG. ImageField verifies the bytes decode, so a stub will not do."""
    from django.core.files.uploadedfile import SimpleUploadedFile
    from PIL import Image

    buffer = BytesIO()
    Image.new("RGB", (8, 8), color="white").save(buffer, format="PNG")
    data = buffer.getvalue()

    if size_bytes is not None:
        # Pad past the limit. Trailing bytes after IEND still decode as a PNG,
        # which is what makes this a *size* test rather than a corruption one.
        data = data + b"\0" * (size_bytes - len(data))

    return SimpleUploadedFile(name, data, content_type="image/png")


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
    return Engagement.objects.create(
        society=society,
        resident=resident,
        worker=worker,
        service_type=maid_service,
        days_of_week=[0, 1, 2, 3, 4, 5, 6],
        start_time=dt.time(9, 0),
        expected_duration_minutes=90,
        monthly_rate=4000,
    )


class TestCompletionPhoto:
    def url(self):
        return reverse("v1:scheduling:mark-task-complete")

    def test_a_photo_is_stored_with_the_completion(
        self, authenticated_client, worker_user, engagement
    ):
        response = authenticated_client(worker_user).post(
            self.url(),
            {"engagement": engagement.pk, "photo": photo()},
            format="multipart",
        )

        assert response.status_code == 201
        completion = TaskCompletion.objects.get()
        assert completion.photo
        assert completion.photo.size > 0

    def test_the_photo_url_reaches_the_household_schedule(
        self, authenticated_client, worker_user, resident_user, engagement
    ):
        """Proof nobody can see is not proof."""
        authenticated_client(worker_user).post(
            self.url(),
            {"engagement": engagement.pk, "photo": photo()},
            format="multipart",
        )

        response = authenticated_client(resident_user).get(
            reverse("v1:scheduling:my-today")
        )

        assert response.data["results"][0]["completion_photo_url"]

    def test_a_photo_is_never_required(
        self, authenticated_client, worker_user, engagement
    ):
        """A flat battery or a cracked camera must not become an unpaid day."""
        response = authenticated_client(worker_user).post(
            self.url(), {"engagement": engagement.pk}, format="multipart"
        )

        assert response.status_code == 201
        assert TaskCompletion.objects.get().photo.name in {"", None}

    def test_an_oversized_photo_is_refused_with_the_limit_named(
        self, authenticated_client, worker_user, engagement
    ):
        """512 MB instance: Django buffers the upload before storage sees it."""
        response = authenticated_client(worker_user).post(
            self.url(),
            {
                "engagement": engagement.pk,
                "photo": photo(size_bytes=MAX_PHOTO_BYTES + 1024),
            },
            format="multipart",
        )

        assert response.status_code == 400
        assert "8 MB" in str(response.data)

    def test_a_retry_does_not_store_a_second_copy(
        self, authenticated_client, worker_user, engagement
    ):
        """The unique constraint decides before the file is attached."""
        client = authenticated_client(worker_user)
        payload = {"engagement": engagement.pk}

        client.post(self.url(), {**payload, "photo": photo()}, format="multipart")
        client.post(self.url(), {**payload, "photo": photo()}, format="multipart")

        assert TaskCompletion.objects.count() == 1

    def test_a_storage_outage_is_retryable_not_a_500(
        self, authenticated_client, worker_user, engagement
    ):
        """A media backend that is down must not cost a worker the record of a
        day she actually worked."""
        with patch(
            "django.core.files.storage.FileSystemStorage._save",
            side_effect=OSError("storage is down"),
        ):
            response = authenticated_client(worker_user).post(
                self.url(),
                {"engagement": engagement.pk, "photo": photo()},
                format="multipart",
            )

        assert response.status_code == 503
        assert response.data["error"]["code"] == "storage_unavailable"


class TestComplaintEvidencePhoto:
    def url(self):
        return reverse("v1:administration:complaint-list")

    def payload(self, **overrides):
        return {
            "category": ComplaintCategory.OTHER,
            "subject": "Broken tap",
            "description": "The kitchen tap has been leaking for three days.",
            **overrides,
        }

    def test_evidence_is_stored(self, authenticated_client, resident_user, resident):
        response = authenticated_client(resident_user).post(
            self.url(),
            self.payload(photo=photo("evidence.png")),
            format="multipart",
        )

        assert response.status_code == 201
        complaint = Complaint.objects.get()
        assert complaint.photo
        assert complaint.photo.size > 0

    def test_the_photo_url_comes_back(
        self, authenticated_client, resident_user, resident
    ):
        response = authenticated_client(resident_user).post(
            self.url(),
            self.payload(photo=photo("evidence.png")),
            format="multipart",
        )

        assert response.data["complaint"]["photo_url"]

    def test_evidence_is_optional(
        self, authenticated_client, resident_user, resident
    ):
        response = authenticated_client(resident_user).post(
            self.url(), self.payload(), format="multipart"
        )

        assert response.status_code == 201

    def test_an_oversized_photo_is_refused(
        self, authenticated_client, resident_user, resident
    ):
        response = authenticated_client(resident_user).post(
            self.url(),
            self.payload(photo=photo("big.png", size_bytes=MAX_PHOTO_BYTES + 1024)),
            format="multipart",
        )

        assert response.status_code == 400

    def test_a_storage_outage_does_not_swallow_the_complaint(
        self, authenticated_client, resident_user, resident
    ):
        """Least of all a safety one."""
        with patch(
            "django.core.files.storage.FileSystemStorage._save",
            side_effect=OSError("storage is down"),
        ):
            response = authenticated_client(resident_user).post(
                self.url(),
                self.payload(photo=photo("evidence.png")),
                format="multipart",
            )

        assert response.status_code == 503
        assert response.data["error"]["code"] == "storage_unavailable"


class TestStoragePaths:
    def test_uploads_are_namespaced_so_the_bucket_stays_browsable(
        self, authenticated_client, resident_user, resident
    ):
        authenticated_client(resident_user).post(
            reverse("v1:administration:complaint-list"),
            {
                "category": ComplaintCategory.OTHER,
                "subject": "Broken tap",
                "description": "Leaking for three days.",
                "photo": photo("evidence.png"),
            },
            format="multipart",
        )

        complaint = Complaint.objects.get()
        assert complaint.photo.name.startswith(
            f"complaints/society_{complaint.society_id}/"
        )

    def test_completion_photos_are_dated(
        self, authenticated_client, worker_user, engagement
    ):
        authenticated_client(worker_user).post(
            reverse("v1:scheduling:mark-task-complete"),
            {"engagement": engagement.pk, "photo": photo()},
            format="multipart",
        )

        name = TaskCompletion.objects.get().photo.name
        assert name.startswith("visits/completions/")
        assert timezone.localdate().strftime("%Y/%m") in name
