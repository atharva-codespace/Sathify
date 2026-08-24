"""
Module 8.10 — the operational loop around hourly invoices.

Four jobs, in the order a month runs them:

1. :func:`close_stale_sessions` — nightly, shuts sessions nobody stopped.
2. :func:`accrue_session` — prices a closed session onto the period's draft.
3. :func:`close_period` — opens the review window; later, issues.
4. :func:`resolve_query` — releases a hold and carries the correction forward.

They live here rather than in ``services.py`` because that module is about
moving money through Razorpay, and this one is about deciding what is owed.
Keeping the decision separate from the settlement is what lets the whole of this
file be tested without a gateway.
"""

from __future__ import annotations

import datetime as dt
import logging

from django.db import transaction
from django.utils import timezone

from apps.attendance.models import SessionSource, SessionStatus, WorkSession

from .hourly import BillingConfig, SessionTiming, price_session
from .models import Invoice, InvoiceStatus, QueryStage
from .services import payment_due_at

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 1. The nightly close
# ---------------------------------------------------------------------------


def close_stale_sessions(*, now=None, society=None) -> int:
    """Close sessions nobody stopped. Returns how many were closed.

    Forgetting to tap Stop is the commonest failure in any attendance product,
    and the tempting fix — bill until the session is closed — is the one that
    must not be built: it turns a lapse of memory into an open-ended charge
    against a resident and an unearned credit to a worker, and both parties
    discover it at month end.

    So a stale session closes **at its scheduled departure**, never at the
    current time, and is flagged for review. She is paid her scheduled hours.
    Any genuine overtime she worked needed the resident's approval before it was
    worked (rule 5), so nothing legitimate is lost by closing early.
    """
    now = now or timezone.now()
    closed = 0

    queryset = WorkSession.objects.filter(status=SessionStatus.OPEN).select_related(
        "engagement", "society"
    )
    if society is not None:
        queryset = queryset.filter(society=society)

    for session in queryset:
        config = BillingConfig.from_society(session.society)
        timing = SessionTiming.for_engagement(session.engagement)

        scheduled_end = timezone.make_aware(
            dt.datetime.combine(session.visit_date, timing.departure),
            timezone.get_current_timezone(),
        )
        if scheduled_end < (session.started_at or scheduled_end):
            scheduled_end += dt.timedelta(days=1)

        from apps.societies.models import SocietyBillingConfig

        grace = SocietyBillingConfig.for_society(session.society).autoclose_after_minutes
        if now < scheduled_end + dt.timedelta(minutes=grace):
            continue

        with transaction.atomic():
            session.close(at=scheduled_end, auto=True)
            price_session(session, timing=timing, config=config)
        closed += 1

    if closed:
        logger.info("Auto-closed %d stale work session(s).", closed)
    return closed


def mark_no_shows(*, day: dt.date, society=None) -> int:
    """Record scheduled visits that produced no session and had no leave.

    Written as its own pass rather than folded into the nightly close because
    the two are different claims: one says "she was here and nobody stopped the
    clock", the other says "she was not here at all". Conflating them would let
    a capture failure be recorded as an absence, which costs her the day.
    """
    from apps.hiring.models import Engagement, EngagementStatus, RateBasis
    from apps.scheduling.models import LeaveRequest

    engagements = Engagement.objects.filter(
        status=EngagementStatus.ACTIVE, rate_basis=RateBasis.HOURLY
    ).select_related("society", "worker")
    if society is not None:
        engagements = engagements.filter(society=society)

    created = 0
    for engagement in engagements:
        if not engagement.occurs_on(day):
            continue
        if WorkSession.objects.filter(engagement=engagement, visit_date=day).exists():
            continue
        # `live()` is any leave that was not withdrawn — approved, waived,
        # awaiting a replacement or unfilled. All of them mean she told somebody
        # she was not coming, which is the opposite of a no-show whatever
        # happened to the cover arrangements afterwards.
        on_leave = (
            LeaveRequest.objects.live()
            .filter(engagement=engagement, leave_date=day)
            .exists()
        )
        if on_leave:
            continue

        WorkSession.objects.create(
            society=engagement.society,
            engagement=engagement,
            worker=engagement.worker,
            visit_date=day,
            source=SessionSource.DERIVED,
            status=SessionStatus.NO_SHOW,
            needs_review=True,
            review_note="No check-in and no leave request. Confirm before this stands.",
        )
        created += 1
    return created


# ---------------------------------------------------------------------------
# 2-3. Accrual and the period close
# ---------------------------------------------------------------------------


