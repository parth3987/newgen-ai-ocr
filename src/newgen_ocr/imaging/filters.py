"""Greyscale enhancement filters used by preprocessing profiles. Each returns a new image."""

from __future__ import annotations

from enum import StrEnum

import cv2
import numpy as np

from newgen_ocr.imaging.geometry import GrayImage


class DenoiseMethod(StrEnum):
    MEDIAN = "median"
    BILATERAL = "bilateral"
    NLMEANS = "nlmeans"  # best quality, slowest; see docs/PERFORMANCE.md


def _odd(value: float, minimum: int = 3) -> int:
    n = max(round(value), minimum)
    return n if n % 2 else n + 1


def _u8(image: object) -> GrayImage:
    return np.asarray(image, dtype=np.uint8)


def denoise(image: GrayImage, method: DenoiseMethod, strength: int) -> GrayImage:
    if method is DenoiseMethod.MEDIAN:
        return _u8(cv2.medianBlur(image, _odd(strength)))
    if method is DenoiseMethod.BILATERAL:
        return _u8(cv2.bilateralFilter(image, _odd(strength, 5), 50, 50))
    return _u8(cv2.fastNlMeansDenoising(image, None, h=float(strength * 3), templateWindowSize=7))


def u8_percentile(image: GrayImage, pct: float) -> int:
    """Percentile of an 8-bit image via its histogram (no sort; exact for uint8 data)."""
    cumulative = np.cumsum(np.bincount(image.ravel(), minlength=256))
    return int(np.searchsorted(cumulative, cumulative[-1] * pct / 100))


def stretch_contrast(image: GrayImage, low_pct: float = 0.5, high_pct: float = 99.5) -> GrayImage:
    """Linear stretch of the robust intensity range to 0..255 (outliers clipped)."""
    low, high = u8_percentile(image, low_pct), u8_percentile(image, high_pct)
    if high - low < 1:
        return image.copy()
    scaled = (image.astype(np.float32) - low) * (255.0 / (high - low))
    return _u8(np.clip(scaled, 0, 255))


def enhance_contrast(image: GrayImage, clip_limit: float, tile_grid: int) -> GrayImage:
    """Global robust stretch, then CLAHE for local contrast.

    CLAHE alone caps amplification by design, so a uniformly faded page (e.g. grey ink on
    a grey background) stays faded; the stretch restores the full range first.
    """
    clahe = cv2.createCLAHE(clipLimit=clip_limit, tileGridSize=(tile_grid, tile_grid))
    return _u8(clahe.apply(stretch_contrast(image)))


def normalize_illumination(image: GrayImage, dpi: int | None) -> GrayImage:
    """Divide by an estimated background so shadows and gradients become uniform white.

    The background is estimated at ~50 DPI (closing removes text strokes, which are at
    most a pixel or two wide there), then upscaled smoothly; the division is at full size.
    """
    scale = min(1.0, 50 / (dpi or 150))
    small = cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    kernel_size = _odd(50 * 0.2, 5)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (kernel_size, kernel_size))
    background_small = cv2.morphologyEx(small, cv2.MORPH_CLOSE, kernel)
    height, width = image.shape[:2]
    background = cv2.resize(background_small, (width, height), interpolation=cv2.INTER_LINEAR)
    return _u8(cv2.divide(image, background, scale=255))


def adaptive_threshold(image: GrayImage, dpi: int | None, block_inches: float, c: int) -> GrayImage:
    block = _odd((dpi or 150) * block_inches)
    return _u8(
        cv2.adaptiveThreshold(
            image, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, block, c
        )
    )
