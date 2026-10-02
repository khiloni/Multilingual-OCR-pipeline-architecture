# Purpose: OpenCV image preprocessing (deskew, denoise) and PyMuPDF page rasterization.
# Provides rasterize_pdf_page() to extract a page image and preprocess_page() to clean it
# before handing off to the OCR engine. All functions operate on raw bytes in / np.ndarray out.

import logging
import math
from typing import Optional

import cv2
import fitz  # PyMuPDF
import numpy as np

logger = logging.getLogger(__name__)

# -------------------------------------------------------------------
# Constants
# -------------------------------------------------------------------
# Minimum skew angle (degrees) worth correcting. Below this threshold the
# warpAffine is skipped to avoid resampling artefacts on straight pages.
_MIN_SKEW_DEG: float = 0.5

# DPI used when rasterizing PDF pages to images. 200 Dpi gives ~1654×2339 px
# for an A4 page — high enough for PaddleOCR, low enough to stay memory-safe
# on CPU-only workers.
_RASTER_DPI: int = 200

# Hard cap on the longest rasterized edge, in pixels. Some scanned PDFs wrap a
# high-resolution source image directly as a full-page image with the PDF
# MediaBox sized to match the image's pixel dimensions (common output from
# scan-to-PDF tools) rather than a standard page size — applying the fixed
# _RASTER_DPI zoom on top of an already-oversized MediaBox multiplies the
# pixel count further and can exceed available worker memory (observed:
# SIGKILL / WorkerLostError on a ~9.4MP page with PaddleOCR + Surya + layout
# detection models all resident in the same process). Downscaling to this cap
# keeps every page within a predictable memory budget regardless of the
# source page's physical point-size.
_MAX_RASTER_EDGE_PX: int = 2200

# fastNlMeansDenoising tuning — keep conservative defaults so clean prints
# are not over-smoothed.
_DENOISE_H: int = 10          # luminance filter strength
_DENOISE_HCOLOR: int = 10     # color filter strength
_DENOISE_TEMPLATE_WIN: int = 7
_DENOISE_SEARCH_WIN: int = 21

# Laplacian variance below which we consider the image noisy enough to
# warrant denoising. Very high-variance images (clean sharp text) are left
# untouched.
_NOISE_VARIANCE_THRESHOLD: float = 500.0


# -------------------------------------------------------------------
# Public API
# -------------------------------------------------------------------

def rasterize_pdf_page(pdf_bytes: bytes, page_index: int, dpi: int = _RASTER_DPI) -> bytes:
    """
    Renders a single PDF page to a PNG image using PyMuPDF.

    Args:
        pdf_bytes:  Raw bytes of the PDF file.
        page_index: Zero-based page index.
        dpi:        Output resolution (default 200 DPI).

    Returns:
        PNG image data as raw bytes, ready for cv2.imdecode or direct storage.

    Raises:
        IndexError: If page_index is out of range for the document.
        ValueError: If the PDF bytes cannot be parsed.
    """
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception as exc:
        raise ValueError(f"Failed to open PDF stream: {exc}") from exc

    if page_index < 0 or page_index >= len(doc):
        raise IndexError(
            f"page_index {page_index} out of range for a {len(doc)}-page document"
        )

    page = doc[page_index]
    # fitz uses a zoom matrix — DPI / 72 converts to the desired resolution.
    zoom = dpi / 72.0

    # Clamp zoom so the longest output edge never exceeds _MAX_RASTER_EDGE_PX,
    # regardless of how large the page's own point-size already is.
    longest_edge_pt = max(page.rect.width, page.rect.height)
    projected_px = longest_edge_pt * zoom
    if projected_px > _MAX_RASTER_EDGE_PX:
        scale_factor = _MAX_RASTER_EDGE_PX / projected_px
        zoom *= scale_factor
        logger.info(
            "Rasterize: page %d point-size %.0fx%.0f would exceed %dpx at %d DPI — "
            "scaling zoom down to keep longest edge within the cap",
            page_index, page.rect.width, page.rect.height, _MAX_RASTER_EDGE_PX, dpi,
        )

    mat = fitz.Matrix(zoom, zoom)
    pix = page.get_pixmap(matrix=mat, alpha=False)
    png_bytes: bytes = pix.tobytes("png")
    doc.close()

    logger.debug(
        "Rasterized page %d at %d DPI → %dx%d px (%d bytes)",
        page_index,
        dpi,
        pix.width,
        pix.height,
        len(png_bytes),
    )
    return png_bytes


def preprocess_page(image_bytes: bytes) -> np.ndarray:
    """
    Decodes raw image bytes (PNG/JPEG), applies deskew then denoise,
    and returns a BGR numpy array ready for OCR.

    Args:
        image_bytes: Raw image bytes (output of rasterize_pdf_page or similar).

    Returns:
        Preprocessed BGR image as np.ndarray, shape (H, W, 3).

    Raises:
        ValueError: If the bytes cannot be decoded as an image.
    """
    logger.info("Decoding page image and running preprocessing pipeline")

    nparr = np.frombuffer(image_bytes, dtype=np.uint8)
    image = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError(
            "cv2.imdecode returned None — bytes are not a valid image format"
        )

    deskewed = deskew_image(image)
    denoised = denoise_image(deskewed)
    return denoised


# -------------------------------------------------------------------
# Internal helpers
# -------------------------------------------------------------------

