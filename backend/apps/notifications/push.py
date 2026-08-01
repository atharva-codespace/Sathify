"""
Module 10.1 — Firebase Cloud Messaging, HTTP v1.

-------------------------------------------------------------------------------
WHY THE REST API AND NOT firebase-admin
-------------------------------------------------------------------------------
``firebase-admin`` pulls in a large dependency tree for what is, in the end, one
authenticated POST per message. On a 512 MB instance that already cannot hold
the CV stack (docs/free-tier-constraints.md §3), that is a poor trade. So this
module gets an OAuth token from the service account with ``google-auth`` and
posts to the FCM v1 endpoint with ``requests`` — both already dependencies.

-------------------------------------------------------------------------------
NOTHING HERE RAISES
-------------------------------------------------------------------------------
Every failure path returns a :class:`PushResult` instead: not configured, no
token for the user, provider refused, network down, library missing. A push
failure must never break the thing that triggered it — a payment that succeeded
should not 500 because a phone was unreachable — and Module 10.2's SMS fallback
needs a *result* to react to, not an exception to catch.

A token FCM reports as invalid is returned in :attr:`PushResult.invalid_tokens`
so the caller can clear it. Left in place, a dead token is retried on every
notification forever.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

FCM_ENDPOINT = "https://fcm.googleapis.com/v1/projects/{project_id}/messages:send"
FCM_SCOPE = "https://www.googleapis.com/auth/firebase.messaging"

REQUEST_TIMEOUT_SECONDS = 10

#: Errors that mean the token is dead rather than the request being wrong.
_DEAD_TOKEN_CODES = {"UNREGISTERED", "INVALID_ARGUMENT", "NOT_FOUND"}


@dataclass
class PushResult:
    """What one push attempt achieved."""

    sent: int = 0
    failed: int = 0
    #: Tokens FCM rejected as dead. The caller should clear these.
    invalid_tokens: list[str] = field(default_factory=list)
    available: bool = True
    reason: str = ""

    @property
    def succeeded(self) -> bool:
        return self.sent > 0


def is_configured() -> bool:
    config = getattr(settings, "FCM_SETTINGS", {})
    return bool(
        config.get("ENABLED")
        and config.get("PROJECT_ID")
        and config.get("CREDENTIALS_PATH")
    )


def _access_token() -> str | None:
    """Mint a short-lived OAuth token from the service account.

    ``google-auth`` caches and refreshes the credential object, but this creates
    one per call for simplicity — the refresh is cheap relative to the HTTP
    round trip that follows, and holding a module-level credential would need
    thread-safety care for no real gain at this scale.
    """
    config = getattr(settings, "FCM_SETTINGS", {})

    try:
        from google.auth.transport.requests import Request
        from google.oauth2 import service_account
    except ImportError:
        logger.warning("google-auth is not installed; push notifications are off.")
        return None

    try:
        credentials = service_account.Credentials.from_service_account_file(
            config["CREDENTIALS_PATH"], scopes=[FCM_SCOPE]
        )
        credentials.refresh(Request())
        return credentials.token
    except Exception as exc:  # noqa: BLE001 — a bad key file must not 500 a request
        logger.warning("Could not obtain an FCM access token: %s", exc)
        return None


def _build_message(*, token: str, title: str, body: str, data: dict) -> dict:
    """One FCM v1 message.

    ``data`` values are stringified because FCM rejects non-string data values —
    a silent 400 on an int is a genuinely confusing failure to debug.
    """
    return {
        "message": {
            "token": token,
            "notification": {"title": title, "body": body},
            "data": {key: str(value) for key, value in (data or {}).items()},
            "android": {
                # Notifications here are timely — a visit starting, a gate
                # decision — so they are worth waking the device for.
                "priority": "high",
                "notification": {"channel_id": "sathify_default"},
            },
        }
    }


def send(
    *, tokens: list[str], title: str, body: str, data: dict | None = None
) -> PushResult:
    """Push one message to a user's devices.

    FCM v1 has no multicast endpoint, so this posts once per token. A worker has
    one or two devices, so the loop is short; if that stops being true, batching
    belongs here rather than at the call site.
    """
    tokens = [token for token in tokens if token]
    if not tokens:
        return PushResult(available=False, reason="No device tokens for this user.")

    if not is_configured():
        return PushResult(
            available=False,
            reason="Push notifications are not configured on this server.",
        )

    access_token = _access_token()
    if access_token is None:
        return PushResult(
            available=False, reason="Could not authenticate with Firebase."
        )

    config = settings.FCM_SETTINGS
    url = FCM_ENDPOINT.format(project_id=config["PROJECT_ID"])
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json; UTF-8",
    }

    result = PushResult()

    for token in tokens:
        try:
            response = requests.post(
                url,
                json=_build_message(token=token, title=title, body=body, data=data or {}),
                headers=headers,
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
        except requests.RequestException as exc:
            logger.warning("FCM request failed: %s", exc)
            result.failed += 1
            result.reason = "Could not reach Firebase."
            continue

        if response.status_code == 200:
            result.sent += 1
            continue

        result.failed += 1
        code = _error_code(response)
        if code in _DEAD_TOKEN_CODES:
            # The device uninstalled or the token rotated. Kept apart from a
            # transient failure so the caller can clear it rather than retrying
            # a dead address forever.
            result.invalid_tokens.append(token)
        result.reason = f"Firebase refused the message ({code or response.status_code})."

    return result


def _error_code(response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return ""

    details = payload.get("error", {}).get("details", [])
    for detail in details:
        if "errorCode" in detail:
            return detail["errorCode"]
    return payload.get("error", {}).get("status", "")


__all__ = ["FCM_ENDPOINT", "PushResult", "is_configured", "send"]
