"""
Module 11 — complaint orchestration and the escalation sweep.

The models move state; this decides when, tells the right people, and writes the
history entry. Keeping that split means a transition can be tested without
asserting on notifications, and a notification failure can never leave a
complaint half-transitioned.

-------------------------------------------------------------------------------
ESCALATION IS A SWEEP, NOT A TIMER
-------------------------------------------------------------------------------
There is no scheduler on the free tier (docs/free-tier-constraints.md §2), so
"automated escalation fires if it goes unresolved past a defined SLA window"
is implemented the way every other deadline in this codebase is: a bounded,
idempotent sweep run on read, on a management command, and on an endpoint an
external pinger can call.

:func:`escalate_overdue` is safe to call on every queue load. ``escalated_at``
is what makes it idempotent — a complaint escalates once, however many times the
sweep runs.
"""

from __future__ import annotations

import logging

from django.db import transaction
from django.utils import timezone

from .models import (
    ESCALATED_ON_ARRIVAL,
    Complaint,
    ComplaintPriority,
    ComplaintStatus,
    ComplaintUpdate,
    UnmetDemand,
)

logger = logging.getLogger(__name__)

#: The in-app route a complaint notification opens. Named here rather than
#: inlined so there is one place to keep in step with the Flutter route table.
COMPLAINTS_ROUTE = "/complaints"

#: How many complaints one sweep will escalate. Bounded so a society that has
#: been unattended for a month cannot turn one queue load into a thousand
#: notifications on a 512 MB instance.
ESCALATION_BATCH = 100


class ComplaintError(Exception):
    """Base for refusals that are business rules, not bugs."""

    code = "complaint_error"


class AlreadyClosed(ComplaintError):
    code = "already_closed"


# ---------------------------------------------------------------------------
# Notification helper
# ---------------------------------------------------------------------------


def _notify(*, recipient, title: str, body: str, society=None, route: str = "") -> None:
    """Best-effort notification.

    Lazily imported and non-raising, in line with every other module's call
    sites: a complaint that was recorded must not be rolled back because a phone
    was unreachable.
    """
    if recipient is None:
        return

    try:
        from apps.notifications.models import NotificationCategory
        from apps.notifications.services import notify

        notify(
            recipient=recipient,
            category=NotificationCategory.COMPLAINT,
            title=title,
            body=body,
            data={"route": route or "/complaints"},
            society=society,
        )
    except Exception:  # noqa: BLE001 — notifying must not break the caller
        logger.exception("Could not notify user %s about a complaint", recipient.pk)


def _society_admins(society_id):
    """Approved administrators of one society.

    Imported here rather than at module scope to keep this app's import graph
    shallow — administration is imported by payments' dispute path, and a
    top-level accounts import would make that a longer cycle than it needs to be.
    """
    from apps.accounts.models import Role, User

    return User.objects.filter(
        society_id=society_id, role=Role.SOCIETY_ADMIN, is_active=True
    )


def _notify_admins(society_id, *, title: str, body: str) -> int:
    """Tell every administrator of a society. Returns how many were told.

    Not deduplicated against who triggered it: an administrator who raises a
    complaint about a worker still wants it in their own queue, and suppressing
    it would make their copy of the queue disagree with everyone else's.
    """
    admins = list(_society_admins(society_id))
    for admin in admins:
        # "/complaints", not "/admin/complaints": the app has one complaints
        # route for every role and shows the society queue to an administrator.
        # A route the client cannot match is a crash on tapping the
        # notification, so this string is part of the client contract — see
        # `Routes` in mobile/lib/core/routing/app_router.dart.
        _notify(recipient=admin, title=title, body=body, route=COMPLAINTS_ROUTE)
    return len(admins)


# ---------------------------------------------------------------------------
# 11.3 Raising
# ---------------------------------------------------------------------------


def default_priority_for(category: str) -> str:
    """Where a complaint enters the queue.

    Only safety jumps the queue automatically. Letting the person raising the
    complaint choose their own priority would make every complaint urgent within
    a week, and the ones that genuinely are would be indistinguishable.
    """
    return (
        ComplaintPriority.URGENT
        if category in ESCALATED_ON_ARRIVAL
        else ComplaintPriority.NORMAL
    )


