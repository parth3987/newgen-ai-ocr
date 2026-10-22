"""Apply a preprocessing profile to one page image and record every decision.

The source image is never modified. The pipeline returns a new greyscale image only when at
least one step changed the page; otherwise it returns ``None`` and OCR uses the source.
"""

from __future__ import annotations

import time
from collections.abc import Callable

import numpy as np
import numpy.typing as npt

from newgen_ocr.imaging import filters, geometry
from newgen_ocr.imaging.quality import (
    QualityFlag,
    QualityReport,
    analysis_image,
    assess,
    effective_dpi,
    to_gray,
)
from newgen_ocr.ingestion.models import PageImageInfo
from newgen_ocr.preprocessing.models import PagePreprocessing, PageStatus, StepParam, StepRecord
from newgen_ocr.preprocessing.profiles import ProfileConfig, StepMode

GrayImage = geometry.GrayImage


def _decide(mode: StepMode, flag: QualityFlag, flags: list[QualityFlag]) -> tuple[bool, str]:
    if mode is StepMode.NEVER:
        return False, "disabled"
    if mode is StepMode.ALWAYS:
        return True, "always"
    return (True, f"flag:{flag.value}") if flag in flags else (False, "not_flagged")


class _Recorder:
    def __init__(self) -> None:
        self.steps: list[StepRecord] = []

    def skip(self, name: str, reason: str, **params: StepParam) -> None:
        self.steps.append(StepRecord(name=name, applied=False, reason=reason, params=params))

    def apply(
        self,
        name: str,
        reason: str,
        image: GrayImage,
        transform: Callable[[GrayImage], GrayImage],
        **params: StepParam,
    ) -> GrayImage:
        start = time.perf_counter()
        result = transform(image)
        duration = round((time.perf_counter() - start) * 1000, 2)
        self.steps.append(
            StepRecord(name=name, applied=True, reason=reason, params=params, duration_ms=duration)
        )
        return result


def preprocess_page(
    page_number: int,
    image: npt.NDArray[np.uint8],
    dpi: int | None,
    profile: ProfileConfig,
    quality: QualityReport,
) -> tuple[GrayImage | None, PagePreprocessing]:
    start = time.perf_counter()
    flags = quality.flags
    record = PagePreprocessing(
        page_number=page_number,
        status=PageStatus.UNCHANGED,
        skew_detected_deg=quality.metrics.skew_deg,
        quality_flags_before=list(flags),
    )
    if QualityFlag.BLANK_PAGE in flags:
        record.status, record.skip_reason = PageStatus.SKIPPED, "blank_page"
        record.duration_ms = round((time.perf_counter() - start) * 1000, 2)
        return None, record

    steps = _Recorder()
    gray = to_gray(image)
    eff_dpi = effective_dpi(dpi, gray.shape[1], gray.shape[0])

    # 1. Coarse orientation: make text lines horizontal.
    if not profile.orientation:
        steps.skip("orientation", "disabled")
    elif QualityFlag.ROTATED in flags:
        gray = steps.apply("orientation", "flag:rotated", gray, geometry.rotate_quarter, degrees=90)
        record.rotation_applied_deg = 90
        # Skew measured before rotation was relative to vertical lines; re-measure.
        points = geometry.ink_points(analysis_image(gray, eff_dpi).ink)
        skew = geometry.estimate_skew(points)
        record.skew_detected_deg = skew.angle_deg if skew.reliable else None
    else:
        steps.skip("orientation", f"text_axis:{quality.metrics.text_axis.value}")

    # 2. Deskew.
    skew_deg = record.skew_detected_deg
    if not profile.deskew:
        steps.skip("deskew", "disabled")
    elif skew_deg is None:
        steps.skip("deskew", "skew_undetermined")
    elif abs(skew_deg) < profile.deskew_min_angle:
        steps.skip("deskew", "below_min_angle", angle_deg=skew_deg)
    else:
        angle = skew_deg
        gray = steps.apply(
            "deskew",
            "skew_detected",
            gray,
            lambda g: geometry.rotate_bound(g, -angle),
            angle_deg=angle,
        )
        record.skew_corrected_deg = angle

    # 3. Illumination flattening.
    apply, reason = _decide(profile.illumination, QualityFlag.UNEVEN_BACKGROUND, flags)
    if apply:
        gray = steps.apply(
            "illumination", reason, gray, lambda g: filters.normalize_illumination(g, eff_dpi)
        )
    else:
        steps.skip("illumination", reason)

    # 4. Denoise.
    apply, reason = _decide(profile.denoise, QualityFlag.NOISY, flags)
    if apply:
        gray = steps.apply(
            "denoise",
            reason,
            gray,
            lambda g: filters.denoise(g, profile.denoise_method, profile.denoise_strength),
            method=profile.denoise_method.value,
            strength=profile.denoise_strength,
        )
    else:
        steps.skip("denoise", reason)

    # 5. Contrast enhancement.
    apply, reason = _decide(profile.contrast, QualityFlag.POOR_CONTRAST, flags)
    if apply:
        gray = steps.apply(
            "contrast",
            reason,
            gray,
            lambda g: filters.enhance_contrast(
                g, profile.clahe_clip_limit, profile.clahe_tile_grid
            ),
            method="stretch+clahe",
            clip_limit=profile.clahe_clip_limit,
            tile_grid=profile.clahe_tile_grid,
        )
    else:
        steps.skip("contrast", reason)

    # 6. Adaptive thresholding (opt-in only).
    if profile.adaptive_threshold:
        gray = steps.apply(
            "threshold",
            "enabled",
            gray,
            lambda g: filters.adaptive_threshold(
                g, eff_dpi, profile.threshold_block_inches, profile.threshold_c
            ),
            method="adaptive_gaussian",
            block_inches=profile.threshold_block_inches,
            c=profile.threshold_c,
        )
    else:
        steps.skip("threshold", "disabled")

    record.steps = steps.steps
    output: GrayImage | None = None
    if any(step.applied for step in steps.steps):
        output = gray
        record.status = PageStatus.PROCESSED
        record.output = PageImageInfo(width_px=gray.shape[1], height_px=gray.shape[0], dpi=dpi)
        record.quality_after, _ = assess(gray, dpi)
    record.duration_ms = round((time.perf_counter() - start) * 1000, 2)
    return output, record
