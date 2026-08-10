"""
Module 9 — orchestration: submitting ratings and recomputing trust.

-------------------------------------------------------------------------------
WHY RECOMPUTATION IS EVENT-DRIVEN AND NOT A CRON JOB
-------------------------------------------------------------------------------
Modspec 9.3 calls for a scheduled scoring job. There is no scheduler on the free
tier — the same constraint that made hire requests expire lazily and reminders
sweep on read.

So the score is recomputed at the moments that can change it (a rating landing,
a flag being resolved), and a management command exists to sweep everyone for
the inputs that change without a trigger of their own — attendance accruing,
payments settling. Both paths call the same :func:`recompute_worker_trust`, so
adding a real scheduler later changes what calls it, not what it does.

-------------------------------------------------------------------------------
A CHANGED SCORE IS ALWAYS LOGGED WITH ITS BREAKDOWN
-------------------------------------------------------------------------------
:class:`TrustScoreLog` is written whenever the number moves, carrying the
component breakdown *as it was*. That is what makes a disputed score answerable
months later — recomputing it then would give today's answer against today's
data, which is not the number anyone acted on.
"""

from __future__ import annotations

import logging
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.db.models import Avg, Count, Q
from django.utils import timezone

from apps.attendance.models import AttendanceEvent, Decision, Direction
from apps.bookings.models import Booking, BookingStatus
from apps.hiring.models import Engagement, EngagementEndReason, EngagementStatus
from apps.payments.models import DisputeStatus, Payment, PaymentDispute, PaymentStatus

from . import detection, sentiment
from .models import (
    FlagStatus,
    Rating,
    RatingDirection,
    ReviewFlag,
    ReviewSentiment,
    TrustScoreLog,
    TrustSubject,
)
from .trust import (
    ResidentTrustInputs,
    TrustScore,
    WorkerTrustInputs,
    resident_trust,
    worker_trust,
)

logger = logging.getLogger(__name__)


class RatingError(Exception):
    """Base for refusals that are business rules, not bugs."""

    code = "rating_error"


class AlreadyRated(RatingError):
    code = "already_rated"


class NotRateable(RatingError):
    code = "not_rateable"


# ---------------------------------------------------------------------------
# 9.1 Submitting a rating
# ---------------------------------------------------------------------------


def rateable_engagements(user, *, direction: str):
    """Completed engagements this user may still rate.

    Only terminated engagements: modspec 9.1 rates "each completed engagement",
    and rating one still running would be rating an opinion rather than an
    outcome.
    """
    queryset = Engagement.objects.filter(status=EngagementStatus.TERMINATED)

    if direction == RatingDirection.RESIDENT_TO_WORKER:
        queryset = queryset.filter(resident__user=user)
    else:
        queryset = queryset.filter(worker__user=user)

    return queryset.exclude(ratings__direction=direction).select_related(
        # ``service_type`` is here because PendingRatingsView reads its name for
        # every row's title; without it the list costs one extra query per
        # engagement, on the endpoint a user hits first on a cold backend.
        "worker__user", "resident__user", "resident__flat__tower", "service_type"
    )


def rateable_bookings(user, *, direction: str):
    """Completed bookings this user may still rate."""
    queryset = Booking.objects.filter(status=BookingStatus.COMPLETED)

    if direction == RatingDirection.RESIDENT_TO_WORKER:
        queryset = queryset.filter(resident__user=user)
    else:
        queryset = queryset.filter(worker__user=user)

    return queryset.exclude(ratings__direction=direction).select_related(
        "worker__user", "resident__user", "resident__flat__tower", "category"
    )


def _already_rated(*, direction: str, engagement, booking) -> bool:
    """Whether this side has rated this job already.

    A named function rather than an inline query so a test can suppress it and
    exercise what happens when two taps both get past it — which is the only
    way the database constraint underneath is ever reached.
    """
    return Rating.objects.filter(
        direction=direction, engagement=engagement, booking=booking
    ).exists()