def draft_invoice_for(engagement, *, period_start: dt.date, period_end: dt.date) -> Invoice:
    """The engagement's draft for this period, created on first use."""
    invoice, _created = Invoice.objects.get_or_create(
        engagement=engagement,
        period_start=period_start,
        period_end=period_end,
        defaults={
            "society": engagement.society,
            "resident": engagement.resident,
            "worker": engagement.worker,
        },
    )
    return invoice


@transaction.atomic
def accrue_session(session, *, period_start: dt.date, period_end: dt.date) -> Invoice | None:
    """Price a session and put it on the period's draft invoice.

    Both halves are idempotent — ``price_session`` by ``priced_at`` and
    ``add_session`` by the existing line — so re-running an accrual after a
    partial failure cannot double-charge anybody.
    """
    from apps.hiring.models import RateBasis

    if session.engagement.rate_basis != RateBasis.HOURLY:
        return None

    price_session(session)
    invoice = draft_invoice_for(
        session.engagement, period_start=period_start, period_end=period_end
    )
    if invoice.status != InvoiceStatus.DRAFT:
        # The period already closed. A late-arriving session cannot be slipped
        # into a frozen bill; it becomes an adjustment on the next one instead.
        logger.info(
            "Session %s arrived after invoice %s closed; leaving for adjustment.",
            session.pk, invoice.number,
        )
        return invoice

    invoice.add_session(session)
    return invoice


def close_period(invoice: Invoice, *, hours: int | None = None) -> Invoice:
    """Stop accrual and open the review window."""
    if hours is None:
        from apps.societies.models import SocietyBillingConfig

        hours = SocietyBillingConfig.for_society(invoice.society).review_window_hours
    invoice.open_review(hours=hours)
    return invoice


def issue_after_review(invoice: Invoice, *, now=None):
    """Issue once the window has passed. Returns the Payment, or None.

    Held lines stay held: the payment raised here is for the undisputed
    remainder only, which is the whole point of the hold.
    """
    now = now or timezone.now()
    if invoice.status != InvoiceStatus.REVIEW:
        return None
    if invoice.review_closes_at and invoice.review_closes_at > now:
        return None

    due_at = payment_due_at(
        kind="engagement_salary",
        engagement=invoice.engagement,
        period_end=invoice.period_end,
    )
    return invoice.issue(due_at=due_at)


# ---------------------------------------------------------------------------
# 4. Resolving a query
# ---------------------------------------------------------------------------


@transaction.atomic
def resolve_query(query, *, resolution: str, by=None, adjustment_paise: int = 0):
    """Settle a query, release its hold, and carry any correction forward.

    The correction never edits the invoice it came from. If that invoice is
    still open the hold simply lifts and the line bills normally; if it has been
    issued, the adjustment lands on the *next* draft, carrying the query id that
    produced it. Same rule ``AttendanceEvent`` sets: a wrong entry is corrected
    by a superseding one, so a resident who queries a three-month-old charge is
    shown the number that actually happened plus the answer to it.
    """
    if not query.is_open:
        return None

    query.resolve(resolution=resolution, by=by, adjustment_paise=adjustment_paise)

    invoice = query.invoice
    if invoice is None:
        return None

    invoice.lines.filter(session=query.session).update(is_held=False)
    invoice.recalculate()

    if adjustment_paise and invoice.status in {InvoiceStatus.ISSUED, InvoiceStatus.SETTLED}:
        following = draft_invoice_for(
            invoice.engagement,
            period_start=invoice.period_end + dt.timedelta(days=1),
            period_end=invoice.period_end + dt.timedelta(days=31),
        )
        following.add_adjustment(
            amount_paise=adjustment_paise,
            description=f"Adjustment from {invoice.number}",
            query=query,
        )
        return following

    return invoice


def escalate_stale_queries(*, now=None) -> int:
    """Move queries nobody answered to the society administrator.

    Stage three of the §9.4a ladder, and the only one that costs a volunteer
    committee member their evening — which is why the two cheaper stages run
    first and why this only ever sees what they could not settle.
    """
    from .models import SessionQuery

    now = now or timezone.now()
    stale = SessionQuery.objects.filter(
        stage__in=[QueryStage.EVIDENCE, QueryStage.BILATERAL],
        escalates_at__isnull=False,
        escalates_at__lte=now,
    )
    return stale.update(stage=QueryStage.ADMIN)


__all__ = [
    "accrue_session",
    "close_period",
    "close_stale_sessions",
    "draft_invoice_for",
    "escalate_stale_queries",
    "issue_after_review",
    "mark_no_shows",
    "resolve_query",
]
