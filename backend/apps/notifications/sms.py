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


def send(*, phone_number: str, title: str, body: str) -> SmsResult:
    """Send one SMS. Never raises."""
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
        config.get("MESSAGE_FIELD", "message"): compose(title=title, body=body),
        "sender": config.get("SENDER_ID", "SATHFY"),
    }

    try:
        response = requests.post(
            config["ENDPOINT"],
            data=payload,
            headers={"Authorization": f"Bearer {config['API_KEY']}"},
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        logger.warning("SMS gateway unreachable: %s", exc)
        return SmsResult(sent=False, reason="Could not reach the SMS gateway.")

    if response.status_code >= 400:
        logger.warning(
            "SMS gateway refused a message (%s): %s",
            response.status_code,
            response.text[:200],
        )
        return SmsResult(
            sent=False, reason=f"The SMS gateway refused it ({response.status_code})."
        )

    return SmsResult(sent=True)


__all__ = ["MAX_SMS_LENGTH", "SmsResult", "compose", "is_configured", "send"]
