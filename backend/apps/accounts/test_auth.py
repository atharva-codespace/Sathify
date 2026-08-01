"""
Module 1 — registration, login, JWT lifecycle and OTP tests.

The security-critical assertions here are the ones about *privilege*: that a
self-registering user cannot choose their own role, cannot approve themselves,
and cannot create staff accounts. Those are the failures that would matter.
"""

import pytest
from django.urls import reverse
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken

from apps.accounts.models import DeviceSession, OtpCode, OtpPurpose, Role, User
from apps.societies.models import Society, SocietyStatus

pytestmark = pytest.mark.django_db


def _register_payload(**overrides):
    payload = {
        "phone_number": "9812345670",
        "first_name": "Meera",
        "last_name": "Joshi",
        "password": "str0ng-pass-word",
        "password_confirm": "str0ng-pass-word",
    }
    payload.update(overrides)
    return payload


# ---------------------------------------------------------------------------
# 1.1 Registration
# ---------------------------------------------------------------------------


class TestResidentRegistration:
    def test_resident_can_self_register(self, api_client, society):
        response = api_client.post(
            reverse("v1:accounts:register-resident"),
            _register_payload(society=society.id),
            format="json",
        )
        assert response.status_code == 201

        user = User.objects.get(phone_number="9812345670")
        assert user.role == Role.RESIDENT
        assert user.society_id == society.id

    def test_new_resident_is_not_approved(self, api_client, society):
        """Registration alone must grant nothing (SRS 3.1)."""
        response = api_client.post(
            reverse("v1:accounts:register-resident"),
            _register_payload(society=society.id),
            format="json",
        )
        assert response.data["requires_approval"] is True
        assert User.objects.get(phone_number="9812345670").is_approved is False

    def test_cannot_escalate_role_through_registration_payload(self, api_client, society):
        """A resident must not be able to register themselves as an administrator."""
        api_client.post(
            reverse("v1:accounts:register-resident"),
            _register_payload(society=society.id, role=Role.SOCIETY_ADMIN),
            format="json",
        )
        assert User.objects.get(phone_number="9812345670").role == Role.RESIDENT

    def test_cannot_self_approve_through_registration_payload(self, api_client, society):
        api_client.post(
            reverse("v1:accounts:register-resident"),
            _register_payload(society=society.id, is_approved=True),
            format="json",
        )
        assert User.objects.get(phone_number="9812345670").is_approved is False

    def test_duplicate_phone_number_is_rejected(self, api_client, society, resident_user):
        response = api_client.post(
            reverse("v1:accounts:register-resident"),
            _register_payload(society=society.id, phone_number=resident_user.phone_number),
            format="json",
        )
        assert response.status_code == 400
        assert "phone_number" in response.data["error"]["details"]

    def test_mismatched_passwords_are_rejected(self, api_client, society):
        response = api_client.post(
            reverse("v1:accounts:register-resident"),
            _register_payload(society=society.id, password_confirm="something-else"),
            format="json",
        )
        assert response.status_code == 400
        assert "password_confirm" in response.data["error"]["details"]

    def test_weak_password_is_rejected(self, api_client, society):
        response = api_client.post(
            reverse("v1:accounts:register-resident"),
            _register_payload(society=society.id, password="12345678", password_confirm="12345678"),
            format="json",
        )
        assert response.status_code == 400

    def test_invalid_phone_number_is_rejected(self, api_client, society):
        response = api_client.post(
            reverse("v1:accounts:register-resident"),
            _register_payload(society=society.id, phone_number="12345"),
            format="json",
        )
        assert response.status_code == 400

    def test_cannot_register_into_an_unverified_society(self, api_client):
        """A society still pending verification must not accumulate residents."""
        pending = Society.objects.create(
            name="Unverified Heights", address_line="X", city="Pune",
            state="MH", pincode="411001", status=SocietyStatus.PENDING,
        )
        response = api_client.post(
            reverse("v1:accounts:register-resident"),
            _register_payload(society=pending.id),
            format="json",
        )
        assert response.status_code == 400
        assert "society" in response.data["error"]["details"]


class TestWorkerAndAdminRegistration:
    def test_worker_self_registration_sets_worker_role(self, api_client, society):
        response = api_client.post(
            reverse("v1:accounts:register-worker"),
            _register_payload(society=society.id),
            format="json",
        )
        assert response.status_code == 201
        user = User.objects.get(phone_number="9812345670")
        assert user.role == Role.WORKER
        assert user.is_approved is False

    def test_society_admin_registers_without_a_society(self, api_client):
        """The admin registers their society separately, in Module 2.1."""
        response = api_client.post(
            reverse("v1:accounts:register-admin"), _register_payload(), format="json"
        )
        assert response.status_code == 201
        user = User.objects.get(phone_number="9812345670")
        assert user.role == Role.SOCIETY_ADMIN
        assert user.society_id is None
        assert user.is_approved is False