@transaction.atomic
def raise_complaint(
    *,
    raised_by,
    society,
    category: str,
    subject: str,
    description: str,
    against_worker=None,
    against_resident=None,
    photo=None,
    payment_dispute=None,
) -> Complaint:
    """Open a complaint and tell the administrators about it."""
    complaint = Complaint.objects.create(
        society=society,
        raised_by=raised_by,
        category=category,
        subject=subject[:150],
        description=description,
        against_worker=against_worker,
        against_resident=against_resident,
        photo=photo,
        payment_dispute=payment_dispute,
        priority=default_priority_for(category),
    )

    ComplaintUpdate.objects.create(
        complaint=complaint,
        author=raised_by,
        note=f"Complaint raised: {subject[:150]}",
        new_status=ComplaintStatus.OPEN,
    )

    _note_suggested_category(complaint)

    notified = _notify_admins(
        society.pk,
        title=f"New complaint: {complaint.get_category_display()}",
        body=f"{complaint.reference} — {complaint.subject}",
    )

    logger.info(
        "Complaint %s raised by user %s (%s admins notified)",
        complaint.reference,
        raised_by.pk,
        notified,
    )
    return complaint


def _note_suggested_category(complaint: Complaint) -> None:
    """Ask Module 12.5 what this complaint looks like, and record the answer.

    Recorded as an internal note, never applied. The person raising a complaint
    knows what it is about better than a classifier does, and silently
    reclassifying somebody's safety report as "quality" would move it out of the
    queue position they were promised.

    Only written when the classifier disagrees *and* is confident, so an
    administrator's history is not padded with a line agreeing with the obvious
    on every complaint. Lazily imported and non-raising, like every other
    cross-module call site.
    """
    try:
        from apps.ai_services.analysis import classify_complaint

        suggestion = classify_complaint(
            complaint.subject, complaint.description, user=complaint.raised_by
        )
    except Exception:  # noqa: BLE001 — classification must not break raising
        logger.exception("Could not classify complaint %s", complaint.pk)
        return

    verdict = suggestion.value
    if verdict.category == complaint.category or not verdict.is_confident:
        return

    note = (
        f"Automatic classification suggests this may be a "
        f"'{verdict.category}' complaint rather than '{complaint.category}'. "
        f"{verdict.rationale}"
    ).strip()

    ComplaintUpdate.objects.create(
        complaint=complaint,
        note=note[:2000],
        is_system=True,
        is_internal=True,
    )


def raise_from_payment_dispute(dispute) -> Complaint | None:
    """Module 8.6's join into this queue.

    Module 8 kept ``PaymentDispute`` deliberately thin and said the handling
    belonged here rather than in a parallel workflow. This is that promise: a
    dispute opens a payment-category complaint against the other party, and the
    administrator works one queue instead of two.

    Returns ``None`` if a complaint already exists for this dispute, so the call
    site can be retried safely.
    """
    if getattr(dispute, "complaint", None) is not None:
        return None

    payment = dispute.payment
    raiser = dispute.raised_by

    # The complaint is against whoever is on the other side of the payment.
    raised_by_worker = getattr(payment.worker, "user_id", None) == raiser.pk

    return raise_complaint(
        raised_by=raiser,
        society=dispute.society,
        category="payment",
        subject=f"Payment dispute — {payment.receipt_number}",
        description=dispute.description,
        against_worker=None if raised_by_worker else payment.worker,
        against_resident=payment.resident if raised_by_worker else None,
        payment_dispute=dispute,
    )


# ---------------------------------------------------------------------------
# 11.3 Working the queue
# ---------------------------------------------------------------------------


@transaction.atomic
def add_update(
    complaint: Complaint, *, author, note: str, is_internal: bool = False
) -> ComplaintUpdate:
    """Append a comment. Records a first response if this is one.

    An administrator's first comment counts as the response that stops the
    "nobody has looked at this" clock, even when the status has not moved —
    which is usually what actually happens first.
    """
    entry = ComplaintUpdate.objects.create(
        complaint=complaint, author=author, note=note, is_internal=is_internal
    )

    is_admin = bool(getattr(author, "is_society_admin", False))
    if is_admin and complaint.first_response_at is None:
        complaint.first_response_at = timezone.now()
        complaint.save(update_fields=["first_response_at", "updated_at"])

    if is_admin and not is_internal and complaint.raised_by_id != getattr(author, "pk", None):
        _notify(
            recipient=complaint.raised_by,
            title=f"Update on {complaint.reference}",
            body=note[:180],
            society=complaint.society,
        )

    return entry


