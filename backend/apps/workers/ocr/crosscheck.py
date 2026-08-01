"""
OCR pipeline — STAGE 8: Cross-check against the registration form.

Compares what the worker typed during registration against what OCR read from
their Aadhaar card, and reports a per-field verdict:

    matched  |  mismatch — please review  |  not compared

The pipeline deliberately does NOT pick a winner. Both sources can be wrong —
the worker can typo, and OCR can misread — so choosing silently would bury the
disagreement precisely when a human should see it. The verdict is surfaced in
the admin approval screen and the administrator decides.

Matching rules, per the specification:
  * name — case-insensitive
  * dob  — exact
"""

from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass, field
from enum import Enum

from .extraction import ExtractedFields
from .verhoeff import normalise_aadhaar

logger = logging.getLogger(__name__)


class MatchStatus(str, Enum):
    MATCHED = "matched"
    MISMATCH = "mismatch"
    #: One side is missing, so there is nothing to compare. Distinct from a
    #: mismatch: absence of evidence is not evidence of conflict.
    NOT_COMPARED = "not_compared"


@dataclass
class FieldComparison:
    field_name: str
    form_value: str
    ocr_value: str
    status: MatchStatus
    note: str = ""

    @property
    def is_mismatch(self) -> bool:
        return self.status is MatchStatus.MISMATCH


@dataclass
class CrossCheckResult:
    comparisons: list[FieldComparison] = field(default_factory=list)

    @property
    def mismatches(self) -> list[FieldComparison]:
        return [c for c in self.comparisons if c.is_mismatch]

    @property
    def has_mismatch(self) -> bool:
        return bool(self.mismatches)

    @property
    def overall_status(self) -> MatchStatus:
        if self.has_mismatch:
            return MatchStatus.MISMATCH
        if any(c.status is MatchStatus.MATCHED for c in self.comparisons):
            return MatchStatus.MATCHED
        return MatchStatus.NOT_COMPARED

    def as_dict(self) -> dict[str, dict[str, str]]:
        """Shape consumed by the admin approval screen."""
        return {
            comparison.field_name: {
                "form": comparison.form_value,
                "ocr": comparison.ocr_value,
                "status": comparison.status.value,
                "note": comparison.note,
            }
            for comparison in self.comparisons
        }

    def summary(self) -> str:
        if self.overall_status is MatchStatus.MATCHED:
            return "All compared fields matched."
        if self.has_mismatch:
            names = ", ".join(c.field_name for c in self.mismatches)
            return f"Mismatch — please review: {names}."
        return "Not enough information to compare."


def normalise_name(value: str) -> str:
    """Fold a name for comparison.

    Handles the differences that are not real disagreements: case, extra
    whitespace, punctuation, accents, and honorifics that appear on a form but
    never on the card.
    """
    if not value:
        return ""

    # Strip accents so "Rahúl" and "Rahul" compare equal.
    decomposed = unicodedata.normalize("NFKD", value)
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))

    # Apostrophes are DELETED rather than replaced with a space, so "O'Brien"
    # and "OBrien" normalise identically. Replacing them would yield "o brien"
    # and make those two spellings mismatch.
    without_apostrophes = re.sub(r"['’`]", "", stripped.lower())

    cleaned = re.sub(r"[^\w\s]", " ", without_apostrophes)
    words = [w for w in cleaned.split() if w not in _HONORIFICS]
    return " ".join(words)


_HONORIFICS = {"mr", "mrs", "ms", "miss", "shri", "smt", "dr", "kumari", "sri"}


