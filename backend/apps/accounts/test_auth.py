"""
Module 1 — registration, passwordless login, JWT lifecycle and OTP tests.

The security-critical assertions here are the ones about *privilege*: that a
self-registering user cannot choose their own role, cannot approve themselves,
and cannot create staff accounts. Those are the failures that would matter.

Close behind are the ones in ``TestOtpScoping``. Sign-in is phone plus password;
the OTP exists only to prove a phone number is real, at sign-up and at password
reset. Keeping those two code families apart is what stops a registration code —
which a stranger can trigger for any number — from being worth a password reset.
"""

import pytest
from django.urls import reverse
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken

from apps.accounts.models import DeviceSession, OtpCode, OtpPurpose, Role, User
from apps.societies.models import Society, SocietyStatus

pytestmark = pytest.mark.django_db


#: The password every fixture user is created with, from conftest's _make_user.
FIXTURE_PASSWORD = "test-pass-12345"


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


def _login(api_client, user, password=FIXTURE_PASSWORD, device=None):
    body = {"phone_number": user.phone_number, "password": password}
    if device:
        body["device"] = device
    return api_client.post(reverse("v1:accounts:login"), body, format="json")


# ``otp_outbox`` and ``request_otp_code`` live in the root conftest.


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

    def test_registration_sends_a_verification_code(self, api_client, society, otp_outbox):
        """"The Check", part one: creating an account must text the user a code."""
        response = api_client.post(
            reverse("v1:accounts:register-resident"),
            _register_payload(society=society.id),
            format="json",
        )
        assert response.status_code == 201
        assert response.data["otp_sent"] is True
        assert response.data["otp_purpose"] == OtpPurpose.REGISTRATION

        assert OtpCode.objects.filter(
            phone_number="9812345670", purpose=OtpPurpose.REGISTRATION
        ).exists()
        # And it actually reached the delivery channel, not just the database.
        assert otp_outbox.code_for("9812345670")

    def test_the_chosen_password_works_for_signing_in(self, api_client, society):
        """The password set at sign-up is the credential from then on."""
        api_client.post(
            reverse("v1:accounts:register-resident"),
            _register_payload(society=society.id),
            format="json",
        )

        user = User.objects.get(phone_number="9812345670")
        assert _login(api_client, user, password="str0ng-pass-word").status_code == 200

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

    def test_registration_survives_a_throttled_code(self, api_client, society, otp_outbox):
        """The account is created either way; rolling it back would trap the number."""
        for _ in range(OtpCode.MAX_SENDS_PER_HOUR):
            OtpCode.generate("9812345670", OtpPurpose.REGISTRATION)

        response = api_client.post(
            reverse("v1:accounts:register-resident"),
            _register_payload(society=society.id),
            format="json",
        )
        assert response.status_code == 201
        assert response.data["otp_sent"] is False
        assert User.objects.filter(phone_number="9812345670").exists()

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
        response = _login(api_client, resident_user)

        assert response.status_code == 200
        assert "access" in response.data and "refresh" in response.data
        assert response.data["user"]["role"] == Role.RESIDENT

    def test_access_token_carries_role_and_society_claims(self, api_client, guard_user):
        """The guard app reads these offline; they must be in the token."""
        from rest_framework_simplejwt.tokens import AccessToken

        response = _login(api_client, guard_user)

        token = AccessToken(response.data["access"])
        assert token["role"] == Role.GUARD
        assert token["society_id"] == guard_user.society_id
        assert token["is_approved"] is True

    def test_the_returned_token_actually_authenticates(self, api_client, resident_user):
        """End to end: the password buys a token that opens a protected endpoint."""
        tokens = _login(api_client, resident_user).data

        api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens['access']}")
        me = api_client.get(reverse("v1:accounts:me"))

        assert me.status_code == 200
        assert me.data["phone_number"] == resident_user.phone_number

    def test_wrong_password_is_rejected(self, api_client, resident_user):
        assert _login(api_client, resident_user, password="wrong").status_code == 401

    def test_login_needs_no_otp(self, api_client, resident_user):
        """The code is for verifying a phone, not for signing in.

        Nobody has requested a code for this user, and sign-in works anyway.
        Were that ever to change, every returning user would be made to wait on
        an SMS the design does not intend to send them.
        """
        assert not OtpCode.objects.filter(phone_number=resident_user.phone_number).exists()
        assert _login(api_client, resident_user).status_code == 200

    def test_unapproved_user_can_still_sign_in(
        self, api_client, django_user_model, society
    ):
        """They must reach a 'pending approval' screen, not a dead end."""
        user = django_user_model.objects.create_user(
            phone_number="9811111111", password=FIXTURE_PASSWORD,
            role=Role.WORKER, society=society, is_approved=False,
        )
        response = _login(api_client, user)

        assert response.status_code == 200
        assert response.data["user"]["is_approved"] is False

    def test_unverified_phone_does_not_block_sign_in(
        self, api_client, django_user_model, society
    ):
        """Someone whose SMS never arrived must not be locked out.

        Refusing here would strand them: the code prompt is reached from inside
        the app, so a user who cannot sign in has no screen to ask for another
        code from. `IsPhoneVerified` gates the features that need it instead.
        """
        user = django_user_model.objects.create_user(
            phone_number="9811111113", password=FIXTURE_PASSWORD,
            role=Role.WORKER, society=society,
        )
        assert user.is_phone_verified is False

        response = _login(api_client, user)
        assert response.status_code == 200
        assert response.data["user"]["is_phone_verified"] is False

    def test_login_opens_a_device_session(self, api_client, resident_user):
        _login(
            api_client,
            resident_user,
            device={"device_id": "abc-123", "device_name": "Pixel 7", "platform": "android"},
        )
        session = DeviceSession.objects.get(user=resident_user, device_id="abc-123")
        assert session.device_name == "Pixel 7"
        assert session.is_revoked is False


