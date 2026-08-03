"""
The 8-stage Aadhaar OCR pipeline (Module 3.2 / SRS 3.14).

    STAGE 1  Image input          — image_input.py    JPG/PNG/PDF/scan/camera
    STAGE 2  Preprocessing        — preprocessing.py  OpenCV, six steps
    STAGE 3  Text detection       — engines.py        locate each text region
    STAGE 4  Character recognition— engines.py        region -> text
    STAGE 5  Output               — output.py         (box, text, confidence)
    STAGE 6  Field extraction     — extraction.py     regex -> structured fields
    STAGE 7  Aadhaar checksum     — verhoeff.py       Verhoeff validation
    STAGE 8  Form cross-check     — crosscheck.py     matched / mismatch

PaddleOCR is primary; EasyOCR is the automatic fallback. Stages 1, 2 and 5-8
depend only on OpenCV and the standard library, so most of the pipeline is
testable without either engine installed.

Stages 1 and 2 are the only ones that need OpenCV, which ships in
requirements/cv.txt (or ml.txt, which includes it) rather than in base.txt.
Importing them here eagerly would make
``import apps.workers.ocr`` fail on a base install — and Django does exactly
that import at startup, via apps.workers.admin -> services -> here, so every
management command would fail with a NumPy/OpenCV traceback that says nothing
about KYC. They are therefore resolved on first attribute access instead (PEP
562, ``__getattr__`` below); everything else is pure Python and imported
normally.
"""

from importlib import import_module

from .crosscheck import CrossCheckResult, MatchStatus, cross_check_with_form
from .engines import OcrEngineError, recognise
from .extraction import ExtractedFields, calculate_age, extract_fields
from .output import CRITICAL_FIELD_CONFIDENCE, OcrLine, OcrResult, build_ocr_result
from .pipeline import (
    MINIMUM_WORKER_AGE,
    OcrPipelineError,
    PipelineResult,
    run_ocr_pipeline,
)
from .verhoeff import generate_check_digit, is_valid_aadhaar, mask_aadhaar, normalise_aadhaar

#: Names re-exported from the two OpenCV-backed stage modules, mapped to the
#: submodule that defines them. Kept in __all__ below so the package's public
#: surface is unchanged — only *when* the import happens moved.
_OPENCV_BACKED = {
    "ImageInputError": "image_input",
    "load_image": "image_input",
    "preprocess_image": "preprocessing",
}


def __getattr__(name: str):
    """Resolve the OpenCV-backed names on first use (PEP 562).

    Raises the underlying ImportError when OpenCV is genuinely missing, rather
    than masking it as an AttributeError: a caller reaching for ``load_image``
    on a base install has a dependency problem, and should be told so.
    """
    if name in _OPENCV_BACKED:
        value = getattr(import_module(f".{_OPENCV_BACKED[name]}", __name__), name)
        globals()[name] = value  # later lookups bypass __getattr__ entirely
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

__all__ = [
    "CRITICAL_FIELD_CONFIDENCE",
    "MINIMUM_WORKER_AGE",
    "CrossCheckResult",
    "ExtractedFields",
    "MatchStatus",
    "OcrEngineError",
    "OcrLine",
    "OcrPipelineError",
    "OcrResult",
    "PipelineResult",
    "build_ocr_result",
    "calculate_age",
    "cross_check_with_form",
    "extract_fields",
    "generate_check_digit",
    "is_valid_aadhaar",
    "load_image",
    "mask_aadhaar",
    "normalise_aadhaar",
    "preprocess_image",
    "recognise",
    "run_ocr_pipeline",
]
