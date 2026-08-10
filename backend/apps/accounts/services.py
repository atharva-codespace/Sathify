"""
Module 1 — service layer: OTP delivery and session bookkeeping.

Business logic lives here rather than in views so that it can be unit-tested
without HTTP, and reused from a management command or a Celery task later.

------------------------------------------------------------------------------
ZERO-BUDGET NOTE — SMS HAS NO GENUINELY FREE OPTION
------------------------------------------------------------------------------
There is no SMS gateway in India that is free with no card on file. Real
per-message cost is roughly Rs 0.15-0.30, and every "free trial" either expires
or requires payment details. This is stated plainly rather than papered over.

What we do instead:

* ``ConsoleSMSBackend`` (default) prints the OTP to the server log. That is
  fully sufficient for development, testing, and a supervised demo.
* The delivery mechanism sits behind ``SMSBackend`` so a real gateway becomes a
  one-line settings change if the project is ever funded.
* The closest genuinely-free production path is **Firebase Phone Auth**, run
  client-side in Flutter. It reuses the Firebase project already needed for FCM
  in Module 10, and the app posts the resulting Firebase ID token to Django for
  verification instead of Django sending the SMS itself. Note that Firebase's
  free phone-auth quota is small and subject to change, so confirm current
  limits before depending on it. ``FirebasePhoneAuthBackend`` below marks where
  that verification would land.
"""

import logging
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.models import update_last_login
from django.db import transaction
from django.utils import timezone
from rest_framework_simplejwt.tokens import RefreshToken

from .models import DeviceSession, OtpCode, OtpPurpose, User

logger = logging.getLogger(__name__)


class SMSDeliveryError(Exception):
    """Raised when an OTP could not be handed to the delivery channel."""


class SMSBackend:
    """Interface every SMS delivery mechanism implements."""

    def send(self, phone_number: str, message: str) -> None:  # pragma: no cover
        raise NotImplementedError


class ConsoleSMSBackend(SMSBackend):
    """Writes the message to the application log instead of sending an SMS.

    The development default. In DEBUG the code is printed prominently so a
    developer or demo operator can read it straight from the runserver output.

    Outside DEBUG the body is deliberately NOT logged. An OTP is a credential,
    and a log line containing one hands account access to anybody who can read
    the logs — on Render that is anybody with dashboard access, and log
    aggregators retain it long after the two-minute window closes. The warning
    fires instead, because reaching this branch in production means real users
    are being sent codes they will never receive.
    """

    def send(self, phone_number: str, message: str) -> None:
        if settings.DEBUG:
            # flush=True because Python block-buffers stdout whenever it is not
            # a terminal, and a developer aid that appears minutes later — or
            # only once the server is stopped — is no aid at all. That bites
            # whenever runserver output is piped, redirected to a file, or
            # captured by an IDE's run panel.
            print(
                f"\n{'=' * 62}\n  SMS -> {phone_number}\n  {message}\n{'=' * 62}\n",
                flush=True,
            )
            logger.info("SMS (console backend) to %s: %s", phone_number, message)
            return

        logger.warning(
            "SMS is not configured: no message was delivered to %s. "
            "Set SMS_ENABLED and the gateway credentials, or users cannot sign up.",
            phone_number,
        )


class GatewaySMSBackend(SMSBackend):
    """Sends through the configured HTTP gateway (Module 10.2's ``SMS_SETTINGS``).

    Reuses the notifications sender rather than opening a second HTTP path to
    the same provider: that one already handles timeouts, unreachable gateways
    and refusals without raising, and a duplicate would drift from it.
    """

    def send(self, phone_number: str, message: str) -> None:
        # Local import: apps.notifications imports the user model indirectly, so
        # importing it at module scope here risks an app-registry cycle.
        from apps.notifications import sms as sms_gateway

        result = sms_gateway.send_text(phone_number=phone_number, message=message)
        if not result.sent:
            # Raised, unlike in the notification path: there a failed SMS leaves
            # the in-app record intact, but a failed OTP leaves the user with no
            # way in at all. The caller turns this into an error the user sees,
            # rather than a success they wait on forever.
            raise SMSDeliveryError(result.reason or "The SMS could not be sent.")


