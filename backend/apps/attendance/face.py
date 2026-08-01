"""
Module 7.3 / SRS 3.15 — face verification at the gate.

A live photo is compared against the worker's registered profile photo. The
comparison is behind one interface with two backends and a defined
"unavailable" outcome, for the same reason the OCR pipeline is: the constraints
document establishes that the heavy CV stack cannot run on the free web tier
(docs/free-tier-constraints.md §3), so "no engine here" is a supported state
rather than a crash.

-------------------------------------------------------------------------------
UNAVAILABLE AND FAILED ARE NOT THE SAME THING, AND NEITHER DENIES ENTRY
-------------------------------------------------------------------------------
Three distinct outcomes, and conflating them would hurt real people:

* **verified** — the faces match above threshold. Entry proceeds.
* **not verified** — a real comparison ran and came back below threshold. This
  produces a *guard review*, never an automatic denial.
* **unavailable** — no engine is installed, or the photo could not be processed.
  Nothing was measured, so nothing can be concluded; the guard decides as they
  would have before this feature existed.

The reason a below-threshold match never auto-denies is not politeness. Face
recognition is measurably less accurate for darker skin tones, older cameras
and poor lighting — which describes the gate conditions and the workforce this
platform is built for. A false rejection costs someone a day's wages for a
model's error, so the model gets a vote, not a veto. ``FACE_SETTINGS`` carries
the same note.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from functools import lru_cache

from django.conf import settings

logger = logging.getLogger(__name__)

#: Similarity at or above which a match is accepted, on 0–1. Deliberately not
#: aggressive: the cost of a false accept here is a guard also looking at the
#: person, while the cost of a false reject is someone turned away from work.
DEFAULT_THRESHOLD = 0.62


@dataclass(frozen=True)
class FaceResult:
    """The outcome of one comparison."""

    #: Whether a comparison actually ran. False means nothing was measured.
    available: bool
    verified: bool = False
    #: Similarity on 0–1. None when nothing ran.
    score: float | None = None
    engine: str = ""
    reason: str = ""

    @property
    def needs_guard_review(self) -> bool:
        """Whether a human has to decide.

        True both when the comparison failed *and* when it could not run — in
        neither case has the system established identity, and in neither case
        may it turn someone away on its own.
        """
        return not self.verified


def _threshold() -> float:
    return float(
        getattr(settings, "FACE_SETTINGS", {}).get("THRESHOLD", DEFAULT_THRESHOLD)
    )


def is_enabled() -> bool:
    return bool(getattr(settings, "FACE_SETTINGS", {}).get("ENABLED", True))


@lru_cache(maxsize=1)
def available_engine() -> str:
    """Which backend this deployment actually has. Cached — imports are slow.

    Order follows the constraints document's recommendation: the specified
    DeepFace path first, then OpenCV's bundled SFace recogniser, which is a few
    megabytes and needs no TensorFlow, as the deployment-profile fallback.
    """
    try:
        import deepface  # noqa: F401

        return "deepface"
    except Exception:  # noqa: BLE001 — any import failure means "not usable here"
        pass

    try:
        import cv2

        # SFace ships with opencv-contrib; plain opencv-python does not have it.
        if hasattr(cv2, "FaceRecognizerSF"):
            return "sface"
    except Exception:  # noqa: BLE001
        pass

    return ""


def _verify_with_deepface(live_path: str, reference_path: str) -> FaceResult:
    from deepface import DeepFace

    face_settings = getattr(settings, "FACE_SETTINGS", {})
    outcome = DeepFace.verify(
        img1_path=live_path,
        img2_path=reference_path,
        model_name=face_settings.get("MODEL", "Facenet"),
        detector_backend=face_settings.get("DETECTOR_BACKEND", "opencv"),
        distance_metric=face_settings.get("DISTANCE_METRIC", "cosine"),
        enforce_detection=False,
    )

    # DeepFace reports distance; the rest of this module speaks similarity, so
    # convert once here rather than leaving every caller to remember which is
    # which.
    distance = float(outcome.get("distance", 1.0))
    score = max(0.0, min(1.0, 1.0 - distance))

    return FaceResult(
        available=True,
        verified=score >= _threshold(),
        score=score,
        engine="deepface",
    )


def _verify_with_sface(live_path: str, reference_path: str) -> FaceResult:
    """OpenCV SFace fallback.

    Present so a deployment without TensorFlow still has *a* comparison rather
    than none. Accuracy is lower than the specified DeepFace path, which is why
    it is second and why the engine used is recorded on every event.
    """
    import cv2

    model_path = getattr(settings, "FACE_SETTINGS", {}).get("SFACE_MODEL_PATH", "")
    if not model_path:
        return FaceResult(
            available=False,
            engine="sface",
            reason="No SFace model file configured (FACE_SETTINGS['SFACE_MODEL_PATH']).",
        )

    recogniser = cv2.FaceRecognizerSF.create(model_path, "")
    live = cv2.imread(live_path)
    reference = cv2.imread(reference_path)
    if live is None or reference is None:
        return FaceResult(
            available=False, engine="sface", reason="One of the images could not be read."
        )

    score = float(
        recogniser.match(
            recogniser.feature(live),
            recogniser.feature(reference),
            cv2.FaceRecognizerSF_FR_COSINE,
        )
    )
    score = max(0.0, min(1.0, score))

    return FaceResult(
        available=True,
        verified=score >= _threshold(),
        score=score,
        engine="sface",
    )


def verify_face(live_path: str, reference_path: str) -> FaceResult:
    """Compare a live gate photo against a worker's registered photo.

    Never raises. Every failure path — feature disabled, no engine installed,
    unreadable image, engine crash — comes back as ``available=False``, because
    an exception here would either 500 a gate scan or, worse, be caught
    somewhere upstream and read as a denial.
    """
    if not is_enabled():
        return FaceResult(available=False, reason="Face verification is switched off.")

    engine = available_engine()
    if not engine:
        return FaceResult(
            available=False,
            reason=(
                "No face recognition engine is installed on this server. "
                "The guard verifies visually instead."
            ),
        )

    try:
        if engine == "deepface":
            return _verify_with_deepface(live_path, reference_path)
        return _verify_with_sface(live_path, reference_path)
    except Exception as exc:  # noqa: BLE001
        # A crash inside a third-party CV library must not cost the worker
        # their entry. Logged with a traceback so it can be fixed; reported as
        # "nothing measured" so the guard simply decides.
        logger.exception("Face verification failed")
        return FaceResult(
            available=False,
            engine=engine,
            reason=f"The comparison could not be completed: {exc}",
        )


__all__ = [
    "DEFAULT_THRESHOLD",
    "FaceResult",
    "available_engine",
    "is_enabled",
    "verify_face",
]
