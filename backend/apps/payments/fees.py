"""
Module 8.7 — what Sathify charges, in one greppable place.

-------------------------------------------------------------------------------
WHY THIS SHIPS AT A ZERO RATE
-------------------------------------------------------------------------------
"A fee exists" and "the fee has a rate" are two different changes, and doing
them together is how a receipt layout gets designed under deadline pressure on
the day pricing goes live. So the column, the calculation and the receipt line
land now, computing zero; turning them on later is one setting.

:data:`BOOKING_FEE_RATE` is therefore the *intended* price, not the live one.
:func:`platform_fee_paise` returns 0 until ``PLATFORM_FEES_ENABLED`` is true.

-------------------------------------------------------------------------------
WHAT IS NEVER CHARGED, AND WHY
-------------------------------------------------------------------------------
The fee applies to one-day bookings and nothing else. A booking is a genuine
transaction: the platform found somebody, for one job, and there is no ongoing
relationship to disintermediate.

A recurring salary is not that. It is a wage transfer between two people who
already know each other, and a percentage of it is:

* a large fraction of the worker's income and invisible to the resident,
* trivially avoided — the informal market charges nothing and is one phone call
  away, so the fee does not collect revenue, it collects churn, and
* charged for a match the platform made once, months ago.

Tips are exempt for a stronger reason still: the app tells residents that all of
it reaches the worker. That claim is checkable.
"""

from __future__ import annotations

from django.conf import settings

from .models import PaymentKind

#: Fraction of a one-day booking added as a convenience fee. The *intended*
#: price — see the module docstring. Not live until PLATFORM_FEES_ENABLED.
BOOKING_FEE_RATE = 0.08

#: Ceiling, in paise. Without it a ₹2,000 deep-clean carries a ₹160 fee, which
#: is not twenty times more platform than a ₹100 one.
BOOKING_FEE_CAP_PAISE = 4_000

#: Kinds that are never charged, whatever the rate is set to:
#:
#:   ENGAGEMENT_SALARY   — a wage transfer between two people.
#:   TIP                 — the app promises all of it reaches the worker.
#:   REPLACEMENT         — already a deduction from somebody's day.
#:   REFUND              — charging a fee to undo a charge is indefensible.
#:   EMERGENCY_SURCHARGE — already the platform's own fee (Module 5.5). A
#:                         percentage on top of it would be a fee on a fee, and
#:                         the household would see two platform lines on one
#:                         charge with no way to tell them apart.
FEE_EXEMPT_KINDS = frozenset(
    {
        PaymentKind.ENGAGEMENT_SALARY,
        PaymentKind.TIP,
        PaymentKind.REPLACEMENT,
        PaymentKind.REFUND,
        PaymentKind.EMERGENCY_SURCHARGE,
    }
)


def fees_enabled() -> bool:
    """Whether any fee is currently charged. Off unless explicitly switched on."""
    return bool(getattr(settings, "PLATFORM_FEES_ENABLED", False))


def platform_fee_paise(*, kind: str, amount_paise: int, society=None) -> int:
    """The fee on one payment, in paise. Zero for every exempt case.

    Rounds **down**: where a fraction of a paise exists it stays with the people
    in the transaction rather than the platform, consistent with
    ``ReplacementSplit.split`` sending its remainder to whoever did the work.

    ``society`` is accepted so a subscription tier can waive the fee — Plus
    bundles it — without every call site having to know that rule.
    """
    if kind in FEE_EXEMPT_KINDS or amount_paise <= 0:
        return 0
    if not fees_enabled():
        return 0

    subscription = getattr(society, "subscription", None)
    if subscription is not None and subscription.waives_booking_fee:
        return 0

    return min(int(amount_paise * BOOKING_FEE_RATE), BOOKING_FEE_CAP_PAISE)


def quote(*, kind: str, amount_paise: int, society=None) -> dict:
    """What the resident is shown *before* they confirm.

    A fee discovered on the receipt is worse than a larger fee disclosed up
    front, so this exists to be rendered on the confirmation screen rather than
    reconstructed there from a rate the client would have to know.
    """
    fee = platform_fee_paise(kind=kind, amount_paise=amount_paise, society=society)
    return {
        "amount_paise": amount_paise,
        "platform_fee_paise": fee,
        "total_paise": amount_paise + fee,
        # Named so the screen can say "Platform fee" and stay silent at zero.
        "fee_applies": fee > 0,
    }


__all__ = [
    "BOOKING_FEE_CAP_PAISE",
    "BOOKING_FEE_RATE",
    "FEE_EXEMPT_KINDS",
    "fees_enabled",
    "platform_fee_paise",
    "quote",
]
