"""Module 3 onboarding, driven the way the app drives it.

Covers the two calls that were reported failing on the deployed backend: the
"Your details" save and the Aadhaar upload. Both pass here, which is what
localised the fault to the deployment rather than the code - see
``manage.py check_media_storage``.
"""

from __future__ import annotations

import io

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from PIL import Image

from apps.accounts.models import Role
from apps.workers.models import KycDocument, WorkerProfile


def _image_bytes(width=900, height=600, fmt="JPEG"):
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), (200, 200, 200)).save(buffer, format=fmt)
    return buffer.getvalue()


def _upload(name="photo.jpg", content_type="image/jpeg"):
    return SimpleUploadedFile(name, _image_bytes(), content_type=content_type)


@pytest.fixture
def worker_user(db, django_user_model, society):
    return django_user_model.objects.create_user(
        phone_number="9811111101", password="test-pass-12345",
        role=Role.WORKER, society=society, first_name="Sunita", last_name="D",
        is_approved=False,
    )


@pytest.mark.django_db
def test_create_profile_with_photo(authenticated_client, worker_user):
    response = authenticated_client(worker_user).post(
        reverse("v1:workers:my-profile"),
        {
            "years_of_experience": 3,
            "is_available": True,
            "bio": "Ten years of house cleaning.",
            "languages_spoken": "Hindi, Marathi",
            "photo": _upload(),
        },
        format="multipart",
    )
    print("\n  CREATE:", response.status_code, str(response.data)[:400])
    assert response.status_code == 201, response.data
    assert WorkerProfile.objects.get(user=worker_user).photo


@pytest.mark.django_db
def test_patch_the_details_screen_exactly_as_the_app_sends_it(
    authenticated_client, worker_user
):
    """Screenshot 2: the 'Your details' save returning 'something went wrong
    on our side'."""
    client = authenticated_client(worker_user)
    client.post(
        reverse("v1:workers:my-profile"),
        {"years_of_experience": 1, "is_available": True, "photo": _upload()},
        format="multipart",
    )

    response = client.patch(
        reverse("v1:workers:my-profile"),
        {
            "years_of_experience": 3,
            "is_available": True,
            "bio": "Anything a resident should know",
            "languages_spoken": "Hindi",
            "available_from": "10:00",
            "available_until": "16:00",
        },
        format="multipart",
    )
    print("\n  PATCH:", response.status_code, str(response.data)[:400])
    assert response.status_code == 200, response.data


@pytest.mark.django_db
def test_patch_with_no_optional_fields_at_all(authenticated_client, worker_user):
    """The screenshot shows an empty bio and the hours untouched."""
    client = authenticated_client(worker_user)
    client.post(
        reverse("v1:workers:my-profile"),
        {"years_of_experience": 1, "is_available": True, "photo": _upload()},
        format="multipart",
    )

    response = client.patch(
        reverse("v1:workers:my-profile"),
        {"years_of_experience": 0, "is_available": True},
        format="multipart",
    )
    print("\n  PATCH minimal:", response.status_code, str(response.data)[:400])
    assert response.status_code == 200, response.data


@pytest.mark.django_db
def test_upload_aadhaar_document(authenticated_client, worker_user):
    """Screenshot 3: 'We could not save your document just now.'"""
    client = authenticated_client(worker_user)
    client.post(
        reverse("v1:workers:my-profile"),
        {"years_of_experience": 1, "is_available": True, "photo": _upload()},
        format="multipart",
    )

    response = client.post(
        reverse("v1:workers:kyc-upload"),
        {
            "document": _upload("aadhaar.jpg"),
            "consent": True,
        },
        format="multipart",
    )
    print("\n  KYC:", response.status_code, str(response.data)[:600])
    assert response.status_code in (200, 201), response.data
    assert KycDocument.objects.filter(worker__user=worker_user).exists()
