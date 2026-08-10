"""
Shared pytest fixtures available to every module's tests.

Keeping role fixtures here means a test in any module can obtain an
authenticated client for any of the four roles in one line, which matters when
four people are writing tests in parallel across different apps.
"""

import re

import pytest
from django.urls import reverse
from rest_framework.test import APIClient

from apps.accounts.models import OtpCode, OtpPurpose, Role
from apps.societies.models import Society, SocietyStatus


@pytest.fixture
def api_client():
    """An unauthenticated DRF test client."""
    return APIClient()


# ---------------------------------------------------------------------------
# OTP helpers
#
# The plaintext code is handed to the SMS backend exactly once and never
# persisted, so a test that needs to read one intercepts delivery rather than
# reaching for a hash it is not meant to be able to reverse. Intercepting is how
# a real user gets the code too, which makes it the honest seam to test at.
# ---------------------------------------------------------------------------


class RecordingSMSBackend:
    """Captures what would have been texted, so tests can read the code."""

    def __init__(self):
        self.sent = []

    def send(self, phone_number, message):
        self.sent.append((phone_number, message))

    def code_for(self, phone_number):
        """The most recent code texted to ``phone_number``."""
        for number, message in reversed(self.sent):
            if number == phone_number:
                match = re.search(rf"\b(\d{{{OtpCode.CODE_LENGTH}}})\b", message)
                assert match, f"no code found in the message sent to {number}"
                return match.group(1)
        raise AssertionError(f"no code was sent to {phone_number}")


@pytest.fixture
def otp_outbox(monkeypatch):
    """Intercepts OTP delivery for the duration of one test."""
    outbox = RecordingSMSBackend()
    monkeypatch.setattr("apps.accounts.services.get_sms_backend", lambda: outbox)
    return outbox


@pytest.fixture
def request_otp_code(api_client, otp_outbox):
    """Factory: ``request_otp_code(phone, purpose)`` -> the plaintext code.

    Goes over HTTP rather than calling the service directly, so the request half
    of each flow is exercised too.
    """

    def _request(phone_number, purpose=OtpPurpose.REGISTRATION):
        response = api_client.post(
            reverse("v1:accounts:otp-request"),
            {"phone_number": phone_number, "purpose": purpose},
            format="json",
        )
        assert response.status_code == 200, response.data
        return otp_outbox.code_for(phone_number)

    return _request


@pytest.fixture
def society(db):
    """An active society. The tenancy anchor for most other fixtures."""
    return Society.objects.create(
        name="Green Meadows",
        address_line="Baner Road",
        city="Pune",
        state="Maharashtra",
        pincode="411045",
        total_towers=3,
        total_flats=180,
        status=SocietyStatus.ACTIVE,
    )


def _make_user(django_user_model, phone, role, society=None, **kwargs):
    kwargs.setdefault("is_approved", True)
    return django_user_model.objects.create_user(
        phone_number=phone,
        password="test-pass-12345",
        role=role,
        society=society,
        **kwargs,
    )


@pytest.fixture
def resident_user(db, django_user_model, society):
    return _make_user(django_user_model, "9800000001", Role.RESIDENT, society,
                      first_name="Anita", last_name="Desai")


@pytest.fixture
def worker_user(db, django_user_model, society):
    return _make_user(django_user_model, "9800000002", Role.WORKER, society,
                      first_name="Rahul", last_name="Sharma")


@pytest.fixture
def guard_user(db, django_user_model, society):
    return _make_user(django_user_model, "9800000003", Role.GUARD, society,
                      first_name="Vikram", last_name="Singh")


@pytest.fixture
def admin_user(db, django_user_model, society):
    return _make_user(django_user_model, "9800000004", Role.SOCIETY_ADMIN, society,
                      first_name="Priya", last_name="Nair", is_staff=True)


@pytest.fixture
def authenticated_client(api_client):
    """Factory: ``authenticated_client(user)`` -> client with that user's JWT.

    Uses force_authenticate rather than a real token exchange so that tests of
    other modules do not fail merely because something in Module 1 changed.
    """

    def _authenticate(user):
        client = APIClient()
        client.force_authenticate(user=user)
        return client

    return _authenticate