class TestStaffCreation:
    """Guards and admins are created by an administrator, never self-registered."""

    def _payload(self, **overrides):
        payload = {
            "phone_number": "9812345699",
            "first_name": "Suresh",
            "last_name": "Kumar",
            "role": Role.GUARD,
            "password": "str0ng-pass-word",
        }
        payload.update(overrides)
        return payload

    def test_admin_creates_a_preapproved_guard_in_own_society(
        self, admin_user, authenticated_client
    ):
        response = authenticated_client(admin_user).post(
            reverse("v1:accounts:staff-create"), self._payload(), format="json"
        )
        assert response.status_code == 201

        guard = User.objects.get(phone_number="9812345699")
        assert guard.role == Role.GUARD
        assert guard.is_approved is True  # operational staff start usable
        assert guard.society_id == admin_user.society_id

    def test_society_is_taken_from_the_admin_not_the_payload(
        self, admin_user, authenticated_client, society
    ):
        """An admin must not be able to mint staff for another society."""
        other = Society.objects.create(
            name="Other Society", address_line="Y", city="Mumbai",
            state="MH", pincode="400001", status=SocietyStatus.ACTIVE,
        )
        authenticated_client(admin_user).post(
            reverse("v1:accounts:staff-create"),
            self._payload(society=other.id),
            format="json",
        )
        assert User.objects.get(phone_number="9812345699").society_id == society.id

    def test_admin_cannot_create_a_resident_or_worker(self, admin_user, authenticated_client):
        response = authenticated_client(admin_user).post(
            reverse("v1:accounts:staff-create"),
            self._payload(role=Role.RESIDENT),
            format="json",
        )
        assert response.status_code == 400

    @pytest.mark.parametrize("fixture", ["resident_user", "worker_user", "guard_user"])
    def test_non_admins_cannot_create_staff(self, request, fixture, authenticated_client):
        user = request.getfixturevalue(fixture)
        response = authenticated_client(user).post(
            reverse("v1:accounts:staff-create"), self._payload(), format="json"
        )
        assert response.status_code == 403

    def test_anonymous_cannot_create_staff(self, api_client):
        response = api_client.post(
            reverse("v1:accounts:staff-create"), self._payload(), format="json"
        )
        assert response.status_code == 401


# ---------------------------------------------------------------------------
# 1.2 JWT lifecycle
# ---------------------------------------------------------------------------


class TestLogin:
    def test_login_returns_tokens_and_profile(self, api_client, resident_user):
        response = api_client.post(
            reverse("v1:accounts:login"),
            {"phone_number": resident_user.phone_number, "password": "test-pass-12345"},
            format="json",
        )
        assert response.status_code == 200
        assert "access" in response.data and "refresh" in response.data
        assert response.data["user"]["role"] == Role.RESIDENT

    def test_access_token_carries_role_and_society_claims(self, api_client, guard_user):
        """The guard app reads these offline; they must be in the token."""
        from rest_framework_simplejwt.tokens import AccessToken

        response = api_client.post(
            reverse("v1:accounts:login"),
            {"phone_number": guard_user.phone_number, "password": "test-pass-12345"},
            format="json",
        )
        token = AccessToken(response.data["access"])
        assert token["role"] == Role.GUARD
        assert token["society_id"] == guard_user.society_id
        assert token["is_approved"] is True

    def test_wrong_password_is_rejected(self, api_client, resident_user):
        response = api_client.post(
            reverse("v1:accounts:login"),
            {"phone_number": resident_user.phone_number, "password": "wrong"},
            format="json",
        )
        assert response.status_code == 401

    def test_unapproved_user_can_still_sign_in(self, api_client, django_user_model, society):
        """They must reach a 'pending approval' screen, not a dead end."""
        user = django_user_model.objects.create_user(
            phone_number="9811111111", password="test-pass-12345",
            role=Role.WORKER, society=society, is_approved=False,
        )
        response = api_client.post(
            reverse("v1:accounts:login"),
            {"phone_number": user.phone_number, "password": "test-pass-12345"},
            format="json",
        )
        assert response.status_code == 200
        assert response.data["user"]["is_approved"] is False

    def test_login_opens_a_device_session(self, api_client, resident_user):
        api_client.post(
            reverse("v1:accounts:login"),
            {
                "phone_number": resident_user.phone_number,
                "password": "test-pass-12345",
                "device": {"device_id": "abc-123", "device_name": "Pixel 7", "platform": "android"},
            },
            format="json",
        )
        session = DeviceSession.objects.get(user=resident_user, device_id="abc-123")
        assert session.device_name == "Pixel 7"
        assert session.is_revoked is False


