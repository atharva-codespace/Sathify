"""
OCR pipeline — STAGE 6: Field extraction.

Parses name, date of birth, gender and Aadhaar number out of raw OCR text using
regular expressions, producing structured fields:

    {"name": "Rahul Sharma", "dob": "15/08/1998", "aadhaar": "123456789012"}

Each extracted field carries its own confidence and bounding box, taken from the
line it was read from. A single document-wide average would hide the case that
matters most: eleven fields read perfectly and the Aadhaar number read badly.

Real Aadhaar cards vary in layout, print quality and language, so every pattern
here is written to tolerate the common OCR mangling — 'O' for '0', missing
colons after labels, and inconsistent spacing.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime

from .output import CRITICAL_FIELD_CONFIDENCE, OcrResult
from .verhoeff import is_valid_aadhaar, mask_aadhaar, normalise_aadhaar

logger = logging.getLogger(__name__)

#: Fields where a low-confidence reading must never be silently auto-filled.
CRITICAL_FIELDS = {"aadhaar", "dob"}

# --- Patterns ---------------------------------------------------------------

# 12 digits, usually printed in groups of four. Requires a non-digit boundary so
# a longer numeric run is not silently truncated into a false positive.
AADHAAR_PATTERN = re.compile(r"(?<!\d)([2-9]\d{3})\s?(\d{4})\s?(\d{4})(?!\d)")

# Aadhaar prints DOB as DD/MM/YYYY. Some cards carry only a birth year.
DOB_PATTERN = re.compile(
    r"(?:DOB|D\.O\.B|Date\s*of\s*Birth|जन्म\s*तिथि)?\s*[:\-]?\s*"
    r"(\d{2})[/\-.](\d{2})[/\-.](\d{4})",
    re.IGNORECASE,
)
YEAR_OF_BIRTH_PATTERN = re.compile(
    r"(?:Year\s*of\s*Birth|YOB)\s*[:\-]?\s*(\d{4})", re.IGNORECASE
)

GENDER_PATTERN = re.compile(
    r"\b(MALE|FEMALE|TRANSGENDER|पुरुष|महिला)\b", re.IGNORECASE
)

# Lines that are never the holder's name.
_NAME_NOISE = re.compile(
    r"government|india|unique|identification|authority|आधार|भारत|सरकार|"
    r"male|female|transgender|dob|year of birth|address|father|husband|"
    r"^\d+$|^[^A-Za-z]*$",
    re.IGNORECASE,
)


@dataclass
class ExtractedField:
    """One parsed field, with the evidence behind it."""

    value: str
    confidence: float = 0.0
    bounding_box: list[list[int]] = field(default_factory=list)
    source_line: str = ""

    @property
    def is_confident(self) -> bool:
        return self.confidence >= CRITICAL_FIELD_CONFIDENCE


@dataclass
class ExtractedFields:
    """Stage 6 output for one document."""

    name: ExtractedField | None = None
    dob: ExtractedField | None = None
    gender: ExtractedField | None = None
    aadhaar: ExtractedField | None = None

    #: Fields present but read below the confidence threshold. The Flutter form
    #: must ask the worker to confirm these rather than auto-filling them.
    low_confidence_fields: list[str] = field(default_factory=list)

    aadhaar_checksum_valid: bool = False

    def as_dict(self) -> dict[str, str]:
        """The structured shape from the specification.

        NOTE: this returns the FULL Aadhaar number and is for in-memory use
        only — validation and cross-checking. What gets persisted is the masked
        form plus a hash; see ``apps.workers.models.KycDocument``.
        """
        return {
            "name": self.name.value if self.name else "",
            "dob": self.dob.value if self.dob else "",
            "gender": self.gender.value if self.gender else "",
            "aadhaar": self.aadhaar.value if self.aadhaar else "",
        }

    def as_safe_dict(self) -> dict[str, str]:
        """Same, but with the Aadhaar number masked. Safe to log or return."""
        data = self.as_dict()
        data["aadhaar"] = mask_aadhaar(data["aadhaar"])
        return data

    @property
    def needs_manual_confirmation(self) -> bool:
        """True when any critical field was read too poorly to trust."""
        return bool(set(self.low_confidence_fields) & CRITICAL_FIELDS)


def _attach_evidence(value: str, ocr: OcrResult, needle: str) -> ExtractedField:
    """Build a field, attaching the confidence and box of its source line."""
    line = ocr.line_for(needle)
    if line is None:
        return ExtractedField(value=value, confidence=ocr.mean_confidence)
    return ExtractedField(
        value=value,
        confidence=line.confidence,
        bounding_box=line.bounding_box,
        source_line=line.text,
    )


def extract_aadhaar_number(text: str) -> str | None:
    """Find the 12-digit Aadhaar number.

    When several candidates appear — a card sometimes shows a VID or an
    enrolment number too — the one with a valid Verhoeff checksum wins. That is
    a far stronger signal than position on the card.
    """
    candidates = ["".join(match.groups()) for match in AADHAAR_PATTERN.finditer(text)]
    if not candidates:
        return None

    for candidate in candidates:
        if is_valid_aadhaar(candidate):
            return candidate

    # Nothing passed the checksum; return the first so Stage 7 can report the
    # failure against a concrete value rather than silently finding nothing.
    logger.debug("No Aadhaar candidate passed the checksum (%d found).", len(candidates))
    return candidates[0]


def extract_dob(text: str) -> str | None:
    """Find the date of birth, as ``DD/MM/YYYY``.

    Falls back to a year-only card, normalising it to ``01/01/YYYY`` so
    downstream date handling has one format to deal with. The age gate treats
    that conservatively — see ``calculate_age``.
    """
    match = DOB_PATTERN.search(text)
    if match:
        day, month, year = match.groups()
        # Reject impossible dates: OCR misreads produce things like 45/13/1998.
        try:
            datetime(int(year), int(month), int(day))
        except ValueError:
            logger.debug("Discarding implausible DOB %s/%s/%s", day, month, year)
        else:
            return f"{day}/{month}/{year}"

    year_match = YEAR_OF_BIRTH_PATTERN.search(text)
    if year_match:
        return f"01/01/{year_match.group(1)}"

    return None


def extract_gender(text: str) -> str | None:
    """Find the gender, normalised to Male / Female / Transgender."""
    match = GENDER_PATTERN.search(text)
    if not match:
        return None

    value = match.group(1).lower()
    if value in {"male", "पुरुष"}:
        return "Male"
    if value in {"female", "महिला"}:
        return "Female"
    return "Transgender"


def extract_name(text: str) -> str | None:
    """Find the holder's name.

    Aadhaar cards do not label the name, so it is identified by elimination:
    the first line that looks like a person's name and is not a header, a
    field label, or a number. Imperfect by nature, which is exactly why Stage 8
    cross-checks it against what the worker typed.
    """
    for raw_line in text.splitlines():
        line = raw_line.strip()

        if len(line) < 3 or len(line) > 50:
            continue
        if _NAME_NOISE.search(line):
            continue
        if sum(character.isdigit() for character in line) > 2:
            continue

        # Expect mostly letters, allowing spaces, apostrophes and full stops.
        letters = sum(character.isalpha() or character.isspace() for character in line)
        if letters / len(line) < 0.85:
            continue

        words = line.split()
        if not 1 <= len(words) <= 5:
            continue

        return " ".join(word.capitalize() for word in words)

    return None


def calculate_age(dob: str, *, on: date | None = None) -> int | None:
    """Age in whole years from a ``DD/MM/YYYY`` string.

    Returns None when the date cannot be parsed, so the caller can distinguish
    "too young" from "unknown" — those must not be treated the same by the age
    gate.
    """
    try:
        day, month, year = (int(part) for part in dob.split("/"))
        birth_date = date(year, month, day)
    except (ValueError, AttributeError):
        return None

    reference = on or date.today()
    age = reference.year - birth_date.year
    if (reference.month, reference.day) < (birth_date.month, birth_date.day):
        age -= 1
    return age


def extract_fields(ocr: OcrResult) -> ExtractedFields:
    """STAGE 6 — parse structured fields out of an ``OcrResult``.

    Also runs the Stage 7 checksum on whatever Aadhaar number was found, since
    the checksum result determines whether the number is trustworthy enough to
    offer back to the worker at all.
    """
    text = ocr.raw_text
    result = ExtractedFields()

    aadhaar = extract_aadhaar_number(text)
    if aadhaar:
        result.aadhaar = _attach_evidence(aadhaar, ocr, aadhaar[:4])
        # STAGE 7, applied to the extracted value.
        result.aadhaar_checksum_valid = is_valid_aadhaar(aadhaar)

    dob = extract_dob(text)
    if dob:
        result.dob = _attach_evidence(dob, ocr, dob)

    gender = extract_gender(text)
    if gender:
        result.gender = _attach_evidence(gender, ocr, gender)

    name = extract_name(text)
    if name:
        result.name = _attach_evidence(name, ocr, name)

    # Flag anything read too poorly to auto-fill.
    for field_name in ("name", "dob", "gender", "aadhaar"):
        extracted = getattr(result, field_name)
        if extracted is not None and not extracted.is_confident:
            result.low_confidence_fields.append(field_name)

    logger.info(
        "Stage 6 extracted %s (checksum_valid=%s, low_confidence=%s)",
        result.as_safe_dict(),
        result.aadhaar_checksum_valid,
        result.low_confidence_fields,
    )
    return result


def normalise_extracted_aadhaar(value: str) -> str:
    """Convenience re-export so callers need not import from two modules."""
    return normalise_aadhaar(value)
