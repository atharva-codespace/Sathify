"""
Module 11.4 — the analytics dashboard.

Four panels, all computed on read from the tables that already hold the data:
sentiment, trust distribution, unmet demand, and worker availability.

-------------------------------------------------------------------------------
NOTHING IS PRE-AGGREGATED
-------------------------------------------------------------------------------
No rollup tables, no nightly job, no cache. A society has hundreds of workers
and thousands of events, not millions, and every figure here is one indexed
aggregate query. Pre-computing would buy nothing measurable and would cost the
thing that actually matters at this scale: a dashboard that is never stale and
can never disagree with the list it links to.

It is also the only honest option on a free tier with no scheduler.

-------------------------------------------------------------------------------
EVERY PANEL SAYS WHEN IT HAS NOTHING TO SAY
-------------------------------------------------------------------------------
Each returns an explicit ``has_data`` flag rather than an empty structure the
client has to interpret. A brand-new society genuinely has no sentiment and no
trust distribution, and rendering that as a chart of zeros invites people to
read a shape into noise — the same reasoning behind the Bayesian priors in
Modules 4.3 and 9.3.
"""

from __future__ import annotations

import datetime as dt

from django.db.models import Avg, Count, Q
from django.utils import timezone

#: Trust scores are 0–100 (``apps.ratings.trust.TRUST_SCORE_MAX``). Five buckets
#: of twenty: enough shape to see a distribution, few enough that a society with
#: thirty workers still has readable bars.
TRUST_BUCKETS = [
    (0, 20, "0–20"),
    (20, 40, "20–40"),
    (40, 60, "40–60"),
    (60, 80, "60–80"),
    (80, 101, "80–100"),
]

#: How far ahead the availability panel looks.
AVAILABILITY_HORIZON_DAYS = 14

DEFAULT_WINDOW_DAYS = 30


def _window(since: dt.date | None, until: dt.date | None) -> tuple[dt.date, dt.date]:
    end = until or timezone.localdate()
    start = since or (end - dt.timedelta(days=DEFAULT_WINDOW_DAYS))
    return start, end


# ---------------------------------------------------------------------------
# Sentiment
# ---------------------------------------------------------------------------


def sentiment_summary(society_id, *, since: dt.date, until: dt.date) -> dict:
    """What residents and workers have been saying (11.4, from Module 9.2).

    Only sentiment rows the analyser was actually confident about are counted.
    Module 9's built-in lexicon returns UNKNOWN with a low confidence for mixed
    Hindi-English text, which is most of it — folding those in as "neutral"
    would manufacture a reassuring flat line out of an admission of ignorance.
    """
    from apps.ratings.models import ReviewSentiment, SentimentLabel

    rows = ReviewSentiment.objects.filter(
        rating__society_id=society_id,
        rating__created_at__date__gte=since,
        rating__created_at__date__lte=until,
    )

    counts = {label: 0 for label, _display in SentimentLabel.choices}
    analysed = 0
    unreliable = 0
    polarity_total = 0.0
    themes: dict[str, dict[str, int]] = {}

    for row in rows.only("label", "polarity", "confidence", "themes"):
        if not row.is_reliable:
            unreliable += 1
            counts[SentimentLabel.UNKNOWN] = counts.get(SentimentLabel.UNKNOWN, 0) + 1
            continue

        analysed += 1
        counts[row.label] = counts.get(row.label, 0) + 1
        polarity_total += row.polarity

        for theme, verdict in (row.themes or {}).items():
            bucket = themes.setdefault(theme, {"positive": 0, "negative": 0})
            key = "positive" if _is_positive(verdict) else "negative"
            bucket[key] += 1

    return {
        "has_data": analysed > 0,
        "analysed": analysed,
        "not_confident": unreliable,
        "positive": counts.get(SentimentLabel.POSITIVE, 0),
        "neutral": counts.get(SentimentLabel.NEUTRAL, 0),
        "negative": counts.get(SentimentLabel.NEGATIVE, 0),
        "average_polarity": round(polarity_total / analysed, 3) if analysed else 0.0,
        "themes": [
            {"theme": theme, "positive": tally["positive"], "negative": tally["negative"]}
            for theme, tally in sorted(
                themes.items(),
                key=lambda item: item[1]["positive"] + item[1]["negative"],
                reverse=True,
            )
        ],
        # Module 12.5 will replace the lexicon with a real classifier. Saying so
        # here keeps an administrator from over-reading an early, thin panel.
        "note": (
            "Based on automatic analysis of review text. Reviews the analyser "
            "could not read confidently are excluded."
        ),
    }


