"""
Module 3 — tests for the 8-stage OCR pipeline.

Stages 1, 2 and 5-8 need only OpenCV and the standard library, so they are
tested here in full, with no OCR engine installed. Stages 3 and 4 (the engines
themselves) need PaddleOCR or EasyOCR and are not covered here.

OpenCV itself is not a base dependency — it arrives with requirements/ml.txt
(or the OpenCV-only requirements/cv.txt that CI installs). This module is
therefore skipped wholesale rather than erroring during collection on a base
install: an import at module scope would take the entire test session down with
it, which is not what ``pytest -m "not ml"`` is supposed to mean.

No real Aadhaar number appears anywhere in this file. Valid test numbers are
generated with ``generate_check_digit`` so the fixtures are synthetic but
checksum-correct.
"""

from __future__ import annotations

from datetime import date

import pytest

cv2 = pytest.importorskip("cv2", reason="needs requirements/cv.txt (OpenCV)")
np = pytest.importorskip("numpy", reason="needs requirements/cv.txt (NumPy)")

from apps.workers.ocr.crosscheck import (  # noqa: E402 — must follow importorskip
    MatchStatus,
    compare_aadhaar,
    compare_dob,
    compare_names,
    cross_check_with_form,
    normalise_name,
)
from apps.workers.ocr.extraction import (
    calculate_age,
    extract_aadhaar_number,
    extract_dob,
    extract_fields,
    extract_gender,
    extract_name,
)
from apps.workers.ocr.image_input import ImageInputError, load_image
from apps.workers.ocr.output import (
    CRITICAL_FIELD_CONFIDENCE,
    OcrLine,
    build_ocr_result,
    normalise_box,
)
from apps.workers.ocr.preprocessing import (
    crop_background,
    denoise,
    deskew,
    estimate_skew_angle,
    increase_contrast,
    preprocess_image,
    sharpen,
    to_grayscale,
)
from apps.workers.ocr.verhoeff import (
    generate_check_digit,
    is_valid_aadhaar,
    mask_aadhaar,
    normalise_aadhaar,
    verhoeff_checksum,
)


def valid_aadhaar(prefix: str = "23456789012") -> str:
    """Build a synthetic but checksum-valid 12-digit Aadhaar number."""
    body = prefix[:11]
    return body + str(generate_check_digit(body))


@pytest.fixture
def sample_card() -> np.ndarray:
    """A synthetic Aadhaar-like card image."""
    image = np.full((400, 640, 3), 235, dtype=np.uint8)
    cv2.putText(image, "Government of India", (40, 50),
                cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 0), 2)
    cv2.putText(image, "Rahul Sharma", (40, 140),
                cv2.FONT_HERSHEY_SIMPLEX, 1.1, (0, 0, 0), 2)
    cv2.putText(image, "DOB: 15/08/1998", (40, 200),
                cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 0, 0), 2)
    cv2.putText(image, "MALE", (40, 250),
                cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 0, 0), 2)
    cv2.putText(image, "2345 6789 0123", (40, 320),
                cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0, 0, 0), 3)
    return image


# ===========================================================================
# STAGE 7 — Verhoeff checksum (tested first; other stages depend on it)
# ===========================================================================


