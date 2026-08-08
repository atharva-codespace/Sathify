"""
OCR pipeline — STAGES 3 & 4: Text detection and character recognition.

PaddleOCR is the primary engine: higher accuracy than the alternatives, handles
rotated, curved and vertical text, runs fully offline, and covers 80+ languages.
EasyOCR is the automatic fallback, used when PaddleOCR is not installed or
raises at runtime.

Both engines perform detection (Stage 3) and recognition (Stage 4) in a single
call, so the two stages are exposed together per engine but documented
separately. ``detect_text_regions`` is provided for the cases where only Stage 3
is wanted — previewing what the engine found before paying for recognition.

Engines are imported lazily inside the methods. Importing paddle or torch at
module import time would add seconds to every Django start, including the
majority of requests that never touch OCR.

numpy is imported under TYPE_CHECKING for the same reason, one step further:
it appears here only in annotations, and ``from __future__ import annotations``
leaves those unevaluated. It ships in requirements/ml.txt, so importing it for
real would make this module — and therefore Django's whole app registry, which
reaches it via apps.workers.admin — unimportable on a base install.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Protocol

from .output import OcrResult, build_ocr_result

if TYPE_CHECKING:
    import numpy as np

logger = logging.getLogger(__name__)


class OcrEngineError(Exception):
    """Raised when an engine is unavailable or fails to process an image."""


class OcrEngine(Protocol):
    """The contract every engine implements."""

    name: str

    def is_available(self) -> bool: ...

    def run(self, image: np.ndarray) -> list[tuple[Any, str, float]]: ...


class PaddleOcrEngine:
    """Primary engine — PaddleOCR (Baidu, PaddlePaddle backend)."""

    name = "paddleocr"

    def __init__(self, languages: list[str] | None = None):
        self.languages = languages or ["en"]
        self._reader = None

    def is_available(self) -> bool:
        """Whether this engine can actually serve a document.

        Importing is not enough, and assuming it was hid a real failure for a
        while: paddleocr pulls in ``paddlex`` unbounded, and a newer paddlex
        changed a constructor signature paddleocr still calls positionally. The
        module imported perfectly and then raised ``TypeError`` on the first
        real document, so every scan quietly fell through to EasyOCR while this
        method kept reporting the primary engine as ready.

        Constructing the reader is the honest test, and it is cached, so the
        cost is paid once per process either way.
        """
        try:
            import paddleocr  # noqa: F401
        except ImportError:
            return False

        try:
            self._get_reader()
        except Exception as exc:  # noqa: BLE001 — a probe must never raise
            logger.warning("PaddleOCR is installed but unusable: %s", exc)
            return False

        return True

    def _get_reader(self):
        """Build the reader once and reuse it.

        Construction loads detection and recognition models from disk (and
        downloads them on first use), which takes seconds — far too expensive
        to repeat per request.
        """
        if self._reader is not None:
            return self._reader

        try:
            from paddleocr import PaddleOCR
        except ImportError as exc:
            raise OcrEngineError("PaddleOCR is not installed.") from exc

        try:
            self._reader = PaddleOCR(lang=self.languages[0], use_textline_orientation=True)
        except TypeError:
            # Older PaddleOCR releases name this parameter use_angle_cls.
            self._reader = PaddleOCR(lang=self.languages[0], use_angle_cls=True)

        return self._reader

    def run(self, image: np.ndarray) -> list[tuple[Any, str, float]]:
        """STAGES 3 + 4 — detect text regions, then recognise their contents."""
        reader = self._get_reader()

        try:
            raw = reader.predict(image)
        except AttributeError:
            # PaddleOCR 2.x exposed .ocr() instead of .predict().
            raw = reader.ocr(image)

        return _normalise_paddle_output(raw)


class EasyOcrEngine:
    """Automatic fallback — EasyOCR (PyTorch backend)."""

    name = "easyocr"

    def __init__(self, languages: list[str] | None = None):
        self.languages = languages or ["en"]
        self._reader = None

    def is_available(self) -> bool:
        try:
            import easyocr  # noqa: F401

            return True
        except ImportError:
            return False

    def _get_reader(self):
        if self._reader is not None:
            return self._reader

        try:
            import easyocr
        except ImportError as exc:
            raise OcrEngineError("EasyOCR is not installed.") from exc

        # gpu=False: the free-tier deployment target has no GPU, and forcing
        # CUDA detection on a CPU-only box wastes startup time.
        self._reader = easyocr.Reader(self.languages, gpu=False, verbose=False)
        return self._reader

    def run(self, image: np.ndarray) -> list[tuple[Any, str, float]]:
        """STAGES 3 + 4 via EasyOCR's readtext()."""
        reader = self._get_reader()
        raw = reader.readtext(image)
        # EasyOCR already returns (box, text, confidence) triples.
        return [(box, text, confidence) for box, text, confidence in raw]


