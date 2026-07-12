"""Pre-processing chain for the OCR extractor (MM-2).

Per the KFEAT-012 Addendum (§ Scanned PDF Challenges & OCR Pipeline),
Tesseract's word-confidence drops sharply when given a skewed,
noisy, or off-orientation source. This module bundles the standard
pre-processing chain into small, individually unit-testable
functions:

1. :func:`to_greyscale` — drop colour channels
2. :func:`deskew` — Hough-lines + ``cv2.minAreaRect`` rotation
3. :func:`denoise` — median filter
4. :func:`binarise` — Otsu adaptive threshold
5. :func:`ensure_dpi` — bilinear upscale to a target DPI floor (default 300)

Higher-level orchestration (orientation detection via Tesseract
``--psm 0``) lives in :mod:`kairix.extractors.ocr.tesseract_runner`
because it depends on the upstream binding; the functions here are
pure ``numpy`` + ``opencv-python-headless`` so they unit-test against
small synthetic arrays without invoking Tesseract.

All functions are total on ``numpy.ndarray`` inputs (uint8). They
return new arrays; the caller may chain them functionally. The
default constants encode the KFEAT-012 Addendum targets:

* ``TARGET_DPI = 300`` — Tesseract's best-recall sweet spot.
* ``MEDIAN_KERNEL = 3`` — small kernel; aggressive filtering swallows
  thin glyph strokes.
* ``MIN_DESKEW_DEG = 0.5`` — below this we treat the image as already
  upright (rotation incurs a quality cost on near-zero angles).

See :func:`run_chain` for the canonical composition the
:class:`OcrExtractor` uses.
"""

from __future__ import annotations

from typing import Any

import numpy as np

#: Target DPI floor for OCR. Tesseract recall plateaus above ~300.
TARGET_DPI: int = 300

#: Median-filter kernel size. Must be odd; small kernel avoids
#: swallowing thin strokes in serif fonts.
MEDIAN_KERNEL: int = 3

#: Minimum absolute skew angle (degrees) at which deskew rotates the
#: image. Below this we accept the image as upright.
MIN_DESKEW_DEG: float = 0.5


# Re-binding ``cv2`` through this module-level helper keeps the lazy
# import contained and lets tests assert that the production import
# is what the extractor uses.


def _cv2() -> Any:
    """Lazy-import OpenCV.

    The ``ocr`` extra ships ``opencv-python-headless``; the plugin
    raises a typed ``RuntimeError`` if the package is missing, with
    a ``fix:`` action so the user knows the next step.
    """
    try:
        import cv2
    except ImportError as exc:  # pragma: no cover — import path validated by test
        raise RuntimeError(
            "ocr: the 'opencv-python-headless' package is not installed. "
            "fix: pip install 'Kairix-agentic-knowledge-mgt[ocr]' "
            "to opt into the OCR extractor. "
            "next: re-run the connector sync; the OCR plugin will then resolve."
        ) from exc
    return cv2


def to_greyscale(img: np.ndarray) -> np.ndarray:
    """Return a single-channel uint8 array.

    Accepts a 2-D (already greyscale) or 3-D RGB / BGR array. A 3-D
    array is averaged across channels — colour information is not
    useful for OCR and Tesseract handles greyscale natively.
    """
    if img.ndim == 2:
        return img.astype(np.uint8)
    if img.ndim == 3 and img.shape[2] in (3, 4):
        cv2 = _cv2()
        # Drop the alpha channel first if present, then convert.
        rgb = img[:, :, :3]
        grey = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
        return np.asarray(grey.astype(np.uint8))
    raise ValueError(f"to_greyscale: unsupported shape {img.shape}")


def denoise(img: np.ndarray, kernel: int = MEDIAN_KERNEL) -> np.ndarray:
    """Median filter for salt-and-pepper / scan-grain noise."""
    cv2 = _cv2()
    if kernel % 2 == 0:
        raise ValueError(f"denoise: kernel must be odd, got {kernel}")
    return np.asarray(cv2.medianBlur(img, kernel))


