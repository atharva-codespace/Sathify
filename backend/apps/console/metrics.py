"""
Module 14.1 — the numbers behind the console's Overview.

-------------------------------------------------------------------------------
GMV AND REVENUE ARE NEVER ADDED TOGETHER
-------------------------------------------------------------------------------
Two different totals move through this platform and only one of them is income:

* **GMV** — wages flowing resident to worker. Sathify earns nothing on it.
  ``docs/monetisation.md`` is explicit that per-transaction commission on
  recurring wages must not be built, and ``Payment.platform_fee_paise`` is zero
  on every row today, which is that decision showing up in the schema.
* **Revenue** — society subscriptions, and the platform charges in
  ``PLATFORM_KINDS``. This is the line that pays for the company.

They are returned as separate keys and are never summed anywhere in this module.
A dashboard that renders a single "Total ₹" invites the whole company to
optimise the number it does not earn, and the fastest way to prevent that is for
the combined figure to not exist in the first place.
"""

from __future__ import annotations

import datetime as dt

from django.db.models import Count, Q, Sum
from django.utils import timezone

from apps.attendance.models import SOURCE_TIER, SessionStatus, TRUSTED_TIERS, WorkSession
from apps.payments.models import (
    PLATFORM_KINDS,
    Invoice,
    InvoiceStatus,
    Payment,
    PaymentStatus,
    SettledVia,
    SubscriptionTier,
    SocietySubscription,
    WebhookEvent,
)
from apps.societies.models import Society, SocietyStatus

#: Monthly list price per tier, in paise. Held here rather than on the model
#: because it is a commercial fact about today's price list, not a property of a
#: subscription that was sold at whatever price applied when it was sold.
TIER_MRR_PAISE = {
    SubscriptionTier.FREE: 0,
    SubscriptionTier.STANDARD: 150_000,
    SubscriptionTier.PLUS: 400_000,
}

#: How long a settled payment may go without its webhook before it is a gap
#: worth an operator's morning rather than a straggler.
WEBHOOK_GAP_HOURS = 24


def _window(days: int) -> tuple[dt.date, dt.date]:
    today = timezone.localdate()
    return today - dt.timedelta(days=days), today


# ---------------------------------------------------------------------------
# The four tiles
# ---------------------------------------------------------------------------


def revenue_summary() -> dict:
    """What Sathify earns: subscriptions plus platform charges. Not wages."""
    by_tier = {
        row["tier"]: row["n"]
        for row in SocietySubscription.objects.values("tier").annotate(n=Count("id"))
    }
    active_mrr = 0
    for tier, count in by_tier.items():
        active_mrr += TIER_MRR_PAISE.get(tier, 0) * count

    start, end = _window(30)
    platform_charges = (
        Payment.objects.settled()
        .filter(kind__in=PLATFORM_KINDS, paid_at__date__gte=start, paid_at__date__lte=end)
        .aggregate(total=Sum("amount_paise"))["total"]
        or 0
    )

    return {
        "mrr_paise": active_mrr,
        "platform_charges_30d_paise": platform_charges,
        "subscriptions_by_tier": {
            tier: by_tier.get(tier, 0) for tier, _label in SubscriptionTier.choices
        },
        "note": "Subscriptions and platform fees only. Wages are GMV, not revenue.",
    }


def gmv_summary(*, days: int = 30) -> dict:
    """Wages that moved. The platform's cut of this is zero, and says so."""
    start, end = _window(days)
    settled = Payment.objects.settled().exclude(kind__in=PLATFORM_KINDS).filter(
        paid_at__date__gte=start, paid_at__date__lte=end
    )
    totals = settled.aggregate(
        total=Sum("amount_paise"), fees=Sum("platform_fee_paise"), n=Count("id")
    )
    return {
        "settled_paise": totals["total"] or 0,
        "payments": totals["n"] or 0,
        "platform_earned_paise": totals["fees"] or 0,
        "window_days": days,
        "note": "Resident to worker. Sathify earns nothing on this line.",
    }


def society_summary() -> dict:
    counts = Society.objects.aggregate(
        active=Count("id", filter=Q(status=SocietyStatus.ACTIVE)),
        pending=Count("id", filter=Q(status=SocietyStatus.PENDING)),
        suspended=Count("id", filter=Q(status=SocietyStatus.SUSPENDED)),
        total=Count("id"),
    )
    counts["paid"] = SocietySubscription.objects.exclude(
        tier=SubscriptionTier.FREE
    ).count()
    return counts


def worker_payment_summary(*, days: int = 30) -> dict:
    """How many workers were actually paid, and how many are still waiting."""
    start, end = _window(days)
    paid = (
        Payment.objects.settled()
        .exclude(kind__in=PLATFORM_KINDS)
        .filter(paid_at__date__gte=start, paid_at__date__lte=end, worker__isnull=False)
        .values("worker_id")
        .distinct()
        .count()
    )
    unpaid = (
        Payment.objects.filter(
            status__in=[PaymentStatus.CREATED, PaymentStatus.PENDING],
            worker__isnull=False,
        )
        .values("worker_id")
        .distinct()
        .count()
    )
    return {"paid": paid, "awaiting_payment": unpaid, "window_days": days}


# ---------------------------------------------------------------------------
# The work queue — the part of Overview an operator actually acts on
# ---------------------------------------------------------------------------


def webhook_gaps(*, hours: int = WEBHOOK_GAP_HOURS):
    """Payments the gateway settled that no webhook ever confirmed.

    The console's whole reason for existing. Each of these is money that moved
    in the real world and did not move in ours.
    """
    cutoff = timezone.now() - dt.timedelta(hours=hours)
    return (
        Payment.objects.filter(status=PaymentStatus.PAID, paid_at__lte=cutoff)
        .exclude(razorpay_payment_id="")
        .exclude(
            razorpay_payment_id__in=WebhookEvent.objects.filter(processed=True)
            .exclude(payment__isnull=True)
            .values_list("payment__razorpay_payment_id", flat=True)
        )
    )