def compare_names(form_name: str, ocr_name: str) -> FieldComparison:
    """Case-insensitive name comparison, per the specification.

    Also accepts a subset match in either direction: cards frequently carry a
    middle or father's name that the registration form omits, and treating
    "Rahul Sharma" versus "Rahul Kumar Sharma" as a conflict would flood the
    admin queue with false mismatches.
    """
    if not form_name or not ocr_name:
        return FieldComparison("name", form_name, ocr_name, MatchStatus.NOT_COMPARED,
                               "One side is missing.")

    left = normalise_name(form_name)
    right = normalise_name(ocr_name)

    if left == right:
        return FieldComparison("name", form_name, ocr_name, MatchStatus.MATCHED)

    left_words = set(left.split())
    right_words = set(right.split())
    if left_words and right_words and (left_words <= right_words or right_words <= left_words):
        return FieldComparison(
            "name", form_name, ocr_name, MatchStatus.MATCHED,
            "Partial match — one version carries an additional name.",
        )

    return FieldComparison("name", form_name, ocr_name, MatchStatus.MISMATCH,
                           "Names do not correspond.")


def compare_dob(form_dob: str, ocr_dob: str) -> FieldComparison:
    """Exact date-of-birth comparison, per the specification.

    Accepts either ``DD/MM/YYYY`` or ISO ``YYYY-MM-DD`` from the form, since the
    Flutter date picker submits ISO while the card prints the other way round.
    That is a formatting difference, not a data difference.
    """
    if not form_dob or not ocr_dob:
        return FieldComparison("dob", form_dob, ocr_dob, MatchStatus.NOT_COMPARED,
                               "One side is missing.")

    if _to_iso_date(form_dob) == _to_iso_date(ocr_dob):
        return FieldComparison("dob", form_dob, ocr_dob, MatchStatus.MATCHED)

    return FieldComparison("dob", form_dob, ocr_dob, MatchStatus.MISMATCH,
                           "Dates of birth differ.")


def compare_aadhaar(form_aadhaar: str, ocr_aadhaar: str) -> FieldComparison:
    """Compare Aadhaar numbers digit for digit, ignoring separators."""
    if not form_aadhaar or not ocr_aadhaar:
        return FieldComparison("aadhaar", "", "", MatchStatus.NOT_COMPARED,
                               "One side is missing.")

    left = normalise_aadhaar(form_aadhaar)
    right = normalise_aadhaar(ocr_aadhaar)

    # Never echo full numbers into a comparison record that gets stored and
    # displayed; the last four digits are enough to reconcile a mismatch.
    from .verhoeff import mask_aadhaar

    if left == right:
        return FieldComparison("aadhaar", mask_aadhaar(left), mask_aadhaar(right),
                               MatchStatus.MATCHED)

    return FieldComparison("aadhaar", mask_aadhaar(left), mask_aadhaar(right),
                           MatchStatus.MISMATCH, "Aadhaar numbers differ.")


def _to_iso_date(value: str) -> str:
    """Normalise a date string to ``YYYY-MM-DD`` for comparison."""
    value = value.strip()

    match = re.match(r"^(\d{2})[/\-.](\d{2})[/\-.](\d{4})$", value)
    if match:
        day, month, year = match.groups()
        return f"{year}-{month}-{day}"

    match = re.match(r"^(\d{4})[/\-.](\d{2})[/\-.](\d{2})$", value)
    if match:
        year, month, day = match.groups()
        return f"{year}-{month}-{day}"

    return value


def cross_check_with_form(
    extracted: ExtractedFields, form_data: dict[str, str]
) -> CrossCheckResult:
    """STAGE 8 — compare OCR output against the worker's registration form.

    ``form_data`` accepts the keys ``name``, ``dob`` and ``aadhaar``. Missing
    keys yield NOT_COMPARED rather than a mismatch.
    """
    result = CrossCheckResult()

    result.comparisons.append(
        compare_names(
            form_data.get("name", ""),
            extracted.name.value if extracted.name else "",
        )
    )
    result.comparisons.append(
        compare_dob(
            form_data.get("dob", ""),
            extracted.dob.value if extracted.dob else "",
        )
    )

    # Only compared when the worker actually typed a number; the form does not
    # require one, since OCR is meant to save them that keystroke.
    if form_data.get("aadhaar"):
        result.comparisons.append(
            compare_aadhaar(
                form_data["aadhaar"],
                extracted.aadhaar.value if extracted.aadhaar else "",
            )
        )

    logger.info("Stage 8 cross-check: %s", result.summary())
    return result