def binarise(img: np.ndarray) -> np.ndarray:
    """Otsu's adaptive threshold for a clean black-on-white image.

    Accepts a greyscale input. Returns a uint8 array with values in
    ``{0, 255}`` — black foreground on white background, matching
    Tesseract's preferred input.
    """
    cv2 = _cv2()
    grey = to_greyscale(img)
    _threshold, binary = cv2.threshold(grey, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    return np.asarray(binary)


def _detect_skew_angle(img: np.ndarray) -> float:
    """Estimate the dominant skew angle in degrees.

    Uses Canny edges + Hough probabilistic line transform and
    averages the angles of the longest lines, clipped to
    ``[-45, +45]``. Returns 0.0 if no lines are detected.
    """
    cv2 = _cv2()
    grey = to_greyscale(img)
    edges = cv2.Canny(grey, 50, 150, apertureSize=3)
    lines = cv2.HoughLinesP(
        edges,
        rho=1,
        theta=np.pi / 180,
        threshold=80,
        minLineLength=max(20, img.shape[1] // 4),
        maxLineGap=10,
    )
    if lines is None:
        return 0.0
    angles: list[float] = []
    for line in lines:
        # HoughLinesP returns (N, 1, 4) on opencv 4.x but (N, 4) on 5.x/6.x;
        # ravel flattens either shape to the four endpoint coordinates.
        x1, y1, x2, y2 = np.ravel(line)
        if x2 == x1:
            continue
        angle_deg = float(np.degrees(np.arctan2(y2 - y1, x2 - x1)))
        # Normalise to [-45, +45]: lines along the x-axis are 0deg;
        # lines along the y-axis read as ±90 and are page-edges, not
        # text baselines.
        if angle_deg > 45:
            continue
        if angle_deg < -45:
            continue
        angles.append(angle_deg)
    if not angles:
        return 0.0
    return float(np.median(angles))


def deskew(img: np.ndarray) -> np.ndarray:
    """Rotate ``img`` so detected text baselines run horizontally.

    A no-op when the detected angle's magnitude is below
    :data:`MIN_DESKEW_DEG` — rotating a near-upright page costs
    more in resampling artefacts than it gains in OCR recall.
    """
    cv2 = _cv2()
    angle = _detect_skew_angle(img)
    if abs(angle) < MIN_DESKEW_DEG:
        return img
    h, w = img.shape[:2]
    centre = (w / 2.0, h / 2.0)
    matrix = cv2.getRotationMatrix2D(centre, angle, 1.0)
    rotated = cv2.warpAffine(
        img,
        matrix,
        (w, h),
        flags=cv2.INTER_CUBIC,
        borderMode=cv2.BORDER_REPLICATE,
    )
    return np.asarray(rotated)


def ensure_dpi(img: np.ndarray, current_dpi: int, target_dpi: int = TARGET_DPI) -> np.ndarray:
    """Upscale ``img`` so its effective DPI is at least ``target_dpi``.

    Returns the input unchanged if ``current_dpi >= target_dpi``.
    Downscale is never performed — fewer pixels never helps Tesseract.
    """
    if current_dpi >= target_dpi:
        return img
    if current_dpi <= 0:
        raise ValueError(f"ensure_dpi: current_dpi must be > 0, got {current_dpi}")
    cv2 = _cv2()
    scale = target_dpi / current_dpi
    h, w = img.shape[:2]
    new_size = (round(w * scale), round(h * scale))
    return np.asarray(cv2.resize(img, new_size, interpolation=cv2.INTER_CUBIC))


def run_chain(img: np.ndarray, *, current_dpi: int = TARGET_DPI) -> np.ndarray:
    """Canonical pre-processing chain the :class:`OcrExtractor` uses.

    The composition order matches the KFEAT-012 Addendum:

      1. DPI normalisation (no-op when already at target).
      2. Greyscale conversion.
      3. Deskew.
      4. Noise removal (median filter).
      5. Binarisation (Otsu).

    Orientation detection is delegated to
    :mod:`kairix.extractors.ocr.tesseract_runner` because it needs
    Tesseract and lives outside this pure module.
    """
    img = ensure_dpi(img, current_dpi=current_dpi)
    img = to_greyscale(img)
    img = deskew(img)
    img = denoise(img)
    img = binarise(img)
    return img


__all__ = [
    "MEDIAN_KERNEL",
    "MIN_DESKEW_DEG",
    "TARGET_DPI",
    "binarise",
    "denoise",
    "deskew",
    "ensure_dpi",
    "run_chain",
    "to_greyscale",
]