def _recent_context(rating: Rating) -> dict:
    """The activity Module 9.4's heuristics compare against."""
    now = timezone.now()

    recent_by_rater = Rating.objects.filter(
        rater=rating.rater, created_at__gte=now - detection.BURST_WINDOW
    ).count()

    subject_filter = (
        Q(worker=rating.worker, direction=RatingDirection.RESIDENT_TO_WORKER)
        if rating.subject_is_worker
        else Q(resident=rating.resident, direction=RatingDirection.WORKER_TO_RESIDENT)
    )
    recent_for_subject = (
        Rating.objects.filter(subject_filter)
        .filter(created_at__gte=now - detection.UNIFORM_WINDOW)
        .exclude(pk=rating.pk)
    )

    return {
        "recent_by_rater": recent_by_rater,
        "recent_stars": list(recent_for_subject.values_list("stars", flat=True))
        + [rating.stars],
        "recent_reviews": [
            text
            for text in recent_for_subject.values_list("review", flat=True)
            if text
        ],
    }


def _subject_user_id(rating: Rating):
    return (
        rating.worker.user_id if rating.subject_is_worker else rating.resident.user_id
    )


def run_detection(rating: Rating) -> list[ReviewFlag]:
    """Module 9.4 — inspect a new rating and raise flags, never delete.

    A flagged rating is withheld from *scoring* until an administrator looks at
    it. It is not hidden from the person who wrote it and it is not deleted —
    every heuristic here has an innocent explanation (see detection.py).
    """
    context = _recent_context(rating)
    suspicions = detection.inspect(
        review=rating.review,
        stars=rating.stars,
        rater_id=rating.rater_id,
        subject_user_id=_subject_user_id(rating),
        **context,
    )

    if not suspicions:
        return []

    flags = [
        ReviewFlag.objects.create(
            society=rating.society,
            rating=rating,
            reason=suspicion.reason,
            detail=suspicion.detail,
        )
        for suspicion in suspicions
    ]

    rating.is_flagged = True
    rating.is_withheld = True
    rating.save(update_fields=["is_flagged", "is_withheld", "updated_at"])

    logger.info(
        "Rating %s flagged: %s",
        rating.pk,
        ", ".join(suspicion.reason for suspicion in suspicions),
    )
    return flags


def run_sentiment(rating: Rating) -> ReviewSentiment | None:
    """Module 9.2 — analyse the review text and store the result separately.

    Skipped for a rating with no text: there is nothing to analyse, and an empty
    row would look like a model that found nothing rather than a review that
    said nothing.
    """
    if not rating.review.strip():
        return None

    result = sentiment.analyse(rating.review)

    return ReviewSentiment.objects.update_or_create(
        rating=rating,
        defaults={
            "label": result.label,
            "polarity": result.polarity,
            "confidence": result.confidence,
            "themes": result.themes,
            "detected_language": result.language,
            "engine": result.engine,
        },
    )[0]


@transaction.atomic
def submit_rating(
    *,
    rater,
    direction: str,
    stars: int,
    review: str = "",
    engagement=None,
    booking=None,
) -> Rating:
    """Module 9.1 — record one side's verdict, then everything that follows.

    Sentiment, flagging and trust recomputation all happen here rather than
    being left to the caller, because a rating that skipped any of them would
    silently be a rating that does not count.
    """
    job = engagement or booking
    if job is None:
        raise NotRateable("A rating must attach to a completed job.")

    if engagement is not None:
        worker, resident, society = job.worker, job.resident, job.society
        if job.status != EngagementStatus.TERMINATED:
            raise NotRateable("This engagement has not finished yet.")
    else:
        worker, resident, society = job.worker, job.resident, job.society
        if job.status != BookingStatus.COMPLETED:
            raise NotRateable("This booking has not been completed yet.")

    if _already_rated(direction=direction, engagement=engagement, booking=booking):
        raise AlreadyRated("You have already rated this job.")

    # The check above loses a race between two taps — which is exactly why the
    # uniqueness rule is also a database constraint (see Rating.Meta). Catching
    # the constraint here is what turns the loser of that race into the same
    # "you have already rated this" the checker would have given, instead of an
    # IntegrityError escaping as a 500: nothing in the DRF exception handler
    # knows about database errors, so it would reach the user as a crash.
    #
    # The savepoint matters. An IntegrityError poisons the transaction it was
    # raised in, so the create needs its own block for the outer one to survive
    # and roll back cleanly.
    try:
        with transaction.atomic():
            rating = Rating.objects.create(
                society=society,
                direction=direction,
                worker=worker,
                resident=resident,
                rater=rater,
                engagement=engagement,
                booking=booking,
                stars=stars,
                review=review.strip(),
            )
    except IntegrityError as exc:
        raise AlreadyRated("You have already rated this job.") from exc

    run_sentiment(rating)
    run_detection(rating)

    if rating.subject_is_worker:
        recompute_worker_trust(worker, trigger="rating submitted")
    else:
        recompute_resident_trust(resident, trigger="rating submitted")

    logger.info("Rating %s submitted (%s, %s★)", rating.pk, direction, stars)
    return rating