def _normalise_paddle_output(raw: Any) -> list[tuple[Any, str, float]]:
    """Flatten PaddleOCR's output, whose shape differs between major versions.

    PaddleOCR 3.x returns a list of dicts keyed ``rec_texts`` / ``rec_scores`` /
    ``rec_polys``; 2.x returned a nested list of ``[box, (text, score)]``.
    Supporting both keeps a version bump from breaking KYC.
    """
    if not raw:
        return []

    results: list[tuple[Any, str, float]] = []

    # --- PaddleOCR 3.x dict form -------------------------------------------
    first = raw[0] if isinstance(raw, list) and raw else raw
    if isinstance(first, dict):
        texts = first.get("rec_texts", [])
        scores = first.get("rec_scores", [])
        polys = first.get("rec_polys", first.get("dt_polys", []))
        for index, text in enumerate(texts):
            box = polys[index] if index < len(polys) else []
            score = scores[index] if index < len(scores) else 0.0
            results.append((box, text, score))
        return results

    # --- PaddleOCR 2.x nested-list form ------------------------------------
    pages = raw if isinstance(raw[0], list) else [raw]
    for page in pages:
        if not page:
            continue
        for entry in page:
            try:
                box, (text, score) = entry[0], entry[1]
                results.append((box, text, score))
            except (IndexError, TypeError, ValueError):
                logger.debug("Skipping unrecognised PaddleOCR entry: %r", entry)
    return results


def detect_text_regions(image: np.ndarray, engine: OcrEngine | None = None) -> list[Any]:
    """STAGE 3 (alone) — return just the bounding boxes of detected text.

    Useful for previewing detection quality without paying for recognition.
    The full pipeline does not call this: both engines detect and recognise in
    one pass, so calling it separately would run detection twice.
    """
    engine = engine or PaddleOcrEngine()
    return [box for box, _text, _confidence in engine.run(image)]


def recognise(
    image: np.ndarray,
    *,
    primary: OcrEngine | None = None,
    fallback: OcrEngine | None = None,
    languages: list[str] | None = None,
) -> OcrResult:
    """STAGES 3 + 4 — run OCR with automatic fallback.

    Tries the primary engine; on unavailability or any runtime error, falls
    through to the fallback. The reason is recorded on the result so the admin
    review screen can show which engine actually produced a reading — accuracy
    expectations differ between them.

    Raises ``OcrEngineError`` only when BOTH engines fail, at which point the
    caller must fall back to manual entry (SRS 2.5: AI features degrade to
    manual workflows).
    """
    primary = primary or PaddleOcrEngine(languages)
    fallback = fallback or EasyOcrEngine(languages)

    if primary.is_available():
        try:
            raw_lines = primary.run(image)
            logger.debug("OCR served by primary engine '%s'", primary.name)
            return build_ocr_result(raw_lines, engine=primary.name)
        except Exception as exc:
            reason = f"{primary.name} failed at runtime: {exc}"
            logger.warning("%s — falling back to %s", reason, fallback.name)
    else:
        reason = f"{primary.name} is not installed"
        logger.info("%s — falling back to %s", reason, fallback.name)

    if not fallback.is_available():
        raise OcrEngineError(
            f"No OCR engine available ({reason}; {fallback.name} is not installed "
            f"either). Install requirements/ml.txt, or enter the details manually."
        )

    try:
        raw_lines = fallback.run(image)
    except Exception as exc:
        raise OcrEngineError(
            f"Both OCR engines failed ({reason}; {fallback.name}: {exc}). "
            f"Please enter the details manually."
        ) from exc

    logger.info("OCR served by fallback engine '%s'", fallback.name)
    return build_ocr_result(raw_lines, engine=fallback.name, fallback_reason=reason)
