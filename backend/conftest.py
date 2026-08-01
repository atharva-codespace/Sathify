"""
Shared pytest fixtures available to every module's tests.

Keeping role fixtures here means a test in any module can obtain an
authenticated client for any of the four roles in one line, which matters when
four people are writing tests in parallel across different apps.
"""

import pytest
from rest_framework.test import APIClient

from apps.accounts.models import Role
from apps.societies.models import Society, SocietyStatus


@pytest.fixture
def api_client():
    """An unauthenticated DRF test client."""
    return APIClient()


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
