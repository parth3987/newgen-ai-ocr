from __future__ import annotations

import numpy as np
import pytest

from newgen_ocr.imaging.geometry import TextAxis
from newgen_ocr.imaging.quality import QualityFlag, assess
from newgen_ocr.preprocessing.models import PagePreprocessing, PageStatus
from newgen_ocr.preprocessing.pipeline import preprocess_page
from newgen_ocr.preprocessing.profiles import BUILTIN_PROFILES
from tests.fixtures.pages import (
    Gray,
    blank_page,
    gaussian_noise,
    quarter_turn,
    rotate,
    shadow,
    text_page,
)

CLEAN = text_page(dpi=200)


def _run(page: Gray, profile: str = "standard") -> tuple[Gray | None, PagePreprocessing]:
    quality, _ = assess(page, 200)
    return preprocess_page(1, page, 200, BUILTIN_PROFILES[profile], quality)


def _step(record: PagePreprocessing, name: str) -> tuple[bool, str]:
    [step] = [s for s in record.steps if s.name == name]
    return step.applied, step.reason


def test_clean_page_is_unchanged_by_standard_profile() -> None:
    output, record = _run(CLEAN)
    assert output is None
    assert record.status is PageStatus.UNCHANGED
    assert record.output is None
    assert [s.applied for s in record.steps] == [False] * 6
    assert _step(record, "denoise") == (False, "not_flagged")


def test_skewed_page_is_deskewed() -> None:
    output, record = _run(rotate(CLEAN, 4.0))
    assert output is not None
    assert _step(record, "deskew") == (True, "skew_detected")
    assert record.skew_corrected_deg == pytest.approx(4.0, abs=0.15)
    assert record.quality_after is not None
    assert QualityFlag.SKEWED in record.quality_flags_before
    assert QualityFlag.SKEWED not in record.quality_after.flags
    assert record.quality_after.metrics.skew_deg == pytest.approx(0, abs=0.2)


def test_small_skew_below_threshold_is_left_alone() -> None:
    _, record = _run(rotate(CLEAN, 0.2))
    assert _step(record, "deskew")[1] in {"below_min_angle", "skew_undetermined"}


@pytest.mark.parametrize("turns", [1, 3])
def test_rotated_page_is_turned_to_horizontal_text(turns: int) -> None:
    rotated = rotate(quarter_turn(CLEAN, turns), 2.0)
    output, record = _run(rotated)
    assert output is not None
    assert record.rotation_applied_deg == 90
    assert _step(record, "orientation") == (True, "flag:rotated")
    assert record.quality_after is not None
    assert record.quality_after.metrics.text_axis is TextAxis.HORIZONTAL
    # Skew is re-measured after the quarter turn and corrected too.
    assert _step(record, "deskew")[0] is True
    assert abs(record.quality_after.metrics.skew_deg or 0) < 0.3


def test_noisy_page_is_denoised_only_when_flagged() -> None:
    output, record = _run(gaussian_noise(CLEAN, 20))
    assert output is not None
    assert _step(record, "denoise") == (True, "flag:noisy")
    assert record.quality_after is not None
    assert QualityFlag.NOISY not in record.quality_after.flags


def test_faded_page_gets_contrast_enhancement() -> None:
    output, record = _run(text_page(dpi=200, ink=150, background=200))
    assert output is not None
    assert _step(record, "contrast") == (True, "flag:poor_contrast")
    assert record.quality_after is not None
    assert QualityFlag.POOR_CONTRAST not in record.quality_after.flags


def test_none_profile_never_changes_pages() -> None:
    output, record = _run(rotate(gaussian_noise(CLEAN, 20), 5.0), "none")
    assert output is None
    assert record.status is PageStatus.UNCHANGED
    assert {s.reason for s in record.steps} == {"disabled"}


def test_degraded_scan_profile_always_filters() -> None:
    output, record = _run(CLEAN, "degraded_scan")
    assert output is not None
    assert _step(record, "denoise") == (True, "always")
    assert _step(record, "contrast") == (True, "always")


def test_photo_profile_flattens_illumination() -> None:
    output, record = _run(shadow(CLEAN), "photo")
    assert output is not None
    assert _step(record, "illumination") == (True, "always")
    assert record.quality_after is not None
    assert QualityFlag.UNEVEN_BACKGROUND not in record.quality_after.flags


def test_binarize_profile_outputs_black_and_white() -> None:
    output, record = _run(CLEAN, "binarize")
    assert output is not None
    assert set(np.unique(output)) <= {0, 255}
    assert _step(record, "threshold") == (True, "enabled")


def test_blank_page_is_skipped() -> None:
    output, record = _run(blank_page(), "degraded_scan")
    assert output is None
    assert record.status is PageStatus.SKIPPED
    assert record.skip_reason == "blank_page"


def test_source_image_is_never_modified() -> None:
    page = rotate(gaussian_noise(CLEAN, 20), 3.0)
    before = page.copy()
    output, _ = _run(page, "degraded_scan")
    assert output is not None
    assert np.array_equal(page, before)
    assert output is not page


def test_decisions_and_timings_are_recorded() -> None:
    # Noise is added after rotating: interpolation during rotation would smooth it away.
    _, record = _run(gaussian_noise(rotate(CLEAN, 3.0), 20))
    assert [s.name for s in record.steps] == [
        "orientation",
        "deskew",
        "illumination",
        "denoise",
        "contrast",
        "threshold",
    ]
    applied = [s for s in record.steps if s.applied]
    assert {s.name for s in applied} == {"deskew", "denoise"}
    assert all(s.duration_ms > 0 for s in applied)
    assert record.duration_ms >= sum(s.duration_ms for s in applied)
    denoise = next(s for s in applied if s.name == "denoise")
    assert denoise.params == {"method": "median", "strength": 3}
