"""
Module 8.9 — the UPI QR, hosted and watched by Razorpay.

-------------------------------------------------------------------------------
WHAT THIS REPLACED, AND WHY
-------------------------------------------------------------------------------
The first version of this module built a ``upi://pay`` link against a plain VPA
and rendered it on the phone. Every UPI app scanned it, FamApp included, and the
money went straight into a bank account — which was exactly the problem. A
transfer to a VPA produces **no callback of any kind**, so nothing could move the
``Payment`` to PAID except an administrator reading a bank statement and typing
in a UTR. Real money, a real household, and an app that says "unpaid" until a
human intervenes. For an emergency that is worse than a missing feature: the
broadcast is triggered by settlement, so the request would reach nobody.

Routing the QR through Razorpay changes nothing for the payer — they still scan
with whatever UPI app they have — and changes everything for us. The money lands
inside the gateway, and the gateway sends a signed ``qr_code.credited`` webhook
that settles the payment through the same verified path a card payment uses.

-------------------------------------------------------------------------------
FAMPAY STILL WORKS, FOR THE SAME REASON IT EVER DID
-------------------------------------------------------------------------------
FamApp (FamPay) publishes no merchant API — it is a consumer UPI app, so the only
way to "pay with FamPay" was always to give it something standard to read. A
Razorpay UPI QR is exactly that. Nothing about this module is per-app, and a new
UPI app on the market needs no code change here.

-------------------------------------------------------------------------------
THE QR IS SINGLE-USE AND AMOUNT-LOCKED
-------------------------------------------------------------------------------
Razorpay closes the code the moment it is paid and refuses any other amount. So
a screenshot forwarded to somebody else is dead rather than a live claim on an
account, and a code for ₹450 can never settle a ₹4,363 charge. That is the same
guarantee the hand-built link got from putting ``am`` and ``tr`` inside the
string, now enforced by the gateway instead of by us.
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from django.utils import timezone

from . import gateway

logger = logging.getLogger(__name__)

#: UPI apps worth naming on the pay screen, most-used first.
#:
#: Purely presentational, and deliberately kept after the move to Razorpay: a
#: resident looking for their own app needs to see it is supported. Every one of
#: them scans the same QR, so this list is a signpost and never a branch.
KNOWN_UPI_APPS: list[dict] = [
    {"key": "gpay", "label": "Google Pay"},
    {"key": "phonepe", "label": "PhonePe"},
    {"key": "paytm", "label": "Paytm"},
    # The one the brief asked for. A UPI app like any other here, which is
    # precisely why no integration work was needed to support it.
    {"key": "famapp", "label": "FamApp (FamPay)"},
    {"key": "bhim", "label": "BHIM"},
]


class UpiNotConfigured(Exception):
    """Razorpay is not configured, so no QR can be opened."""

    code = "upi_not_configured"


def is_configured() -> bool:
    """Whether a QR can be produced at all.

    Now simply "is Razorpay set up", because there is no separate VPA to hold —
    one less thing to configure, and one less way to point a QR at the wrong
    account.
    """
    return gateway.is_configured()


def format_amount(amount_paise: int) -> str:
    """Paise → the rupee string shown beside the code, e.g. 105050 -> "1050.50".

    ``Decimal`` rather than float division, for the reason ``models.py`` gives:
    a float ledger drifts, and a figure printed beside a QR that disagrees with
    the one inside it is the kind of thing nobody notices until it is disputed.
    """
    rupees = (Decimal(int(amount_paise)) / Decimal(100)).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
    return f"{rupees:.2f}"


@dataclass(frozen=True)
class UpiQr:
    """One scannable code for one payment, however it was produced.

    Two shapes, and the client is told which:

    * ``image_url`` set — Razorpay hosts the QR image; the app loads it.
    * ``payload`` set — the app encodes that string itself. It is a Razorpay
      payment-link URL, and scanning it opens the gateway's own hosted page.

    Only one is ever populated. The distinction matters to the renderer and to
    nothing else: both encode this payment's exact amount and reference, and
    both settle through a signed webhook.
    """

    kind: str
    amount_paise: int
    amount_display: str
    reference: str
    qr_code_id: str = ""
    image_url: str = ""
    payload: str = ""
    expires_at: object | None = None

    def as_dict(self) -> dict:
        return {
            "kind": self.kind,
            "qr_code_id": self.qr_code_id,
            # Razorpay hosts the image on the QR path. The client loads it
            # rather than encoding a string itself, because the string is the
            # gateway's — a locally-drawn code could drift from the one Razorpay
            # is actually watching.
            "image_url": self.image_url,
            # The link path has no hosted image, so the client draws this. It is
            # a URL rather than a payment instruction, so encoding it here is
            # safe in a way re-encoding a `upi://` string would not be.
            "payload": self.payload,
            "amount_paise": self.amount_paise,
            "amount_display": self.amount_display,
            "reference": self.reference,
            "expires_at": self.expires_at,
            "apps": KNOWN_UPI_APPS,
            # Said plainly so the screen can set expectations: unlike the card
            # path, nothing confirms in the app the instant the sheet closes.
            "settles": "webhook",
        }


def qr_for_payment(payment) -> UpiQr:
    """The scannable code for a payment, opening one if needed.

    ---------------------------------------------------------------------------
    TWO WAYS TO GET A CODE, TRIED IN ORDER
    ---------------------------------------------------------------------------
    1. **A Razorpay UPI QR.** The better artefact: the payer scans it straight
       into their own UPI app and pays in one step.
    2. **A Razorpay payment link, rendered as a QR.** One hop longer — the scan
       opens the gateway's hosted page, which then collects by UPI, card or
       netbanking.

    The fallback exists because the QR Codes API is enabled *per account* and is
    off by default. On an account without it every call returns 400, and a
    payment screen with no code at all is a feature that simply does not work
    for that deployment. Payment Links are enabled far more widely, so the
    second path is what most accounts will actually use until somebody asks
    Razorpay to switch the first one on.

    Both settle the same way — a signed webhook — so this choice is about what
    can be produced, never about how much the result is trusted.

    Either code is reused while it is still live, so re-opening the sheet does
    not litter the account and, more usefully, the code somebody already
    photographed keeps working. An expired one is replaced rather than returned:
    a dead code is discovered only after walking to another phone.
    """
    if payment.is_settled:
        raise UpiNotConfigured("This payment has already been settled.")
    if not is_configured():
        raise UpiNotConfigured(
            "Razorpay is not configured on this server, so a QR cannot be opened."
        )

    live = _still_live(payment)
    if live is not None:
        return live

    amount = payment.total_paise
    reference = str(payment.pk)
    description = f"{payment.get_kind_display()} · {payment.receipt_number}"

    try:
        return _open_qr_code(payment, amount, reference, description)
    except (gateway.GatewayUnavailable, gateway.LiveKeyRefused):
        # Not a fallback case, and getting this wrong costs the payer real time.
        # "Razorpay is unreachable" and "a live key is configured in test mode"
        # both mean the *next* call fails identically — so retrying against a
        # different endpoint just spends a second timeout before showing the
        # same error. Only a refusal of this particular API is worth falling
        # through on.
        raise
    except gateway.GatewayError as exc:
        # Overwhelmingly "this account cannot use the QR Codes API", which is a
        # per-account switch rather than anything wrong with the request.
        # Falling through turns a hard failure into a slightly longer scan.
        logger.info(
            "QR Codes unavailable for payment %s (%s); falling back to a "
            "payment link", payment.receipt_number, exc,
        )

    return _open_payment_link(payment, amount, reference, description)


def _still_live(payment) -> UpiQr | None:
    """The payment's existing code, if it has one that has not lapsed."""
    # A minute's headroom: a code about to lapse is one the payer will not
    # finish in time, and reissuing costs one request.
    if not payment.qr_expires_at:
        return None
    if payment.qr_expires_at <= timezone.now() + dt.timedelta(minutes=1):
        return None

    common = {
        "amount_paise": payment.total_paise,
        "amount_display": f"₹{format_amount(payment.total_paise)}",
        "reference": str(payment.pk),
        "expires_at": payment.qr_expires_at,
    }
    if payment.razorpay_qr_code_id and payment.razorpay_qr_image_url:
        return UpiQr(
            kind="razorpay_qr",
            qr_code_id=payment.razorpay_qr_code_id,
            image_url=payment.razorpay_qr_image_url,
            **common,
        )
    if payment.razorpay_payment_link_url:
        return UpiQr(
            kind="payment_link",
            qr_code_id=payment.razorpay_payment_link_id,
            payload=payment.razorpay_payment_link_url,
            **common,
        )
    return None


