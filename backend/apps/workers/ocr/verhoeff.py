"""
OCR pipeline — STAGE 7: Aadhaar checksum validation.

Every Aadhaar number's final digit is a Verhoeff check digit over the preceding
eleven. Verifying it catches the overwhelming majority of OCR misreads before a
wrong number is ever stored: single-digit substitutions (8 read as 3) and
adjacent transpositions (…4 7… read as …7 4…) are exactly the error classes the
Verhoeff algorithm is designed to detect, and they are exactly the errors an OCR
engine makes on a photographed card.

Cheap, offline, and deterministic — it needs no UIDAI API and no network.

Reference: J. Verhoeff, "Error Detecting Decimal Codes" (1969).
"""

# Multiplication table for the dihedral group D5.
_D5_MULTIPLICATION = (
    (0, 1, 2, 3, 4, 5, 6, 7, 8, 9),
    (1, 2, 3, 4, 0, 6, 7, 8, 9, 5),
    (2, 3, 4, 0, 1, 7, 8, 9, 5, 6),
    (3, 4, 0, 1, 2, 8, 9, 5, 6, 7),
    (4, 0, 1, 2, 3, 9, 5, 6, 7, 8),
    (5, 9, 8, 7, 6, 0, 4, 3, 2, 1),
    (6, 5, 9, 8, 7, 1, 0, 4, 3, 2),
    (7, 6, 5, 9, 8, 2, 1, 0, 4, 3),
    (8, 7, 6, 5, 9, 3, 2, 1, 0, 4),
    (9, 8, 7, 6, 5, 4, 3, 2, 1, 0),
)

# Permutation table, applied cyclically by digit position.
_PERMUTATION = (
    (0, 1, 2, 3, 4, 5, 6, 7, 8, 9),
    (1, 5, 7, 6, 2, 8, 3, 0, 9, 4),
    (5, 8, 0, 3, 7, 9, 6, 1, 4, 2),
    (8, 9, 1, 6, 0, 4, 3, 5, 2, 7),
    (9, 4, 5, 3, 1, 2, 6, 8, 7, 0),
    (4, 2, 8, 6, 5, 7, 3, 9, 0, 1),
    (2, 7, 9, 3, 8, 0, 6, 4, 1, 5),
    (7, 0, 4, 6, 9, 1, 3, 2, 5, 8),
)

# Multiplicative inverse within D5, used when generating a check digit.
_INVERSE = (0, 4, 3, 2, 1, 5, 6, 7, 8, 9)

AADHAAR_LENGTH = 12


def normalise_aadhaar(raw: str) -> str:
    """Strip spaces, hyphens and other separators from an Aadhaar number.

    Cards print the number as ``1234 5678 9012``, and OCR output may contain
    any mix of spaces or hyphens, so normalise before validating.
    """
    if not raw:
        return ""
    return "".join(character for character in str(raw) if character.isdigit())


def verhoeff_checksum(number: str) -> int:
    """Return the Verhoeff checksum of ``number``. Zero means valid."""
    checksum = 0
    # The algorithm consumes digits right-to-left, position 0 being the last.
    for position, digit in enumerate(reversed(number)):
        checksum = _D5_MULTIPLICATION[checksum][
            _PERMUTATION[position % 8][int(digit)]
        ]
    return checksum


def generate_check_digit(first_eleven: str) -> int:
    """Compute the check digit for an 11-digit Aadhaar body.

    Used by the test suite to build valid synthetic numbers, so that tests never
    need a real person's Aadhaar number.
    """
    digits = normalise_aadhaar(first_eleven)
    if len(digits) != AADHAAR_LENGTH - 1:
        raise ValueError(
            f"Expected {AADHAAR_LENGTH - 1} digits, got {len(digits)}."
        )

    checksum = 0
    # Shifted by one position: the check digit will occupy position 0.
    for position, digit in enumerate(reversed(digits)):
        checksum = _D5_MULTIPLICATION[checksum][
            _PERMUTATION[(position + 1) % 8][int(digit)]
        ]
    return _INVERSE[checksum]


def is_valid_aadhaar(raw: str) -> bool:
    """STAGE 7 — validate an Aadhaar number end to end.

    Applies three checks in order:

    1. Exactly 12 digits after normalisation.
    2. Does not begin with 0 or 1 — UIDAI never issues such numbers, so this
       catches a whole class of misreads the checksum alone would pass.
    3. Verhoeff checksum evaluates to zero.
    """
    digits = normalise_aadhaar(raw)

    if len(digits) != AADHAAR_LENGTH:
        return False
    if digits[0] in {"0", "1"}:
        return False

    return verhoeff_checksum(digits) == 0


def mask_aadhaar(raw: str) -> str:
    """Render an Aadhaar number for display: ``XXXX XXXX 9012``.

    The full number is never shown in the UI (Module 3.3). Anything that is not
    a well-formed 12-digit number renders as fully masked rather than leaking a
    partial value.
    """
    digits = normalise_aadhaar(raw)
    if len(digits) != AADHAAR_LENGTH:
        return "XXXX XXXX XXXX"
    return f"XXXX XXXX {digits[-4:]}"