class TestTokenRefreshAndLogout:
    def _tokens(self, api_client, user):
        return _login(api_client, user).data

    def test_refresh_returns_a_new_access_token(self, api_client, resident_user):
        tokens = self._tokens(api_client, resident_user)
        response = api_client.post(
            reverse("v1:accounts:token-refresh"),
            {"refresh": tokens["refresh"]},
            format="json",
        )
        assert response.status_code == 200
        assert "access" in response.data

    def test_refresh_token_rotates_and_the_spent_one_dies(
        self, api_client, resident_user
    ):
        """Rotation without blacklisting would leave the old token usable too."""
        tokens = self._tokens(api_client, resident_user)

        rotated = api_client.post(
            reverse("v1:accounts:token-refresh"),
            {"refresh": tokens["refresh"]},
            format="json",
        )
        assert rotated.data["refresh"] != tokens["refresh"]

        replayed = api_client.post(
            reverse("v1:accounts:token-refresh"),
            {"refresh": tokens["refresh"]},
            format="json",
        )
        assert replayed.status_code == 401

    def test_logout_blacklists_the_refresh_token(
        self, api_client, resident_user, authenticated_client
    ):
        tokens = self._tokens(api_client, resident_user)
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
        tokens = self._tokens(api_client, resident_user)
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


