"""
Module 4.3 — Recommendation scoring (rule-based v1).

A weighted formula over five signals: trust score, rating average, availability
match, response rate, and proximity. The SRS asks for the result to be shown as
a percentage match ("Priya — 98% Match", SRS 3.12), so the output is a 0–1 score
plus a per-component breakdown that the UI can render as the *reason* for the
number.

-------------------------------------------------------------------------------
WHY THIS FILE HAS NO DATABASE ACCESS
-------------------------------------------------------------------------------
Everything here is a pure function over plain numbers. Module 12.1 is specified
as "the scoring logic from Module 4.3, exposed as its own internal service so it
can be swapped for a learned model later without touching the hiring flow" — a
swap that is only cheap if the formula never learned to query. Gathering the
inputs is ``services.py``'s job; deciding what they are worth is this file's.

-------------------------------------------------------------------------------
COLD START — WHY THE SMOOTHING IS NOT OPTIONAL
-------------------------------------------------------------------------------
At launch every worker has ``average_rating = 0`` and no request history, because
Module 9 has nothing to compute from yet. Scoring those raw values would rank a
brand-new worker below everyone forever, and no resident would ever hire them —
a self-fulfilling freeze-out that also starves the future learned model of the
very training data it needs.

Both ratings and response rate are therefore shrunk toward a neutral prior with
a strength of a few observations (see ``_smoothed``). A worker with no history
scores at the prior; each real data point pulls them away from it. This is
standard Bayesian shrinkage, and it is what makes the ranking honest about the
difference between "known to be mediocre" and "not yet known".
"""

from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# Contract with Module 9
#
# Module 9 computes trust_score and average_rating; this module only reads them.
# The ranges below are the agreed contract between the two — if Module 9 emits a
# trust score on a different scale, change it here in one place, not at each
# call site.
# ---------------------------------------------------------------------------

#: ``WorkerProfile.trust_score`` runs 0–100.
TRUST_SCORE_MAX = 100.0

#: ``WorkerProfile.average_rating`` runs 0–5.
RATING_MAX = 5.0

#: Neutral starting rating for a worker with no completed engagements. Set just
#: above the midpoint: a new worker should look like a reasonable bet, not a
#: proven one.
RATING_PRIOR = 3.5

#: How many real ratings it takes to roughly halve the prior's pull.
RATING_PRIOR_STRENGTH = 3.0

#: A worker with no request history is assumed to be fairly responsive.
RESPONSE_RATE_PRIOR = 0.8
RESPONSE_RATE_PRIOR_STRENGTH = 3.0

#: Distance at which the proximity term reaches zero. Domestic workers commute
#: locally and on foot or by bus, so 10 km is already generous.
PROXIMITY_HORIZON_KM = 10.0

#: Component weights. Trust and rating dominate because they are what a resident
#: is actually asking about; availability is next because a perfect worker at the
#: wrong hour is useless; response rate and proximity are tie-breakers.
WEIGHTS: dict[str, float] = {
    "trust": 0.30,
    "rating": 0.25,
    "availability": 0.20,
    "response_rate": 0.15,
    "proximity": 0.10,
}

COMPONENT_LABELS: dict[str, str] = {
    "trust": "Trust score",
    "rating": "Resident ratings",
    "availability": "Availability match",
    "response_rate": "Responds to requests",
    "proximity": "Distance",
}

# A silent drift here would quietly rescale every match percentage in the app,
# so it is asserted at import rather than left to a test someone might delete.
assert abs(sum(WEIGHTS.values()) - 1.0) < 1e-9, "Recommendation weights must sum to 1.0"


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def _smoothed(observed: float, count: float, *, prior: float, strength: float) -> float:
    """Shrink an observed average toward ``prior`` when ``count`` is small.

    With zero observations the result is exactly the prior; as observations
    accumulate it converges on the observed value. See the module docstring for
    why this is load-bearing rather than a refinement.
    """
    if count <= 0:
        return prior
    return (observed * count + prior * strength) / (count + strength)


