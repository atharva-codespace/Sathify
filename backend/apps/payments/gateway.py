"""
Module 8.1 — the Razorpay boundary.

-------------------------------------------------------------------------------
WHY THIS DOES NOT USE THE RAZORPAY SDK
-------------------------------------------------------------------------------
``razorpay==1.4.2`` imports ``pkg_resources`` at module load. setuptools 81
removed that module, and it does not exist on this project's Python 3.13 — the
SDK cannot be imported here at all. Pinning an old setuptools to keep a thin
convenience wrapper alive would be the wrong trade, so this module talks to
Razorpay's REST API with ``requests`` (already a dependency) and does signature
verification with stdlib ``hmac``.

That turns out to be the better design regardless: the security-critical half of
this file needs no network, no SDK, and no mocking to test.

-------------------------------------------------------------------------------
SIGNATURE VERIFICATION IS THE WHOLE POINT
-------------------------------------------------------------------------------
A client telling the server "that payment succeeded" is the party who benefits
from lying about it. Nothing here trusts a status that did not arrive with a
valid HMAC signed by a secret only Razorpay and this server share.

Both comparisons use :func:`hmac.compare_digest`. A plain ``==`` on a signature
leaks its correct prefix through timing, which is enough to forge one given
patience — and forging one here means taking a worker's wages.
"""

from __future__ import annotations

import hashlib
import hmac
import logging

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

RAZORPAY_ORDERS_URL = "https://api.razorpay.com/v1/orders"
RAZORPAY_PAYMENTS_URL = "https://api.razorpay.com/v1/payments"
RAZORPAY_QR_CODES_URL = "https://api.razorpay.com/v1/payments/qr_codes"
RAZORPAY_PAYMENT_LINKS_URL = "https://api.razorpay.com/v1/payment_links"

#: How long a payment QR stays scannable.
#:
#: Razorpay allows 2 minutes to 2 hours on a single-use code. Half an hour is
#: long enough for somebody to fetch another phone or walk to a neighbour, and
#: short enough that a screenshot forwarded later is simply dead rather than a
#: live claim on somebody's account.
QR_VALID_SECONDS = 30 * 60

#: Razorpay is a payment gateway on the far side of a mobile network. Long
#: enough to survive a slow round trip, short enough that a resident is not
#: left staring at a spinner.
REQUEST_TIMEOUT_SECONDS = 20


class GatewayError(Exception):
    """Base for anything that stops a payment being set up."""

    code = "gateway_error"


class GatewayUnavailable(GatewayError):
    """No usable configuration, or Razorpay could not be reached."""

    code = "gateway_unavailable"


class LiveKeyRefused(GatewayError):
    """A live key was supplied while the project is in test mode."""

    code = "live_key_refused"


def _config() -> dict:
    return getattr(settings, "RAZORPAY_SETTINGS", {})


def is_configured() -> bool:
    """Whether real orders can be created at all."""
    config = _config()
    return bool(config.get("KEY_ID") and config.get("KEY_SECRET"))


def assert_not_live() -> None:
    """The guard rail ``RAZORPAY_SETTINGS["TEST_MODE"]`` exists for.

    This project is an academic build with no real settlement account. If
    TEST_MODE is on but a live key has been pasted in, that is somebody about to
    charge a real card by accident — so it fails loudly rather than proceeding.
    """
    config = _config()
    if config.get("TEST_MODE", True) and str(config.get("KEY_ID", "")).startswith(
        "rzp_live_"
    ):
        raise LiveKeyRefused(
            "A live Razorpay key is configured while RAZORPAY_TEST_MODE is on. "
            "Use a test key, or set RAZORPAY_TEST_MODE=False deliberately."
        )