class TestPasswordReset:
    """"Forgot password", answered by SMS rather than an emailed link.

    Module 1.4's premise is that many domestic workers have no reliable email
    address, so a reset email would exclude a large share of the intended users.
    The phone number is the account anchor, so a code sent to it is both the
    strongest available proof and the one every user can actually complete.
    """

    def _reset(self, api_client, user, code, new_password="brand-new-pass-99", device=None):
        body = {
            "phone_number": user.phone_number,
            "code": code,
            "new_password": new_password,
        }
        if device:
            body["device"] = device
        return api_client.post(reverse("v1:accounts:password-reset"), body, format="json")

    def test_a_valid_code_sets_the_new_password(
        self, api_client, resident_user, request_otp_code
    ):
        code = request_otp_code(resident_user.phone_number, OtpPurpose.PASSWORD_RESET)

        assert self._reset(api_client, resident_user, code).status_code == 200

        resident_user.refresh_from_db()
        assert resident_user.check_password("brand-new-pass-99")

    def test_the_old_password_stops_working(
        self, api_client, resident_user, request_otp_code
    ):
        code = request_otp_code(resident_user.phone_number, OtpPurpose.PASSWORD_RESET)
        self._reset(api_client, resident_user, code)

        assert _login(api_client, resident_user).status_code == 401
        assert _login(
            api_client, resident_user, password="brand-new-pass-99"
        ).status_code == 200

    def test_reset_signs_the_user_in(self, api_client, resident_user, request_otp_code):
        """They have just proved the phone and chosen the password; making them
        type it again immediately would be ceremony."""
        code = request_otp_code(resident_user.phone_number, OtpPurpose.PASSWORD_RESET)

        response = self._reset(api_client, resident_user, code)

        assert "access" in response.data and "refresh" in response.data
        assert response.data["user"]["phone_number"] == resident_user.phone_number

    def test_reset_revokes_every_other_session(
        self, api_client, resident_user, request_otp_code
    ):
        """A forgotten password is indistinguishable from a compromised one from
        the server's side, and the cost of guessing wrong runs only one way."""
        _login(api_client, resident_user, device={"device_id": "old-phone"})
        assert DeviceSession.objects.get(device_id="old-phone").is_revoked is False

        code = request_otp_code(resident_user.phone_number, OtpPurpose.PASSWORD_RESET)
        self._reset(api_client, resident_user, code, device={"device_id": "new-phone"})

        assert DeviceSession.objects.get(device_id="old-phone").is_revoked is True
        assert DeviceSession.objects.get(device_id="new-phone").is_revoked is False

    def test_the_revoked_session_token_stops_refreshing(
        self, api_client, resident_user, request_otp_code
    ):
        """Revocation must bite, or the old device keeps working for 30 days."""
        stale = _login(api_client, resident_user, device={"device_id": "old-phone"}).data

        code = request_otp_code(resident_user.phone_number, OtpPurpose.PASSWORD_RESET)
        self._reset(api_client, resident_user, code, device={"device_id": "new-phone"})

        response = api_client.post(
            reverse("v1:accounts:token-refresh"),
            {"refresh": stale["refresh"]},
            format="json",
        )
        assert response.status_code == 401

    def test_a_wrong_code_changes_nothing(
        self, api_client, resident_user, request_otp_code
    ):
        request_otp_code(resident_user.phone_number, OtpPurpose.PASSWORD_RESET)

        assert self._reset(api_client, resident_user, "000000").status_code == 400

        resident_user.refresh_from_db()
        assert resident_user.check_password(FIXTURE_PASSWORD)

    def test_a_weak_new_password_is_rejected(
        self, api_client, resident_user, request_otp_code
    ):
        code = request_otp_code(resident_user.phone_number, OtpPurpose.PASSWORD_RESET)

        response = self._reset(api_client, resident_user, code, new_password="12345678")

        assert response.status_code == 400
        resident_user.refresh_from_db()
        assert resident_user.check_password(FIXTURE_PASSWORD)

    def test_reset_needs_no_current_password(
        self, api_client, resident_user, request_otp_code
    ):
        """The whole point: the user is here because they do not have it."""
        code = request_otp_code(resident_user.phone_number, OtpPurpose.PASSWORD_RESET)

        assert self._reset(api_client, resident_user, code).status_code == 200