def haversine_km(lat1, lon1, lat2, lon2) -> float:
    """Great-circle distance in kilometres between two coordinates."""
    radius_km = 6371.0
    p1, p2 = math.radians(float(lat1)), math.radians(float(lat2))
    d_phi = p2 - p1
    d_lambda = math.radians(float(lon2) - float(lon1))
    a = (
        math.sin(d_phi / 2) ** 2
        + math.cos(p1) * math.cos(p2) * math.sin(d_lambda / 2) ** 2
    )
    return 2 * radius_km * math.asin(math.sqrt(a))


# ---------------------------------------------------------------------------
# Individual signals
#
# Each returns 0–1, and each is separately testable — which matters because
# these are the pieces a learned model will eventually replace one at a time.
# ---------------------------------------------------------------------------


def trust_component(trust_score: float | None) -> float:
    """Module 9's trust score, normalised onto 0–1."""
    if not trust_score:
        return 0.0
    return _clamp01(float(trust_score) / TRUST_SCORE_MAX)


def rating_component(average_rating: float | None, rating_count: int) -> float:
    """Rating average, shrunk toward the neutral prior for lightly-rated workers."""
    observed = float(average_rating or 0.0)
    smoothed = _smoothed(
        observed, rating_count, prior=RATING_PRIOR, strength=RATING_PRIOR_STRENGTH
    )
    return _clamp01(smoothed / RATING_MAX)


def response_rate_component(answered: int, ignored: int) -> float:
    """Share of past requests the worker actually answered, smoothed.

    ``ignored`` counts only requests that lapsed unanswered. A request the
    resident withdrew is neither answered nor ignored and belongs in neither
    figure — see ``HireRequestQuerySet.answered``.
    """
    total = answered + ignored
    observed = (answered / total) if total else 0.0
    return _clamp01(
        _smoothed(
            observed,
            total,
            prior=RESPONSE_RATE_PRIOR,
            strength=RESPONSE_RATE_PRIOR_STRENGTH,
        )
    )


def availability_component(
    worker_from,
    worker_until,
    requested_from=None,
    requested_until=None,
) -> float:
    """How much of the resident's requested window the worker's hours cover.

    Returns 1.0 when either side leaves the window open: a worker who has
    declared no hours has not declared a *restriction*, and a resident who did
    not filter by time is not expressing a preference to score against. Reading
    a blank as "unavailable" would bury every worker who skipped an optional
    profile field.
    """
    if requested_from is None or requested_until is None:
        return 1.0
    if worker_from is None or worker_until is None:
        return 1.0

    def minutes(t) -> int:
        return t.hour * 60 + t.minute

    req_start, req_end = minutes(requested_from), minutes(requested_until)
    work_start, work_end = minutes(worker_from), minutes(worker_until)

    requested_span = req_end - req_start
    if requested_span <= 0:
        # A zero-length or inverted request window carries no information.
        return 1.0

    overlap = min(req_end, work_end) - max(req_start, work_start)
    return _clamp01(overlap / requested_span)


def proximity_component(distance_km: float | None) -> float:
    """Linear decay from 1.0 at zero distance to 0.0 at the horizon.

    ``None`` means "not measurable" — the two societies are the same, or one has
    no coordinates recorded — and scores 1.0 rather than 0.0, so that a society
    which never filled in its latitude does not have its whole worker pool
    silently penalised.

    In v1 this is 1.0 for every candidate, because search is society-scoped and
    a worker belongs to exactly one society. The term is wired up now so that
    cross-society search (workers serving several nearby societies) can be turned
    on later by widening the queryset alone, without reopening the formula.
    """
    if distance_km is None:
        return 1.0
    return _clamp01(1.0 - (distance_km / PROXIMITY_HORIZON_KM))


