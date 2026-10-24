"""Page image quality assessment.

All metrics are computed on an *analysis image*: greyscale, downscaled to ``ANALYSIS_DPI``
when the resolution is known, so thresholds refer to physical scale rather than pixel
counts. Thresholds were calibrated on synthetic pages (see docs/DECISIONS.md ADR-0017) and
are heuristics to be re-tuned against the evaluation set. They are not accuracy claims.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from enum import StrEnum

import cv2
import numpy as np
import numpy.typing as npt
from pydantic import BaseModel

from newgen_ocr.imaging.geometry import (
    GrayImage,
    TextAxis,
    detect_axis,
    ink_points,
)

ANALYSIS_DPI = 150
MIN_DPI = 200
# Without DPI metadata, the shorter side in pixels below which a page is treated as low-res.
MIN_SIDE_PX_UNKNOWN_DPI = 1000
# DPI values below this on large images are assumed to be default metadata, not real.
IMPLAUSIBLE_DPI = 100

BLANK_MAX_SPREAD = 20
BLANK_MAX_INK_FRACTION = 0.001
BLANK_MAX_CONTRAST = 15
POOR_CONTRAST = 60
BLURRY_SHARPNESS = 0.6
NOISY_SIGMA = 6.0
NOISY_IMPULSE_RATIO = 0.002
UNEVEN_BACKGROUND = 40
SKEW_FLAG_DEG = 0.5

_NOISE_KERNEL = np.array([[1, -2, 1], [-2, 4, -2], [1, -2, 1]], dtype=np.float32)


class QualityFlag(StrEnum):
    BLANK_PAGE = "blank_page"
    LOW_RESOLUTION = "low_resolution"
    RESOLUTION_UNKNOWN = "resolution_unknown"
    BLURRY = "blurry"
    POOR_CONTRAST = "poor_contrast"
    NOISY = "noisy"
    UNEVEN_BACKGROUND = "uneven_background"
    SKEWED = "skewed"
    ROTATED = "rotated"  # text lines run vertically


class QualityMetrics(BaseModel):
    width_px: int
    height_px: int
    dpi: int | None
    effective_dpi: int | None  # None when unknown or implausible
    ink_fraction: float
    intensity_spread: float  # p99.5 - p0.5
    text_contrast: float  # background level minus mean ink level
    sharpness: float | None  # edge steepness relative to contrast; ~1.1 sharp, <0.6 blurry
    noise_sigma: float | None
    impulse_noise_ratio: float
    background_variation: float  # p95 - p5 of estimated background illumination
    text_axis: TextAxis
    axis_ratio: float
    skew_deg: float | None  # None when unreliable
    skew_confidence: float


class QualityReport(BaseModel):
    flags: list[QualityFlag]
    metrics: QualityMetrics
    duration_ms: float


@dataclass(frozen=True)
class AnalysisImage:
    gray: GrayImage
    ink: npt.NDArray[np.bool_]
    otsu_threshold: float


def to_gray(image: npt.NDArray[np.uint8]) -> GrayImage:
    if image.ndim == 2:
        return image
    return np.asarray(cv2.cvtColor(image, cv2.COLOR_RGB2GRAY), dtype=np.uint8)


def effective_dpi(dpi: int | None, width: int, height: int) -> int | None:
    if dpi is None:
        return None
    if dpi < IMPLAUSIBLE_DPI and min(width, height) >= MIN_SIDE_PX_UNKNOWN_DPI:
        return None
    return dpi


def analysis_image(gray: GrayImage, dpi: int | None) -> AnalysisImage:
    if dpi is not None and dpi > ANALYSIS_DPI:
        scale = ANALYSIS_DPI / dpi
        gray = np.asarray(
            cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA),
            dtype=np.uint8,
        )
    threshold, binary_inv = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)
    return AnalysisImage(gray, binary_inv > 0, float(threshold))


def _contrast(a: AnalysisImage) -> float:
    background = a.gray[~a.ink]
    ink = a.gray[a.ink]
    if background.size == 0 or ink.size == 0:
        return 0.0
    return float(np.median(background)) - float(ink.mean())


def _sharpness(gray: GrayImage, contrast: float) -> float | None:
    if contrast < BLANK_MAX_CONTRAST:
        return None
    gx = cv2.Sobel(gray, cv2.CV_64F, 1, 0, ksize=3)
    gy = cv2.Sobel(gray, cv2.CV_64F, 0, 1, ksize=3)
    magnitude = np.hypot(gx, gy)
    # A Sobel response to an ideal step edge of height C peaks at 4C.
    edges = magnitude[magnitude > 0.2 * 4 * contrast]
    if edges.size < 100:
        return None
    return float(np.percentile(edges, 90) / (4 * contrast))


def _noise(gray: GrayImage, ink_threshold: float) -> tuple[float | None, float]:
    """Pixel-level noise, measured at native resolution (downscaling averages it away).

    Returns the Immerkaer noise sigma over background pixels away from ink, and the
    fraction of pixels that are isolated dark specks (salt-and-pepper / dust).
    """
    ink = (gray <= ink_threshold).astype(np.uint8)
    background = cv2.erode(1 - ink, np.ones((5, 5), np.uint8)) > 0
    sigma = None
    if background.any():
        residual = np.abs(cv2.filter2D(gray.astype(np.float32), -1, _NOISE_KERNEL))
        sigma = float(np.sqrt(np.pi / 2) / 6 * residual[background].mean())
    # Ink pixels with no ink neighbour in their 3x3 window. Text strokes are never a single
    # isolated pixel at document resolutions.
    neighbours = cv2.filter2D(ink, -1, np.ones((3, 3), np.float32), borderType=cv2.BORDER_CONSTANT)
    isolated = (ink == 1) & (neighbours == 1)
    return sigma, float(isolated.mean())


def _background_variation(gray: GrayImage) -> float:
    """Spread of the illumination field. It is low-frequency, so it is estimated on a small
    copy (longest side ~400 px), which is ~50x faster than at analysis scale."""
    scale = min(1.0, 400 / max(gray.shape))
    small = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    kernel_size = max(small.shape) // 40 * 2 + 1
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (kernel_size, kernel_size))
    background = cv2.morphologyEx(small, cv2.MORPH_CLOSE, kernel)
    low, high = np.percentile(background, [5, 95])
    return float(high - low)


def assess(image: npt.NDArray[np.uint8], dpi: int | None) -> tuple[QualityReport, AnalysisImage]:
    """Assess a page image. Returns the report and the analysis image for reuse."""
    start = time.perf_counter()
    gray = to_gray(image)
    height, width = gray.shape
    eff_dpi = effective_dpi(dpi, width, height)
    a = analysis_image(gray, eff_dpi)

    p_low, p_high = np.percentile(a.gray, [0.5, 99.5])
    spread = float(p_high - p_low)
    ink_fraction = float(a.ink.mean())
    contrast = _contrast(a)
    # The spread (0.5th to 99.5th percentile) is 0 on any page with under 0.5% ink, so on
    # its own it would call a sparse page (a short note, one line of handwriting) blank and
    # skip its OCR. It only confirms blankness where the ink also has no contrast.
    blank = (
        ink_fraction < BLANK_MAX_INK_FRACTION
        or contrast < BLANK_MAX_CONTRAST
        or (spread < BLANK_MAX_SPREAD and contrast < POOR_CONTRAST)
    )

    flags: list[QualityFlag] = []
    sharpness = noise_sigma = skew = None
    impulse_ratio = background_variation = axis_ratio = skew_confidence = 0.0
    axis = TextAxis.UNDETERMINED

    if eff_dpi is None:
        flags.append(QualityFlag.RESOLUTION_UNKNOWN)
        if min(width, height) < MIN_SIDE_PX_UNKNOWN_DPI:
            flags.append(QualityFlag.LOW_RESOLUTION)
    elif eff_dpi < MIN_DPI:
        flags.append(QualityFlag.LOW_RESOLUTION)

    if blank:
        flags.append(QualityFlag.BLANK_PAGE)
    else:
        sharpness = _sharpness(a.gray, contrast)
        noise_sigma, impulse_ratio = _noise(gray, a.otsu_threshold)
        background_variation = _background_variation(a.gray)
        axis_estimate, skew_estimate = detect_axis(ink_points(a.ink))
        axis, axis_ratio = axis_estimate.axis, axis_estimate.ratio
        skew_confidence = skew_estimate.confidence
        skew = skew_estimate.angle_deg if skew_estimate.reliable else None

        if contrast < POOR_CONTRAST:
            flags.append(QualityFlag.POOR_CONTRAST)
        if sharpness is not None and sharpness < BLURRY_SHARPNESS:
            flags.append(QualityFlag.BLURRY)
        if (noise_sigma is not None and noise_sigma > NOISY_SIGMA) or (
            impulse_ratio > NOISY_IMPULSE_RATIO
        ):
            flags.append(QualityFlag.NOISY)
        if background_variation > UNEVEN_BACKGROUND:
            flags.append(QualityFlag.UNEVEN_BACKGROUND)
        if axis is TextAxis.VERTICAL:
            flags.append(QualityFlag.ROTATED)
        if skew is not None and abs(skew) >= SKEW_FLAG_DEG:
            flags.append(QualityFlag.SKEWED)

    metrics = QualityMetrics(
        width_px=width,
        height_px=height,
        dpi=dpi,
        effective_dpi=eff_dpi,
        ink_fraction=round(ink_fraction, 5),
        intensity_spread=round(spread, 1),
        text_contrast=round(contrast, 1),
        sharpness=None if sharpness is None else round(sharpness, 3),
        noise_sigma=None if noise_sigma is None else round(noise_sigma, 2),
        impulse_noise_ratio=round(impulse_ratio, 5),
        background_variation=round(background_variation, 1),
        text_axis=axis,
        axis_ratio=axis_ratio,
        skew_deg=skew,
        skew_confidence=skew_confidence,
    )
    duration = round((time.perf_counter() - start) * 1000, 2)
    return QualityReport(flags=flags, metrics=metrics, duration_ms=duration), a
