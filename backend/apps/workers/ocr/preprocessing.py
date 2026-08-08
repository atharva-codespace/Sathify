"""
OCR pipeline — STAGE 2: Preprocessing with OpenCV.

This stage is NOT cosmetic and is never skipped. Recognition accuracy on an
angled, shadowed phone photo of an Aadhaar card drops sharply without it —
which is the realistic input here, not a flatbed scan.

Six steps, applied in this order, each exposed as its own function so it can be
tested and tuned independently:

    1. to_grayscale     — colour carries no information for text
    2. denoise          — removes sensor noise from low-light phone captures
    3. increase_contrast— CLAHE, which handles uneven lighting far better than
                          a global stretch
    4. sharpen          — recovers edge definition softened by denoising
    5. deskew           — corrects rotation; detection degrades quickly past
                          a few degrees of tilt
    6. crop_background  — discards the desk/floor around the card

Order matters. Denoising before sharpening avoids amplifying noise into
false edges; deskewing after contrast work gives the angle estimator cleaner
input to measure.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import cv2
import numpy as np

logger = logging.getLogger(__name__)

#: Rotations beyond this are treated as a mis-detection rather than a tilt.
#: A genuinely sideways photo is a different problem, and forcing a 40-degree
#: "correction" on a correctly-oriented card destroys it.
MAX_DESKEW_DEGREES = 20.0

#: Below this the rotation is not worth the interpolation blur it costs.
MIN_DESKEW_DEGREES = 0.3


@dataclass
class PreprocessingResult:
    """The processed image plus what was done to it.

    The record of applied steps is surfaced in the admin review screen: when a
    field reads poorly, knowing the image was rotated 12 degrees and heavily
    cropped explains why.
    """

    image: np.ndarray
    steps_applied: list[str] = field(default_factory=list)
    deskew_angle: float = 0.0
    was_cropped: bool = False


def to_grayscale(image: np.ndarray) -> np.ndarray:
    """Step 1 — collapse to a single channel.

    Colour carries no signal for text recognition, and dropping it makes every
    later step cheaper.
    """
    if image.ndim == 2:
        return image
    return cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)


def denoise(image: np.ndarray, strength: int = 10) -> np.ndarray:
    """Step 2 — suppress sensor noise.

    Non-local means preserves character edges much better than a Gaussian blur,
    which matters because the next step sharpens whatever survives.

    ``strength`` is OpenCV's ``h``. Below about 10 the weighting is so peaked
    that each pixel is effectively averaged only with itself and the filter
    becomes a no-op; 10 measurably halves the error on a noisy document scan
    without visibly softening glyph edges.
    """
    return cv2.fastNlMeansDenoising(image, None, h=strength,
                                    templateWindowSize=7, searchWindowSize=21)


def increase_contrast(image: np.ndarray, clip_limit: float = 2.5) -> np.ndarray:
    """Step 3 — CLAHE (contrast-limited adaptive histogram equalisation).

    Adaptive rather than global on purpose: a phone photo is frequently bright
    on one side and shadowed on the other, and a global stretch would blow out
    the bright half while leaving the shadow unreadable.
    """
    clahe = cv2.createCLAHE(clipLimit=clip_limit, tileGridSize=(8, 8))
    return clahe.apply(image)


def sharpen(image: np.ndarray, amount: float = 1.5) -> np.ndarray:
    """Step 4 — unsharp mask, restoring edges softened by denoising."""
    blurred = cv2.GaussianBlur(image, (0, 0), sigmaX=3)
    return cv2.addWeighted(image, amount, blurred, -(amount - 1.0), 0)


def estimate_skew_angle(image: np.ndarray) -> float:
    """Measure the document's rotation in degrees.

    Works from the minimum-area rectangle enclosing the dark (text) pixels.
    Text lines dominate that shape, so its angle approximates the page angle.

    Returns 0.0 when no confident estimate is possible — doing nothing is
    always safer than applying a wrong rotation.
    """
    # Invert so text becomes the foreground OpenCV measures.
    threshold = cv2.threshold(image, 0, 255,
                              cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)[1]

    coordinates = np.column_stack(np.where(threshold > 0))
    if coordinates.shape[0] < 50:  # too little ink to judge
        return 0.0

    angle = cv2.minAreaRect(coordinates.astype(np.float32))[-1]

    # Normalise into (-45, 45], the only range a page tilt meaningfully occupies.
    #
    # Both tails have to be handled, and the missing one is why this silently
    # stopped working. ``minAreaRect``'s angle convention is not stable across
    # OpenCV versions — 3.x and early 4.x report [-90, 0), 4.5+ report (0, 90],
    # and which tail a given tilt lands in also depends on the point ordering.
    # This build (OpenCV 5.0) returns -82.9 for a 7-degree tilt: correct, but
    # expressed as "82.9 degrees the other way about the other axis".
    #
    # Handling only ``angle > 45`` left that untouched, then rejected it for
    # exceeding MAX_DESKEW_DEGREES — so every skewed document silently came back
    # as "no tilt" and went to OCR crooked. A quiet accuracy regression rather
    # than an error, which is the kind that survives a long time.
    if angle > 45:
        angle -= 90
    elif angle < -45:
        angle += 90

    if abs(angle) > MAX_DESKEW_DEGREES:
        logger.debug("Skew estimate %.1f deg exceeds limit; ignoring.", angle)
        return 0.0

    return float(angle)


def deskew(image: np.ndarray) -> tuple[np.ndarray, float]:
    """Step 5 — rotate the image upright.

    Returns ``(image, angle_applied)``. Rotations below ``MIN_DESKEW_DEGREES``
    are skipped: the interpolation blur would cost more accuracy than the tilt.
    """
    angle = estimate_skew_angle(image)
    if abs(angle) < MIN_DESKEW_DEGREES:
        return image, 0.0

    height, width = image.shape[:2]
    centre = (width // 2, height // 2)
    matrix = cv2.getRotationMatrix2D(centre, angle, 1.0)

    rotated = cv2.warpAffine(
        image, matrix, (width, height),
        flags=cv2.INTER_CUBIC,
        borderMode=cv2.BORDER_REPLICATE,  # avoids black wedges at the corners
    )
    return rotated, angle


#: Below this fraction of the original area, a crop is treated as a
#: mis-detection and skipped. Losing half the card is far worse than leaving
#: some desk in the frame.
MIN_CROP_AREA_RATIO = 0.4

#: How far a pixel must differ from the estimated background to count as
#: content. Loose enough to survive uneven lighting, tight enough to exclude
#: a plain surface.
_CONTENT_DELTA = 25


def estimate_background_intensity(image: np.ndarray, border: int = 8) -> float:
    """Estimate the surrounding surface's brightness from the image border.

    The outer frame of a phone photo is nearly always the desk, floor or hand
    the card is resting on, not the card itself.
    """
    top = image[:border, :]
    bottom = image[-border:, :]
    left = image[:, :border]
    right = image[:, -border:]
    return float(
        np.median(np.concatenate([top.ravel(), bottom.ravel(),
                                  left.ravel(), right.ravel()]))
    )


def crop_background(image: np.ndarray, margin: int = 10) -> tuple[np.ndarray, bool]:
    """Step 6 — trim the desk, floor or hand around the card.

    Works by locating pixels that differ from the background rather than by
    thresholding on absolute brightness. Absolute thresholding only works when
    the document is brighter than its surroundings; a card photographed on a
    pale desk (very common) inverts that assumption and would previously cause
    the whole frame to be selected, cropping nothing.

    Returns ``(image, was_cropped)``.
    """
    if image.ndim != 2 or image.size == 0:
        return image, False

    background = estimate_background_intensity(image)
    content_mask = np.abs(image.astype(np.int16) - background) > _CONTENT_DELTA

    if not content_mask.any():
        return image, False

    rows = np.where(content_mask.any(axis=1))[0]
    columns = np.where(content_mask.any(axis=0))[0]
    y, y_end = int(rows[0]), int(rows[-1])
    x, x_end = int(columns[0]), int(columns[-1])

    width_span = x_end - x + 1
    height_span = y_end - y + 1

    original_area = image.shape[0] * image.shape[1]
    if width_span * height_span < original_area * MIN_CROP_AREA_RATIO:
        logger.debug(
            "Crop candidate too small (%d%% of original); skipping.",
            int(100 * width_span * height_span / original_area),
        )
        return image, False

    height, width = image.shape[:2]
    x0 = max(x - margin, 0)
    y0 = max(y - margin, 0)
    x1 = min(x_end + 1 + margin, width)
    y1 = min(y_end + 1 + margin, height)

    # Nothing was actually trimmed — report honestly rather than claiming a crop.
    if (x0, y0, x1, y1) == (0, 0, width, height):
        return image, False

    cropped = image[y0:y1, x0:x1]
    if cropped.size == 0:
        return image, False

    return cropped, True


def preprocess_image(
    image: np.ndarray,
    *,
    apply_denoise: bool = True,
    apply_deskew: bool = True,
    apply_crop: bool = True,
) -> PreprocessingResult:
    """STAGE 2 — run the full preprocessing chain.

    The keyword flags exist for testing and for tuning against a specific
    document type. They are all on by default and should stay that way in
    production: accuracy on angled phone photos depends on this stage.
    """
    steps: list[str] = []

    processed = to_grayscale(image)
    steps.append("grayscale")

    if apply_denoise:
        processed = denoise(processed)
        steps.append("denoise")

    processed = increase_contrast(processed)
    steps.append("contrast")

    processed = sharpen(processed)
    steps.append("sharpen")

    angle = 0.0
    if apply_deskew:
        processed, angle = deskew(processed)
        if angle:
            steps.append(f"deskew({angle:.1f}deg)")

    cropped = False
    if apply_crop:
        processed, cropped = crop_background(processed)
        if cropped:
            steps.append("crop")

    logger.debug("Preprocessing applied: %s", ", ".join(steps))
    return PreprocessingResult(
        image=processed, steps_applied=steps, deskew_angle=angle, was_cropped=cropped
    )