class TestTokenRefreshAndLogout:
    def _login(self, api_client, user):
        return api_client.post(
            reverse("v1:accounts:login"),
            {"phone_number": user.phone_number, "password": "test-pass-12345"},
            format="json",
        ).data

    def test_refresh_returns_a_new_access_token(self, api_client, resident_user):
        tokens = self._login(api_client, resident_user)
        response = api_client.post(
            reverse("v1:accounts:token-refresh"),
            {"refresh": tokens["refresh"]},
            format="json",
        )
        assert response.status_code == 200
        assert "access" in response.data

    def test_logout_blacklists_the_refresh_token(self, api_client, resident_user, authenticated_client):
        tokens = self._login(api_client, resident_user)
        client = authenticated_client(resident_user)

        response = client.post(
            reverse("v1:accounts:logout"), {"refresh": tokens["refresh"]}, format="json"
        )
        assert response.status_code == 205
        assert BlacklistedToken.objects.exists()

    def test_blacklisted_refresh_token_cannot_be_reused(
        self, api_client, resident_user, authenticated_client
    ):
        """Otherwise a 'signed out' device keeps working for up to 30 days."""
        tokens = self._login(api_client, resident_user)
        authenticated_client(resident_user).post(
            reverse("v1:accounts:logout"), {"refresh": tokens["refresh"]}, format="json"
        )
        response = api_client.post(
            reverse("v1:accounts:token-refresh"), {"refresh": tokens["refresh"]}, format="json"
        )
        assert response.status_code == 401

    def test_logout_without_refresh_token_is_rejected(self, resident_user, authenticated_client):
        response = authenticated_client(resident_user).post(
            reverse("v1:accounts:logout"), {}, format="json"
        )
        assert response.status_code == 400

    def test_logout_requires_authentication(self, api_client):
        response = api_client.post(reverse("v1:accounts:logout"), {}, format="json")
        assert response.status_code == 401


# ---------------------------------------------------------------------------
# Profile
# ---------------------------------------------------------------------------


class TestMeEndpoint:
    def test_returns_the_callers_own_profile(self, worker_user, authenticated_client):
        response = authenticated_client(worker_user).get(reverse("v1:accounts:me"))
        assert response.status_code == 200
        assert response.data["phone_number"] == worker_user.phone_number

    def test_requires_authentication(self, api_client):
        assert api_client.get(reverse("v1:accounts:me")).status_code == 401

    def test_can_update_own_editable_fields(self, worker_user, authenticated_client):
        response = authenticated_client(worker_user).patch(
            reverse("v1:accounts:me"),
            {"first_name": "Rahulkumar", "preferred_language": "hi"},
            format="json",
        )
        assert response.status_code == 200
        worker_user.refresh_from_db()
        assert worker_user.first_name == "Rahulkumar"
        assert worker_user.preferred_language == "hi"

    def test_cannot_escalate_role_or_approval_via_patch(self, worker_user, authenticated_client):
        """The single most damaging bug this module could ship."""
        authenticated_client(worker_user).patch(
            reverse("v1:accounts:me"),
            {"role": Role.SOCIETY_ADMIN, "is_approved": True, "society": None},
            format="json",
        )
        worker_user.refresh_from_db()
        assert worker_user.role == Role.WORKER

    def test_cannot_move_self_to_another_society(self, worker_user, authenticated_client):
        other = Society.objects.create(
            name="Elsewhere", address_line="Z", city="Delhi",
            state="DL", pincode="110001", status=SocietyStatus.ACTIVE,
        )
        original = worker_user.society_id
        authenticated_client(worker_user).patch(
            reverse("v1:accounts:me"), {"society": other.id}, format="json"
        )
        worker_user.refresh_from_db()
        assert worker_user.society_id == original


class TestPasswordChange:
    def test_password_can_be_changed(self, resident_user, authenticated_client):
        response = authenticated_client(resident_user).post(
            reverse("v1:accounts:password-change"),
            {"current_password": "test-pass-12345", "new_password": "brand-new-pass-99"},
            format="json",
        )
        assert response.status_code == 200
        resident_user.refresh_from_db()
        assert resident_user.check_password("brand-new-pass-99")

    def test_wrong_current_password_is_rejected(self, resident_user, authenticated_client):
        response = authenticated_client(resident_user).post(
            reverse("v1:accounts:password-change"),
            {"current_password": "not-it", "new_password": "brand-new-pass-99"},
            format="json",
        )
        assert response.status_code == 400

    def test_changing_password_revokes_other_sessions(
        self, api_client, resident_user, authenticated_client
    ):
        """If the change was prompted by a compromise, live sessions defeat it."""
        api_client.post(
            reverse("v1:accounts:login"),
            {
                "phone_number": resident_user.phone_number,
                "password": "test-pass-12345",
                "device": {"device_id": "old-device"},
            },
            format="json",
        )
        authenticated_client(resident_user).post(
            reverse("v1:accounts:password-change"),
            {"current_password": "test-pass-12345", "new_password": "brand-new-pass-99"},
            format="json",
        )
        assert DeviceSession.objects.get(device_id="old-device").is_revoked is True


