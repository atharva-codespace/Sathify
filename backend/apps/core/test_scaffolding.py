"""
Scaffolding smoke tests.

These verify that the project skeleton itself is sound — settings load, all
thirteen module apps are registered, the custom user model is wired correctly,
and the shared error/pagination contracts behave as the Flutter client expects.

They are deliberately cheap and dependency-free so they keep passing as every
later module lands.
"""

import pytest
from django.apps import apps as django_apps
from django.conf import settings
from django.contrib.auth import get_user_model
from django.urls import reverse

from apps.accounts.models import Role

User = get_user_model()

EXPECTED_MODULE_APPS = [
    "apps.core",
    "apps.accounts",
    "apps.societies",
    "apps.workers",
    "apps.hiring",
    "apps.bookings",
    "apps.scheduling",
    "apps.attendance",
    "apps.payments",
    "apps.ratings",
    "apps.notifications",
    "apps.administration",
    "apps.ai_services",
]


class TestProjectConfiguration:
    def test_every_module_app_is_registered(self):
        installed = set(settings.INSTALLED_APPS)
        missing = [app for app in EXPECTED_MODULE_APPS if app not in installed]
        assert not missing, f"Module apps missing from INSTALLED_APPS: {missing}"

    def test_all_apps_load(self):
        """Each module app must be importable with the 'apps.' label prefix."""
        for label in EXPECTED_MODULE_APPS:
            short = label.split(".")[-1]
            assert django_apps.get_app_config(short) is not None

    def test_custom_user_model_is_active(self):
        assert settings.AUTH_USER_MODEL == "accounts.User"
        assert User.USERNAME_FIELD == "phone_number"

    def test_api_defaults_deny_anonymous_access(self):
        """Endpoints must be authenticated by default, not public by default."""
        assert (
            "rest_framework.permissions.IsAuthenticated"
            in settings.REST_FRAMEWORK["DEFAULT_PERMISSION_CLASSES"]
        )

    def test_jwt_rotation_and_blacklist_enabled(self):
        """Required so logout / lost-device revocation actually invalidates."""
        assert settings.SIMPLE_JWT["ROTATE_REFRESH_TOKENS"] is True
        assert settings.SIMPLE_JWT["BLACKLIST_AFTER_ROTATION"] is True
        assert "rest_framework_simplejwt.token_blacklist" in settings.INSTALLED_APPS


class TestAiLayerConfiguration:
    """The four-tier fallback chain is configuration, so assert its shape here."""

    def test_exactly_four_tiers_in_documented_order(self):
        names = [tier["name"] for tier in settings.AI_SETTINGS["TIERS"]]
        assert names == ["gemini", "groq", "openrouter", "huggingface"]

    def test_xai_grok_is_not_in_the_chain(self):
        """xAI's Grok has no durable no-strings free tier; it must stay out."""
        names = " ".join(tier["name"] for tier in settings.AI_SETTINGS["TIERS"])
        assert "xai" not in names.lower()
        assert "grok" not in names.lower().replace("groq", "")

    def test_every_tier_reads_model_and_endpoint_from_config(self):
        for tier in settings.AI_SETTINGS["TIERS"]:
            assert tier["model"], f"{tier['name']} has no configurable model id"
            assert tier["endpoint"], f"{tier['name']} has no configurable endpoint"

    def test_openrouter_model_id_is_a_free_variant(self):
        openrouter = settings.AI_SETTINGS["TIERS"][2]
        assert openrouter["model"].endswith(":free")

    def test_openrouter_daily_cap_matches_free_tier_ceiling(self):
        openrouter = settings.AI_SETTINGS["TIERS"][2]
        assert openrouter["daily_request_cap"] <= 50
        assert openrouter["per_minute_cap"] <= 20


class TestOcrAndFaceConfiguration:
    def test_paddleocr_is_primary_with_easyocr_fallback(self):
        assert settings.OCR_SETTINGS["PRIMARY_ENGINE"] == "paddleocr"
        assert settings.OCR_SETTINGS["FALLBACK_ENGINE"] == "easyocr"

    def test_ocr_confidence_threshold_is_085(self):
        """Below this, a critical field must never be silently auto-filled."""
        assert settings.OCR_SETTINGS["CONFIDENCE_THRESHOLD"] == pytest.approx(0.85)

    def test_face_verification_always_permits_manual_override(self):
        """A failed match must never be the sole reason a worker is turned away."""
        assert settings.FACE_SETTINGS["ALLOW_MANUAL_OVERRIDE"] is True


class TestRazorpayConfiguration:
    def test_test_mode_is_the_default(self):
        assert settings.RAZORPAY_SETTINGS["TEST_MODE"] is True


@pytest.mark.django_db
class TestUserModel:
    def test_create_user_with_phone_number(self):
        user = User.objects.create_user(
            phone_number="9876543210", password="secret-pass-1", role=Role.WORKER
        )
        assert user.phone_number == "9876543210"
        assert user.check_password("secret-pass-1")
        assert user.is_worker
        assert not user.is_resident

    def test_new_users_are_unapproved_by_default(self):
        """Registration alone must grant nothing (SRS 3.1, 3.2)."""
        user = User.objects.create_user(
            phone_number="9876543211", password="secret-pass-1", role=Role.RESIDENT
        )
        assert user.is_approved is False

    def test_create_user_without_phone_number_is_rejected(self):
        with pytest.raises(ValueError):
            User.objects.create_user(phone_number="", password="x", role=Role.RESIDENT)

    def test_superuser_is_staff_and_preapproved(self):
        admin = User.objects.create_superuser(
            phone_number="9999999999", password="secret-pass-1"
        )
        assert admin.is_staff and admin.is_superuser
        assert admin.is_approved is True

    def test_approve_is_idempotent(self, society):
        user = User.objects.create_user(
            phone_number="9876543212", password="secret-pass-1",
            role=Role.WORKER, society=society,
        )
        user.approve()
        first_timestamp = user.approved_at
        user.approve()
        assert user.approved_at == first_timestamp

    def test_role_predicates_are_mutually_exclusive(self):
        user = User.objects.create_user(
            phone_number="9876543213", password="secret-pass-1", role=Role.GUARD
        )
        flags = [user.is_resident, user.is_worker, user.is_guard, user.is_society_admin]
        assert sum(flags) == 1


@pytest.mark.django_db
class TestHealthAndSchema:
    def test_health_endpoint_is_public(self, api_client):
        response = api_client.get(reverse("health-check"))
        assert response.status_code == 200
        assert response.json()["status"] == "ok"

    def test_openapi_schema_generates(self, api_client, admin_user, authenticated_client):
        """A broken schema breaks the Flutter client contract, so assert it builds."""
        response = authenticated_client(admin_user).get(reverse("schema"))
        assert response.status_code == 200
