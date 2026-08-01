"""
Module 10 — notification orchestration.

One entry point, :func:`notify`, which every other module calls. It records the
message, respects the recipient's preferences, tries push, and falls back to SMS
— in that order, and never raising.

-------------------------------------------------------------------------------
NOTIFYING MUST NEVER BREAK THE THING THAT TRIGGERED IT
-------------------------------------------------------------------------------
A payment that settled, a gate entry that was logged, a worker that was
approved — none of those should fail because a phone was unreachable or a
credential file was missing. So every failure in this module is recorded on the
notification row and swallowed, and callers can treat :func:`notify` as
best-effort.

That is also why the row is written first: even with both channels dead, the
message reaches the person the next time they open the app.

-------------------------------------------------------------------------------
A MUTED CATEGORY STILL GETS A ROW
-------------------------------------------------------------------------------
Muting means "stop interrupting me", not "keep this from me". So a muted
category is still recorded in the notification centre and simply not pushed —
the user chose quiet, not ignorance. Safety-critical categories cannot be muted
at all (see ``models.SAFETY_CRITICAL_CATEGORIES``).
"""

from __future__ import annotations

import logging

from django.db import transaction
from django.utils import timezone

from apps.accounts.models import DeviceSession

from . import push, sms
from .models import (
    DeliveryState,
    Notification,
    NotificationCategory,
    NotificationPreference,
)

logger = logging.getLogger(__name__)


def active_tokens(user) -> list[str]:
    """FCM tokens for a user's live devices.

    Revoked sessions are excluded, and ``DeviceSession.revoke`` already clears
    the token — a device that was cut off must stop receiving pushes, which
    would otherwise be a way to keep reading a household's activity after
    losing access.
    """
    return list(
        DeviceSession.objects.filter(user=user, revoked_at__isnull=True)
        .exclude(fcm_token="")
        .values_list("fcm_token", flat=True)
    )


def _clear_dead_tokens(tokens: list[str]) -> None:
    """Forget tokens FCM reported as dead.

    Left in place they are retried on every notification forever, which wastes
    a request per message per dead device and makes delivery stats meaningless.
    """
    if not tokens:
        return
    cleared = DeviceSession.objects.filter(fcm_token__in=tokens).update(fcm_token="")
    if cleared:
        logger.info("Cleared %s dead FCM token(s)", cleared)


@transaction.atomic
def _record(
    *, recipient, category: str, title: str, body: str, data: dict | None, society
) -> Notification:
    return Notification.objects.create(
        society=society,
        recipient=recipient,
        category=category,
        title=title[:120],
        body=body[:400],
        data=data or {},
    )


def deliver(notification: Notification, *, allow_sms: bool = True) -> Notification:
    """Attempt delivery of an already-recorded notification.

    Separated from :func:`notify` so a retry sweep can re-attempt an old
    notification without creating a second copy of it.
    """
    recipient = notification.recipient

    if NotificationPreference.is_muted(recipient, notification.category):
        # Recorded, not pushed. See the module docstring.
        notification.record_delivery(
            channel="push",
            state=DeliveryState.SKIPPED,
            note="The recipient has muted this category.",
        )
        return notification

    result = push.send(
        tokens=active_tokens(recipient),
        title=notification.title,
        body=notification.body,
        data={**notification.data, "category": notification.category},
    )
    _clear_dead_tokens(result.invalid_tokens)

    if result.succeeded:
        notification.record_delivery(channel="push", state=DeliveryState.SENT)
        return notification

    notification.record_delivery(
        channel="push",
        state=DeliveryState.SKIPPED if not result.available else DeliveryState.FAILED,
        note=result.reason,
    )

    if allow_sms:
        send_sms_fallback(notification)

    return notification


def send_sms_fallback(notification: Notification) -> Notification:
    """Module 10.2 — try SMS after push did not land."""
    recipient = notification.recipient
    result = sms.send(
        phone_number=recipient.phone_number,
        title=notification.title,
        body=notification.body,
    )

    notification.record_delivery(
        channel="sms",
        state=(
            DeliveryState.SENT
            if result.sent
            else DeliveryState.SKIPPED if not result.available else DeliveryState.FAILED
        ),
        note=result.reason,
    )
    return notification


def notify(
    *,
    recipient,
    category: str,
    title: str,
    body: str,
    data: dict | None = None,
    society=None,
) -> Notification | None:
    """Tell someone something. The entry point every other module uses.

    Returns the notification, or ``None`` if it could not even be recorded —
    which is the only case a caller might care about, and even then only for
    logging. Never raises.
    """
    try:
        notification = _record(
            recipient=recipient,
            category=category,
            title=title,
            body=body,
            data=data,
            society=society or recipient.society,
        )
    except Exception:  # noqa: BLE001 — notifying must not break the caller
        logger.exception("Could not record a notification for user %s", recipient.pk)
        return None

    try:
        return deliver(notification)
    except Exception:  # noqa: BLE001
        logger.exception("Delivery failed for notification %s", notification.pk)
        return notification


def notify_many(*, recipients, category: str, title: str, body: str, data=None) -> int:
    """Notify several people. Returns how many were recorded.

    Each is handled independently: one recipient with a broken device must not
    stop the rest being told.
    """
    return sum(
        1
        for recipient in recipients
        if notify(
            recipient=recipient,
            category=category,
            title=title,
            body=body,
            data=data,
        )
        is not None
    )


# ---------------------------------------------------------------------------
# Module 6.4 — draining the reminder queue
# ---------------------------------------------------------------------------


def deliver_due_reminders(*, society_id=None, limit: int = 200) -> int:
    """Turn Module 6.4's due reminders into notifications. Returns how many.

    Module 6 deliberately stopped at "a durable row with a send_after
    timestamp", leaving delivery to this module. This is the join between them,
    and it is idempotent through the reminder's own status: a reminder marked
    sent is not picked up again.
    """
    from apps.scheduling.services import due_reminders

    sent = 0
    for reminder in due_reminders(society_id=society_id)[:limit]:
        notification = notify(
            recipient=reminder.recipient,
            category=NotificationCategory.SCHEDULE,
            title=reminder.title,
            body=reminder.body,
            data={"reminder_id": reminder.pk, "route": "/schedule"},
            society=reminder.society,
        )

        if notification is None:
            reminder.mark_failed("Could not record the notification.")
            continue

        # Marked sent once it is recorded, not once it is pushed: the in-app
        # centre is the system of record, and a reminder that reached it has
        # done its job even if the phone was off.
        reminder.mark_sent()
        sent += 1

    if sent:
        logger.info("Delivered %s due reminder(s)", sent)
    return sent


def retry_failed_deliveries(*, limit: int = 200) -> int:
    """Re-attempt SMS for notifications push never reached.

    Bounded and re-runnable. Only touches notifications where push failed or was
    skipped and SMS has not yet been tried, so a successful delivery is never
    re-sent.
    """
    pending = Notification.objects.needing_sms_fallback().select_related("recipient")[
        :limit
    ]

    retried = 0
    for notification in pending:
        send_sms_fallback(notification)
        retried += 1

    return retried


__all__ = [
    "active_tokens",
    "deliver",
    "deliver_due_reminders",
    "notify",
    "notify_many",
    "retry_failed_deliveries",
    "send_sms_fallback",
]