# ---------------------------------------------------------------------------
# 9.3 Gathering trust inputs
# ---------------------------------------------------------------------------


def _rating_stats(queryset) -> tuple[float, int]:
    """Average and count over ratings that actually count."""
    stats = queryset.visible().aggregate(average=Avg("stars"), total=Count("pk"))
    return float(stats["average"] or 0.0), int(stats["total"] or 0)


def worker_trust_inputs(worker) -> WorkerTrustInputs:
    """Gather a worker's evidence. The only DB-touching half of the score."""
    average, count = _rating_stats(Rating.objects.of_worker(worker))

    attended = (
        AttendanceEvent.objects.filter(
            worker=worker, direction=Direction.ENTRY, decision=Decision.ALLOWED
        )
        .values("occurred_at__date")
        .distinct()
        .count()
    )
    # Every gate event linked to a scheduled visit is one that was expected;
    # counting the schedule again here would double-count and would disagree
    # with what attendance actually recorded.
    expected = AttendanceEvent.objects.filter(worker=worker, was_expected=True).count()

    kyc = worker.latest_kyc

    completed = Booking.objects.filter(
        worker=worker, status=BookingStatus.COMPLETED
    ).count()
    # Leaving is not abandoning. Leaving *without warning* is.
    #
    # This is the whole enforcement mechanism for Module 4.6's notice period,
    # and it is deliberately the only one: withholding earned wages from a
    # worker who left early would be legally exposed under the Payment of Wages
    # Act, would fall hardest on the people this platform exists to serve, and
    # would be self-defeating — a worker who knows that leaving costs a week's
    # pay does not give notice, she simply stops turning up, and the household
    # gets *less* warning. So the cost of walking out is a factual mark that
    # decays, and the cost of giving notice is nothing at all.
    #
    # ``notice_given_at`` is the test rather than whether the last working day
    # was reached: the harm being measured is "the household got no warning",
    # and notice is the warning. Somebody who gave notice and then also left
    # early is a rarer and different failure, and conflating the two would
    # penalise the behaviour this is meant to encourage.
    abandoned = Booking.objects.filter(
        worker=worker, status=BookingStatus.DECLINED
    ).count() + Engagement.objects.filter(
        worker=worker,
        status=EngagementStatus.TERMINATED,
        notice_given_at__isnull=True,
        end_reason__in=[
            EngagementEndReason.WORKER_ENDED,
            EngagementEndReason.WORKER_LEFT_SOCIETY,
        ],
    ).count()

    return WorkerTrustInputs(
        average_rating=average,
        rating_count=count,
        expected_visits=max(expected, attended),
        attended_visits=attended,
        is_approved=worker.user.is_approved,
        id_verified=bool(kyc and kyc.aadhaar_checksum_valid),
        has_photo=bool(worker.photo),
        completed_jobs=completed,
        abandoned_jobs=abandoned,
    )


def resident_trust_inputs(resident) -> ResidentTrustInputs:
    """Gather a resident's evidence (SRS 3.9)."""
    average, count = _rating_stats(Rating.objects.of_resident(resident))

    payments = Payment.objects.filter(resident=resident)
    due = payments.exclude(status=PaymentStatus.CANCELLED).count()
    settled = payments.filter(
        status__in=[PaymentStatus.PAID, PaymentStatus.REFUNDED]
    ).count()

    disputes = PaymentDispute.objects.filter(payment__resident=resident)

    return ResidentTrustInputs(
        payments_due=due,
        payments_settled=settled,
        average_rating=average,
        rating_count=count,
        disputes_against=disputes.count(),
        # Module 8 records an upheld dispute as RESOLVED and a rejected one as
        # REJECTED, so only RESOLVED counts against anyone. An open dispute is
        # an allegation; letting it lower a score would make the complaint
        # button a weapon.
        disputes_upheld_against=disputes.filter(
            status=DisputeStatus.RESOLVED
        ).count(),
    )