def deskew_image(image: np.ndarray) -> np.ndarray:
    """
    Detects text skew angle via minAreaRect on the largest foreground contour
    and rotates the image to correct it.

    Strategy:
    1. Convert to grayscale and binarise with Otsu's threshold.
    2. Find all external contours; pick the largest by area.
    3. Fit a minAreaRect — its angle encodes the dominant text orientation.
    4. Rotate only when |angle| > _MIN_SKEW_DEG to avoid unnecessary resampling.

    Args:
        image: BGR image as np.ndarray.

    Returns:
        Deskewed BGR image (same dtype/shape).
    """
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)

    # Otsu binarisation — inverted so text pixels are white (foreground)
    _, thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)

    # Find contours on the binary mask
    contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    if not contours:
        logger.debug("deskew: no contours found, skipping rotation")
        return image

    # Use the largest contour (most likely the text body)
    largest = max(contours, key=cv2.contourArea)
    if cv2.contourArea(largest) < 100:
        logger.debug("deskew: largest contour too small, skipping rotation")
        return image

    rect = cv2.minAreaRect(largest)
    angle = rect[2]

    # Normalise the raw minAreaRect angle to the smallest-magnitude
    # correction. minAreaRect's angle convention for a near-axis-aligned
    # box is NOT consistent across OpenCV versions — confirmed empirically
    # that the same page image gives angle=-90.0 on OpenCV 5.0.0 but
    # angle=+90.0 on OpenCV 4.10.0 (the version actually pinned in
    # requirements-worker.txt / installed via opencv-python-headless), and
    # the original code here only normalised the negative side (angle <
    # -45), so a real axis-aligned page came back as a bogus 90-degree
    # rotation on 4.10.0 — silently corrupting every page's geometry
    # (bboxes, reading order, everything downstream) despite the page
    # never actually being skewed. Handling both ends symmetrically fixes
    # this regardless of which convention the installed OpenCV uses.
    if angle < -45:
        angle = 90 + angle   # e.g. -80° → +10°
    elif angle > 45:
        angle = angle - 90   # e.g. +90° → 0°, +80° → -10°

    if abs(angle) < _MIN_SKEW_DEG:
        logger.debug("deskew: angle %.2f° below threshold, skipping rotation", angle)
        return image

    logger.info("deskew: correcting skew of %.2f°", angle)
    h, w = image.shape[:2]
    center = (w / 2.0, h / 2.0)
    rotation_matrix = cv2.getRotationMatrix2D(center, angle, scale=1.0)
    rotated = cv2.warpAffine(
        image,
        rotation_matrix,
        (w, h),
        flags=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_REPLICATE,  # fill borders with edge pixels, not black
    )
    return rotated


def denoise_image(image: np.ndarray) -> np.ndarray:
    """
    Removes scan noise using fastNlMeansDenoisingColored, but only when
    the image is actually noisy (Laplacian variance below threshold).

    Clean, high-contrast prints are returned unchanged to avoid blurring
    fine strokes (important for CJK / Devanagari scripts).

    Args:
        image: BGR image as np.ndarray.

    Returns:
        Denoised BGR image (same dtype/shape).
    """
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    laplacian_var = float(cv2.Laplacian(gray, cv2.CV_64F).var())

    if laplacian_var >= _NOISE_VARIANCE_THRESHOLD:
        logger.debug(
            "denoise: Laplacian variance %.1f >= %.1f — image is clean, skipping denoising",
            laplacian_var,
            _NOISE_VARIANCE_THRESHOLD,
        )
        return image

    logger.info(
        "denoise: Laplacian variance %.1f — applying fastNlMeansDenoisingColored",
        laplacian_var,
    )
    denoised = cv2.fastNlMeansDenoisingColored(
        image,
        None,
        h=_DENOISE_H,
        hColor=_DENOISE_HCOLOR,
        templateWindowSize=_DENOISE_TEMPLATE_WIN,
        searchWindowSize=_DENOISE_SEARCH_WIN,
    )
    return denoised


# -------------------------------------------------------------------
# Image-variant retry (Phase 2 item 2) — router.py tries these on a
# low-confidence page, SAME engine, before falling back to Surya.
# -------------------------------------------------------------------

_CLAHE_CLIP_LIMIT: float = 3.0
_CLAHE_TILE_GRID: tuple = (8, 8)


def enhance_contrast_clahe(image: np.ndarray) -> np.ndarray:
    """
    Contrast-Limited Adaptive Histogram Equalization on the luminance
    channel only (converts to LAB, equalizes L, converts back) — fixes
    low-contrast/washed-out scans without blowing out color or introducing
    the global-histogram-equalization artifacts flat `cv2.equalizeHist`
    would on a BGR image.
    """
    lab = cv2.cvtColor(image, cv2.COLOR_BGR2LAB)
    l_channel, a_channel, b_channel = cv2.split(lab)
    clahe = cv2.createCLAHE(clipLimit=_CLAHE_CLIP_LIMIT, tileGridSize=_CLAHE_TILE_GRID)
    l_enhanced = clahe.apply(l_channel)
    enhanced = cv2.merge((l_enhanced, a_channel, b_channel))
    return cv2.cvtColor(enhanced, cv2.COLOR_LAB2BGR)


def adaptive_threshold_variant(image: np.ndarray) -> np.ndarray:
    """
    Adaptive (Gaussian) thresholding to a clean black-on-white binary
    image — helps on pages with uneven lighting/shadows where a single
    global threshold would lose text on the darker side of the page.
    Returned as a 3-channel BGR image so it's a drop-in replacement for
    the original (PaddleOCR expects BGR input throughout the pipeline).
    """
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    binary = cv2.adaptiveThreshold(
        gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY,
        blockSize=31, C=15,
    )
    return cv2.cvtColor(binary, cv2.COLOR_GRAY2BGR)
