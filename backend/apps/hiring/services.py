"""
Module 4 — the database side of discovery and hiring.

Split from ``scoring.py`` on purpose: this module knows about Django models and
nothing about weights, and that one keeps the formula and knows about neither.
Module 12.1 will re-expose the formula as a standalone service, and the seam has
to already exist for that to be a move rather than a rewrite.

Two things here are load-bearing for performance:

* **Search must not be N+1.** Ranking a page of workers needs each one's request
  history and engagement count. Fetched per worker that is 3 queries × 20 rows on
  a Render free instance talking to a Supabase database in another region. The
  counts are therefore annotated as subqueries in one pass — see
  ``annotate_hiring_stats``.
* **Counts use subqueries, not multiple ``Count`` joins.** Annotating counts over
  two different reverse relations in a single queryset multiplies the join and
  silently inflates both numbers. Correlated subqueries avoid the trap entirely.
"""

from __future__ import annotations

import datetime as dt
import logging

from django.db import transaction
from django.db.models import Count, IntegerField, OuterRef, QuerySet, Subquery, Value
from django.db.models.functions import Coalesce
from django.utils import timezone

from apps.notifications.models import NotificationCategory
from apps.workers.models import WorkerProfile

from .models import (
    NOTICE_PERIOD_DAYS,
    Engagement,
    EngagementStatus,
    HireRequest,
    HireRequestStatus,
)
from .scoring import MatchScore, ScoringInputs, haversine_km

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Domain errors
#
# Plain exceptions rather than DRF's ValidationError, so this layer stays usable
# from a management command, an admin action, or the Module 12 AI service — none
# of which have a request to raise an HTTP error into. Views translate them.
# ---------------------------------------------------------------------------


def _notify(*, recipient, category: str, title: str, body: str, route: str = "") -> None:
    """Tell someone something, without this module caring whether it worked.

    ``notify`` is imported inside the function so ``apps.hiring`` does not
    depend on ``apps.notifications.services`` at import time — that module
    reaches into scheduling, and a module-level import here would make the
    dependency mutual for no benefit. The *category enum* is imported at module
    scope because ``notifications.models`` is a leaf, and the categories need to
    be referenceable from the call sites: passing a bare ``"hire"`` string, as
    this used to, silently produces a notification no filter matches the first
    time somebody mistypes it.

    ``notify`` never raises, so a failed push cannot roll back an accepted hire
    request.
    """
    from apps.notifications.services import notify

    notify(
        recipient=recipient,
        category=category,
        title=title,
        body=body,
        data={"route": route} if route else {},
    )


class HiringError(Exception):
    """Base for refusals that are business rules, not bugs."""

    code = "hiring_error"


class RequestNotActionable(HiringError):
    code = "request_not_actionable"


class DuplicateEngagement(HiringError):
    code = "duplicate_engagement"


# ---------------------------------------------------------------------------
# 4.1 Search
# ---------------------------------------------------------------------------


def searchable_workers(society_id) -> QuerySet[WorkerProfile]:
    """Workers a resident of ``society_id`` may actually discover.

    Mirrors ``WorkerProfile.is_searchable`` — approved, self-marked available,
    and carrying a photo — but as a queryset, because the property cannot be
    pushed into SQL. The two must agree; the property is the readable statement
    of the rule and this is its executable form, and a test pins them together.

    The photo condition is not cosmetic: it is the reference image that gate face
    verification (Module 7) compares against, so a worker without one could be
    hired and then refused entry at the gate.
    """
    if society_id is None:
        return WorkerProfile.objects.none()

    return (
        WorkerProfile.objects.filter(
            user__society_id=society_id,
            user__is_approved=True,
            is_available=True,
        )
        .exclude(photo="")
        .select_related("user", "user__society")
        .prefetch_related("service_types")
    )


def annotate_hiring_stats(queryset: QuerySet[WorkerProfile]) -> QuerySet[WorkerProfile]:
    """Attach the per-worker counts the scorer needs, in one round trip.

    Adds ``answered_requests``, ``ignored_requests`` and ``engagement_count``.

    Note that a *withdrawn* request appears in neither request count. The
    resident took it off the table, so treating it as an ignored request would
    penalise a worker for someone else's change of mind.
    """

    def _count_requests(*statuses):
        return Subquery(
            HireRequest.objects.filter(worker=OuterRef("pk"), status__in=statuses)
            .values("worker")
            .annotate(n=Count("pk"))
            .values("n")[:1],
            output_field=IntegerField(),
        )

    return queryset.annotate(
        answered_requests=Coalesce(
            _count_requests(HireRequestStatus.ACCEPTED, HireRequestStatus.DECLINED),
            Value(0),
        ),
        ignored_requests=Coalesce(
            _count_requests(HireRequestStatus.EXPIRED), Value(0)
        ),
        engagement_count=Coalesce(
            Subquery(
                Engagement.objects.filter(worker=OuterRef("pk"))
                .values("worker")
                .annotate(n=Count("pk"))
                .values("n")[:1],
                output_field=IntegerField(),
            ),
            Value(0),
        ),
    )


