"""
Module 10.2 — SMS fallback.

Triggered when a push fails, and for a recipient who has no smartphone at all.
That second case is the one that matters most here: the platform's edge-case
handling assumes some domestic workers will not own a smartphone, and for them
SMS is not a fallback but the only channel there is.

-------------------------------------------------------------------------------
PROVIDER-AGNOSTIC BY CONFIGURATION
-------------------------------------------------------------------------------
Indian SMS gateways — MSG91, Textlocal, Fast2SMS — all expose an authenticated
HTTP POST with slightly different field names. Those names live in
``SMS_SETTINGS`` rather than in code, so switching provider is an environment
change and no vendor SDK is pulled in for one request.

-------------------------------------------------------------------------------
DISABLED BY DEFAULT, AND HONEST ABOUT IT
-------------------------------------------------------------------------------
SMS costs real money per message and this project has no gateway account. With
it off, :func:`send` reports ``available=False`` and the notification is recorded
as undelivered rather than being quietly dropped — the in-app centre still holds
the message, which is why Module 10.3 exists alongside the push channel.

Nothing here raises, for the same reason nothing in ``push.py`` does.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT_SECONDS = 15

#: A single SMS is 160 GSM-7 characters, and far fewer once any Devanagari
#: appears (UCS-2 drops it to 70). Longer messages are split and billed per
#: part, so the body is trimmed to something that fits rather than silently
#: costing three times as much.
MAX_SMS_LENGTH = 160


@dataclass(frozen=True)
class SmsResult:
    """What one SMS attempt achieved."""

    sent: bool = False
    available: bool = True
    reason: str = ""


def is_configured() -> bool:
    config = getattr(settings, "SMS_SETTINGS", {})
    return bool(config.get("ENABLED") and config.get("ENDPOINT") and config.get("API_KEY"))


def compose(*, title: str, body: str) -> str:
    """Build the message text, trimmed to one billable SMS.

    Title first: on a feature phone the notification is read from a preview, and
    the first few words have to say what it is about.
    """
    text = f"{title}: {body}".strip()
    if len(text) <= MAX_SMS_LENGTH:
        return text
    return text[: MAX_SMS_LENGTH - 1].rstrip() + "…"


def _auth_headers(config) -> dict:
    """Present the API key the way this particular gateway expects it."""
    scheme = (config.get("AUTH_SCHEME") or "").strip()
    key = config["API_KEY"]
    return {config.get("AUTH_HEADER", "Authorization"): f"{scheme} {key}".strip()}


def send_text(*, phone_number: str, message: str) -> SmsResult:
    """Send one already-composed message. Never raises.

    The raw entry point, used where the caller owns the exact wording — an OTP
    body must not be reshaped, both because the code has to survive intact and
    because Indian carriers match the text against a registered DLT template.
    """
    if not phone_number:
        return SmsResult(available=False, reason="No phone number for this user.")

    if not is_configured():
        return SmsResult(
            available=False,
            reason="SMS is not configured on this server.",
        )

    config = settings.SMS_SETTINGS
    payload = {
        config.get("TO_FIELD", "to"): phone_number,
        config.get("MESSAGE_FIELD", "message"): message,
        "sender": config.get("SENDER_ID", "SATHFY"),
        **config.get("EXTRA_PARAMS", {}),
    }

    try:
        response = requests.post(
            config["ENDPOINT"],
            data=payload,
            headers=_auth_headers(config),
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        logger.warning("SMS gateway unreachable: %s", exc)
        return SmsResult(sent=False, reason="Could not reach the SMS gateway.")

    if response.status_code >= 400:
        # The body is logged because a gateway's rejection reason is the only
        # way to tell "wrong API key" from "unregistered DLT template", and
        # those have completely different fixes. It carries no message text.
        logger.warning(
            "SMS gateway refused a message (%s): %s",
            response.status_code,
            response.text[:200],
        )
        return SmsResult(
            sent=False, reason=f"The SMS gateway refused it ({response.status_code})."
        )

    return SmsResult(sent=True)


def send(*, phone_number: str, title: str, body: str) -> SmsResult:
    """Send one notification SMS, trimmed to a single billable message."""
    return send_text(
        phone_number=phone_number, message=compose(title=title, body=body)
    )


__all__ = [
    "MAX_SMS_LENGTH",
    "SmsResult",
    "compose",
    "is_configured",
    "send",
    "send_text",
]