def unsigned_settlements():
    """Payments resting on an administrator's word rather than an HMAC.

    ``settled_via`` exists precisely so this question is a filter rather than a
    code review, and the console gives it a saved view of its own.
    """
    return Payment.objects.filter(
        status=PaymentStatus.PAID, settled_via=SettledVia.UPI_MANUAL
    )


def societies_over_cap():
    """Free-tier societies past their worker cap. The upgrade conversation."""
    over = []
    for society in Society.objects.filter(status=SocietyStatus.ACTIVE):
        subscription = getattr(society, "subscription", None)
        tier = subscription.effective_tier if subscription else SubscriptionTier.FREE
        from apps.payments.models import TIER_LIMITS

        cap = TIER_LIMITS[tier]["workers"]
        if cap is None:
            continue
        count = society.users.filter(role="worker", is_approved=True).count()
        if count > cap:
            over.append({"society": society, "workers": count, "cap": cap, "tier": tier})
    return over


def needs_attention() -> list[dict]:
    """The console's landing queue, most severe first.

    Every row carries the money or the count at risk, so an operator can triage
    the whole list without opening anything.
    """
    items: list[dict] = []

    gaps = webhook_gaps()
    gap_total = gaps.aggregate(total=Sum("amount_paise"))["total"] or 0
    if gaps.exists():
        items.append({
            "severity": "critical",
            "code": "webhook_gap",
            "label": "Paid at the gateway, no webhook received",
            "count": gaps.count(),
            "amount_paise": gap_total,
        })

    over_cap = societies_over_cap()
    if over_cap:
        items.append({
            "severity": "critical",
            "code": "over_worker_cap",
            "label": "Society over its tier's worker cap",
            "count": len(over_cap),
            "detail": [
                {"society": row["society"].name, "workers": row["workers"], "cap": row["cap"]}
                for row in over_cap[:10]
            ],
        })

    unreviewed = WorkSession.objects.filter(
        needs_review=True, visit_date__gte=timezone.localdate() - dt.timedelta(days=7)
    )
    if unreviewed.exists():
        items.append({
            "severity": "warning",
            "code": "sessions_need_review",
            "label": "Work sessions flagged and unreviewed (7 days)",
            "count": unreviewed.count(),
            "societies": unreviewed.values("society_id").distinct().count(),
        })

    stale_reviews = Invoice.objects.filter(
        status=InvoiceStatus.REVIEW, review_closes_at__lt=timezone.now()
    )
    if stale_reviews.exists():
        items.append({
            "severity": "warning",
            "code": "review_window_lapsed",
            "label": "Invoices past their review window, not yet issued",
            "count": stale_reviews.count(),
            "amount_paise": sum(i.payable_paise for i in stale_reviews),
        })

    held = Invoice.objects.filter(held_paise__gt=0).exclude(status=InvoiceStatus.SETTLED)
    if held.exists():
        items.append({
            "severity": "warning",
            "code": "amounts_held",
            "label": "Wages held pending a query",
            "count": held.count(),
            "amount_paise": held.aggregate(total=Sum("held_paise"))["total"] or 0,
        })

    return items


# ---------------------------------------------------------------------------
# Billing integrity — the leading indicator
# ---------------------------------------------------------------------------


def billing_integrity(*, days: int = 30, society_id=None) -> dict:
    """Whether this platform's wage numbers can be trusted.

    Three measures, and the first is the gate on enabling hourly billing at all:
    below roughly 90% tier-1/2 capture, a wage figure rests on inference rather
    than on anybody's observation, and the party who pays for that is the worker.
    """
    start = timezone.localdate() - dt.timedelta(days=days)
    sessions = WorkSession.objects.filter(visit_date__gte=start)
    if society_id:
        sessions = sessions.filter(society_id=society_id)

    total = sessions.count()
    if not total:
        return {
            "sessions": 0,
            "trusted_capture_rate": None,
            "auto_close_rate": None,
            "flagged_rate": None,
            "by_tier": {},
            "hourly_billing_advised": False,
            "window_days": days,
        }

    by_tier: dict[int, int] = {}
    for source, tier in SOURCE_TIER.items():
        by_tier[tier] = by_tier.get(tier, 0) + sessions.filter(source=source).count()

    trusted = sum(count for tier, count in by_tier.items() if tier in TRUSTED_TIERS)
    auto_closed = sessions.filter(status=SessionStatus.AUTO_CLOSED).count()
    flagged = sessions.filter(needs_review=True).count()

    trusted_rate = round(trusted / total, 4)
    return {
        "sessions": total,
        "trusted_capture_rate": trusted_rate,
        "auto_close_rate": round(auto_closed / total, 4),
        "flagged_rate": round(flagged / total, 4),
        "by_tier": by_tier,
        # The §9.3 threshold, returned as a decision rather than a number the
        # caller has to remember the meaning of.
        "hourly_billing_advised": trusted_rate >= 0.90,
        "window_days": days,
    }


def overview(*, days: int = 30) -> dict:
    return {
        "revenue": revenue_summary(),
        "gmv": gmv_summary(days=days),
        "societies": society_summary(),
        "workers": worker_payment_summary(days=days),
        "needs_attention": needs_attention(),
        "billing_integrity": billing_integrity(days=days),
    }


__all__ = [
    "billing_integrity",
    "gmv_summary",
    "needs_attention",
    "overview",
    "revenue_summary",
    "societies_over_cap",
    "society_summary",
    "unsigned_settlements",
    "webhook_gaps",
    "worker_payment_summary",
]