class FirebasePhoneAuthBackend(SMSBackend):
    """Placeholder for the Firebase-verified phone flow.

    In this model Django never sends an SMS: the Flutter app performs phone
    verification through the Firebase SDK and posts the resulting ID token,
    which the server verifies. Implemented alongside Module 10, where the
    Firebase Admin credentials are introduced.
    """

    def send(self, phone_number: str, message: str) -> None:
        raise SMSDeliveryError(
            "Firebase phone auth is client-side: the Flutter app performs "
            "verification and posts an ID token. Nothing to send from Django."
        )


def get_sms_backend() -> SMSBackend:
    """Return the SMS backend to deliver with.

    Selected from configuration rather than named explicitly, so that filling in
    the gateway credentials is all it takes for real codes to start arriving —
    there is no second switch to remember, and therefore no state where the
    gateway is configured but still unused.

    ``SMS_BACKEND`` forces a specific backend when that is wanted, which is
    mainly useful for keeping a staging deployment on the console while its
    credentials are shared with production.
    """
    override = getattr(settings, "SMS_BACKEND", "")
    if override:
        return {
            "console": ConsoleSMSBackend,
            "gateway": GatewaySMSBackend,
            "firebase": FirebasePhoneAuthBackend,
        }.get(override, ConsoleSMSBackend)()

    # Local import keeps the app-registry cycle noted on GatewaySMSBackend away.
    from apps.notifications import sms as sms_gateway

    return GatewaySMSBackend() if sms_gateway.is_configured() else ConsoleSMSBackend()


# ---------------------------------------------------------------------------
# OTP orchestration
# ---------------------------------------------------------------------------


class OtpThrottled(Exception):
    """Raised when an OTP request breaches the resend cooldown or hourly cap."""

    def __init__(self, message: str, retry_after_seconds: int = 0):
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds


def request_otp(phone_number: str, purpose: str) -> OtpCode:
    """Generate, rate-limit and dispatch an OTP.

    Rate limiting protects two different things: somebody else's phone from
    being spammed, and (once a paid gateway is in play) the SMS budget.
    """
    now = timezone.now()

    # --- Resend cooldown ---------------------------------------------------
    latest = (
        OtpCode.objects.filter(phone_number=phone_number, purpose=purpose)
        .order_by("-created_at")
        .first()
    )
    if latest:
        elapsed = (now - latest.created_at).total_seconds()
        if elapsed < OtpCode.RESEND_COOLDOWN_SECONDS:
            wait = int(OtpCode.RESEND_COOLDOWN_SECONDS - elapsed)
            raise OtpThrottled(
                f"Please wait {wait} seconds before requesting another code.",
                retry_after_seconds=wait,
            )

    # --- Hourly ceiling ----------------------------------------------------
    sent_last_hour = OtpCode.objects.filter(
        phone_number=phone_number, created_at__gte=now - timedelta(hours=1)
    ).count()
    if sent_last_hour >= OtpCode.MAX_SENDS_PER_HOUR:
        raise OtpThrottled(
            "Too many verification codes requested. Please try again in an hour.",
            retry_after_seconds=3600,
        )

    otp, plaintext = OtpCode.generate(phone_number, purpose)

    # Raises SMSDeliveryError if the gateway refuses or is unreachable. The code
    # row is left behind rather than deleted: a retry supersedes it anyway, and
    # unwinding it here would mean a failed send also reset the rate limiting
    # that stops this endpoint being used to hammer somebody else's phone.
    get_sms_backend().send(
        phone_number,
        f"{plaintext} is your Sathify verification code. "
        f"It expires in {OtpCode.VALIDITY_MINUTES} minutes. Do not share it.",
    )
    logger.info("OTP issued for %s (purpose=%s)", phone_number, purpose)
    return otp