def _expiry_from(epoch_seconds) -> dt.datetime | None:
    if not epoch_seconds:
        return None
    return dt.datetime.fromtimestamp(epoch_seconds, tz=dt.timezone.utc)


def _open_qr_code(payment, amount: int, reference: str, description: str) -> UpiQr:
    created = gateway.create_qr_code(
        amount_paise=amount,
        reference=reference,
        description=description,
        notes={"receipt_number": payment.receipt_number},
    )

    payment.razorpay_qr_code_id = created.get("id", "")
    payment.razorpay_qr_image_url = created.get("image_url", "")
    payment.qr_expires_at = _expiry_from(created.get("close_by"))
    payment.save(
        update_fields=[
            "razorpay_qr_code_id", "razorpay_qr_image_url", "qr_expires_at",
            "updated_at",
        ]
    )

    logger.info(
        "Opened Razorpay QR %s for payment %s",
        payment.razorpay_qr_code_id, payment.receipt_number,
    )
    return UpiQr(
        kind="razorpay_qr",
        qr_code_id=payment.razorpay_qr_code_id,
        image_url=payment.razorpay_qr_image_url,
        amount_paise=amount,
        amount_display=f"₹{format_amount(amount)}",
        reference=reference,
        expires_at=payment.qr_expires_at,
    )


