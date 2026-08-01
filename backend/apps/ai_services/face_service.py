"""
Module 12.4 — the face-verification service.

Module 7.3's comparison, behind the Module 12 boundary, with the two things the
modspec names made explicit in the type: "a confidence threshold and a mandatory
guard-override path built in from the start".

-------------------------------------------------------------------------------
THE OVERRIDE PATH IS NOT OPTIONAL, AND THE TYPE SAYS SO
-------------------------------------------------------------------------------
:class:`FaceCheck` has no state meaning "denied". It has ``verified`` and
``requires_human_decision``, and the second is true whenever the first is not —
whether the comparison ran and failed, or never ran at all.

That is a deliberate refusal to give this model a veto. Face recognition is
measurably less accurate for darker skin tones, older camera sensors and poor
lighting, which between them describe the gate, the phone and the workforce this
platform is built for. A false reject costs a worker a day's wages for a model's
error, so the model gets a vote and a guard decides. Module 7's ``face.py``
carries the same note, and the gate flow turns a below-threshold match into
``PENDING_REVIEW`` rather than ``DENIED``.

-------------------------------------------------------------------------------
THE THRESHOLD IS CONFIGURATION AND IS REPORTED WITH EVERY RESULT
-------------------------------------------------------------------------------
A score is meaningless without the bar it was measured against, and the bar
moves between deployments and between engines. Returning both means a gate log
read six months later still says what "0.58" meant at the time.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from apps.attendance import face as face_engine

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FaceCheck:
    """The outcome of one comparison, as Module 12 exposes it."""

    #: Whether a comparison actually ran. False means nothing was measured, and
    #: therefore nothing may be concluded.
    available: bool

    verified: bool = False

    #: Similarity on 0–1, or None when nothing ran.
    score: float | None = None

    #: The bar the score was measured against. Stored with the result so an old
    #: gate log stays interpretable after the threshold is retuned.
    threshold: float = 0.0

    engine: str = ""
    reason: str = ""

    @property
    def requires_human_decision(self) -> bool:
        """The mandatory override path.

        True unless the comparison ran *and* passed. There is deliberately no
        combination of fields that means "turn this person away".
        """
        return not (self.available and self.verified)

    @property
    def outcome(self) -> str:
        """A short label for logs and for the guard's screen."""
        if not self.available:
            return "unavailable"
        return "verified" if self.verified else "below_threshold"


def is_available() -> bool:
    """Whether this deployment can compare faces at all.

    The heavy CV stack does not fit on a 512 MB instance (constraints §3), so
    "no" is the expected answer in production and the guard app is built for it.
    """
    return bool(face_engine.is_enabled() and face_engine.available_engine())


def threshold() -> float:
    """The similarity bar currently in force."""
    return face_engine._threshold()  # noqa: SLF001 — one owner, deliberately shared


def verify(live_path: str, reference_path: str) -> FaceCheck:
    """Compare a live gate photo against a worker's registered photo.

    Never raises — see :mod:`apps.attendance.face`, which this delegates to.
    Every failure becomes ``available=False``, which
    :attr:`requires_human_decision` turns into a guard's decision rather than a
    refusal.
    """
    result = face_engine.verify_face(live_path, reference_path)

    return FaceCheck(
        available=result.available,
        verified=result.verified,
        score=result.score,
        threshold=threshold(),
        engine=result.engine,
        reason=result.reason,
    )


__all__ = ["FaceCheck", "is_available", "threshold", "verify"]