def _is_positive(verdict) -> bool:
    """Read one theme verdict, whatever shape Module 9 or 12 stored it in."""
    if isinstance(verdict, bool):
        return verdict
    if isinstance(verdict, (int, float)):
        return verdict >= 0
    return str(verdict).lower() in {"positive", "good", "yes", "true"}


# ---------------------------------------------------------------------------
# Trust distribution
# ---------------------------------------------------------------------------


def trust_distribution(society_id) -> dict:
    """How trust scores are spread across workers and residents (11.4).

    Workers with no ratings yet are separated out rather than counted in the
    lowest bucket. Their score is zero because nothing has happened, not because
    they did badly, and lumping them together would make a society that has just
    onboarded look like one full of untrustworthy workers.
    """
    from apps.societies.models import Resident
    from apps.workers.models import WorkerProfile

    workers = WorkerProfile.objects.filter(user__society_id=society_id)
    residents = Resident.objects.filter(flat__tower__society_id=society_id)

    rated_workers = workers.filter(rating_count__gt=0)
    rated_residents = residents.filter(rating_count__gt=0)

    worker_total = workers.count()
    resident_total = residents.count()

    return {
        "has_data": rated_workers.exists() or rated_residents.exists(),
        "workers": {
            "total": worker_total,
            "rated": rated_workers.count(),
            "unrated": worker_total - rated_workers.count(),
            "average": _rounded(rated_workers.aggregate(v=Avg("trust_score"))["v"]),
            "buckets": _bucket(rated_workers),
        },
        "residents": {
            "total": resident_total,
            "rated": rated_residents.count(),
            "unrated": resident_total - rated_residents.count(),
            "average": _rounded(rated_residents.aggregate(v=Avg("trust_score"))["v"]),
            "buckets": _bucket(rated_residents),
        },
    }


def _bucket(queryset) -> list[dict]:
    """Count a queryset into the trust bands, in one query.

    A conditional aggregate rather than a query per band: five round trips to
    Supabase for one panel is the kind of thing that makes a free-tier dashboard
    feel broken.
    """
    aggregates = {
        f"b{index}": Count(
            "pk", filter=Q(trust_score__gte=low, trust_score__lt=high)
        )
        for index, (low, high, _label) in enumerate(TRUST_BUCKETS)
    }
    counted = queryset.aggregate(**aggregates)

    return [
        {"label": label, "count": counted[f"b{index}"]}
        for index, (_low, _high, label) in enumerate(TRUST_BUCKETS)
    ]


def _rounded(value) -> float:
    return round(float(value), 1) if value is not None else 0.0


# ---------------------------------------------------------------------------
# Unmet demand
# ---------------------------------------------------------------------------


def unmet_demand(society_id, *, since: dt.date, until: dt.date, limit: int = 20) -> dict:
    """What people asked for that the society could not supply (11.4).

    The one panel here a committee can act on directly: it names the service and
    counts how often it went unserved, which is a recruiting brief.
    """
    from .models import DemandKind, UnmetDemand

    rows = UnmetDemand.objects.filter(
        society_id=society_id, created_at__date__gte=since, created_at__date__lte=until
    )

    by_kind = {
        entry["kind"]: entry["n"]
        for entry in rows.values("kind").annotate(n=Count("pk"))
    }
    by_service = (
        rows.exclude(service_label="")
        .values("service_label")
        .annotate(n=Count("pk"))
        .order_by("-n")[:limit]
    )

    total = rows.count()
    return {
        "has_data": total > 0,
        "total": total,
        "by_kind": [
            {"kind": value, "label": str(label), "count": by_kind.get(value, 0)}
            for value, label in DemandKind.choices
        ],
        "by_service": [
            {"service": entry["service_label"], "count": entry["n"]}
            for entry in by_service
        ],
    }


# ---------------------------------------------------------------------------
# Availability
# ---------------------------------------------------------------------------


