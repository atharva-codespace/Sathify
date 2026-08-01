"""
Module 9.4 — basic fake-review detection.

Rate-limiting and burst-pattern heuristics over recent rating activity.

-------------------------------------------------------------------------------
THESE FLAG. THEY DO NOT DELETE.
-------------------------------------------------------------------------------
The modspec is explicit that suspicious reviews are "escalated to admin review
rather than auto-deleted", and that is the right call, because every heuristic
here has an innocent explanation:

* A burst of ratings from one person is what a resident catching up on a month
  of bookings looks like.
* Uniform five-star reviews are what a genuinely good worker looks like.
* Near-identical text is what happens when someone has little to say and types
  "good work" four times.

Auto-deleting would silently cost honest workers their ratings with no way to
appeal, and the people most likely to write short, repetitive reviews in mixed
script are exactly the users this platform exists for. So a flag withholds a
rating from *scoring* and asks a human — nothing more.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass

#: How far back a burst is measured over.
BURST_WINDOW = dt.timedelta(hours=1)

#: Ratings from one person inside the window before it looks like a burst.
BURST_THRESHOLD = 5

#: Ratings for one subject inside a day before uniformity is worth checking.
UNIFORM_WINDOW = dt.timedelta(days=1)
UNIFORM_THRESHOLD = 4

#: Similarity at or above which two reviews count as near-identical.
DUPLICATE_SIMILARITY = 0.9

_WORD = re.compile(r"[\wऀ-ॿ]+", re.UNICODE)


@dataclass(frozen=True)
class Suspicion:
    """One reason a rating looks off. Never a verdict."""

    reason: str
    detail: str


def _words(text: str) -> set[str]:
    return {word.lower() for word in _WORD.findall(text or "")}


def similarity(left: str, right: str) -> float:
    """Jaccard similarity over word sets, 0–1.

    Word sets rather than character diffing because the same sentiment gets
    typed with different spellings across Hinglish transliterations — "accha",
    "acha", "achha" — and character similarity would call those different while
    calling two genuinely distinct reviews similar for sharing common words.
    """
    left_words, right_words = _words(left), _words(right)
    if not left_words or not right_words:
        return 0.0

    intersection = len(left_words & right_words)
    union = len(left_words | right_words)
    return intersection / union if union else 0.0


def check_burst(*, recent_by_rater: int) -> Suspicion | None:
    """Many ratings from one person in a short window.

    ``recent_by_rater`` counts ratings that person left inside
    :data:`BURST_WINDOW`, including the one being checked.
    """
    if recent_by_rater < BURST_THRESHOLD:
        return None
    return Suspicion(
        reason="burst",
        detail=(
            f"{recent_by_rater} ratings from this person within "
            f"{int(BURST_WINDOW.total_seconds() // 3600)} hour(s)."
        ),
    )


def check_uniformity(*, recent_stars: list[int]) -> Suspicion | None:
    """Several ratings for one subject, all identical, in a day.

    Identical *and* numerous is the signal — a worker with four genuine
    five-star reviews across a week is not suspicious, which is why the window
    is short.
    """
    if len(recent_stars) < UNIFORM_THRESHOLD:
        return None
    if len(set(recent_stars)) != 1:
        return None

    return Suspicion(
        reason="uniform",
        detail=(
            f"{len(recent_stars)} ratings of {recent_stars[0]}★ for this person "
            "within a day, with no variation."
        ),
    )


def check_duplicate_text(*, review: str, recent_reviews: list[str]) -> Suspicion | None:
    """Near-identical review text among recent reviews of the same subject."""
    if not review or not review.strip():
        return None

    for other in recent_reviews:
        score = similarity(review, other)
        if score >= DUPLICATE_SIMILARITY:
            return Suspicion(
                reason="duplicate_text",
                detail=f"Review text is {score:.0%} identical to another recent review.",
            )
    return None


def check_self_interest(*, rater_id, subject_user_id) -> Suspicion | None:
    """Somebody rating themselves.

    Should be impossible — the API only offers a rating to the counterparty of a
    completed job — so if it happens, something upstream is wrong and an
    administrator should see it rather than the row being quietly dropped.
    """
    if rater_id and subject_user_id and rater_id == subject_user_id:
        return Suspicion(
            reason="self_interest",
            detail="The rater and the person being rated are the same account.",
        )
    return None


def inspect(
    *,
    review: str,
    stars: int,
    rater_id,
    subject_user_id,
    recent_by_rater: int,
    recent_stars: list[int],
    recent_reviews: list[str],
) -> list[Suspicion]:
    """Run every check. Returns whatever looked off, possibly nothing.

    All checks run rather than short-circuiting on the first hit: an
    administrator deciding whether a rating is genuine is better served by every
    reason at once than by them appearing one at a time across repeated reviews.
    """
    found = [
        check_self_interest(rater_id=rater_id, subject_user_id=subject_user_id),
        check_burst(recent_by_rater=recent_by_rater),
        check_uniformity(recent_stars=recent_stars),
        check_duplicate_text(review=review, recent_reviews=recent_reviews),
    ]
    return [suspicion for suspicion in found if suspicion is not None]


__all__ = [
    "BURST_THRESHOLD",
    "BURST_WINDOW",
    "DUPLICATE_SIMILARITY",
    "UNIFORM_THRESHOLD",
    "UNIFORM_WINDOW",
    "Suspicion",
    "check_burst",
    "check_duplicate_text",
    "check_self_interest",
    "check_uniformity",
    "inspect",
    "similarity",
]
