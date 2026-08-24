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
HOW A PAYMENT REACHES PAID — THREE PATHS, AND WHY THE THIRD EXISTS
-------------------------------------------------------------------------------
**Two are gateway paths, and both check an HMAC first**: the client handing back
a signed Razorpay Checkout response, and a webhook. Nothing a client asserts
about its own success is trusted, because the client is the party that benefits
from lying about it. That has not changed.

**The third is UPI reconciliation, and it is a deliberate reversal.** This file
used to say there was "no third path — no admin action, no client assertion,
nothing that would let a payment be marked paid because someone said so", and
that was the right rule for a system where every rupee moved through Razorpay.

Module 8.9 broke that assumption. A UPI QR collects straight into a VPA, which
means real money can arrive with **no callback of any kind** — no signature to
verify, because no gateway was involved. Holding the old line would not have
kept anything safe; it would have meant a household pays, the money lands in the
bank account, and the app says "unpaid" forever. For an emergency that is worse
than useless: the broadcast is triggered by settlement, so the request would
never reach a single worker.

So the third path exists, and the honesty is moved rather than abandoned. It is
not "an admin may mark things paid":

* only a society administrator, only within their own society;
* only against an unsettled payment;
* the amount observed must equal what the payment asked for, checked through
  :func:`upi.reconcile_reference`;
* the bank's UTR is required and is **unique across the ledger**, so one line on
  a statement can settle at most one payment;
* who confirmed it, when, and against which UTR are all stored as evidence, in
  the same spirit as ``WebhookEvent`` — applying and forgetting would leave only
  the conclusion.

The signature is replaced by a named human and a bank reference that can be
checked against a statement. That is a weaker guarantee than an HMAC and it is
recorded as such: ``Payment.settled_via`` says which path settled every row, so
nobody has to guess later which ones rest on a person's word.
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass

from django.db import transaction
from django.utils import timezone

from apps.attendance.models import AttendanceEvent, Decision, Direction
from apps.scheduling.schedule import worker_schedule

from .fees import platform_fee_paise