def availability_summary(society_id, *, horizon_days: int = AVAILABILITY_HORIZON_DAYS) -> dict:
    """Who is actually available, now and over the next fortnight (11.4).

    The modspec asks for this as "visibility into seasonal churn" — the pattern
    an administrator needs to see before a festival week, not after it, when
    half the workers have gone home to their villages and nobody planned for it.

    ``is_available`` is the worker's own global toggle; the per-date rows are
    Module 5.3's opt-ins and block-outs, which override it for a specific day.
    """
    from apps.bookings.models import DayAvailability
    from apps.workers.models import WorkerProfile

    today = timezone.localdate()
    horizon = today + dt.timedelta(days=max(1, horizon_days))

    workers = WorkerProfile.objects.filter(
        user__society_id=society_id, user__is_approved=True
    )
    total = workers.count()
    generally_available = workers.filter(is_available=True).count()

    rows = DayAvailability.objects.filter(
        worker__user__society_id=society_id, date__gte=today, date__lte=horizon
    ).values("date", "is_available")

    per_day: dict[dt.date, dict[str, int]] = {}
    for row in rows:
        bucket = per_day.setdefault(row["date"], {"open": 0, "blocked": 0})
        bucket["open" if row["is_available"] else "blocked"] += 1

    days = [
        {
            "date": day,
            "workers_open": per_day.get(day, {}).get("open", 0),
            "workers_blocked": per_day.get(day, {}).get("blocked", 0),
        }
        for day in (today + dt.timedelta(days=offset) for offset in range(horizon_days))
    ]

    blocked_days = sum(entry["workers_blocked"] for entry in days)

    return {
        "has_data": total > 0,
        "workers_total": total,
        "workers_available_now": generally_available,
        "workers_unavailable_now": total - generally_available,
        "horizon_days": horizon_days,
        "days": days,
        # The headline number for churn: how many worker-days are blocked out
        # across the horizon. A jump here two weeks before Diwali is the signal.
        "blocked_worker_days": blocked_days,
    }


# ---------------------------------------------------------------------------
# Complaint health
# ---------------------------------------------------------------------------


def complaint_summary(society_id, *, since: dt.date, until: dt.date) -> dict:
    """How the complaint queue is doing (11.3 feeding 11.4)."""
    from .models import Complaint, ComplaintCategory

    period = Complaint.objects.filter(society_id=society_id).for_period(since, until)
    open_now = Complaint.objects.filter(society_id=society_id).open()

    raised = period.count()
    resolved = period.closed().count()

    by_category = {
        entry["category"]: entry["n"]
        for entry in period.values("category").annotate(n=Count("pk"))
    }

    closed_rows = period.closed().only("created_at", "resolved_at", "sla_due_at")
    late = sum(
        1
        for complaint in closed_rows
        if complaint.sla_due_at
        and complaint.resolved_at
        and complaint.resolved_at > complaint.sla_due_at
    )

    return {
        "has_data": raised > 0 or open_now.exists(),
        "raised": raised,
        "resolved": resolved,
        "open_now": open_now.count(),
        "overdue_now": open_now.filter(sla_due_at__lt=timezone.now()).count(),
        "resolved_within_sla": resolved - late,
        "resolved_late": late,
        "by_category": [
            {"category": value, "label": str(label), "count": by_category.get(value, 0)}
            for value, label in ComplaintCategory.choices
        ],
    }


# ---------------------------------------------------------------------------
# The whole dashboard
# ---------------------------------------------------------------------------


def dashboard(society_id, *, since: dt.date | None = None, until: dt.date | None = None) -> dict:
    """Every panel, for one society, over one window."""
    start, end = _window(since, until)

    return {
        "period_start": start,
        "period_end": end,
        "sentiment": sentiment_summary(society_id, since=start, until=end),
        "trust": trust_distribution(society_id),
        "complaints": complaint_summary(society_id, since=start, until=end),
        "unmet_demand": unmet_demand(society_id, since=start, until=end),
        "availability": availability_summary(society_id),
    }


__all__ = [
    "AVAILABILITY_HORIZON_DAYS",
    "DEFAULT_WINDOW_DAYS",
    "TRUST_BUCKETS",
    "availability_summary",
    "complaint_summary",
    "dashboard",
    "sentiment_summary",
    "trust_distribution",
    "unmet_demand",
]