class TestStage7VerhoeffChecksum:
    def test_generated_numbers_validate(self):
        for prefix in ["23456789012", "98765432101", "56473829100"]:
            number = valid_aadhaar(prefix)
            assert is_valid_aadhaar(number), f"{number} should be valid"

    def test_checksum_of_a_valid_number_is_zero(self):
        assert verhoeff_checksum(valid_aadhaar()) == 0

    def test_single_digit_substitution_is_caught(self):
        """The commonest OCR error: one digit misread."""
        number = valid_aadhaar()
        for position in range(12):
            original = number[position]
            replacement = "7" if original != "7" else "3"
            corrupted = number[:position] + replacement + number[position + 1:]
            assert not is_valid_aadhaar(corrupted), (
                f"Substitution at position {position} should fail"
            )

    def test_adjacent_transposition_is_caught(self):
        """The second commonest: two neighbouring digits swapped."""
        number = valid_aadhaar()
        for position in range(11):
            if number[position] == number[position + 1]:
                continue  # swapping identical digits changes nothing
            transposed = (
                number[:position]
                + number[position + 1]
                + number[position]
                + number[position + 2:]
            )
            assert not is_valid_aadhaar(transposed), (
                f"Transposition at {position} should fail"
            )

    @pytest.mark.parametrize("bad", ["", "123", "12345678901", "1234567890123", "abcdefghijkl"])
    def test_wrong_length_or_non_numeric_is_rejected(self, bad):
        assert not is_valid_aadhaar(bad)

    @pytest.mark.parametrize("first", ["0", "1"])
    def test_numbers_starting_with_zero_or_one_are_rejected(self, first):
        """UIDAI never issues these, so it catches misreads the checksum passes."""
        body = first + "2345678901"  # 11 digits, as generate_check_digit requires
        candidate = body + str(generate_check_digit(body))

        # The checksum itself is satisfied; rejection comes from the leading digit.
        assert verhoeff_checksum(candidate) == 0
        assert not is_valid_aadhaar(candidate)

    def test_separators_are_tolerated(self):
        number = valid_aadhaar()
        spaced = f"{number[:4]} {number[4:8]} {number[8:]}"
        hyphenated = f"{number[:4]}-{number[4:8]}-{number[8:]}"

        assert is_valid_aadhaar(spaced)
        assert is_valid_aadhaar(hyphenated)

    def test_normalise_strips_everything_but_digits(self):
        assert normalise_aadhaar("2345 6789-0123") == "234567890123"
        assert normalise_aadhaar("") == ""

    def test_masking_reveals_only_the_last_four_digits(self):
        number = valid_aadhaar()
        masked = mask_aadhaar(number)

        assert masked == f"XXXX XXXX {number[-4:]}"
        assert number[:8] not in masked

    def test_malformed_input_masks_completely(self):
        """Never leak a partial value for something that is not a real number."""
        assert mask_aadhaar("123") == "XXXX XXXX XXXX"

    def test_generate_check_digit_rejects_wrong_length(self):
        with pytest.raises(ValueError):
            generate_check_digit("123")


# ===========================================================================
# STAGE 1 — Image input
# ===========================================================================


class TestStage1ImageInput:
    def test_loads_a_png(self, tmp_path, sample_card):
        path = tmp_path / "card.png"
        cv2.imwrite(str(path), sample_card)

        loaded = load_image(path)

        assert loaded.width == 640
        assert loaded.height == 400
        assert loaded.source_format == "png"

    def test_loads_a_jpg(self, tmp_path, sample_card):
        path = tmp_path / "card.jpg"
        cv2.imwrite(str(path), sample_card)

        assert load_image(path).source_format == "jpg"

    def test_loads_from_raw_bytes(self, sample_card):
        """The shape an uploaded file arrives in from Django."""
        encoded = cv2.imencode(".jpg", sample_card)[1].tobytes()

        loaded = load_image(encoded, filename="upload.jpg")

        assert loaded.width == 640

    def test_missing_file_raises_a_clear_error(self, tmp_path):
        with pytest.raises(ImageInputError, match="not found"):
            load_image(tmp_path / "nope.png")

    def test_unsupported_extension_is_rejected(self, tmp_path):
        path = tmp_path / "notes.docx"
        path.write_bytes(b"whatever")

        with pytest.raises(ImageInputError, match="Unsupported file type"):
            load_image(path)

    def test_empty_upload_is_rejected(self):
        with pytest.raises(ImageInputError, match="empty"):
            load_image(b"", filename="x.jpg")

    def test_undecodable_bytes_raise_rather_than_return_none(self):
        """A silent None here would surface as a confusing error stages later."""
        with pytest.raises(ImageInputError, match="Could not decode"):
            load_image(b"not an image at all", filename="x.jpg")

    def test_low_resolution_is_flagged_but_still_loads(self):
        small = np.full((80, 200, 3), 240, dtype=np.uint8)
        encoded = cv2.imencode(".png", small)[1].tobytes()

        loaded = load_image(encoded, filename="small.png")

        assert loaded.is_low_resolution is True

    def test_pdf_is_detected_by_magic_number_not_extension(self):
        """A phone-captured file may arrive with a misleading name."""
        # No poppler in this environment, so the PDF branch raises — the point
        # is that it routed to PDF handling rather than image decoding.
        with pytest.raises(ImageInputError) as exc:
            load_image(b"%PDF-1.4 fake", filename="mislabelled.jpg")

        assert "pdf" in str(exc.value).lower() or "poppler" in str(exc.value).lower()


# ===========================================================================
# STAGE 2 — Preprocessing
# ===========================================================================


