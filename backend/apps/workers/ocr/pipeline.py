"""
OCR pipeline — orchestration.

Runs all eight stages end to end and returns one result object carrying the
output of each, so the admin approval screen can show not just *what* was read
but *how* it was arrived at.

    STAGE 1  load_image             image_input.py
    STAGE 2  preprocess_image       preprocessing.py
    STAGE 3  text detection    ┐
    STAGE 4  character recognition  engines.py  (both engines do 3+4 in one pass)
    STAGE 5  build_ocr_result       output.py
    STAGE 6  extract_fields         extraction.py
    STAGE 7  is_valid_aadhaar       verhoeff.py
    STAGE 8  cross_check_with_form  crosscheck.py

Each stage is independently importable and testable; this module only sequences
them. Stages 1, 2 and 5-8 need nothing beyond OpenCV and the standard library,
so the majority of the pipeline is testable without the heavy OCR engines
installed.

Stages 1 and 2 are imported inside :func:`run_ocr_pipeline` rather than at
module scope, because they are the only two that need OpenCV — and OpenCV ships
in requirements/cv.txt (or ml.txt), not base.txt. Django reaches this module at
startup
(apps.workers.admin -> services -> ocr), so a module-scope OpenCV import would
make every management command fail on a base install. Deferring it means a
missing OpenCV surfaces as ``OcrPipelineError`` at the point of use, which is
already the manual-entry fallback path every caller handles (SRS 2.5).
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from django.conf import settings

from .crosscheck import CrossCheckResult, MatchStatus, cross_check_with_form
from .engines import OcrEngineError, recognise
from .extraction import ExtractedFields, calculate_age, extract_fields
from .output import OcrResult
from .verhoeff import mask_aadhaar

if TYPE_CHECKING:
    from .preprocessing import PreprocessingResult

logger = logging.getLogger(__name__)

#: A worker under this age cannot be onboarded. Module 3.4 — a hard block, not
#: an administrator's discretion.
MINIMUM_WORKER_AGE = 18


class OcrPipelineError(Exception):
    """Raised when the pipeline cannot produce any result at all."""


@dataclass
class PipelineResult:
    """Everything the eight stages produced, for review and for storage."""

    # --- Stage outputs ------------------------------------------------------
    preprocessing: PreprocessingResult | None = None
    ocr: OcrResult | None = None
    fields: ExtractedFields | None = None
    cross_check: CrossCheckResult | None = None

    # --- Verdicts -----------------------------------------------------------
    aadhaar_checksum_valid: bool = False
    age: int | None = None
    #: Module 3.4 — automatic, non-overridable rejection when under 18.
    is_minor: bool = False

    engine_used: str = ""
    duration_seconds: float = 0.0
    source_format: str = ""

    @property
    def needs_manual_confirmation(self) -> bool:
        """True when a critical field was read below the confidence threshold."""
        return bool(self.fields and self.fields.needs_manual_confirmation)

    @property
    def has_mismatch(self) -> bool:
        return bool(self.cross_check and self.cross_check.has_mismatch)

    @property
    def can_auto_approve(self) -> bool:
        """Whether nothing needs a human eye.

        Never used to approve automatically — Module 3.5 requires an
        administrator to look at every worker. It only tells the review screen
        which records are straightforward and which need attention.
        """
        return (
            self.aadhaar_checksum_valid
            and not self.is_minor
            and not self.needs_manual_confirmation
            and not self.has_mismatch
        )

    def as_dict(self) -> dict:
        """Safe summary for storage and for the API. Aadhaar is masked."""
        fields = self.fields.as_safe_dict() if self.fields else {}
        return {
            "fields": fields,
            "aadhaar_masked": mask_aadhaar(
                self.fields.aadhaar.value if self.fields and self.fields.aadhaar else ""
            ),
            "aadhaar_checksum_valid": self.aadhaar_checksum_valid,
            "age": self.age,
            "is_minor": self.is_minor,
            "low_confidence_fields": self.fields.low_confidence_fields if self.fields else [],
            "needs_manual_confirmation": self.needs_manual_confirmation,
            "cross_check": self.cross_check.as_dict() if self.cross_check else {},
            "cross_check_summary": self.cross_check.summary() if self.cross_check else "",
            "engine_used": self.engine_used,
            "preprocessing_steps": self.preprocessing.steps_applied if self.preprocessing else [],
            "mean_confidence": round(self.ocr.mean_confidence, 4) if self.ocr else 0.0,
            "duration_seconds": round(self.duration_seconds, 3),
            "source_format": self.source_format,
        }


def run_ocr_pipeline(
    source: str | Path | bytes,
    *,
    filename: str | None = None,
    form_data: dict[str, str] | None = None,
    languages: list[str] | None = None,
    skip_preprocessing: bool = False,
) -> PipelineResult:
    """Run all eight stages against one document.

    ``form_data`` supplies what the worker typed at registration, enabling
    Stage 8. Omitting it skips only that stage.

    Raises ``OcrPipelineError`` when the image cannot be read, when OpenCV is
    not installed, or when no OCR engine is available — in every case the caller
    falls back to manual entry, as SRS 2.5 requires. All three are ordinary
    states on a machine that has not installed the CV stack, not faults.
    """
    started = time.perf_counter()
    ocr_settings = getattr(settings, "OCR_SETTINGS", {})
    languages = languages or ocr_settings.get("LANGUAGES", ["en"])

    # Stages 1 and 2 are the OpenCV-backed ones — see the module docstring for
    # why they are reached here and not at import time.
    try:
        from .image_input import ImageInputError, load_image
        from .preprocessing import PreprocessingResult, preprocess_image
    except ImportError as exc:
        raise OcrPipelineError(
            "OpenCV is not installed, so documents cannot be scanned here "
            "(`pip install -r requirements/cv.txt`, or ml.txt for the OCR "
            "engines too). Enter the details manually."
        ) from exc

    result = PipelineResult()

    # --- STAGE 1: image input ----------------------------------------------
    try:
        loaded = load_image(source, filename=filename)
    except ImageInputError as exc:
        raise OcrPipelineError(str(exc)) from exc

    result.source_format = loaded.source_format
    if loaded.is_low_resolution:
        logger.warning(
            "Low-resolution upload (%dx%d); accuracy will suffer.",
            loaded.width, loaded.height,
        )

    # --- STAGE 2: preprocessing --------------------------------------------
    # Never skipped in production. The flag exists so tests can isolate the
    # engine's behaviour from the preprocessing chain's.
    if skip_preprocessing:
        from .preprocessing import to_grayscale

        result.preprocessing = PreprocessingResult(
            image=to_grayscale(loaded.image), steps_applied=["grayscale"]
        )
    else:
        result.preprocessing = preprocess_image(loaded.image)

    # --- STAGES 3 + 4 + 5: detect, recognise, normalise ---------------------
    try:
        result.ocr = recognise(result.preprocessing.image, languages=languages)
    except OcrEngineError as exc:
        raise OcrPipelineError(str(exc)) from exc

    result.engine_used = result.ocr.engine
    if result.ocr.fallback_reason:
        logger.info("OCR fell back: %s", result.ocr.fallback_reason)

    # --- STAGE 6: field extraction (runs STAGE 7 on the Aadhaar number) -----
    result.fields = extract_fields(result.ocr)
    result.aadhaar_checksum_valid = result.fields.aadhaar_checksum_valid

    # --- Module 3.4: age gate ----------------------------------------------
    if result.fields.dob:
        result.age = calculate_age(result.fields.dob.value)
        # An unreadable date is NOT treated as a minor: that would reject
        # adults over an OCR failure. It surfaces for manual review instead.
        result.is_minor = result.age is not None and result.age < MINIMUM_WORKER_AGE

    # --- STAGE 8: cross-check against the registration form -----------------
    if form_data:
        result.cross_check = cross_check_with_form(result.fields, form_data)

    result.duration_seconds = time.perf_counter() - started
    logger.info(
        "OCR pipeline finished in %.2fs via %s (checksum_valid=%s, minor=%s, mismatch=%s)",
        result.duration_seconds,
        result.engine_used,
        result.aadhaar_checksum_valid,
        result.is_minor,
        result.has_mismatch,
    )
    return result


__all__ = [
    "MINIMUM_WORKER_AGE",
    "MatchStatus",
    "OcrPipelineError",
    "PipelineResult",
    "run_ocr_pipeline",
]