# ---------------------------------------------------------------------------
# Composition
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ScoreComponent:
    """One weighted signal, kept alongside the raw value that produced it.

    The raw value travels with the score because the UI shows *why* a worker
    matched ("4.6★", "responds to 9 in 10 requests"), and recomputing it in the
    serializer would mean two places that must agree.
    """

    key: str
    label: str
    weight: float
    normalised: float
    raw: float | None = None

    @property
    def contribution(self) -> float:
        return self.weight * self.normalised


@dataclass(frozen=True)
class MatchScore:
    """A worker's overall match, and the breakdown that justifies it."""

    components: tuple[ScoreComponent, ...] = field(default_factory=tuple)

    @property
    def total(self) -> float:
        """Overall match on 0–1."""
        return _clamp01(sum(c.contribution for c in self.components))

    @property
    def percentage(self) -> int:
        """The number the app displays, e.g. 98 for "98% Match"."""
        return int(round(self.total * 100))

    def explain(self) -> list[dict]:
        """Per-component breakdown, ordered by how much it moved the result.

        Module 9's trust score carries an explicit requirement that a computed
        score be explainable rather than a black box; the same standard is
        applied here, since this number is equally visible to residents.
        """
        return [
            {
                "key": c.key,
                "label": c.label,
                "weight": round(c.weight, 4),
                "score": round(c.normalised, 4),
                "contribution": round(c.contribution, 4),
                "raw": c.raw,
            }
            for c in sorted(self.components, key=lambda c: c.contribution, reverse=True)
        ]


@dataclass(frozen=True)
class ScoringInputs:
    """Everything the formula needs, already gathered. No model objects.

    Deliberately a flat bag of primitives: it is what keeps this module free of
    Django imports, and it doubles as the feature vector a learned model would
    eventually consume.
    """

    trust_score: float = 0.0
    average_rating: float = 0.0
    rating_count: int = 0
    answered_requests: int = 0
    ignored_requests: int = 0
    worker_available_from: dt.time | None = None
    worker_available_until: dt.time | None = None
    requested_from: dt.time | None = None
    requested_until: dt.time | None = None
    distance_km: float | None = None


def score(inputs: ScoringInputs) -> MatchScore:
    """Apply the v1 weighted formula. Pure — same inputs, same answer, always."""
    response_total = inputs.answered_requests + inputs.ignored_requests
    response_normalised = response_rate_component(
        inputs.answered_requests, inputs.ignored_requests
    )

    components = (
        ScoreComponent(
            key="trust",
            label=COMPONENT_LABELS["trust"],
            weight=WEIGHTS["trust"],
            normalised=trust_component(inputs.trust_score),
            raw=round(float(inputs.trust_score or 0.0), 2),
        ),
        ScoreComponent(
            key="rating",
            label=COMPONENT_LABELS["rating"],
            weight=WEIGHTS["rating"],
            normalised=rating_component(inputs.average_rating, inputs.rating_count),
            raw=round(float(inputs.average_rating or 0.0), 2),
        ),
        ScoreComponent(
            key="availability",
            label=COMPONENT_LABELS["availability"],
            weight=WEIGHTS["availability"],
            normalised=availability_component(
                inputs.worker_available_from,
                inputs.worker_available_until,
                inputs.requested_from,
                inputs.requested_until,
            ),
            raw=None,
        ),
        ScoreComponent(
            key="response_rate",
            label=COMPONENT_LABELS["response_rate"],
            weight=WEIGHTS["response_rate"],
            normalised=response_normalised,
            # Raw is the observed rate, not the smoothed one: showing a resident
            # "responds to 80% of requests" for a worker who has never received
            # one would be inventing history.
            raw=(
                round(inputs.answered_requests / response_total, 4)
                if response_total
                else None
            ),
        ),
        ScoreComponent(
            key="proximity",
            label=COMPONENT_LABELS["proximity"],
            weight=WEIGHTS["proximity"],
            normalised=proximity_component(inputs.distance_km),
            raw=(round(inputs.distance_km, 2) if inputs.distance_km is not None else None),
        ),
    )
    return MatchScore(components=components)
