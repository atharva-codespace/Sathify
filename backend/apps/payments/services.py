"""
Module 8 — payment orchestration.

-------------------------------------------------------------------------------
ATTENDANCE DRIVES THE AMOUNT, BUT DOES NOT DECIDE IT
-------------------------------------------------------------------------------
SRS 3.6 says attendance "feeds directly into payment calculation", and
:func:`salary_basis` does exactly that: it counts the visits that were expected
in the period against the ones the gate actually logged, and pro-rates the
agreed monthly rate.

It returns that as a **basis, not a verdict**. The resident confirms the amount
they are paying. Silently docking someone's wages because a gate scanner was
broken, or because the resident themselves cancelled a visit, would make this
module the arbiter of a dispute it cannot see the facts of. The figures are
shown, the arithmetic is explained, and a person presses the button.

-------------------------------------------------------------------------------
PAID IS ONLY EVER REACHED THROUGH A VERIFIED SIGNATURE
-------------------------------------------------------------------------------
Two paths settle a payment, and both check an HMAC first: the client handing
back a signed Razorpay Checkout response, and a webhook. There is deliberately
no third path — no admin action, no client assertion, nothing that would let a
payment be marked paid because someone said so.
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass

from django.db import transaction
from django.utils import timezone

from apps.attendance.models import AttendanceEvent, Decision, Direction
from apps.scheduling.schedule import worker_schedule

from . import gateway
from .models import (
    Payment,
    PaymentKind,
    PaymentStatus,
    WebhookEvent,
    rupees_to_paise,
)

logger = logging.getLogger(__name__)


class PaymentError(Exception):
    """Base for refusals that are business rules, not bugs."""

    code = "payment_error"


class AlreadyPaid(PaymentError):
    code = "already_paid"


class SignatureInvalid(PaymentError):
    code = "signature_invalid"


class NothingToPay(PaymentError):
    code = "nothing_to_pay"


# ---------------------------------------------------------------------------
# Attendance-driven salary basis
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SalaryBasis:
    """The arithmetic behind a suggested monthly amount.

    Every field is exposed so the app can show its working. A number a resident
    cannot account for is a number they will not trust, and a worker cannot
    contest what they cannot see.
    """

    expected_visits: int
    attended_visits: int
    full_rate_paise: int
    suggested_paise: int
    period_start: dt.date
    period_end: dt.date

    @property
    def attendance_rate(self) -> float:
        return self.attended_visits / self.expected_visits if self.expected_visits else 1.0

    @property
    def is_full(self) -> bool:
        return self.suggested_paise >= self.full_rate_paise

    def explain(self) -> str:
        if not self.expected_visits:
            return "No visits were scheduled in this period."
        if self.is_full:
            return (
                f"All {self.expected_visits} scheduled visits were attended."
            )
        return (
            f"{self.attended_visits} of {self.expected_visits} scheduled visits "
            "were logged at the gate."
        )


def salary_basis(engagement, *, period_start: dt.date, period_end: dt.date) -> SalaryBasis:
    """Pro-rate an engagement's monthly rate by attendance over a period.

    Expected visits come from Module 6's derived schedule, so the gate, the
    worker's calendar and payroll all count the same days. Attended visits are
    gate entries that were *allowed* — a denied or still-pending entry is not
    attendance.
    """
    full_rate = rupees_to_paise(engagement.monthly_rate)

    expected = [
        item
        for item in worker_schedule(engagement.worker_id, period_start, period_end)
        if item.source == "engagement" and item.source_id == engagement.pk
    ]

    attended = (
        AttendanceEvent.objects.filter(
            worker_id=engagement.worker_id,
            engagement_id=engagement.pk,
            direction=Direction.ENTRY,
            decision=Decision.ALLOWED,
            occurred_at__date__gte=period_start,
            occurred_at__date__lte=period_end,
        )
        # One worker may pass the gate twice in a day; payroll counts days.
        .values("occurred_at__date")
        .distinct()
        .count()
    )

    if not expected:
        # Nothing was scheduled, so there is nothing to pro-rate against.
        # Suggesting zero would be as wrong as suggesting the full rate, so the
        # full rate stands and a person decides.
        suggested = full_rate
    else:
        capped = min(attended, len(expected))
        suggested = full_rate * capped // len(expected)

    return SalaryBasis(
        expected_visits=len(expected),
        attended_visits=attended,
        full_rate_paise=full_rate,
        suggested_paise=suggested,
        period_start=period_start,
        period_end=period_end,
    )


# ---------------------------------------------------------------------------
# 8.1 / 8.2 Creating a payment
# ---------------------------------------------------------------------------


@transaction.atomic
def create_payment(
    *,
    resident,
    worker,
    society,
    kind: str,
    amount_paise: int,
    tip_paise: int = 0,
    engagement=None,
    booking=None,
    period_start: dt.date | None = None,
    period_end: dt.date | None = None,
    note: str = "",
) -> Payment:
    """Open a ledger row. No money moves yet — see :func:`open_order`."""
    if amount_paise <= 0:
        raise NothingToPay("A payment must be for more than zero.")

    payment = Payment.objects.create(
        society=society,
        resident=resident,
        worker=worker,
        engagement=engagement,
        booking=booking,
        kind=kind,
        amount_paise=amount_paise,
        tip_paise=max(0, tip_paise),
        period_start=period_start,
        period_end=period_end,
        note=note,
    )
    logger.info(
        "Payment %s created: %s paise (%s) resident=%s worker=%s",
        payment.receipt_number, payment.total_paise, kind, resident.pk, worker.pk,
    )
    return payment


def open_order(payment: Payment) -> dict:
    """Create the Razorpay order and return what the app needs for checkout.

    The order id is stored before the payload is returned. If the app crashes
    between checkout and confirmation, the webhook can still match the payment
    by that id — without it, a real payment would have no ledger row to land on.
    """
    if payment.is_settled:
        raise AlreadyPaid("This payment has already been settled.")

    order = gateway.create_order(
        amount_paise=payment.total_paise,
        receipt=str(payment.pk),
        notes={
            "receipt_number": payment.receipt_number,
            "worker": str(payment.worker_id),
            "kind": payment.kind,
        },
    )

    payment.razorpay_order_id = order.get("id", "")
    payment.status = PaymentStatus.PENDING
    payment.save(update_fields=["razorpay_order_id", "status", "updated_at"])

    return gateway.checkout_payload(
        order=order,
        amount_paise=payment.total_paise,
        name=payment.get_kind_display(),
    )


# ---------------------------------------------------------------------------
# 8.1 Settlement
# ---------------------------------------------------------------------------


@transaction.atomic
def confirm_checkout(payment: Payment, *, razorpay_payment_id: str, signature: str) -> Payment:
    """Settle from the client's signed checkout response.

    The signature is what makes this trustworthy; the client's word is not.
    """
    locked = Payment.objects.select_for_update().get(pk=payment.pk)

    if locked.is_settled:
        # Not an error. The webhook may well have arrived first, which is the
        # normal race, and the app should see success either way.
        return locked

    if not gateway.verify_checkout_signature(
        order_id=locked.razorpay_order_id,
        payment_id=razorpay_payment_id,
        signature=signature,
    ):
        logger.warning(
            "Rejected an unsigned checkout confirmation for payment %s", locked.pk
        )
        raise SignatureInvalid(
            "That payment could not be verified with the payment provider."
        )

    locked.mark_paid(razorpay_payment_id=razorpay_payment_id, signature=signature)
    _notify_worker_paid(locked)
    return locked


def _notify_worker_paid(payment: Payment) -> None:
    """Tell the worker their money arrived.

    Lazily imported and non-raising: a settled payment must never be undone
    because a phone was unreachable. This is arguably the single most welcome
    notification the platform sends.
    """
    from apps.notifications.models import NotificationCategory
    from apps.notifications.services import notify
    from .models import format_paise

    notify(
        recipient=payment.worker.user,
        category=NotificationCategory.PAYMENT,
        title="You have been paid",
        body=f"{format_paise(payment.total_paise)} from "
        f"{payment.resident.user.get_full_name()}.",
        data={"route": "/earnings", "payment_id": str(payment.pk)},
        society=payment.society,
    )


def _payment_for_event(entity: dict) -> Payment | None:
    """Find the ledger row a webhook refers to.

    Razorpay echoes our own receipt (the payment UUID) and the order id, so
    either identifies the row. The order id is tried first because it is
    present on every payment event.
    """
    order_id = entity.get("order_id") or ""
    if order_id:
        found = Payment.objects.filter(razorpay_order_id=order_id).first()
        if found is not None:
            return found

    receipt = entity.get("receipt") or entity.get("notes", {}).get("receipt_number", "")
    if receipt:
        return (
            Payment.objects.filter(pk=receipt).first()
            or Payment.objects.filter(receipt_number=receipt).first()
        )
    return None


@transaction.atomic
def apply_webhook(event: WebhookEvent) -> Payment | None:
    """Apply a verified webhook to the ledger.

    Only called for events whose signature already checked out. Idempotent by
    construction: ``WebhookEvent.event_id`` is unique, so a retried delivery is
    recognised before this runs, and ``mark_paid`` is itself idempotent for the
    case where two different events both report success.
    """
    payload = event.payload or {}
    entity = (
        payload.get("payload", {}).get("payment", {}).get("entity", {})
        if isinstance(payload, dict)
        else {}
    )

    payment = _payment_for_event(entity)
    if payment is None:
        # Worth recording rather than swallowing: a payment event with no
        # matching row means either a stray webhook or a lost order id.
        event.mark_processed(error="No matching payment for this event.")
        logger.warning("Webhook %s matched no payment", event.event_id)
        return None

    event.payment = payment
    event.save(update_fields=["payment", "updated_at"])

    if event.event_type in {"payment.captured", "payment.authorized"}:
        # mark_paid is idempotent, so a webhook arriving after the client's own
        # confirmation is a no-op — and the worker is only told once.
        if payment.mark_paid(
            razorpay_payment_id=entity.get("id", ""), signature="webhook"
        ):
            _notify_worker_paid(payment)
    elif event.event_type == "payment.failed":
        payment.mark_failed(
            reason=entity.get("error_description", "The payment failed.")
        )
    elif event.event_type.startswith("refund."):
        payment.mark_refunded(amount_paise=entity.get("amount"))

    event.mark_processed()
    return payment


def record_webhook(
    *, event_id: str, event_type: str, payload: dict, signature_valid: bool
) -> tuple[WebhookEvent, bool]:
    """Store a webhook. Returns ``(event, created)``.

    Invalid signatures are stored too. A run of them is someone probing the
    endpoint, and an operator should be able to see that rather than have it
    silently dropped.
    """
    event, created = WebhookEvent.objects.get_or_create(
        event_id=event_id,
        defaults={
            "event_type": event_type,
            "payload": payload,
            "signature_valid": signature_valid,
        },
    )
    return event, created


# ---------------------------------------------------------------------------
# 8.5 Replacement split
# ---------------------------------------------------------------------------


def split_for_replacement(engagement, *, day_rate_paise: int) -> tuple[int, int]:
    """Divide a day's pay between replacement and original worker.

    Defaults to the whole amount going to the replacement when no rule has been
    agreed — they are the person who actually did the work, and taking a share
    off them requires an explicit prior agreement, not a default.
    """
    split = getattr(engagement, "replacement_split", None)
    if split is None:
        return day_rate_paise, 0
    return split.split(day_rate_paise)


def daily_rate_paise(engagement) -> int:
    """A single day's share of a monthly rate.

    Divided by the visits actually scheduled in a month rather than by 30: a
    worker who comes twice a week is not paid a thirtieth of their month for one
    visit.
    """
    per_month = max(1, len(engagement.days_of_week) * 4)
    return rupees_to_paise(engagement.monthly_rate) // per_month


__all__ = [
    "AlreadyPaid",
    "NothingToPay",
    "PaymentError",
    "SalaryBasis",
    "SignatureInvalid",
    "apply_webhook",
    "confirm_checkout",
    "create_payment",
    "daily_rate_paise",
    "open_order",
    "record_webhook",
    "salary_basis",
    "split_for_replacement",
]