# ---------------------------------------------------------------------------
# 1.4 OTP
# ---------------------------------------------------------------------------


class TestOtp:
    def test_requesting_an_otp_creates_a_hashed_code(self, api_client):
        response = api_client.post(
            reverse("v1:accounts:otp-request"),
            {"phone_number": "9877777777", "purpose": OtpPurpose.REGISTRATION},
            format="json",
        )
        assert response.status_code == 200

        otp = OtpCode.objects.get(phone_number="9877777777")
        # The plaintext must never be recoverable from the database.
        assert otp.code_hash and len(otp.code_hash) > 20
        assert not otp.code_hash.isdigit()

    def test_response_does_not_reveal_whether_the_number_is_registered(
        self, api_client, resident_user
    ):
        """Otherwise this endpoint becomes a user-enumeration oracle."""
        known = api_client.post(
            reverse("v1:accounts:otp-request"),
            {"phone_number": resident_user.phone_number},
            format="json",
        )
        unknown = api_client.post(
            reverse("v1:accounts:otp-request"), {"phone_number": "9876500000"}, format="json"
        )
        assert known.data == unknown.data

    def test_correct_code_verifies_and_marks_phone_verified(self, api_client, django_user_model, society):
        user = django_user_model.objects.create_user(
            phone_number="9877777778", password="test-pass-12345",
            role=Role.WORKER, society=society,
        )
        _otp, plaintext = OtpCode.generate("9877777778", OtpPurpose.REGISTRATION)

        response = api_client.post(
            reverse("v1:accounts:otp-verify"),
            {"phone_number": "9877777778", "code": plaintext},
            format="json",
        )
        assert response.status_code == 200
        user.refresh_from_db()
        assert user.is_phone_verified is True

    def test_wrong_code_is_rejected(self, api_client):
        OtpCode.generate("9877777779", OtpPurpose.REGISTRATION)
        response = api_client.post(
            reverse("v1:accounts:otp-verify"),
            {"phone_number": "9877777779", "code": "000000"},
            format="json",
        )
        assert response.status_code == 400

    def test_resend_cooldown_is_enforced(self, api_client):
        payload = {"phone_number": "9877777780"}
        api_client.post(reverse("v1:accounts:otp-request"), payload, format="json")
        second = api_client.post(reverse("v1:accounts:otp-request"), payload, format="json")

        assert second.status_code == 429
        assert second.data["error"]["details"]["retry_after_seconds"] > 0

    def test_code_cannot_be_reused(self, api_client):
        _otp, plaintext = OtpCode.generate("9877777781", OtpPurpose.REGISTRATION)
        body = {"phone_number": "9877777781", "code": plaintext}

        assert api_client.post(reverse("v1:accounts:otp-verify"), body, format="json").status_code == 200
        assert api_client.post(reverse("v1:accounts:otp-verify"), body, format="json").status_code == 400

    def test_attempts_are_capped_to_prevent_brute_force(self):
        """A 6-digit code has only 10^6 possibilities; the cap is what protects it."""
        otp, plaintext = OtpCode.generate("9877777782", OtpPurpose.REGISTRATION)

        for _ in range(OtpCode.MAX_ATTEMPTS):
            assert otp.verify("000000") is False

        # Even the correct code is refused once attempts are exhausted.
        assert otp.verify(plaintext) is False

    def test_generating_a_new_code_supersedes_the_previous_one(self):
        _first, first_code = OtpCode.generate("9877777783", OtpPurpose.REGISTRATION)
        OtpCode.generate("9877777783", OtpPurpose.REGISTRATION)

        from apps.accounts.services import verify_otp

        assert verify_otp("9877777783", OtpPurpose.REGISTRATION, first_code) is False

    def test_expired_code_is_rejected(self):
        from datetime import timedelta

        from django.utils import timezone

        otp, plaintext = OtpCode.generate("9877777784", OtpPurpose.REGISTRATION)
        otp.expires_at = timezone.now() - timedelta(minutes=1)
        otp.save(update_fields=["expires_at"])

        assert otp.verify(plaintext) is False
