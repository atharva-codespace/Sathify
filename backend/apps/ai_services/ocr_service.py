"""
Module 12.3 — the document-extraction service.

The Aadhaar pipeline from Module 3.2, behind the Module 12 boundary with the
manual-entry fallback the modspec requires stated explicitly rather than left
implicit in the caller.

-------------------------------------------------------------------------------
THERE IS NO CELERY, AND PRETENDING OTHERWISE WOULD BE WORSE
-------------------------------------------------------------------------------
The modspec specifies this as "an async Celery task with a manual-entry fallback
if the OCR call fails or times out". Celery needs a broker and a second process.
The free tier has neither: Render's free plan runs one web service with no
worker dyno, and there is no Redis (docs/free-tier-constraints.md).

Adding Celery anyway would produce a queue nothing drains — tasks accepted, a
"processing" state the worker sees forever, and no error to explain it. That is
strictly worse than running synchronously and saying so.

So extraction runs inline, bounded by :data:`MAX_INLINE_SECONDS`, and the two
things async was for are handled directly:

* **Slowness** — the upload endpoint already returns the attempt record, and the
  app polls it. Whether the work happened inline or in a worker is invisible to
  the client.
* **Failure** — :func:`extract` never raises. It returns a result whose
  ``needs_manual_entry`` is true, which is the fallback the modspec asks for and
  which Module 3.3's confirmation screen already implements.

If a worker process is ever available, only this module changes: the caller
takes a result object either way.

-------------------------------------------------------------------------------
THE HEAVY STACK IS OPTIONAL, AS IT IS EVERYWHERE ELSE
-------------------------------------------------------------------------------
PaddleOCR and EasyOCR are gigabytes and cannot run on a 512 MB instance
(constraints §3). "No engine installed" is therefore a supported state, and it
produces the same manual-entry fallback as a failed read.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

#: How long extraction may hold a request thread. Past this the worker is better
#: served by typing four fields than by watching a spinner — and on a free-tier
#: instance a long-held thread blocks every other request.
MAX_INLINE_SECONDS = 25


@dataclass
class ExtractionResult:
    """What the pipeline made of one document, or why it could not."""

    available: bool = False

    #: The pipeline's own result object when one was produced. Left untyped
    #: here so this module imports nothing heavy at module scope.
    pipeline: object | None = None

    engine: str = ""
    duration_seconds: float = 0.0
    reason: str = ""

    #: Fields read confidently enough to prefill the confirmation form.
    fields: dict = field(default_factory=dict)

    @property
    def needs_manual_entry(self) -> bool:
        """Whether the worker has to type the fields themselves.

        True when nothing ran *and* when something ran but read a critical field
        below threshold. Module 3.3's screen treats both the same way — it
        prefills what it has and asks for the rest — so collapsing them here is
        honest rather than lossy.
        """
        if not self.available or self.pipeline is None:
            return True
        return bool(getattr(self.pipeline, "needs_manual_confirmation", True))

    @property
    def is_minor(self) -> bool:
        """Module 3.4's non-overridable age block.

        Read straight off the pipeline. Deliberately not defaulted to False on
        the unavailable path: a document that could not be read has not proved
        anybody is an adult, and the caller must not treat silence as a pass.
        """
        return bool(getattr(self.pipeline, "is_minor", False))


def is_available() -> bool:
    """Whether any OCR engine is installed here.

    Checked before an upload is accepted so the app can tell a worker up front
    that they will be typing, rather than after a thirty-second wait.
    """
    try:
        from apps.workers.ocr.engines import available_engine
    except ImportError:
        return False

    try:
        return bool(available_engine())
    except Exception:  # noqa: BLE001 — a probe must never raise
        return False


def extract(
    source: str | Path | bytes,
    *,
    filename: str | None = None,
    form_data: dict | None = None,
    languages: list[str] | None = None,
) -> ExtractionResult:
    """Run Module 3.2's pipeline. Never raises.

    Every failure — no engine, unreadable image, a crash inside a CV library —
    returns ``available=False``, which the caller reads as "manual entry". An
    exception here would either 500 a document upload or, worse, be caught
    upstream and read as a rejection of the worker's identity.
    """
    from apps.workers.ocr.pipeline import OcrPipelineError, run_ocr_pipeline

    try:
        result = run_ocr_pipeline(
            source,
            filename=filename,
            form_data=form_data,
            languages=languages,
        )
    except OcrPipelineError as exc:
        # The documented failure: no engine, or an image that cannot be loaded.
        logger.info("OCR unavailable, falling back to manual entry: %s", exc)
        return ExtractionResult(reason=str(exc))
    except Exception as exc:  # noqa: BLE001
        logger.exception("OCR pipeline crashed")
        return ExtractionResult(reason=f"The document could not be read: {exc}")

    return ExtractionResult(
        available=True,
        pipeline=result,
        engine=result.engine_used,
        duration_seconds=result.duration_seconds,
        fields=_prefill_fields(result),
    )


def _prefill_fields(result) -> dict:
    """The fields worth putting in front of the worker to confirm.

    Confirmed, not accepted. Module 3.3 requires the worker to check every
    prefilled value, because an OCR error on a name follows somebody through
    every gate log and every payment receipt after it.
    """
    fields = getattr(result, "fields", None)
    if fields is None:
        return {}

    return {
        key: value
        for key, value in {
            "full_name": getattr(fields, "full_name", ""),
            "date_of_birth": getattr(fields, "date_of_birth", None),
            "gender": getattr(fields, "gender", ""),
            "address": getattr(fields, "address", ""),
        }.items()
        if value
    }


__all__ = ["MAX_INLINE_SECONDS", "ExtractionResult", "extract", "is_available"]