def society_distance_km(a, b) -> float | None:
    """Distance between two societies, or ``None`` when it cannot be measured.

    ``None`` for the same society, or when either lacks coordinates — both are
    "no information", which the proximity term scores as neutral rather than
    distant. In v1 this always returns ``None``, since search never crosses a
    society boundary.
    """
    if a is None or b is None or a.pk == b.pk:
        return None
    if None in (a.latitude, a.longitude, b.latitude, b.longitude):
        return None
    return haversine_km(a.latitude, a.longitude, b.latitude, b.longitude)


def build_scoring_inputs(
    worker: WorkerProfile,
    *,
    resident_society=None,
    requested_from: dt.time | None = None,
    requested_until: dt.time | None = None,
) -> ScoringInputs:
    """Flatten a worker into the primitives the formula consumes.

    ``worker`` is expected to have been through :func:`annotate_hiring_stats`;
    the ``getattr`` defaults keep a single un-annotated worker (the 4.2 detail
    view) working rather than raising.

    ``rating_count`` is the real tally, maintained by Module 9 alongside the
    average. It used to stand in as ``completed_engagements``; now that ratings
    exist, the actual count is what the smoothing needs — one five-star review
    and fifty are very different evidence, and only the count says which.
    """
    return ScoringInputs(
        trust_score=float(worker.trust_score or 0),
        average_rating=float(worker.average_rating or 0),
        rating_count=worker.rating_count or 0,
        answered_requests=getattr(worker, "answered_requests", 0) or 0,
        ignored_requests=getattr(worker, "ignored_requests", 0) or 0,
        worker_available_from=worker.available_from,
        worker_available_until=worker.available_until,
        requested_from=requested_from,
        requested_until=requested_until,
        distance_km=society_distance_km(resident_society, worker.user.society),
    )


def score_worker(worker: WorkerProfile, **kwargs) -> MatchScore:
    """Convenience wrapper: gather this worker's inputs and score them.

    The scoring itself goes through Module 12.1's service rather than calling
    ``scoring.score`` directly. That is the seam the modspec asks for: swapping
    the rule-based formula for a learned model becomes an edit to
    ``apps/ai_services/recommendation.py`` and nothing in the hiring flow, the
    booking matcher, or their tests.

    Imported inside the function because Module 12 imports ``hiring.scoring``
    for its own implementation, and a module-scope import would be a cycle.
    """
    from apps.ai_services.recommendation import score_inputs

    return score_inputs(build_scoring_inputs(worker, **kwargs))


def rank_workers(workers, **kwargs) -> list[tuple[WorkerProfile, MatchScore]]:
    """Score every worker and order them best-first.

    Ties break on trust score then rating, so that equal matches still come back
    in a stable order — an unstable ordering makes pagination lose and repeat
    rows between pages.
    """
    scored = [(w, score_worker(w, **kwargs)) for w in workers]
    scored.sort(
        key=lambda pair: (
            pair[1].total,
            float(pair[0].trust_score or 0),
            float(pair[0].average_rating or 0),
            -pair[0].pk,
        ),
        reverse=True,
    )
    return scored


# ---------------------------------------------------------------------------
# 4.4 Request → engagement
# ---------------------------------------------------------------------------


def has_live_engagement(resident_id, worker_id, service_type_id) -> bool:
    """Whether this exact arrangement already exists and is not finished."""
    return (
        Engagement.objects.live()
        .filter(
            resident_id=resident_id,
            worker_id=worker_id,
            service_type_id=service_type_id,
        )
        .exists()
    )