class TestPasswordChange:
    """Changing a password you already know, from inside the app."""

    def test_password_can_be_changed(self, resident_user, authenticated_client):
        response = authenticated_client(resident_user).post(
            reverse("v1:accounts:password-change"),
            {"current_password": FIXTURE_PASSWORD, "new_password": "brand-new-pass-99"},
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
        _login(api_client, resident_user, device={"device_id": "old-device"})

        authenticated_client(resident_user).post(
            reverse("v1:accounts:password-change"),
            {"current_password": FIXTURE_PASSWORD, "new_password": "brand-new-pass-99"},
            format="json",
        )
        assert DeviceSession.objects.get(device_id="old-device").is_revoked is True


class TestOtpScoping:
    """The OTP proves a phone number; it never signs anybody in.

    A registration code can be triggered by anyone who knows a phone number. If
    one were also spendable on a password reset, every account would be a single
    intercepted SMS away from being taken over — which is why the two code
    families are kept strictly apart.
    """

    def test_a_registration_code_cannot_reset_a_password(
        self, api_client, resident_user, request_otp_code
    ):
        code = request_otp_code(resident_user.phone_number, OtpPurpose.REGISTRATION)

        response = api_client.post(
            reverse("v1:accounts:password-reset"),
            {
                "phone_number": resident_user.phone_number,
                "code": code,
                "new_password": "brand-new-pass-99",
            },
            format="json",
        )

        assert response.status_code == 400
        resident_user.refresh_from_db()
        assert resident_user.check_password(FIXTURE_PASSWORD)

    def test_a_reset_code_cannot_verify_a_registration(
        self, api_client, resident_user, request_otp_code
    ):
        code = request_otp_code(resident_user.phone_number, OtpPurpose.PASSWORD_RESET)

        response = api_client.post(
            reverse("v1:accounts:otp-verify"),
            {"phone_number": resident_user.phone_number, "code": code},
            format="json",
        )

        assert response.status_code == 400

    def test_there_is_no_passwordless_login_purpose(self):
        """Sign-in is the password. A code issued for it would be a second,
        weaker credential path into the same account."""
        assert "login" not in OtpPurpose.values


class TestSmsDelivery:
    """How a code actually reaches a phone.

    The console backend is a development convenience, not a delivery mechanism.
    These tests pin the boundary between the two, because the failure they guard
    against is silent: a server that looks healthy, returns 200, and sends
    nobody anything.
    """

    def _configure_gateway(self, settings, **overrides):
        settings.SMS_BACKEND = ""
        settings.SMS_SETTINGS = {
            "ENABLED": True,
            "ENDPOINT": "https://sms.example.test/send",
            "API_KEY": "test-key",
            "SENDER_ID": "SATHFY",
            "TO_FIELD": "to",
            "MESSAGE_FIELD": "message",
            "AUTH_HEADER": "Authorization",
            "AUTH_SCHEME": "Bearer",
            "EXTRA_PARAMS": {},
            **overrides,
        }

    def test_console_backend_is_used_when_no_gateway_is_configured(self, settings):
        from apps.accounts.services import ConsoleSMSBackend, get_sms_backend

        settings.SMS_BACKEND = ""
        settings.SMS_SETTINGS = {"ENABLED": False, "ENDPOINT": "", "API_KEY": ""}

        assert isinstance(get_sms_backend(), ConsoleSMSBackend)

    def test_configuring_a_gateway_is_all_it_takes_to_go_live(self, settings):
        """No second switch: a configured gateway is a used gateway.

        Any "configured but still on console" state would mean a deployment that
        believes it is texting people and is not.
        """
        from apps.accounts.services import GatewaySMSBackend, get_sms_backend

        self._configure_gateway(settings)

        assert isinstance(get_sms_backend(), GatewaySMSBackend)

    def test_the_code_reaches_the_gateway_intact(self, settings, monkeypatch):
        """Carriers match the text against a registered DLT template, so the
        body must arrive exactly as composed — not trimmed or re-titled."""
        from apps.accounts.services import request_otp
        from apps.notifications import sms as sms_gateway

        self._configure_gateway(settings)
        captured = {}

        class _Response:
            status_code = 200
            text = "ok"

        def _fake_post(endpoint, data=None, headers=None, timeout=None):
            captured.update(endpoint=endpoint, data=data, headers=headers)
            return _Response()

        monkeypatch.setattr(sms_gateway.requests, "post", _fake_post)

        request_otp("9812345670", OtpPurpose.REGISTRATION)

        assert captured["endpoint"] == "https://sms.example.test/send"
        assert captured["data"]["to"] == "9812345670"
        message = captured["data"]["message"]
        assert "is your Sathify verification code" in message
        assert "2 minutes" in message
        # The six digits must survive; a trimmed body is an undeliverable code.
        import re

        assert re.match(r"^\d{6} ", message)

    @pytest.mark.parametrize(
        ("scheme", "expected"),
        [("Bearer", "Bearer test-key"), ("", "test-key")],
    )
    def test_the_api_key_is_presented_the_way_the_gateway_expects(
        self, settings, monkeypatch, scheme, expected
    ):
        """MSG91 and Fast2SMS want the bare key; a stray "Bearer" is a 401."""
        from apps.accounts.services import request_otp
        from apps.notifications import sms as sms_gateway

        self._configure_gateway(settings, AUTH_HEADER="authkey", AUTH_SCHEME=scheme)
        captured = {}

        class _Response:
            status_code = 200
            text = "ok"

        monkeypatch.setattr(
            sms_gateway.requests,
            "post",
            lambda endpoint, data=None, headers=None, timeout=None: (
                captured.update(headers=headers) or _Response()
            ),
        )

        request_otp("9812345671", OtpPurpose.REGISTRATION)

        assert captured["headers"] == {"authkey": expected}

    def test_a_refused_send_is_reported_not_swallowed(self, api_client, settings, monkeypatch):
        """Otherwise the user waits forever for a code that was never sent."""
        from apps.notifications import sms as sms_gateway

        self._configure_gateway(settings)

        class _Response:
            status_code = 402
            text = "insufficient balance"

        monkeypatch.setattr(
            sms_gateway.requests,
            "post",
            lambda *a, **kw: _Response(),
        )

        response = api_client.post(
            reverse("v1:accounts:otp-request"),
            {"phone_number": "9812345672", "purpose": OtpPurpose.REGISTRATION},
            format="json",
        )

        assert response.status_code == 503
        assert response.data["error"]["code"] == "sms_unavailable"

    def test_registration_still_succeeds_when_the_gateway_is_down(
        self, api_client, society, settings, monkeypatch
    ):
        """The account must not be lost to a gateway outage; the user resends."""
        from apps.notifications import sms as sms_gateway

        self._configure_gateway(settings)
        monkeypatch.setattr(
            sms_gateway.requests,
            "post",
            lambda *a, **kw: (_ for _ in ()).throw(
                sms_gateway.requests.RequestException("connection refused")
            ),
        )

        response = api_client.post(
            reverse("v1:accounts:register-resident"),
            _register_payload(society=society.id),
            format="json",
        )

        assert response.status_code == 201
        assert response.data["otp_sent"] is False
        assert User.objects.filter(phone_number="9812345670").exists()

    def test_the_console_backend_does_not_log_codes_outside_debug(
        self, settings, caplog, monkeypatch
    ):
        """An OTP in a log file is a credential handed to anyone who can read it."""
        import logging

        from apps.accounts.services import ConsoleSMSBackend

        # settings.LOGGING gives the "apps" logger propagate=False, so its
        # records never reach the root handler caplog installs. Re-enabling
        # propagation for this test is what makes them visible.
        monkeypatch.setattr(logging.getLogger("apps"), "propagate", True)

        settings.DEBUG = False
        with caplog.at_level(logging.DEBUG):
            ConsoleSMSBackend().send("9812345673", "424242 is your Sathify code.")

        assert "424242" not in caplog.text
        # And it says so loudly, because nobody received that code.
        assert any(r.levelno >= logging.WARNING for r in caplog.records)

    def test_the_console_backend_still_shows_codes_in_debug(
        self, settings, caplog, monkeypatch
    ):
        """The counterpart: local development must stay workable."""
        import logging

        from apps.accounts.services import ConsoleSMSBackend

        monkeypatch.setattr(logging.getLogger("apps"), "propagate", True)

        settings.DEBUG = True
        with caplog.at_level(logging.DEBUG):
            ConsoleSMSBackend().send("9812345673", "424242 is your Sathify code.")

        assert "424242" in caplog.text


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

    def test_correct_code_verifies_and_grants_immediate_access(
        self, api_client, django_user_model, society
    ):
        """The registration flow's finish line: code in, session out."""
        user = django_user_model.objects.create_user(
            phone_number="9877777778", password=FIXTURE_PASSWORD,
            role=Role.WORKER, society=society,
        )
        _otp, plaintext = OtpCode.generate("9877777778", OtpPurpose.REGISTRATION)

        response = api_client.post(
            reverse("v1:accounts:otp-verify"),
            {"phone_number": "9877777778", "code": plaintext},
            format="json",
        )
        assert response.status_code == 200
        assert response.data["verified"] is True
        # Not just "verified" — actually signed in.
        assert "access" in response.data and "refresh" in response.data
        assert response.data["user"]["phone_number"] == "9877777778"

        user.refresh_from_db()
        assert user.is_phone_verified is True

    def test_a_valid_code_for_a_number_with_no_account_grants_nothing(self, api_client):
        """And the code is still spent, so it cannot be retried later."""
        _otp, plaintext = OtpCode.generate("9877777799", OtpPurpose.REGISTRATION)

        response = api_client.post(
            reverse("v1:accounts:otp-verify"),
            {"phone_number": "9877777799", "code": plaintext},
            format="json",
        )
        assert response.status_code == 400
        assert OtpCode.objects.get(phone_number="9877777799").is_consumed is True

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

    def test_code_cannot_be_reused(self, api_client, django_user_model, society):
        django_user_model.objects.create_user(
            phone_number="9877777781", password=FIXTURE_PASSWORD,
            role=Role.WORKER, society=society,
        )
        _otp, plaintext = OtpCode.generate("9877777781", OtpPurpose.REGISTRATION)
        body = {"phone_number": "9877777781", "code": plaintext}

        assert api_client.post(reverse("v1:accounts:otp-verify"), body, format="json").status_code == 200
        assert api_client.post(reverse("v1:accounts:otp-verify"), body, format="json").status_code == 400

    def test_attempts_are_capped_at_five(self):
        """A 6-digit code has only 10^6 possibilities; the cap is what protects it."""
        assert OtpCode.MAX_ATTEMPTS == 5

        otp, plaintext = OtpCode.generate("9877777782", OtpPurpose.REGISTRATION)

        for _ in range(OtpCode.MAX_ATTEMPTS):
            assert otp.verify("000000") is False

        # Even the correct code is refused once attempts are exhausted.
        assert otp.verify(plaintext) is False

    def test_the_cap_holds_across_http_requests(self, api_client, resident_user):
        """The cap is worthless if it only counts in-process attempts."""
        OtpCode.generate(resident_user.phone_number, OtpPurpose.PASSWORD_RESET)
        body = {
            "phone_number": resident_user.phone_number,
            "code": "000000",
            "new_password": "brand-new-pass-99",
        }

        for _ in range(OtpCode.MAX_ATTEMPTS):
            assert api_client.post(
                reverse("v1:accounts:password-reset"), body, format="json"
            ).status_code == 400

        otp = OtpCode.objects.get(phone_number=resident_user.phone_number)
        assert otp.attempts == OtpCode.MAX_ATTEMPTS
        # Exhausted: the code is now inactive whatever is sent to it.
        assert not OtpCode.objects.active().filter(pk=otp.pk).exists()

    def test_each_wrong_guess_is_durably_recorded(self, api_client, resident_user):
        """Regression: the counter must survive the request that failed.

        An earlier revision wrapped the whole of ``authenticate_with_otp`` in a
        transaction. The rejection raised inside it rolled back the very
        increment that rejection was supposed to record, so an attacker could
        guess without limit while ``attempts`` sat at zero. The cap is only as
        real as this assertion.
        """
        OtpCode.generate(resident_user.phone_number, OtpPurpose.PASSWORD_RESET)

        api_client.post(
            reverse("v1:accounts:password-reset"),
            {
                "phone_number": resident_user.phone_number,
                "code": "000000",
                "new_password": "brand-new-pass-99",
            },
            format="json",
        )

        assert OtpCode.objects.get(phone_number=resident_user.phone_number).attempts == 1

    def test_codes_are_valid_for_two_minutes(self):
        """The window is a hard requirement, not a tunable default."""
        from datetime import timedelta

        assert OtpCode.VALIDITY_MINUTES == 2

        otp, _plaintext = OtpCode.generate("9877777785", OtpPurpose.REGISTRATION)
        lifetime = otp.expires_at - otp.created_at
        assert abs(lifetime - timedelta(minutes=2)) < timedelta(seconds=5)

    def test_a_code_just_past_the_window_is_refused(self, api_client, resident_user):
        from datetime import timedelta

        from django.utils import timezone

        otp, plaintext = OtpCode.generate(
            resident_user.phone_number, OtpPurpose.PASSWORD_RESET
        )
        otp.expires_at = timezone.now() - timedelta(seconds=1)
        otp.save(update_fields=["expires_at"])

        response = api_client.post(
            reverse("v1:accounts:password-reset"),
            {
                "phone_number": resident_user.phone_number,
                "code": plaintext,
                "new_password": "brand-new-pass-99",
            },
            format="json",
        )
        assert response.status_code == 400

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