def verify_otp(phone_number: str, purpose: str, code: str) -> bool:
    """Validate ``code`` against the newest active OTP for this phone/purpose."""
    otp = (
        OtpCode.objects.active()
        .filter(phone_number=phone_number, purpose=purpose)
        .order_by("-created_at")
        .first()
    )
    if otp is None:
        logger.info("OTP verification failed for %s: no active code", phone_number)
        return False

    verified = otp.verify(code)
    logger.info(
        "OTP verification for %s: %s", phone_number, "success" if verified else "failure"
    )
    return verified


# ---------------------------------------------------------------------------
# Passwordless authentication (Module 1.2 + 1.4)
# ---------------------------------------------------------------------------


class OtpVerificationError(Exception):
    """Raised when a code is wrong/expired, or maps to no usable account.

    Deliberately one exception with one message for every cause. Distinguishing
    "wrong code" from "no such account" would turn these endpoints into a
    user-enumeration oracle, undoing the care taken in ``OtpRequestView``.
    """

    def __init__(self, message="That code is incorrect or has expired."):
        super().__init__(message)


def build_token_pair(user) -> RefreshToken:
    """Mint a refresh token carrying Sathify's custom claims.

    Claims set here propagate to the access token automatically — simplejwt
    copies every claim except the type/exp/jti trio when deriving one from the
    other. Embedding role and society lets the Flutter app route straight to the
    right dashboard without a round trip to /me, which matters on a gate
    terminal with a poor connection.

    The claim set is shared with the password-login serializer, so a token
    issued after the registration OTP is indistinguishable from one issued at
    sign-in. Never put anything secret in a JWT: the payload is base64, not
    encrypted.
    """
    # Local import: serializers imports models, and models is imported here.
    from .serializers import token_claims_for

    token = RefreshToken.for_user(user)
    for claim, value in token_claims_for(user).items():
        token[claim] = value
    return token


def issue_session_tokens(*, user, request=None, device: dict | None = None) -> dict:
    """Issue an access/refresh pair and open the device session behind it.

    The single place tokens are minted, so every entry point — registration,
    login, recovery — produces an identical, session-backed result. A token
    issued without its ``DeviceSession`` row would be unrevocable by Module
    1.5's lost-phone path, which is the whole reason that model exists.
    """
    refresh = build_token_pair(user)
    register_device_session(user=user, refresh_token=refresh, request=request, device=device)

    if settings.SIMPLE_JWT.get("UPDATE_LAST_LOGIN"):
        # Handled by TokenObtainPairSerializer on the password flow that used to
        # live here; nothing does it for us now.
        update_last_login(None, user)

    return {"access": str(refresh.access_token), "refresh": str(refresh)}


def _verified_user_for(phone_number: str, purpose: str, code: str) -> User:
    """Check a code and return the account it belongs to, or raise.

    Note what is NOT wrapped in a transaction here or in either caller: the
    verification itself. That is load-bearing. ``verify_otp`` writes as it goes
    — it increments the attempt counter on a wrong guess and stamps
    ``consumed_at`` on a right one — and those writes must survive the failure
    that follows them. Wrapping verification and its rejection in one ``atomic``
    block (as an earlier revision did) rolls the increment back along with
    everything else, so five wrong guesses record zero attempts and the
    ``MAX_ATTEMPTS`` cap silently stops existing. The brute-force protection is
    only as real as the counter's durability.
    """
    if not verify_otp(phone_number, purpose, code):
        raise OtpVerificationError

    user = User.objects.filter(phone_number=phone_number).first()
    if user is None or not user.is_active:
        # A valid code for a number with no usable account: possible after the
        # account was deleted or deactivated mid-flow. The code stays spent,
        # which is the correct outcome — it must not survive to be retried.
        logger.warning("OTP verified for %s but no active account exists", phone_number)
        raise OtpVerificationError

    return user