# ---------------------------------------------------------------------------
# 9.3 Recomputation
# ---------------------------------------------------------------------------


def _log_change(
    *, subject_type: str, worker, resident, society, previous, score: TrustScore, trigger: str
) -> TrustScoreLog | None:
    """Write the audit row, but only when the number actually moved.

    A no-op recomputation from the nightly sweep should not fill the log with
    rows saying nothing changed — that would bury the changes somebody is
    actually looking for.
    """
    new_value = Decimal(str(score.value))
    if Decimal(str(previous)) == new_value:
        return None

    return TrustScoreLog.objects.create(
        society=society,
        subject_type=subject_type,
        worker=worker,
        resident=resident,
        previous_score=previous,
        new_score=new_value,
        components=score.explain(),
        trigger=trigger[:60],
    )


@transaction.atomic
def recompute_worker_trust(worker, *, trigger: str = "") -> TrustScore:
    """Recompute and store a worker's trust score, rating average and count.

    All three move together on purpose: Module 4.3 reads the average *and* the
    count, and an average updated without its count would let one five-star
    review look like fifty.
    """
    score = worker_trust(worker_trust_inputs(worker))
    average, count = _rating_stats(Rating.objects.of_worker(worker))

    previous = worker.trust_score
    log = _log_change(
        subject_type=TrustSubject.WORKER,
        worker=worker,
        resident=None,
        society=worker.user.society,
        previous=previous,
        score=score,
        trigger=trigger,
    )

    worker.trust_score = Decimal(str(score.value))
    worker.average_rating = Decimal(str(round(average, 2)))
    worker.rating_count = count
    worker.save(
        update_fields=["trust_score", "average_rating", "rating_count", "updated_at"]
    )

    if log is not None:
        logger.info(
            "Worker %s trust %s → %s (%s)", worker.pk, previous, score.value, trigger
        )
    return score


@transaction.atomic
def recompute_resident_trust(resident, *, trigger: str = "") -> TrustScore:
    """Recompute and store a resident's trust score."""
    score = resident_trust(resident_trust_inputs(resident))
    average, count = _rating_stats(Rating.objects.of_resident(resident))

    previous = resident.trust_score
    log = _log_change(
        subject_type=TrustSubject.RESIDENT,
        worker=None,
        resident=resident,
        society=resident.flat.tower.society,
        previous=previous,
        score=score,
        trigger=trigger,
    )

    resident.trust_score = Decimal(str(score.value))
    resident.average_rating = Decimal(str(round(average, 2)))
    resident.rating_count = count
    resident.save(
        update_fields=["trust_score", "average_rating", "rating_count", "updated_at"]
    )

    if log is not None:
        logger.info(
            "Resident %s trust %s → %s (%s)", resident.pk, previous, score.value, trigger
        )
    return score


def resolve_flag(flag: ReviewFlag, *, upheld: bool, by, note: str = "") -> bool:
    """Close a flag and recompute whatever it was suppressing.

    Dismissing restores a rating to scoring, so the subject's score has to move
    with it — otherwise clearing a false positive would leave the penalty in
    place, which is the worst of both outcomes.
    """
    if not flag.resolve(upheld=upheld, by=by, note=note):
        return False

    rating = flag.rating
    if rating.subject_is_worker:
        recompute_worker_trust(rating.worker, trigger="review flag resolved")
    else:
        recompute_resident_trust(rating.resident, trigger="review flag resolved")
    return True


__all__ = [
    "AlreadyRated",
    "NotRateable",
    "RatingError",
    "rateable_bookings",
    "rateable_engagements",
    "recompute_resident_trust",
    "recompute_worker_trust",
    "resident_trust_inputs",
    "resolve_flag",
    "run_detection",
    "run_sentiment",
    "submit_rating",
    "worker_trust_inputs",
]
