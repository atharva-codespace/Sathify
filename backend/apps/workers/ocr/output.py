"""
OCR pipeline — STAGE 5: Output format.

Every engine is normalised to one shape, matching the specification:

    [([[x, y], [x, y], [x, y], [x, y]], "John Smith", 0.98)]

where the first element is the bounding box, the second the recognised text and
the third the confidence. PaddleOCR and EasyOCR return subtly different
structures, so normalising here means Stages 6 and 8 are written once against a
single format and never branch on which engine ran.

The confidence threshold is enforced here rather than downstream, because the
rule is about trust in a *reading*, not about what the field means: below
``CRITICAL_FIELD_CONFIDENCE`` a critical field is never silently auto-filled.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

BoundingBox = list[list[int]]

#: Below this, a critical field (above all the Aadhaar number) must be flagged
#: for manual confirmation rather than auto-filled. Mirrors
#: settings.OCR_SETTINGS["CONFIDENCE_THRESHOLD"].
CRITICAL_FIELD_CONFIDENCE = 0.85


@dataclass
class OcrLine:
    """One recognised text region."""

    bounding_box: BoundingBox
    text: str
    confidence: float

    def as_tuple(self) -> tuple[BoundingBox, str, float]:
        """The specified output shape: ``([[x,y],...], "text", 0.98)``."""
        return (self.bounding_box, self.text, self.confidence)

    @property
    def is_confident(self) -> bool:
        return self.confidence >= CRITICAL_FIELD_CONFIDENCE

    @property
    def top_y(self) -> int:
        """Vertical position, used to read the document in visual order."""
        return min(point[1] for point in self.bounding_box) if self.bounding_box else 0

    @property
    def left_x(self) -> int:
        return min(point[0] for point in self.bounding_box) if self.bounding_box else 0


@dataclass
class OcrResult:
    """The complete Stage 5 output for one document."""

    lines: list[OcrLine] = field(default_factory=list)
    engine: str = ""
    #: Populated when the primary engine failed and the fallback served instead.
    fallback_reason: str = ""

    @property
    def raw_text(self) -> str:
        """All recognised text, in reading order (top to bottom, left to right).

        Field extraction in Stage 6 relies on this ordering: a label and its
        value are frequently adjacent, so scrambled order breaks the regexes.
        """
        ordered = sorted(self.lines, key=lambda line: (line.top_y, line.left_x))
        return "\n".join(line.text for line in ordered)

    @property
    def mean_confidence(self) -> float:
        if not self.lines:
            return 0.0
        return sum(line.confidence for line in self.lines) / len(self.lines)

    def as_tuples(self) -> list[tuple[BoundingBox, str, float]]:
        """Full result in the specified shape."""
        return [line.as_tuple() for line in self.lines]

    def line_for(self, text: str) -> OcrLine | None:
        """Find the line a given value was read from.

        Lets Stage 6 attach the correct per-field confidence and bounding box to
        an extracted value, instead of applying one document-wide average that
        would hide a single badly-read field.
        """
        needle = text.strip().lower()
        for line in self.lines:
            if needle and needle in line.text.strip().lower():
                return line
        return None


def _to_int_point(point: Any) -> list[int]:
    return [int(round(float(point[0]))), int(round(float(point[1])))]


def normalise_box(box: Any) -> BoundingBox:
    """Coerce an engine's box into ``[[x, y], [x, y], [x, y], [x, y]]``.

    Engines variously return numpy arrays, nested lists, or floats; the stored
    result must be plain JSON-serialisable integers.
    """
    if box is None:
        return []
    try:
        return [_to_int_point(point) for point in box]
    except (TypeError, ValueError, IndexError):
        return []


def build_ocr_result(
    raw_lines: list[tuple[Any, str, float]], engine: str, fallback_reason: str = ""
) -> OcrResult:
    """STAGE 5 — assemble normalised engine output into an ``OcrResult``.

    Blank strings are dropped: engines occasionally emit empty regions, and
    those would otherwise pollute ``raw_text`` with stray newlines that the
    Stage 6 regexes then have to tolerate.
    """
    lines = [
        OcrLine(
            bounding_box=normalise_box(box),
            text=str(text).strip(),
            confidence=float(confidence),
        )
        for box, text, confidence in raw_lines
        if str(text).strip()
    ]
    return OcrResult(lines=lines, engine=engine, fallback_reason=fallback_reason)