def complete_registration(
    *,
    phone_number: str,
    code: str,
    request=None,
    device: dict | None = None,
) -> tuple[User, dict]:
    """Finish sign-up: verify the phone, then sign the new user in.

    Returns ``(user, tokens)``. The account already holds the password chosen
    during registration — that is what every later sign-in uses. Issuing a
    session here spares the user retyping it at the exact moment they are most
    likely to abandon sign-up.

    Only the post-verification work is atomic, where atomicity buys something:
    a half-opened session with tokens issued but no ``DeviceSession`` row would
    be unrevocable by Module 1.5's lost-phone path.

    Unapproved users are signed in successfully — they receive a token whose
    ``is_approved`` claim is false so the app can show their pending status.
    Authorisation to *act* is a separate gate (``IsApproved``); conflating the
    two would leave a pending worker staring at an error with no explanation.
    """
    user = _verified_user_for(phone_number, OtpPurpose.REGISTRATION, code)

    with transaction.atomic():
        if not user.is_phone_verified:
            user.is_phone_verified = True
            user.save(update_fields=["is_phone_verified", "updated_at"])

        tokens = issue_session_tokens(user=user, request=request, device=device)

    logger.info("Registration verified: %s (%s)", user.phone_number, user.role)
    return user, tokens


def reset_password_with_otp(
    *,
    phone_number: str,
    code: str,
    new_password: str,
    request=None,
    device: dict | None = None,
) -> tuple[User, dict]:
    """Set a new password against a valid reset code, and sign the user in.

    The replacement for a password-reset email, which this market cannot rely on
    (Module 1.4: many domestic workers have no email address). The phone is the
    account's anchor, so proving control of it is the strongest signal available.

    Every other session is revoked. A forgotten password is not usually a
    compromise, but it is indistinguishable from one from the server's side, and
    the cost of being wrong runs one way: leaving an attacker's session alive
    after the real owner has just taken their account back.
    """
    user = _verified_user_for(phone_number, OtpPurpose.PASSWORD_RESET, code)

    with transaction.atomic():
        user.set_password(new_password)
        if not user.is_phone_verified:
            user.is_phone_verified = True
        user.save(update_fields=["password", "is_phone_verified", "updated_at"])

        revoke_other_sessions(user, reason="Password reset")
        tokens = issue_session_tokens(user=user, request=request, device=device)

    logger.info("Password reset completed for %s", user.phone_number)
    return user, tokens


def revoke_other_sessions(user, *, reason: str, keep_jti: str = "") -> int:
    """Revoke every live session for ``user``, optionally sparing one.

    Returns how many were ended. Blacklists each session's refresh token, so a
    revoked device stops working at its next refresh rather than running to the
    30-day expiry.
    """
    stale = list(
        DeviceSession.objects.filter(user=user, revoked_at__isnull=True).exclude(
            refresh_token_jti=keep_jti
        )
        if keep_jti
        else DeviceSession.objects.filter(user=user, revoked_at__isnull=True)
    )
    for session in stale:
        session.revoke(reason=reason)
    if stale:
        logger.info("Revoked %s session(s) for %s: %s", len(stale), user.phone_number, reason)
    return len(stale)


# ---------------------------------------------------------------------------
# Device sessions
# ---------------------------------------------------------------------------


def register_device_session(*, user, refresh_token, request=None, device: dict | None = None):
    """Record (or refresh) the device session backing a newly issued token pair.

    Guards are held to a SINGLE active session: signing in at a gate terminal
    revokes any other device that guard is signed in on. Two guard devices
    active at once would let the same worker be admitted twice, and would make
    the entry log ambiguous about who authorised what.
    """
    device = device or {}
    device_id = device.get("device_id") or "unknown-device"

    session, _created = DeviceSession.objects.update_or_create(
        user=user,
        device_id=device_id,
        defaults={
            "device_name": device.get("device_name", "")[:120],
            "platform": device.get("platform", "")[:20],
            "fcm_token": device.get("fcm_token", "")[:255],
            "refresh_token_jti": refresh_token.get("jti", ""),
            "ip_address": _client_ip(request),
            "last_seen_at": timezone.now(),
            "revoked_at": None,
            "revoked_reason": "",
        },
    )

    if user.is_guard:
        others = DeviceSession.objects.filter(
            user=user, revoked_at__isnull=True
        ).exclude(pk=session.pk)
        for other in others:
            other.revoke(reason="Superseded by a newer guard sign-in")

    return session


def _client_ip(request):
    """Best-effort client IP, honouring Render's proxy header."""
    if request is None:
        return None
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR")
