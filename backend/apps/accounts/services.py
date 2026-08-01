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
from django.utils import timezone

from .models import DeviceSession, OtpCode

logger = logging.getLogger(__name__)


class SMSDeliveryError(Exception):
    """Raised when an OTP could not be handed to the delivery channel."""


class SMSBackend:
    """Interface every SMS delivery mechanism implements."""

    def send(self, phone_number: str, message: str) -> None:  # pragma: no cover
        raise NotImplementedError


class ConsoleSMSBackend(SMSBackend):
    """Writes the message to the application log instead of sending an SMS.

    The default everywhere. In DEBUG the code is printed prominently so a
    developer or demo operator can read it straight from the runserver output.
    """

    def send(self, phone_number: str, message: str) -> None:
        if settings.DEBUG:
            print(f"\n{'=' * 62}\n  SMS -> {phone_number}\n  {message}\n{'=' * 62}\n")
        logger.info("SMS (console backend) to %s: %s", phone_number, message)


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
    """Return the configured SMS backend (console unless overridden)."""
    backend_path = getattr(settings, "SMS_BACKEND", "console")
    return {
        "console": ConsoleSMSBackend,
        "firebase": FirebasePhoneAuthBackend,
    }.get(backend_path, ConsoleSMSBackend)()


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
