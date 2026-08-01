"""
Module 9.3 — trust score computation.

A weighted combination producing a number on 0–100, with the breakdown that
justifies it. SRS 3.9 names the inputs on each side, and this file follows them
exactly rather than inventing its own:

* **Workers** — attendance, ratings, verification status, work completion.
* **Residents** — payment consistency, behaviour, complaint history.

-------------------------------------------------------------------------------
NO DATABASE ACCESS, FOR THE SAME REASON MODULE 4.3 HAS NONE
-------------------------------------------------------------------------------
Everything here is a pure function over plain numbers. That is what makes a
disputed score answerable: the arithmetic can be reproduced from the stored
breakdown alone, with no query and no guessing about what the data looked like
at the time. Gathering the inputs is ``services.py``'s job.

-------------------------------------------------------------------------------
COLD START AGAIN, AND IT MATTERS MORE HERE
-------------------------------------------------------------------------------
A new worker has no ratings, no attendance and no completed jobs. Scoring those
raw gives a trust score of zero, which Module 4 then feeds into a ranking that
buries them — permanently, because they never get the first job that would
produce their first rating.

So every component with a count behind it is shrunk toward a neutral prior, and
a worker with no history lands mid-range rather than at the bottom. Being unknown
and being untrustworthy are different things, and only one of them should cost
somebody work.
"""

from __future__ import annotations

from dataclasses import dataclass, field

#: Scores run 0–100, matching ``apps.hiring.scoring.TRUST_SCORE_MAX``. Module 4
#: divides by that constant, so the two must not drift apart.
TRUST_SCORE_MAX = 100.0

#: Where an unknown component sits. Deliberately just above the midpoint: a
#: newcomer should look like a reasonable bet, not a proven one.
NEUTRAL_PRIOR = 0.6

#: How many real observations it takes to roughly halve the prior's pull.
PRIOR_STRENGTH = 4.0

#: Ratings are 1–5, so a rating carries no information below 1.
RATING_MAX = 5.0

#: Weights per SRS 3.9. Asserted at import rather than left to a test somebody
#: might delete — a silent drift here rescales every trust score in the system.
WORKER_WEIGHTS: dict[str, float] = {
    "ratings": 0.35,
    "attendance": 0.30,
    "verification": 0.20,
    "completion": 0.15,
}

RESIDENT_WEIGHTS: dict[str, float] = {
    "payment": 0.45,
    "behaviour": 0.35,
    "complaints": 0.20,
}

assert abs(sum(WORKER_WEIGHTS.values()) - 1.0) < 1e-9, "Worker weights must sum to 1"
assert abs(sum(RESIDENT_WEIGHTS.values()) - 1.0) < 1e-9, "Resident weights must sum to 1"

COMPONENT_LABELS: dict[str, str] = {
    "ratings": "Ratings from residents",
    "attendance": "Turns up as agreed",
    "verification": "Identity verified",
    "completion": "Sees work through",
    "payment": "Pays on time",
    "behaviour": "Ratings from workers",
    "complaints": "Complaint history",
}


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def _smoothed(observed: float, count: float, *, prior: float = NEUTRAL_PRIOR) -> float:
    """Shrink an observed rate toward the prior when there is little evidence.

    Same technique and the same reasoning as ``apps.hiring.scoring._smoothed``.
    Deliberately duplicated rather than shared: these are two modules' scoring
    policies, and coupling them would mean tuning one silently retunes the
    other.
    """
    if count <= 0:
        return prior
    return (observed * count + prior * PRIOR_STRENGTH) / (count + PRIOR_STRENGTH)


@dataclass(frozen=True)
class TrustComponent:
    """One weighted input, with the evidence behind it.

    ``detail`` is a sentence a person can read. It travels with the number
    because "attendance: 0.72" explains nothing to a worker asking why their
    score fell.
    """

    key: str
    label: str
    weight: float
    normalised: float
    detail: str = ""

    @property
    def contribution(self) -> float:
        return self.weight * self.normalised

    def as_dict(self) -> dict:
        return {
            "key": self.key,
            "label": self.label,
            "weight": round(self.weight, 4),
            "score": round(self.normalised, 4),
            "contribution": round(self.contribution * TRUST_SCORE_MAX, 2),
            "detail": self.detail,
        }


@dataclass(frozen=True)
class TrustScore:
    """A computed score and the breakdown that justifies it."""

    components: tuple[TrustComponent, ...] = field(default_factory=tuple)

    @property
    def fraction(self) -> float:
        return _clamp01(sum(c.contribution for c in self.components))

    @property
    def value(self) -> float:
        """The stored score, 0–100, to two decimals."""
        return round(self.fraction * TRUST_SCORE_MAX, 2)

    def explain(self) -> list[dict]:
        """Ordered by how much each component moved the result."""
        return [
            component.as_dict()
            for component in sorted(
                self.components, key=lambda c: c.contribution, reverse=True
            )
        ]

    def weakest(self) -> TrustComponent | None:
        """The component costing the most, for "how do I improve this?".

        Measured as distance from a perfect score *weighted* — a poor showing on
        a heavily weighted component matters more than a worse one that barely
        counts.
        """
        if not self.components:
            return None
        return max(
            self.components, key=lambda c: (1.0 - c.normalised) * c.weight
        )


# ---------------------------------------------------------------------------
# Workers
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WorkerTrustInputs:
    """Everything the worker formula needs, already gathered. No model objects."""

    average_rating: float = 0.0
    rating_count: int = 0

    expected_visits: int = 0
    attended_visits: int = 0

    is_approved: bool = False
    id_verified: bool = False
    has_photo: bool = False

    completed_jobs: int = 0
    #: Jobs the worker themselves abandoned — declined after accepting,
    #: cancelled, or terminated from their side.
    abandoned_jobs: int = 0