from . import gateway
from .models import (
    Payment,
    PaymentKind,
    PaymentStatus,
    SettledVia,
    UpiSettlement,
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


def _visits_from_terms(engagement, period_start: dt.date, period_end: dt.date) -> int:
    """Visits the terms call for, bounded by the engagement's own lifetime.

    The derived schedule expands **active** engagements only, which is right for
    a calendar and wrong for a final payslip: a worker who has just finished
    their notice period looks, to ``worker_schedule``, like somebody with nothing
    scheduled — and the "nothing scheduled, so the full rate stands" fallback
    below would then suggest a whole month's pay for a fortnight's work.

    So when the schedule cannot answer, the terms are expanded directly and
    clipped to the days the engagement actually ran: never before it started,
    never after its last working day.
    """
    days = set(engagement.days_of_week)
    if not days:
        return 0

    start = period_start
    if engagement.started_on:
        start = max(start, engagement.started_on)

    end = period_end
    finished = engagement.last_working_day
    if finished is None and engagement.ended_at is not None:
        finished = timezone.localtime(engagement.ended_at).date()
    if finished is not None:
        end = min(end, finished)

    if end < start:
        return 0

    count = 0
    day = start
    while day <= end:
        if day.weekday() in days:
            count += 1
        day += dt.timedelta(days=1)
    return count


def salary_basis(engagement, *, period_start: dt.date, period_end: dt.date) -> SalaryBasis:
    """Pro-rate an engagement's monthly rate by attendance over a period.

    Expected visits come from Module 6's derived schedule, so the gate, the
    worker's calendar and payroll all count the same days. Attended visits are
    gate entries that were *allowed* — a denied or still-pending entry is not
    attendance.

    A finished engagement is the one case the schedule cannot answer for; see
    :func:`_visits_from_terms`. This is what makes the Module 4.6 notice period's
    promise — "paid in full, for the days worked" — actually true on the final
    payslip rather than only in the wording.
    """
    from apps.hiring.models import EngagementStatus

    full_rate = rupees_to_paise(engagement.monthly_rate)

    expected = [
        item
        for item in worker_schedule(engagement.worker_id, period_start, period_end)
        if item.source == "engagement" and item.source_id == engagement.pk
    ]
    expected_count = len(expected)

    if expected_count == 0 and engagement.status != EngagementStatus.ACTIVE:
        expected_count = _visits_from_terms(engagement, period_start, period_end)

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

    if not expected_count:
        # Nothing was scheduled, so there is nothing to pro-rate against.
        # Suggesting zero would be as wrong as suggesting the full rate, so the
        # full rate stands and a person decides.
        suggested = full_rate
    else:
        capped = min(attended, expected_count)
        suggested = full_rate * capped // expected_count

    return SalaryBasis(
        expected_visits=expected_count,
        attended_visits=attended,
        full_rate_paise=full_rate,
        suggested_paise=suggested,
        period_start=period_start,
        period_end=period_end,
    )


# ---------------------------------------------------------------------------
# 8.8 When a payment is due
# ---------------------------------------------------------------------------


def payment_due_at(
    *,
    kind: str,
    engagement=None,
    booking=None,
    period_end: dt.date | None = None,
) -> dt.datetime | None:
    """When a payment is owed. Derived from fields that already exist.

    ---------------------------------------------------------------------------
    WHY THIS IS NOT DERIVED FROM ``daily_rate_paise``
    ---------------------------------------------------------------------------
    The obvious place to look for a billing cycle is the thing that already
    divides a monthly rate — but ``daily_rate_paise`` divides by
    ``len(days_of_week) * 4``. That is a *rate* calculation: it implies a
    four-week month and nothing else. No anchor date, no billing day, no cycle
    start. There is no due date hiding in it to reuse, and inventing one there
    would put a billing rule inside a function whose job is arithmetic.

    So the due date comes from whichever field already says when the work is:

    * a **one-day booking** is due on the day it is served — the resident is
      paying for a thing that happens on a date the booking already carries;
    * a **salary for a period** is due at the end of that period, because it
      pays for work already done, and asking for it up front would have a
      household paying for visits nobody has made yet;
    * an **engagement with no period** — which is the case the brief names,
      "resident books a service" — falls back to the engagement's start date.

    Returns ``None`` when nothing implies a date, rather than guessing. A blank
    due date is honest; a wrong one is a demand for money on a day nobody agreed.
    """
    if booking is not None and getattr(booking, "scheduled_date", None):
        start = getattr(booking, "start_time", None) or dt.time(0, 0)
        return _as_aware(dt.datetime.combine(booking.scheduled_date, start))

    if period_end is not None:
        # End of the last day of the period, not its midnight start — a salary
        # for a month ending on the 31st is not overdue at 00:00 on the 31st.
        return _as_aware(dt.datetime.combine(period_end, dt.time(23, 59, 59)))

    if engagement is not None and getattr(engagement, "started_on", None):
        start = getattr(engagement, "start_time", None) or dt.time(0, 0)
        return _as_aware(dt.datetime.combine(engagement.started_on, start))

    return None


def _as_aware(naive: dt.datetime) -> dt.datetime:
    """Attach the current timezone. Payments are compared against ``now()``."""
    if timezone.is_aware(naive):
        return naive
    return timezone.make_aware(naive, timezone.get_current_timezone())


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
    due_at: dt.datetime | None = None,
    note: str = "",
) -> Payment:
    """Open a ledger row. No money moves yet — see :func:`open_order`."""
    if amount_paise <= 0:
        raise NothingToPay("A payment must be for more than zero.")

    # Module 8.7 — frozen here, not derived on read, so a later rate change
    # cannot rewrite what this receipt said at the time. Zero on everything
    # today; see apps/payments/fees.py.
    fee = platform_fee_paise(kind=kind, amount_paise=amount_paise, society=society)

    # Module 8.8 — an explicit due date, derived rather than invented. A
    # caller may pass one (the sample_payment command does, to make a payment
    # due right now); otherwise it comes from whichever field already says
    # when the work is.
    if due_at is None:
        due_at = payment_due_at(
            kind=kind, engagement=engagement, booking=booking, period_end=period_end
        )

    payment = Payment.objects.create(
        society=society,
        resident=resident,
        worker=worker,
        engagement=engagement,
        booking=booking,
        kind=kind,
        amount_paise=amount_paise,
        tip_paise=max(0, tip_paise),
        platform_fee_paise=fee,
        period_start=period_start,
        period_end=period_end,
        due_at=due_at,
        note=note,
    )
    logger.info(
        "Payment %s created: %s paise (%s) resident=%s worker=%s",
        payment.receipt_number, payment.total_paise, kind, resident.pk,
        # None on a platform charge — see the field comment on Payment.worker.
        getattr(worker, "pk", None),
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
    on_payment_settled(locked)
    return locked


def on_payment_settled(payment: Payment) -> None:
    """Everything that has to happen the moment money actually lands.

    ---------------------------------------------------------------------------
    ONE HOOK, TWO SETTLEMENT PATHS
    ---------------------------------------------------------------------------
    A payment reaches PAID down either of two routes — the client handing back a
    signed checkout response, or a webhook — and which one wins is a race
    decided by mobile network latency. Anything that must follow settlement
    therefore has to hang off both, and hanging it off both by hand is how one
    of them ends up missing a step.

    So both call this, and only this. It is safe to run twice: every branch is
    either idempotent in its own right or guarded by the state it moves.
    """
    _notify_worker_paid(payment)
    _release_emergency(payment)


def _release_emergency(payment: Payment) -> None:
    """Module 5.5 — a settled surcharge is what opens the broadcast.

    This is the join that makes "payment first, workers second" true rather than
    merely intended: there is no other code path that can move an emergency
    booking out of PAYMENT_PENDING, so an unpaid request cannot reach a single
    worker's phone even if a client asks it to.

    Lazily imported and non-raising, for the reason every cross-module call on a
    write path is: a broadcast that fails must not un-settle the payment. The
    request stays PAYMENT_PENDING and the next read sweep or a retried webhook
    picks it up.
    """
    if payment.kind != PaymentKind.EMERGENCY_SURCHARGE or payment.booking_id is None:
        return

    try:
        from apps.bookings.emergency import broadcast

        broadcast(payment.booking)
    except Exception:  # noqa: BLE001 — see the docstring
        logger.exception(
            "Could not broadcast emergency booking %s after payment %s settled",
            payment.booking_id, payment.pk,
        )


def _notify_worker_paid(payment: Payment) -> None:
    """Tell the worker their money arrived.

    Lazily imported and non-raising: a settled payment must never be undone
    because a phone was unreachable. This is arguably the single most welcome
    notification the platform sends.

    Silent on a platform charge: the emergency surcharge is Sathify's fee, and
    telling a worker she has "been paid" an amount she will never receive would
    be worse than telling her nothing.
    """
    from apps.notifications.models import NotificationCategory
    from apps.notifications.services import notify
    from .models import format_paise

    if payment.worker_id is None or payment.is_platform_charge:
        return

    notify(
        recipient=payment.worker.user,
        category=NotificationCategory.PAYMENT,
        title="You have been paid",
        body=f"{format_paise(payment.total_paise)} from "
        f"{payment.resident.user.get_full_name()}.",
        data={"route": "/earnings", "payment_id": str(payment.pk)},
        society=payment.society,
    )


# ---------------------------------------------------------------------------
# 8.9 UPI reconciliation
# ---------------------------------------------------------------------------


class AmountMismatch(PaymentError):
    code = "amount_mismatch"


class UtrAlreadyUsed(PaymentError):
    code = "utr_already_used"


#: Loosest defensible shape for a UTR. NPCI's is 12 alphanumerics; banks print
#: NEFT/IMPS references of other lengths, and refusing a genuine one an operator
#: is reading off a statement helps nobody. Long enough that a blank, a dash or
#: "paid" cannot pass as evidence.
MIN_UTR_LENGTH = 6


@transaction.atomic
def confirm_upi_settlement(
    payment: Payment, *, utr: str, amount_paise: int, confirmed_by, note: str = ""
) -> Payment:
    """An administrator confirms a UPI transfer against a bank statement.

    The third settlement path. See this module's docstring for why it exists and
    what stands in for the missing signature; the short version is that a UPI QR
    collects into a VPA with no callback at all, so without this a household can
    pay and the app says "unpaid" forever.

    Every guard here is doing a specific job:

    * **Already settled** returns the row rather than raising. Two administrators
      working the same statement is a normal Monday, not an error.
    * **The amount must match**, checked through :func:`upi.reconcile_reference`
      — the same function and the same rule the QR path uses, so there is one
      answer to "does this bank line belong to this payment".
    * **The UTR is unique across the ledger**, so one line on a statement cannot
      clear several charges. Enforced by the database; the check here only turns
      the collision into a readable refusal.
    * ``on_payment_settled`` still runs, which is what makes a reconciled
      emergency surcharge actually broadcast and a worker actually get told.
    """
    from . import upi

    locked = Payment.objects.select_for_update().get(pk=payment.pk)

    if locked.is_settled:
        return locked

    if locked.status in {PaymentStatus.REFUNDED, PaymentStatus.CANCELLED}:
        raise PaymentError("This payment is closed and cannot be settled.")

    reference = (utr or "").strip().upper()
    if len(reference) < MIN_UTR_LENGTH:
        raise PaymentError(
            "Enter the bank's transaction reference (UTR) for this transfer."
        )

    # The same match rule the QR path documents. An old QR re-scanned for a
    # different sum presents its original amount and fails here.
    if upi.reconcile_reference(reference=str(locked.pk), amount_paise=amount_paise) is None:
        raise AmountMismatch(
            f"The statement shows {amount_paise} paise but this payment is for "
            f"{locked.total_paise}. Confirm you are looking at the right line."
        )

    if UpiSettlement.objects.filter(utr=reference).exists():
        raise UtrAlreadyUsed(
            f"UTR {reference} has already been used to settle another payment."
        )

    UpiSettlement.objects.create(
        payment=locked,
        utr=reference,
        amount_paise=amount_paise,
        confirmed_by=confirmed_by,
        note=note[:300],
    )
    locked.mark_paid(
        # No gateway payment id exists — the money never went through one. The
        # UTR is the bank's identifier and belongs in the audit trail, so it is
        # recorded here rather than left blank and looked up through a join.
        razorpay_payment_id="",
        signature="",
        via=SettledVia.UPI_MANUAL,
    )

    on_payment_settled(locked)

    logger.warning(
        # Warning rather than info, deliberately. This is the one settlement
        # that rests on a person rather than a signature, and a run of them is
        # something an operator should be able to see in a log without looking
        # for it.
        "Payment %s settled MANUALLY by user %s against UTR %s",
        locked.receipt_number, confirmed_by.pk, reference,
    )
    return locked


# ---------------------------------------------------------------------------
# Refunds
# ---------------------------------------------------------------------------


@transaction.atomic
def refund_payment(payment: Payment, *, reason: str = "") -> bool:
    """Give a settled payment back. Returns whether anything moved.

    ---------------------------------------------------------------------------
    THE LEDGER IS WRITTEN EVEN IF THE GATEWAY CANNOT BE REACHED
    ---------------------------------------------------------------------------
    Deliberately the opposite way round from settlement. PAID is only ever
    reached through a verified signature, because the party asserting success
    benefits from lying about it. A refund has no such incentive problem — the
    platform is giving money back — and the failure modes are asymmetric: a
    ledger that says "refunded" while Razorpay has not yet processed it is
    reconciled by the ``refund.*`` webhook that follows, whereas a refund the
    ledger never recorded is money nobody knows to chase.

    So the local record is authoritative for *our* books and the gateway call is
    best-effort. In test mode, which is where this deployment lives
    (docs/free-tier-constraints.md §6), there is no real money to move at all.
    """
    locked = Payment.objects.select_for_update().get(pk=payment.pk)
    if locked.status != PaymentStatus.PAID:
        return False

    if locked.razorpay_payment_id:
        try:
            gateway.create_refund(
                payment_id=locked.razorpay_payment_id,
                amount_paise=locked.total_paise,
                notes={"receipt_number": locked.receipt_number, "reason": reason[:120]},
            )
        except gateway.GatewayError:
            # Recorded and left for reconciliation rather than raised — see the
            # docstring. The operator sees an unmatched refund in the ledger,
            # which is a question they can answer.
            logger.warning(
                "Razorpay refund failed for %s; recorded locally anyway",
                locked.receipt_number,
            )

    locked.mark_refunded()
    if reason:
        locked.note = f"{locked.note} {reason}".strip()[:300]
        locked.save(update_fields=["note", "updated_at"])

    logger.info("Payment %s refunded: %s", locked.receipt_number, reason)
    return True


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


def _payment_for_qr_event(payload: dict) -> Payment | None:
    """Find the row a ``qr_code.*`` webhook refers to.

    Needs its own resolver because a QR payment has **no order**, and Razorpay
    does not put the QR id on the payment entity — it is on the sibling
    ``qr_code`` entity. So :func:`_payment_for_event` cannot see it, and a QR
    credit resolved through that function would silently match nothing.

    Two routes home, in order of reliability:

    1. the QR code id, which we stored when we opened it, and
    2. the ``reference`` we put in ``notes``, which Razorpay echoes on both
       entities — the fallback for a code opened before the id column existed,
       or restored from a backup.
    """
    qr_entity = payload.get("payload", {}).get("qr_code", {}).get("entity", {})
    payment_entity = payload.get("payload", {}).get("payment", {}).get("entity", {})

    qr_id = qr_entity.get("id") or ""
    if qr_id:
        found = Payment.objects.filter(razorpay_qr_code_id=qr_id).first()
        if found is not None:
            return found

    notes = qr_entity.get("notes") or payment_entity.get("notes") or {}
    reference = notes.get("reference") or ""
    if reference:
        return Payment.objects.filter(pk=reference).first()
    return None


def _payment_for_link_event(payload: dict) -> Payment | None:
    """Find the row a ``payment_link.*`` webhook refers to.

    A third resolver, for the same reason there is a second: a payment link
    creates *its own* Razorpay order, so the ``order_id`` on the payment entity
    is one we have never seen and ``_payment_for_event`` would match nothing.

    ``reference_id`` is tried first because we set it to the ``Payment`` id when
    the link was opened, and it survives even if the link row is later reissued.
    """
    link_entity = payload.get("payload", {}).get("payment_link", {}).get("entity", {})

    reference = link_entity.get("reference_id") or ""
    if reference:
        found = Payment.objects.filter(pk=reference).first()
        if found is not None:
            return found

    link_id = link_entity.get("id") or ""
    if link_id:
        return Payment.objects.filter(razorpay_payment_link_id=link_id).first()
    return None


@transaction.atomic
def apply_webhook(event: WebhookEvent) -> Payment | None:
    """Apply a verified webhook to the ledger.

    Only called for events whose signature already checked out. Idempotent by
    construction: ``WebhookEvent.event_id`` is unique, so a retried delivery is
    recognised before this runs, and ``mark_paid`` is itself idempotent for the
    case where two different events both report success.
    """
    payload = event.payload if isinstance(event.payload, dict) else {}
    entity = payload.get("payload", {}).get("payment", {}).get("entity", {})

    # Three ways a payment finds its row, because Razorpay's three objects carry
    # our identifier in three different places. Both of the scan paths would
    # resolve to nothing through `_payment_for_event`, which is what would leave
    # a scanned payment stranded.
    if event.event_type.startswith("qr_code."):
        payment = _payment_for_qr_event(payload)
    elif event.event_type.startswith("payment_link."):
        payment = _payment_for_link_event(payload)
    else:
        payment = _payment_for_event(entity)

    if payment is None:
        # Worth recording rather than swallowing: a payment event with no
        # matching row means either a stray webhook or a lost order id.
        event.mark_processed(error="No matching payment for this event.")
        logger.warning("Webhook %s matched no payment", event.event_id)
        return None

    event.payment = payment
    event.save(update_fields=["payment", "updated_at"])

    if event.event_type in {
        "payment.captured",
        "payment.authorized",
        "qr_code.credited",
        "payment_link.paid",
    }:
        # mark_paid is idempotent, so a webhook arriving after the client's own
        # confirmation is a no-op — and the worker is only told once.
        #
        # `qr_code.credited` is here rather than in a branch of its own because
        # once the row is found, a QR credit *is* a capture: money arrived, the
        # message was signature-verified, and everything downstream — the
        # emergency broadcast, the worker's notification — must happen exactly
        # as it does for a card.
        if payment.mark_paid(
            razorpay_payment_id=entity.get("id", ""),
            signature="webhook",
            via=SettledVia.WEBHOOK,
        ):
            on_payment_settled(payment)
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


def daily_rate_paise(engagement, day: dt.date | None = None) -> int:
    """What one day of this engagement is worth, under either rate basis.

    Called by leave settlement (Module 4.6) and by the replacement split (8.5),
    neither of which should have to learn that hourly terms exist. One function,
    two branches, so both keep working when an engagement opts into hourly.

    The hourly branch **includes the visit fee**, and that is not a detail: a
    replacement worker covering a single day travels exactly as far as the
    regular worker would have. Paying her only for the hours would hand her the
    precise unfairness the visit fee was introduced to remove — a short covering
    shift at a poor effective rate — and it would do it to whoever was standing
    in at short notice.

    ``day`` is only consulted on hourly terms, where a day's worth depends on
    what was scheduled for it. It defaults to today, which is what every
    same-day caller (a leave request, a replacement) actually means.
    """
    from apps.hiring.models import RateBasis

    if engagement.rate_basis != RateBasis.HOURLY:
        # Divided by the visits actually scheduled in a month rather than by 30:
        # a worker who comes twice a week is not paid a thirtieth of their month
        # for one visit.
        per_month = max(1, len(engagement.days_of_week) * 4)
        return rupees_to_paise(engagement.monthly_rate) // per_month

    from .hourly import session_paise

    day = day or timezone.localdate()
    minutes = engagement.scheduled_minutes_on(day)
    if not minutes:
        # Nothing was scheduled that day, so no hours are owed — but if somebody
        # is asking what the day is worth, a visit is contemplated, and a visit
        # costs her the journey either way.
        return rupees_to_paise(engagement.visit_fee)

    rate = rupees_to_paise(engagement.hourly_rate)
    return session_paise(minutes, rate) + rupees_to_paise(engagement.visit_fee)


__all__ = [
    "AlreadyPaid",
    "AmountMismatch",
    "NothingToPay",
    "PaymentError",
    "SalaryBasis",
    "SignatureInvalid",
    "UtrAlreadyUsed",
    "apply_webhook",
    "confirm_checkout",
    "confirm_upi_settlement",
    "create_payment",
    "daily_rate_paise",
    "on_payment_settled",
    "open_order",
    "payment_due_at",
    "record_webhook",
    "refund_payment",
    "salary_basis",
    "split_for_replacement",
]