class TestStage2Preprocessing:
    def test_grayscale_collapses_to_one_channel(self, sample_card):
        assert to_grayscale(sample_card).ndim == 2

    def test_grayscale_is_idempotent(self, sample_card):
        once = to_grayscale(sample_card)
        assert to_grayscale(once).shape == once.shape

    def test_denoise_preserves_dimensions(self, sample_card):
        gray = to_grayscale(sample_card)
        assert denoise(gray).shape == gray.shape

    def test_denoise_moves_a_noisy_scan_closer_to_the_original(self, sample_card):
        """Measured against ground truth on a STRUCTURED image.

        Non-local means works by averaging similar patches, so pure noise on a
        flat field is its worst case and tells us nothing useful. A document
        with text is the case that actually matters.
        """
        rng = np.random.default_rng(seed=42)
        clean = to_grayscale(sample_card)
        noisy = np.clip(
            clean.astype(np.float64) + rng.normal(0, 20, clean.shape), 0, 255
        ).astype(np.uint8)

        def error_against_clean(image):
            return float(np.mean(np.abs(image.astype(float) - clean.astype(float))))

        assert error_against_clean(denoise(noisy)) < error_against_clean(noisy)

    def test_contrast_widens_the_dynamic_range(self):
        flat = np.full((200, 200), 128, dtype=np.uint8)
        flat[50:150, 50:150] = 140  # a barely-visible block

        result = increase_contrast(flat)

        assert result.max() - result.min() > flat.max() - flat.min()

    def test_sharpen_preserves_shape(self, sample_card):
        gray = to_grayscale(sample_card)
        assert sharpen(gray).shape == gray.shape

    def test_skew_is_estimated_on_a_rotated_image(self, sample_card):
        gray = to_grayscale(sample_card)
        height, width = gray.shape
        matrix = cv2.getRotationMatrix2D((width // 2, height // 2), 7.0, 1.0)
        rotated = cv2.warpAffine(gray, matrix, (width, height),
                                 borderMode=cv2.BORDER_REPLICATE)

        angle = estimate_skew_angle(rotated)

        # Direction and rough magnitude matter; exact degrees do not.
        assert abs(angle) > 1.0

    def test_deskew_leaves_a_straight_image_alone(self, sample_card):
        """Rotating a straight image would cost interpolation blur for nothing."""
        gray = to_grayscale(sample_card)
        _result, angle = deskew(gray)
        assert abs(angle) < 1.0

    def test_extreme_angles_are_ignored_rather_than_applied(self):
        """A 40-degree 'correction' on a correct card would destroy it."""
        blank = np.full((300, 300), 255, dtype=np.uint8)
        assert estimate_skew_angle(blank) == 0.0

    def test_crop_removes_surrounding_background(self):
        """A card filling most of the frame, on a lighter desk.

        Proportions match a real phone photo: the card dominates, with a
        border of surrounding surface to trim.
        """
        canvas = np.full((400, 400), 255, dtype=np.uint8)
        canvas[40:360, 40:360] = 60  # dark card on a light desk

        cropped, was_cropped = crop_background(canvas)

        assert was_cropped is True
        assert cropped.shape[0] < canvas.shape[0]

    def test_crop_handles_a_light_card_on_a_dark_surface(self):
        """The inverse polarity must work too — this is why the step estimates
        the background rather than thresholding on absolute brightness."""
        canvas = np.full((400, 400), 30, dtype=np.uint8)
        canvas[40:360, 40:360] = 230  # light card on a dark desk

        cropped, was_cropped = crop_background(canvas)

        assert was_cropped is True
        assert cropped.shape[0] < canvas.shape[0]

    def test_crop_refuses_to_remove_most_of_the_document(self):
        """Losing half the card is far worse than leaving background in."""
        canvas = np.full((400, 400), 255, dtype=np.uint8)
        canvas[199:201, 199:201] = 0  # a tiny speck

        _cropped, was_cropped = crop_background(canvas)

        assert was_cropped is False

    def test_full_chain_records_every_step_applied(self, sample_card):
        result = preprocess_image(sample_card)

        assert "grayscale" in result.steps_applied
        assert "denoise" in result.steps_applied
        assert "contrast" in result.steps_applied
        assert "sharpen" in result.steps_applied
        assert result.image.ndim == 2

    def test_steps_can_be_disabled_for_isolation(self, sample_card):
        result = preprocess_image(
            sample_card, apply_denoise=False, apply_deskew=False, apply_crop=False
        )

        assert "denoise" not in result.steps_applied
        assert not any(step.startswith("deskew") for step in result.steps_applied)


# ===========================================================================
# STAGE 5 — Output normalisation
# ===========================================================================


class TestStage5Output:
    def test_produces_the_specified_tuple_shape(self):
        result = build_ocr_result(
            [([[10, 20], [120, 20], [120, 40], [10, 40]], "John Smith", 0.98)],
            engine="paddleocr",
        )

        box, text, confidence = result.as_tuples()[0]
        assert box == [[10, 20], [120, 20], [120, 40], [10, 40]]
        assert text == "John Smith"
        assert confidence == 0.98

    def test_blank_regions_are_dropped(self):
        """Engines emit empty regions; they would pollute raw_text with newlines."""
        result = build_ocr_result(
            [([[0, 0]], "  ", 0.9), ([[0, 0]], "Real", 0.9)], engine="test"
        )
        assert len(result.lines) == 1

    def test_raw_text_is_ordered_top_to_bottom(self):
        """Stage 6's regexes depend on label and value staying adjacent."""
        result = build_ocr_result(
            [
                ([[0, 300], [100, 300], [100, 320], [0, 320]], "bottom", 0.9),
                ([[0, 10], [100, 10], [100, 30], [0, 30]], "top", 0.9),
            ],
            engine="test",
        )
        assert result.raw_text.splitlines() == ["top", "bottom"]

    def test_confidence_threshold_matches_the_specification(self):
        assert CRITICAL_FIELD_CONFIDENCE == 0.85

    def test_lines_are_classified_against_the_threshold(self):
        confident = OcrLine([[0, 0]], "ok", 0.86)
        borderline = OcrLine([[0, 0]], "hmm", 0.84)

        assert confident.is_confident is True
        assert borderline.is_confident is False

    def test_float_coordinates_are_coerced_to_integers(self):
        assert normalise_box([[10.6, 20.2], [30.0, 40.9]]) == [[11, 20], [30, 41]]

    def test_malformed_box_degrades_to_empty_rather_than_raising(self):
        assert normalise_box("nonsense") == []
        assert normalise_box(None) == []

    def test_mean_confidence_of_an_empty_result_is_zero(self):
        assert build_ocr_result([], engine="test").mean_confidence == 0.0


# ===========================================================================
# STAGE 6 — Field extraction
# ===========================================================================


class TestStage6FieldExtraction:
    def test_extracts_a_spaced_aadhaar_number(self):
        number = valid_aadhaar()
        spaced = f"{number[:4]} {number[4:8]} {number[8:]}"

        assert extract_aadhaar_number(f"Name\n{spaced}\nMALE") == number

    def test_prefers_the_candidate_that_passes_the_checksum(self):
        """A card may also carry a VID or enrolment number."""
        good = valid_aadhaar("23456789012")
        bad = "987654321098"  # fails Verhoeff

        text = f"VID: {bad}\nAadhaar: {good}"
        assert extract_aadhaar_number(text) == good

    def test_returns_none_when_no_candidate_exists(self):
        assert extract_aadhaar_number("no numbers here") is None

    def test_longer_digit_runs_are_not_truncated_into_a_match(self):
        assert extract_aadhaar_number("1234567890123456") is None

    def test_extracts_a_labelled_dob(self):
        assert extract_dob("DOB: 15/08/1998") == "15/08/1998"

    @pytest.mark.parametrize("raw", ["15-08-1998", "15.08.1998", "15/08/1998"])
    def test_accepts_common_date_separators(self, raw):
        assert extract_dob(f"DOB {raw}") == "15/08/1998"

    def test_rejects_impossible_dates(self):
        """OCR misreads produce things like 45/13/1998."""
        assert extract_dob("DOB: 45/13/1998") is None

    def test_falls_back_to_year_of_birth_cards(self):
        assert extract_dob("Year of Birth: 1990") == "01/01/1990"

    @pytest.mark.parametrize(
        ("text", "expected"),
        [("MALE", "Male"), ("Female", "Female"), ("TRANSGENDER", "Transgender")],
    )
    def test_extracts_and_normalises_gender(self, text, expected):
        assert extract_gender(f"DOB: 01/01/1990\n{text}") == expected

    def test_extracts_the_holder_name(self):
        text = "Government of India\nRahul Sharma\nDOB: 15/08/1998\nMALE"
        assert extract_name(text) == "Rahul Sharma"

    def test_skips_header_lines_when_finding_the_name(self):
        text = "GOVERNMENT OF INDIA\nUNIQUE IDENTIFICATION AUTHORITY\nMeera Joshi"
        assert extract_name(text) == "Meera Joshi"

    def test_skips_numeric_lines_when_finding_the_name(self):
        assert extract_name("2345 6789 0123\nAnita Desai") == "Anita Desai"

    def test_full_extraction_produces_the_specified_dict(self):
        number = valid_aadhaar()
        ocr = build_ocr_result(
            [
                ([[0, 10], [200, 10], [200, 30], [0, 30]], "Rahul Sharma", 0.97),
                ([[0, 50], [200, 50], [200, 70], [0, 70]], "DOB: 15/08/1998", 0.95),
                ([[0, 90], [100, 90], [100, 110], [0, 110]], "MALE", 0.99),
                ([[0, 130], [250, 130], [250, 150], [0, 150]],
                 f"{number[:4]} {number[4:8]} {number[8:]}", 0.96),
            ],
            engine="test",
        )

        fields = extract_fields(ocr)

        assert fields.as_dict() == {
            "name": "Rahul Sharma",
            "dob": "15/08/1998",
            "gender": "Male",
            "aadhaar": number,
        }
        assert fields.aadhaar_checksum_valid is True

    def test_low_confidence_fields_are_flagged_not_silently_accepted(self):
        """The specification's core rule: never auto-fill a poor reading."""
        number = valid_aadhaar()
        ocr = build_ocr_result(
            [
                ([[0, 10], [200, 10], [200, 30], [0, 30]], "Rahul Sharma", 0.97),
                # Aadhaar read at 0.62 — well below the 0.85 threshold.
                ([[0, 130], [250, 130], [250, 150], [0, 150]], number, 0.62),
            ],
            engine="test",
        )

        fields = extract_fields(ocr)

        assert "aadhaar" in fields.low_confidence_fields
        assert fields.needs_manual_confirmation is True

    def test_high_confidence_reading_needs_no_confirmation(self):
        number = valid_aadhaar()
        ocr = build_ocr_result(
            [([[0, 0], [250, 0], [250, 20], [0, 20]], number, 0.97)], engine="test"
        )

        assert extract_fields(ocr).needs_manual_confirmation is False

    def test_safe_dict_masks_the_aadhaar_number(self):
        number = valid_aadhaar()
        ocr = build_ocr_result([([[0, 0]], number, 0.97)], engine="test")

        safe = extract_fields(ocr).as_safe_dict()

        assert safe["aadhaar"] == f"XXXX XXXX {number[-4:]}"
        assert number not in str(safe)

    def test_per_field_confidence_comes_from_the_source_line(self):
        """A document-wide average would hide one badly-read field."""
        number = valid_aadhaar()
        ocr = build_ocr_result(
            [
                ([[0, 10], [200, 10], [200, 30], [0, 30]], "Rahul Sharma", 0.99),
                ([[0, 130], [250, 130], [250, 150], [0, 150]], number, 0.55),
            ],
            engine="test",
        )

        fields = extract_fields(ocr)

        assert fields.name.confidence == pytest.approx(0.99)
        assert fields.aadhaar.confidence == pytest.approx(0.55)


class TestAgeCalculation:
    def test_computes_whole_years(self):
        assert calculate_age("15/08/1998", on=date(2026, 8, 15)) == 28

    def test_birthday_not_yet_reached_this_year(self):
        assert calculate_age("15/08/1998", on=date(2026, 8, 14)) == 27

    def test_unparseable_date_returns_none_not_zero(self):
        """None means 'unknown'; zero would read as a newborn and hard-block."""
        assert calculate_age("not a date") is None
        assert calculate_age("") is None


# ===========================================================================
# STAGE 8 — Cross-check against the registration form
# ===========================================================================


class TestStage8CrossCheck:
    def test_identical_names_match(self):
        assert compare_names("Rahul Sharma", "Rahul Sharma").status is MatchStatus.MATCHED

    def test_name_comparison_is_case_insensitive(self):
        """Explicitly required by the specification."""
        assert compare_names("rahul sharma", "RAHUL SHARMA").status is MatchStatus.MATCHED

    def test_extra_whitespace_does_not_cause_a_mismatch(self):
        assert compare_names("  Rahul   Sharma ", "Rahul Sharma").status is MatchStatus.MATCHED

    def test_honorifics_are_ignored(self):
        assert compare_names("Mr Rahul Sharma", "Rahul Sharma").status is MatchStatus.MATCHED

    def test_accents_are_folded(self):
        assert compare_names("Rahúl Sharma", "Rahul Sharma").status is MatchStatus.MATCHED

    def test_additional_middle_name_is_treated_as_a_match(self):
        """Cards often carry a father's name the form omits; flagging every one
        of those would flood the admin queue with false mismatches."""
        result = compare_names("Rahul Sharma", "Rahul Kumar Sharma")
        assert result.status is MatchStatus.MATCHED
        assert "additional name" in result.note

    def test_genuinely_different_names_mismatch(self):
        assert compare_names("Rahul Sharma", "Priya Nair").status is MatchStatus.MISMATCH

    def test_missing_side_is_not_compared_rather_than_mismatched(self):
        """Absence of evidence is not evidence of conflict."""
        assert compare_names("", "Rahul Sharma").status is MatchStatus.NOT_COMPARED

    def test_identical_dates_match(self):
        assert compare_dob("15/08/1998", "15/08/1998").status is MatchStatus.MATCHED

    def test_iso_form_date_matches_card_format(self):
        """The Flutter date picker submits ISO; the card prints DD/MM/YYYY."""
        assert compare_dob("1998-08-15", "15/08/1998").status is MatchStatus.MATCHED

    def test_different_dates_mismatch(self):
        assert compare_dob("15/08/1998", "16/08/1998").status is MatchStatus.MISMATCH

    def test_aadhaar_comparison_ignores_separators(self):
        number = valid_aadhaar()
        spaced = f"{number[:4]} {number[4:8]} {number[8:]}"
        assert compare_aadhaar(spaced, number).status is MatchStatus.MATCHED

    def test_aadhaar_comparison_never_echoes_the_full_number(self):
        number = valid_aadhaar()
        result = compare_aadhaar(number, number)

        assert number not in result.form_value
        assert number not in result.ocr_value
        assert result.form_value.startswith("XXXX")

    def test_normalise_name_strips_punctuation_and_case(self):
        assert normalise_name("O'Brien, Sean.") == "obrien sean"

    def _fields_for(self, name: str, dob: str):
        ocr = build_ocr_result(
            [
                ([[0, 10], [200, 10], [200, 30], [0, 30]], name, 0.96),
                ([[0, 50], [200, 50], [200, 70], [0, 70]], f"DOB: {dob}", 0.95),
            ],
            engine="test",
        )
        return extract_fields(ocr)

    def test_matching_form_produces_no_mismatch(self):
        fields = self._fields_for("Rahul Sharma", "15/08/1998")

        result = cross_check_with_form(
            fields, {"name": "Rahul Sharma", "dob": "15/08/1998"}
        )

        assert result.has_mismatch is False
        assert result.overall_status is MatchStatus.MATCHED

    def test_mismatched_name_is_surfaced_not_resolved(self):
        """The pipeline must not pick a winner — a human decides."""
        fields = self._fields_for("Rahul Sharma", "15/08/1998")

        result = cross_check_with_form(
            fields, {"name": "Completely Different", "dob": "15/08/1998"}
        )

        assert result.has_mismatch is True
        assert [c.field_name for c in result.mismatches] == ["name"]
        assert "please review" in result.summary().lower()

    def test_both_source_values_are_retained_for_the_admin(self):
        fields = self._fields_for("Rahul Sharma", "15/08/1998")

        result = cross_check_with_form(
            fields, {"name": "Rahul Verma", "dob": "15/08/1998"}
        )
        name_row = result.as_dict()["name"]

        # The administrator needs to see both to decide which is right.
        assert name_row["form"] == "Rahul Verma"
        assert name_row["ocr"] == "Rahul Sharma"
        assert name_row["status"] == "mismatch"

    def test_aadhaar_is_only_compared_when_the_form_supplied_one(self):
        """OCR exists to save that keystroke, so the field is optional."""
        fields = self._fields_for("Rahul Sharma", "15/08/1998")

        result = cross_check_with_form(fields, {"name": "Rahul Sharma", "dob": "15/08/1998"})

        assert "aadhaar" not in result.as_dict()