def create_order(*, amount_paise: int, receipt: str, notes: dict | None = None) -> dict:
    """Open a Razorpay order. Returns the gateway's response.

    ``amount_paise`` is passed straight through: Razorpay counts in paise too,
    which is exactly why this module does (see models.py).
    """
    assert_not_live()

    if not is_configured():
        raise GatewayUnavailable(
            "Razorpay is not configured on this server. Set RAZORPAY_KEY_ID and "
            "RAZORPAY_KEY_SECRET."
        )
    if amount_paise <= 0:
        raise GatewayError("A payment must be for more than zero.")

    config = _config()
    payload = {
        "amount": int(amount_paise),
        "currency": config.get("CURRENCY", "INR"),
        # Our own id, echoed back on every webhook — it is how a gateway event
        # is matched to a ledger row.
        "receipt": receipt,
        "notes": notes or {},
    }

    try:
        response = requests.post(
            RAZORPAY_ORDERS_URL,
            json=payload,
            auth=(config["KEY_ID"], config["KEY_SECRET"]),
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        # Network failure is not a payment failure. The order was never opened,
        # so the caller leaves the ledger row CREATED and the resident retries.
        logger.warning("Could not reach Razorpay: %s", exc)
        raise GatewayUnavailable(
            "We could not reach the payment provider. Please try again."
        ) from exc

    if response.status_code >= 400:
        detail = ""
        try:
            detail = response.json().get("error", {}).get("description", "")
        except ValueError:
            detail = response.text[:200]
        logger.error("Razorpay refused an order (%s): %s", response.status_code, detail)
        raise GatewayError(detail or "The payment provider refused this order.")

    return response.json()


def create_qr_code(
    *, amount_paise: int, reference: str, description: str = "", notes: dict | None = None
) -> dict:
    """Open a single-use UPI QR that Razorpay hosts and watches.

    ---------------------------------------------------------------------------
    WHY THE GATEWAY DRAWS THIS QR AND NOT US
    ---------------------------------------------------------------------------
    Sathify used to build its own ``upi://pay`` string against a plain VPA and
    render it on the phone. That worked — any UPI app scanned it — but the money
    landed in a bank account with **no callback of any kind**, so nothing could
    move the payment to PAID except a human reading a statement.

    A Razorpay QR is the same scan for the payer and a completely different
    thing for us: the money arrives inside the gateway, and the gateway sends a
    signed ``qr_code.credited`` webhook. Settlement goes back to being automatic
    and signature-verified, which is the property the direct-VPA path could
    never have.

    ``single_use`` with ``fixed_amount`` is deliberate and does two jobs: the
    code closes itself the moment it is paid, so a forwarded screenshot cannot
    collect twice, and Razorpay refuses any amount other than the one asked for,
    so it cannot be paid for the wrong sum.

    ``reference`` and the receipt travel in ``notes``, which Razorpay echoes on
    the webhook — a second way home if the QR id lookup ever misses.
    """
    assert_not_live()

    if not is_configured():
        raise GatewayUnavailable(
            "Razorpay is not configured on this server. Set RAZORPAY_KEY_ID and "
            "RAZORPAY_KEY_SECRET."
        )
    if amount_paise <= 0:
        raise GatewayError("A payment must be for more than zero.")

    import time

    config = _config()
    payload = {
        "type": "upi_qr",
        "name": "Sathify",
        "usage": "single_use",
        "fixed_amount": True,
        "payment_amount": int(amount_paise),
        "description": (description or "Sathify payment")[:2048],
        "close_by": int(time.time()) + QR_VALID_SECONDS,
        "notes": {"reference": reference, **(notes or {})},
    }

    try:
        response = requests.post(
            RAZORPAY_QR_CODES_URL,
            json=payload,
            auth=(config["KEY_ID"], config["KEY_SECRET"]),
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        logger.warning("Could not reach Razorpay to open a QR: %s", exc)
        raise GatewayUnavailable(
            "We could not reach the payment provider. Please try again."
        ) from exc

    if response.status_code >= 400:
        detail = ""
        try:
            detail = response.json().get("error", {}).get("description", "")
        except ValueError:
            detail = response.text[:200]
        logger.error("Razorpay refused a QR code (%s): %s", response.status_code, detail)
        raise GatewayError(detail or "The payment provider refused this QR code.")

    return response.json()


def create_payment_link(
    *, amount_paise: int, reference: str, description: str = "", notes: dict | None = None
) -> dict:
    """Open a hosted payment page for one payment. Returns Razorpay's response.

    ---------------------------------------------------------------------------
    THE FALLBACK WHEN THE QR CODES API IS NOT AVAILABLE
    ---------------------------------------------------------------------------
    :func:`create_qr_code` is the better artefact — it produces a UPI code a
    payer scans directly in their own app. But the QR Codes API has to be
    switched on per Razorpay account, and on an account without it every call
    returns 400 and the payment screen has no code to show at all.

    Payment Links are enabled far more widely, and they reach the same place by
    one more hop: the link is rendered as a QR, the other phone scans it, and
    Razorpay's own hosted page collects by UPI, card or netbanking. Settlement is
    still a signed ``payment_link.paid`` webhook, so nothing about the trust
    model changes.

    ``reference_id`` carries our ``Payment`` id, which is what the webhook is
    matched on. ``notify`` is switched off deliberately: Razorpay would otherwise
    SMS and email the customer directly, which is a second, unbranded channel
    telling somebody about money — Module 10 owns that conversation.
    """
    assert_not_live()

    if not is_configured():
        raise GatewayUnavailable(
            "Razorpay is not configured on this server. Set RAZORPAY_KEY_ID and "
            "RAZORPAY_KEY_SECRET."
        )
    if amount_paise <= 0:
        raise GatewayError("A payment must be for more than zero.")

    import time

    config = _config()
    payload = {
        "amount": int(amount_paise),
        "currency": config.get("CURRENCY", "INR"),
        "description": (description or "Sathify payment")[:2048],
        # Ours, and the route home on the webhook.
        "reference_id": reference,
        "expire_by": int(time.time()) + QR_VALID_SECONDS,
        "notify": {"sms": False, "email": False},
        "reminder_enable": False,
        "notes": {"reference": reference, **(notes or {})},
    }

    try:
        response = requests.post(
            RAZORPAY_PAYMENT_LINKS_URL,
            json=payload,
            auth=(config["KEY_ID"], config["KEY_SECRET"]),
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        logger.warning("Could not reach Razorpay to open a payment link: %s", exc)
        raise GatewayUnavailable(
            "We could not reach the payment provider. Please try again."
        ) from exc

    if response.status_code >= 400:
        detail = ""
        try:
            detail = response.json().get("error", {}).get("description", "")
        except ValueError:
            detail = response.text[:200]
        logger.error(
            "Razorpay refused a payment link (%s): %s", response.status_code, detail
        )
        raise GatewayError(detail or "The payment provider refused this link.")

    return response.json()


def create_refund(
    *, payment_id: str, amount_paise: int, notes: dict | None = None
) -> dict:
    """Ask Razorpay to refund a captured payment.

    Best-effort by contract: the caller records the refund in the ledger whether
    or not this succeeds, and reconciles from the ``refund.*`` webhook. See
    ``services.refund_payment`` for why that asymmetry with settlement is
    deliberate rather than sloppy.

    Raises :class:`GatewayError` on anything that is not a success, so the caller
    can log the difference between "refunded at the gateway" and "recorded here
    only" — a distinction an operator needs and a user never sees.
    """
    assert_not_live()

    if not is_configured():
        raise GatewayUnavailable("Razorpay is not configured on this server.")

    config = _config()
    try:
        response = requests.post(
            f"{RAZORPAY_PAYMENTS_URL}/{payment_id}/refund",
            json={"amount": int(amount_paise), "notes": notes or {}},
            auth=(config["KEY_ID"], config["KEY_SECRET"]),
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        raise GatewayUnavailable("Could not reach the payment provider.") from exc

    if response.status_code >= 400:
        detail = ""
        try:
            detail = response.json().get("error", {}).get("description", "")
        except ValueError:
            detail = response.text[:200]
        raise GatewayError(detail or "The payment provider refused this refund.")

    return response.json()


def verify_checkout_signature(
    *, order_id: str, payment_id: str, signature: str
) -> bool:
    """Verify the signature Razorpay Checkout hands back to the client.

    Razorpay signs ``"{order_id}|{payment_id}"`` with the API key secret. This
    is what lets the app report its own success without that report being
    trustworthy on its own — the signature is the part that is.
    """
    config = _config()
    secret = config.get("KEY_SECRET", "")
    if not secret or not order_id or not payment_id or not signature:
        return False

    expected = hmac.new(
        secret.encode("utf-8"),
        f"{order_id}|{payment_id}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    return hmac.compare_digest(expected, signature)


def verify_webhook_signature(*, raw_body: bytes, signature: str) -> bool:
    """Verify a webhook against the webhook secret.

    ``raw_body`` must be the **exact bytes received**. Re-serialising the parsed
    JSON changes key order and whitespace, which changes the digest, which
    rejects every genuine webhook — a mistake that looks like Razorpay being
    broken rather than like a bug here.

    Note the webhook secret is a *different* secret from the API key secret;
    Razorpay issues it separately when the webhook is registered.
    """
    secret = _config().get("WEBHOOK_SECRET", "")
    if not secret or not signature:
        return False

    expected = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def checkout_payload(*, order: dict, amount_paise: int, name: str) -> dict:
    """What the Flutter app needs to open Razorpay Checkout.

    The key *id* is public by design — it identifies the merchant in the
    checkout sheet. The key secret never leaves this server, which is the reason
    order creation happens here rather than in the app.
    """
    config = _config()
    return {
        "key": config.get("KEY_ID", ""),
        "order_id": order.get("id", ""),
        "amount": amount_paise,
        "currency": config.get("CURRENCY", "INR"),
        "name": "Sathify",
        "description": name,
        "test_mode": bool(config.get("TEST_MODE", True)),
    }


__all__ = [
    "GatewayError",
    "GatewayUnavailable",
    "LiveKeyRefused",
    "assert_not_live",
    "checkout_payload",
    "create_order",
    "create_payment_link",
    "create_qr_code",
    "create_refund",
    "is_configured",
    "verify_checkout_signature",
    "verify_webhook_signature",
]
