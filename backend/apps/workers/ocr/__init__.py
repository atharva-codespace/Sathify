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
"""

from .crosscheck import CrossCheckResult, MatchStatus, cross_check_with_form
from .engines import OcrEngineError, recognise
from .extraction import ExtractedFields, calculate_age, extract_fields
from .image_input import ImageInputError, load_image
from .output import CRITICAL_FIELD_CONFIDENCE, OcrLine, OcrResult, build_ocr_result
from .pipeline import (
    MINIMUM_WORKER_AGE,
    OcrPipelineError,
    PipelineResult,
    run_ocr_pipeline,
)
from .preprocessing import preprocess_image
from .verhoeff import generate_check_digit, is_valid_aadhaar, mask_aadhaar, normalise_aadhaar

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
