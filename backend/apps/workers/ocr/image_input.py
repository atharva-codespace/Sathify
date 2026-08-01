"""
OCR pipeline — STAGE 1: Image input.

Accepts every format a worker can realistically supply from the Flutter app:
JPG, PNG, a scanned document, a PDF, or a photo taken on the spot with a phone
camera. Everything is normalised to a single BGR numpy array so that Stage 2
onwards never has to care where the image came from.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

logger = logging.getLogger(__name__)

#: Extensions handled directly by OpenCV.
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp"}
PDF_EXTENSIONS = {".pdf"}

SUPPORTED_EXTENSIONS = IMAGE_EXTENSIONS | PDF_EXTENSIONS

#: Below roughly this width, recognition accuracy falls off badly. A modern
#: phone camera clears it easily; the check exists to catch heavily
#: downscaled or cropped uploads early, with a clear message.
MIN_ACCEPTABLE_WIDTH = 600


class ImageInputError(Exception):
    """Raised when input cannot be decoded into an image."""


@dataclass
class LoadedImage:
    """A decoded image plus the provenance Stage 2 and the audit trail need."""

    image: np.ndarray
    source_format: str
    width: int
    height: int
    page_count: int = 1

    @property
    def is_low_resolution(self) -> bool:
        return self.width < MIN_ACCEPTABLE_WIDTH


def load_image(source: str | Path | bytes, *, filename: str | None = None) -> LoadedImage:
    """STAGE 1 — decode ``source`` into a BGR image array.

    ``source`` may be a filesystem path or raw bytes (as arrives from a Django
    ``UploadedFile``). ``filename`` supplies the extension when bytes are passed.

    Raises ``ImageInputError`` for unsupported or undecodable input, rather than
    returning None — a silent failure here would surface as a confusing OCR
    error several stages later.
    """
    if isinstance(source, bytes):
        return _load_from_bytes(source, filename)
    return _load_from_path(Path(source))


def _load_from_path(path: Path) -> LoadedImage:
    if not path.exists():
        raise ImageInputError(f"File not found: {path}")

    extension = path.suffix.lower()
    if extension not in SUPPORTED_EXTENSIONS:
        raise ImageInputError(
            f"Unsupported file type '{extension}'. "
            f"Accepted: {', '.join(sorted(SUPPORTED_EXTENSIONS))}."
        )

    if extension in PDF_EXTENSIONS:
        return _load_pdf(path.read_bytes())

    # np.fromfile rather than cv2.imread: imread cannot handle non-ASCII paths
    # on Windows, which a worker's uploaded filename may well contain.
    buffer = np.fromfile(str(path), dtype=np.uint8)
    image = cv2.imdecode(buffer, cv2.IMREAD_COLOR)
    if image is None:
        raise ImageInputError(f"Could not decode image: {path.name}")

    height, width = image.shape[:2]
    return LoadedImage(image=image, source_format=extension.lstrip("."),
                       width=width, height=height)


def _load_from_bytes(data: bytes, filename: str | None) -> LoadedImage:
    if not data:
        raise ImageInputError("Uploaded file is empty.")

    extension = Path(filename or "").suffix.lower()

    # Sniff the PDF magic number rather than trusting the extension: a
    # phone-captured file may arrive with a misleading or missing name.
    if data[:5] == b"%PDF-" or extension in PDF_EXTENSIONS:
        return _load_pdf(data)

    image = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise ImageInputError(
            "Could not decode the uploaded file as an image. "
            "Please upload a JPG, PNG or PDF."
        )

    height, width = image.shape[:2]
    return LoadedImage(
        image=image,
        source_format=extension.lstrip(".") or "unknown",
        width=width,
        height=height,
    )


def _load_pdf(data: bytes) -> LoadedImage:
    """Render the first page of a PDF.

    Only the first page is used: an Aadhaar e-copy carries the identity details
    on page one, and rendering further pages would cost time for no gain.

    ``pdf2image`` additionally needs the poppler binary on PATH. When either is
    missing we raise a message that says exactly what to install, since this is
    a deployment-environment problem rather than a bad upload.
    """
    try:
        from pdf2image import convert_from_bytes
    except ImportError as exc:
        raise ImageInputError(
            "PDF support requires pdf2image. Install it with "
            "`pip install -r requirements/ml.txt`, or upload a JPG/PNG instead."
        ) from exc

    try:
        # 300 DPI is the usual sweet spot for document OCR: enough detail for
        # small print, without producing an image so large it slows detection.
        pages = convert_from_bytes(data, dpi=300, first_page=1, last_page=1)
    except Exception as exc:  # pdf2image raises several unrelated types
        raise ImageInputError(
            f"Could not render the PDF ({exc}). Poppler may not be installed, "
            "or the file may be corrupt. Uploading a photo instead will work."
        ) from exc

    if not pages:
        raise ImageInputError("The PDF contains no pages.")

    # PIL gives RGB; OpenCV works in BGR throughout the pipeline.
    image = cv2.cvtColor(np.array(pages[0]), cv2.COLOR_RGB2BGR)
    height, width = image.shape[:2]
    return LoadedImage(image=image, source_format="pdf", width=width,
                       height=height, page_count=1)