@transaction.atomic
def start_progress(complaint: Complaint, *, by, note: str = "") -> bool:
    if not complaint.start_progress(by=by):
        return False

    ComplaintUpdate.objects.create(
        complaint=complaint,
        author=by,
        note=note or "An administrator is looking into this.",
        old_status=ComplaintStatus.OPEN,
        new_status=ComplaintStatus.IN_PROGRESS,
    )
    _notify(
        recipient=complaint.raised_by,
        title=f"{complaint.reference} is being looked into",
        body=complaint.subject,
        society=complaint.society,
    )
    return True


@transaction.atomic
def close_complaint(
    complaint: Complaint, *, status: str, resolution: str, by
) -> bool:
    """Resolve, reject or withdraw, with the reason recorded either way.

    A resolution note is required for all three. "Rejected" with no explanation
    is the outcome most likely to be disputed, and the person who raised it is
    entitled to know why.
    """
    previous = complaint.status
    if not complaint.close(status=status, resolution=resolution, by=by):
        raise AlreadyClosed("This complaint has already been closed.")

    ComplaintUpdate.objects.create(
        complaint=complaint,
        author=by,
        note=resolution,
        old_status=previous,
        new_status=status,
    )

    # The person who withdrew it does not need telling that they withdrew it.
    if status != ComplaintStatus.WITHDRAWN:
        _notify(
            recipient=complaint.raised_by,
            title=f"{complaint.reference}: {complaint.get_status_display()}",
            body=resolution[:180],
            society=complaint.society,
        )

    logger.info("Complaint %s closed as %s", complaint.reference, status)
    return True


# ---------------------------------------------------------------------------
# 11.3 Escalation sweep
# ---------------------------------------------------------------------------


def escalate_overdue(*, society_id=None, limit: int = ESCALATION_BATCH) -> int:
    """Escalate complaints past their deadline. Returns how many.

    Idempotent and bounded — see the module docstring. Safe to call on every
    queue load, which is exactly how it is wired: an administrator opening the
    queue is the most reliable trigger this deployment has.
    """
    queryset = Complaint.objects.awaiting_escalation()
    if society_id is not None:
        queryset = queryset.filter(society_id=society_id)

    escalated = 0
    for complaint in queryset.select_related("society", "raised_by")[:limit]:
        if not complaint.escalate():
            continue

        ComplaintUpdate.objects.create(
            complaint=complaint,
            note=(
                f"Escalated automatically: no resolution within the "
                f"{complaint.get_priority_display().lower()} response window."
            ),
            is_system=True,
        )
        _notify_admins(
            complaint.society_id,
            title=f"Overdue complaint: {complaint.reference}",
            body=f"{complaint.subject} — past its response deadline.",
        )
        escalated += 1

    if escalated:
        logger.info("Escalated %s overdue complaint(s)", escalated)
    return escalated


# ---------------------------------------------------------------------------
# 11.4 Unmet demand
# ---------------------------------------------------------------------------


def record_unmet_demand(
    *,
    society,
    kind: str,
    service_label: str = "",
    requested_by=None,
    requested_date=None,
    requested_time=None,
    detail: str = "",
) -> UnmetDemand | None:
    """Log demand the platform could not serve.

    Never raises. This is called from the middle of other modules' happy paths —
    a search that found nobody, a hire request that timed out — and a failure to
    write an analytics row must not turn a legitimate empty result into an error
    the user sees.
    """
    if society is None:
        return None

    try:
        return UnmetDemand.objects.create(
            society=society,
            kind=kind,
            service_label=service_label[:120],
            requested_by=requested_by,
            requested_date=requested_date,
            requested_time=requested_time,
            detail=detail[:300],
        )
    except Exception:  # noqa: BLE001 — analytics must not break a search
        logger.exception("Could not record unmet demand (%s)", kind)
        return None


__all__ = [
    "AlreadyClosed",
    "ComplaintError",
    "add_update",
    "close_complaint",
    "default_priority_for",
    "escalate_overdue",
    "raise_complaint",
    "raise_from_payment_dispute",
    "record_unmet_demand",
    "start_progress",
]