@transaction.atomic
def accept_hire_request(hire_request: HireRequest, *, note: str = "") -> Engagement:
    """Accept a request and create the engagement it becomes (Module 4.4).

    Atomic, and re-reads the request ``FOR UPDATE`` before acting. Two taps on a
    flaky mobile connection arriving together would otherwise both pass the
    status check and create two engagements from one request.
    """
    locked = (
        HireRequest.objects.select_for_update()
        .select_related("resident", "worker", "service_type", "society")
        .get(pk=hire_request.pk)
    )

    if not locked.is_actionable:
        raise RequestNotActionable(
            "This request is no longer open — it was already answered, "
            "withdrawn, or the response window closed."
        )

    if has_live_engagement(locked.resident_id, locked.worker_id, locked.service_type_id):
        raise DuplicateEngagement(
            "An engagement for this worker and service is already running."
        )

    locked.status = HireRequestStatus.ACCEPTED
    locked.responded_at = timezone.now()
    locked.response_note = note
    locked.save(update_fields=["status", "responded_at", "response_note", "updated_at"])

    engagement = Engagement.objects.create(
        society=locked.society,
        resident=locked.resident,
        worker=locked.worker,
        service_type=locked.service_type,
        hire_request=locked,
        # The agreed terms are copied verbatim from the request. This is the
        # point of the shared RecurringTerms base: an engagement must be a
        # faithful record of what the worker actually said yes to.
        days_of_week=locked.days_of_week,
        start_time=locked.start_time,
        expected_duration_minutes=locked.expected_duration_minutes,
        monthly_rate=locked.monthly_rate,
        status=EngagementStatus.ACTIVE,
        started_on=timezone.localdate(),
    )

    _notify(
        recipient=locked.resident.user,
        category=NotificationCategory.HIRE,
        title=f"{locked.worker.user.get_full_name()} accepted",
        body="Your regular help is confirmed. You can see the schedule in the app.",
        route="/engagements",
    )

    logger.info(
        "Hire request %s accepted; engagement %s created (worker=%s resident=%s)",
        locked.pk,
        engagement.pk,
        locked.worker_id,
        locked.resident_id,
    )
    return engagement


@transaction.atomic
def decline_hire_request(hire_request: HireRequest, *, note: str = "") -> HireRequest:
    """Decline a request. Locked for the same reason as acceptance."""
    locked = HireRequest.objects.select_for_update().get(pk=hire_request.pk)

    if not locked.is_actionable:
        raise RequestNotActionable(
            "This request is no longer open — it was already answered, "
            "withdrawn, or the response window closed."
        )

    locked.status = HireRequestStatus.DECLINED
    locked.responded_at = timezone.now()
    locked.response_note = note
    locked.save(update_fields=["status", "responded_at", "response_note", "updated_at"])

    _notify(
        recipient=locked.resident.user,
        category=NotificationCategory.HIRE,
        title=f"{locked.worker.user.get_full_name()} declined",
        body=note or "They are not available for this. You can search for someone else.",
        route="/resident",
    )

    logger.info("Hire request %s declined by worker %s", locked.pk, locked.worker_id)
    return locked


def worker_verification(worker: WorkerProfile) -> dict:
    """The verification badge shown on a worker's profile (Module 4.2).

    Approval is the headline — an administrator reviewed the KYC evidence and
    said yes. The ID-check detail is reported alongside it rather than folded in,
    because a resident deciding who to let into their home deserves to see
    *which* checks actually passed, not a single opaque tick.
    """
    kyc = worker.latest_kyc
    return {
        "is_approved": worker.user.is_approved,
        "id_verified": bool(kyc and kyc.aadhaar_checksum_valid),
        "id_masked": kyc.masked_aadhaar if kyc else None,
        "reviewed_at": worker.reviewed_at,
    }


# ---------------------------------------------------------------------------
# 4.6 Notice period
# ---------------------------------------------------------------------------
#
#   ACTIVE ──give_notice(last_day)──► ACTIVE, serving notice ──► TERMINATED
#     │           refuses a day inside      visits still run,     once the last
#     │           the 10-day window         gate still admits,    working day
#     │                                     attendance counts     has passed
#     │
#     └── terminate(reason) ──► TERMINATED immediately
#         the exceptional path: abuse, safety, mutual consent
#
# WHY NOTICE IS NOT ENFORCED BY WITHHOLDING PAY
# ---------------------------------------------
# The obvious lever is to dock a worker who leaves early. It is the wrong lever,
# on three counts:
#
#   * Wages for days already worked are earned. Withholding them as a penalty is
#     legally exposed under the Payment of Wages Act, and it lands hardest on the
#     workers least able to contest it — which is the population this platform
#     exists to serve.
#   * It is counterproductive. A worker who knows that leaving costs them a
#     week's pay does not give notice; they stop turning up, and the household
#     gets *less* warning, which is the exact harm the rule exists to prevent.
#   * The rule's purpose is warning, not compensation. Ten days is how long a
#     household needs to find somebody, and that purpose is served the moment
#     notice is given.
#
# So the enforcement is reputational and factual: notice given and served is
# unremarkable, and leaving without it is recorded on the trust score
# (apps/ratings/trust.py) where it decays over time rather than costing somebody
# a week's food.


class NoticeTooShort(HiringError):
    """The requested last working day falls inside the notice period."""

    code = "notice_too_short"


class NoticeAlreadyGiven(HiringError):
    """Notice has already been given on this engagement."""

    code = "notice_already_given"


def earliest_last_working_day(*, today=None) -> dt.date:
    """The soonest an engagement may end. Mirrored by ``NoticePeriod`` in Dart."""
    return (today or timezone.localdate()) + dt.timedelta(days=NOTICE_PERIOD_DAYS)