def _open_payment_link(payment, amount: int, reference: str, description: str) -> UpiQr:
    created = gateway.create_payment_link(
        amount_paise=amount,
        reference=reference,
        description=description,
        notes={"receipt_number": payment.receipt_number},
    )

    payment.razorpay_payment_link_id = created.get("id", "")
    payment.razorpay_payment_link_url = created.get("short_url", "")
    payment.qr_expires_at = _expiry_from(created.get("expire_by"))
    payment.save(
        update_fields=[
            "razorpay_payment_link_id", "razorpay_payment_link_url",
            "qr_expires_at", "updated_at",
        ]
    )

    logger.info(
        "Opened Razorpay payment link %s for payment %s",
        payment.razorpay_payment_link_id, payment.receipt_number,
    )
    return UpiQr(
        kind="payment_link",
        qr_code_id=payment.razorpay_payment_link_id,
        payload=payment.razorpay_payment_link_url,
        amount_paise=amount,
        amount_display=f"₹{format_amount(amount)}",
        reference=reference,
        expires_at=payment.qr_expires_at,
    )


def reconcile_reference(*, reference: str, amount_paise: int):
    """Find the payment a confirmed reference belongs to, or ``None``.

    Retained for the manual break-glass path (``confirm_upi_settlement``), which
    is no longer how UPI normally settles but is still how an operator closes out
    a payment whose webhook never arrived — a free-tier instance can be asleep,
    and Razorpay eventually stops retrying.

    Deliberately narrow and deliberately not a settlement function: it answers
    "which row does this reference name, and is the amount the one that row asked
    for". The amount check is what stops an old reference being applied to a
    different charge.
    """
    from .models import Payment

    payment = Payment.objects.filter(pk=reference).first()
    if payment is None:
        logger.info("Reference %s matched no payment", reference)
        return None

    if payment.total_paise != amount_paise:
        logger.warning(
            "Reference %s presented %s paise against a %s paise payment",
            reference, amount_paise, payment.total_paise,
        )
        return None

    return payment


__all__ = [
    "KNOWN_UPI_APPS",
    "UpiNotConfigured",
    "UpiQr",
    "format_amount",
    "is_configured",
    "qr_for_payment",
    "reconcile_reference",
]