def worker_trust(inputs: WorkerTrustInputs) -> TrustScore:
    """Compute a worker's trust score (SRS 3.9)."""
    rating_fraction = _smoothed(
        (inputs.average_rating / RATING_MAX) if inputs.average_rating else 0.0,
        inputs.rating_count,
    )
    ratings = TrustComponent(
        key="ratings",
        label=COMPONENT_LABELS["ratings"],
        weight=WORKER_WEIGHTS["ratings"],
        normalised=_clamp01(rating_fraction),
        detail=(
            f"{inputs.average_rating:.1f} out of 5 from {inputs.rating_count} rating(s)"
            if inputs.rating_count
            else "No ratings yet, so this is held at a neutral starting value"
        ),
    )

    attendance_rate = (
        inputs.attended_visits / inputs.expected_visits if inputs.expected_visits else 0.0
    )
    attendance = TrustComponent(
        key="attendance",
        label=COMPONENT_LABELS["attendance"],
        weight=WORKER_WEIGHTS["attendance"],
        normalised=_clamp01(_smoothed(attendance_rate, inputs.expected_visits)),
        detail=(
            f"Attended {inputs.attended_visits} of {inputs.expected_visits} "
            "expected visits"
            if inputs.expected_visits
            else "No visits scheduled yet"
        ),
    )

    # Verification is the one component with no prior: it is a fact about
    # paperwork, not an estimate from sparse evidence. A worker either passed
    # the checks or has not yet.
    checks = [inputs.is_approved, inputs.id_verified, inputs.has_photo]
    verification = TrustComponent(
        key="verification",
        label=COMPONENT_LABELS["verification"],
        weight=WORKER_WEIGHTS["verification"],
        normalised=sum(1 for check in checks if check) / len(checks),
        detail=(
            "Approved, ID verified, photo on file"
            if all(checks)
            else "Some verification steps are still outstanding"
        ),
    )

    total_jobs = inputs.completed_jobs + inputs.abandoned_jobs
    completion_rate = inputs.completed_jobs / total_jobs if total_jobs else 0.0
    completion = TrustComponent(
        key="completion",
        label=COMPONENT_LABELS["completion"],
        weight=WORKER_WEIGHTS["completion"],
        normalised=_clamp01(_smoothed(completion_rate, total_jobs)),
        detail=(
            f"Completed {inputs.completed_jobs} of {total_jobs} job(s) taken on"
            if total_jobs
            else "No completed jobs yet"
        ),
    )

    return TrustScore(components=(ratings, attendance, verification, completion))


# ---------------------------------------------------------------------------
# Residents
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ResidentTrustInputs:
    """Everything the resident formula needs (SRS 3.9)."""

    payments_due: int = 0
    payments_settled: int = 0

    average_rating: float = 0.0
    rating_count: int = 0

    disputes_against: int = 0
    disputes_upheld_against: int = 0


def resident_trust(inputs: ResidentTrustInputs) -> TrustScore:
    """Compute a resident's trust score.

    A resident's score exists so workers can see who pays and who treats them
    decently — which is the point of SRS 3.9 rating both sides. It is weighted
    toward payment because that is the thing that most directly harms a worker
    when it goes wrong.
    """
    payment_rate = (
        inputs.payments_settled / inputs.payments_due if inputs.payments_due else 0.0
    )
    payment = TrustComponent(
        key="payment",
        label=COMPONENT_LABELS["payment"],
        weight=RESIDENT_WEIGHTS["payment"],
        normalised=_clamp01(_smoothed(payment_rate, inputs.payments_due)),
        detail=(
            f"Settled {inputs.payments_settled} of {inputs.payments_due} payment(s)"
            if inputs.payments_due
            else "No payments due yet"
        ),
    )

    rating_fraction = _smoothed(
        (inputs.average_rating / RATING_MAX) if inputs.average_rating else 0.0,
        inputs.rating_count,
    )
    behaviour = TrustComponent(
        key="behaviour",
        label=COMPONENT_LABELS["behaviour"],
        weight=RESIDENT_WEIGHTS["behaviour"],
        normalised=_clamp01(rating_fraction),
        detail=(
            f"{inputs.average_rating:.1f} out of 5 from {inputs.rating_count} worker(s)"
            if inputs.rating_count
            else "No ratings from workers yet"
        ),
    )

    # Only upheld disputes count against anyone. A raised complaint is an
    # allegation, and letting an unexamined one lower a score would make the
    # complaint button a weapon.
    upheld = inputs.disputes_upheld_against
    complaints = TrustComponent(
        key="complaints",
        label=COMPONENT_LABELS["complaints"],
        weight=RESIDENT_WEIGHTS["complaints"],
        # Each upheld complaint costs a quarter of this component, so four are
        # needed to zero it — one bad month should not be permanent.
        normalised=_clamp01(1.0 - 0.25 * upheld),
        detail=(
            f"{upheld} complaint(s) upheld"
            if upheld
            else "No complaints upheld"
        ),
    )

    return TrustScore(components=(payment, behaviour, complaints))


__all__ = [
    "NEUTRAL_PRIOR",
    "RESIDENT_WEIGHTS",
    "TRUST_SCORE_MAX",
    "WORKER_WEIGHTS",
    "ResidentTrustInputs",
    "TrustComponent",
    "TrustScore",
    "WorkerTrustInputs",
    "resident_trust",
    "worker_trust",
]