@transaction.atomic
def give_notice(engagement, *, by, reason: str = "", requested_last_day=None):
    """Start the notice period on an active engagement.

    Either side may give notice. ``reason`` records which — it is one of
    ``EngagementEndReason`` — because "the worker left" and "the household let
    her go" are very different facts about a worker, and Module 9 reads them.

    The engagement stays ACTIVE and its schedule keeps producing visits. Nothing
    about the gate, attendance or payments changes until the last working day
    passes.
    """
    locked = Engagement.objects.select_for_update().get(pk=engagement.pk)

    if locked.status != EngagementStatus.ACTIVE:
        raise RequestNotActionable(
            "Only an active engagement can be given notice."
        )
    if locked.last_working_day is not None:
        raise NoticeAlreadyGiven(
            f"Notice was already given. The last working day is "
            f"{locked.last_working_day:%d %b %Y}."
        )

    earliest = earliest_last_working_day()
    last_day = requested_last_day or earliest

    if last_day < earliest:
        raise NoticeTooShort(
            f"The agreed notice period is {NOTICE_PERIOD_DAYS} days, so the "
            f"earliest last day is {earliest:%d %b %Y}."
        )

    locked.notice_given_at = timezone.now()
    locked.notice_given_by = by
    locked.last_working_day = last_day
    if reason:
        locked.end_reason = reason
    locked.save(
        update_fields=[
            "notice_given_at",
            "notice_given_by",
            "last_working_day",
            "end_reason",
            "updated_at",
        ]
    )

    visits = locked.visits_remaining()
    ending = "your last day" if by == locked.worker.user else "their last day"

    # Both sides are told, whichever gave it. The household needs the warning;
    # the worker needs the date in writing, because "she said I could stay till
    # the end of the month" is exactly the dispute this record settles.
    _notify(
        recipient=locked.resident.user,
        category=NotificationCategory.HIRE,
        title=f"{locked.worker.user.get_full_name()} is finishing on "
        f"{last_day:%d %b}",
        body=(
            f"{visits} more visit{'s' if visits != 1 else ''} before then. "
            "You can search for someone else now."
        ),
        route="/engagements",
    )
    _notify(
        recipient=locked.worker.user,
        category=NotificationCategory.HIRE,
        title=f"Notice recorded — {ending} is {last_day:%d %b}",
        body=(
            f"You have {visits} more visit{'s' if visits != 1 else ''} at "
            f"{locked.resident.flat}. You will be paid for every day you work."
        ),
        route="/engagements",
    )

    logger.info(
        "Notice given on engagement %s by user %s, last working day %s",
        locked.pk, getattr(by, "pk", None), last_day,
    )
    return locked


def close_engagements_past_notice(*, today=None) -> int:
    """Close engagements whose last working day has gone by. Returns how many.

    Idempotent and cheap, because there is no scheduler on the free tier
    (docs/free-tier-constraints.md §7) and this therefore runs from a read path —
    the engagement list view calls it. The same lazy-sweep convention as hire
    request expiry and Module 6.5's leave.
    """
    today = today or timezone.localdate()
    closed = 0

    for engagement in Engagement.objects.past_notice(today=today).select_related(
        "worker__user", "resident__user"
    ):
        if engagement.finish_notice():
            closed += 1
            _notify(
                recipient=engagement.worker.user,
                category=NotificationCategory.HIRE,
                title="Your work here has finished",
                body=(
                    f"{engagement.resident.flat} — thank you. Your final "
                    "payment covers every day you worked."
                ),
                route="/engagements",
            )

    if closed:
        logger.info("Closed %s engagement(s) past their notice period", closed)
    return closed


def withdraw_notice(engagement, *, by=None):
    """Both sides changed their mind. Only possible while the day has not passed.

    Kept deliberately simple: it clears the end date and the engagement carries
    on. There is no partial state where notice was "half withdrawn", because the
    only question anybody asks of this record is "is there a last day, and when".
    """
    if not engagement.is_serving_notice:
        raise RequestNotActionable("This engagement is not serving notice.")
    if engagement.last_working_day < timezone.localdate():
        raise RequestNotActionable(
            "The last working day has passed, so notice can no longer be withdrawn."
        )

    engagement.notice_given_at = None
    engagement.notice_given_by = None
    engagement.last_working_day = None
    engagement.end_reason = ""
    engagement.save(
        update_fields=[
            "notice_given_at",
            "notice_given_by",
            "last_working_day",
            "end_reason",
            "updated_at",
        ]
    )

    _notify(
        recipient=engagement.worker.user,
        category=NotificationCategory.HIRE,
        title="Notice withdrawn",
        body=f"Your work at {engagement.resident.flat} continues as before.",
        route="/engagements",
    )
    return engagement
