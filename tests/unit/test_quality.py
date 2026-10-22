from __future__ import annotations

import numpy as np
import pytest

from newgen_ocr.imaging.quality import QualityFlag as F
from newgen_ocr.imaging.quality import assess, effective_dpi
from tests.fixtures.pages import (
    Gray,
    blank_page,
    blur,
    gaussian_noise,
    quarter_turn,
    rotate,
    salt_and_pepper,
    shadow,
    text_page,
)

CLEAN = text_page(dpi=200)


def _flags(page: Gray, dpi: int | None = 200) -> set[F]:
    report, _ = assess(page, dpi)
    return set(report.flags)


def test_clean_page_has_no_flags() -> None:
    report, _ = assess(CLEAN, 200)
    assert report.flags == []
    m = report.metrics
    assert m.text_contrast > 150  # anti-aliased glyphs at analysis scale: ~196
    assert m.sharpness is not None and m.sharpness > 0.9
    assert m.skew_deg == pytest.approx(0, abs=0.15)
    assert report.duration_ms > 0


@pytest.mark.parametrize(
    ("page", "expected"),
    [
        (rotate(CLEAN, 3.0), F.SKEWED),
        (quarter_turn(CLEAN, 1), F.ROTATED),
        (gaussian_noise(CLEAN, 20), F.NOISY),
        (salt_and_pepper(CLEAN, 0.02), F.NOISY),
        (text_page(dpi=200, ink=150, background=200), F.POOR_CONTRAST),
        (blur(CLEAN, 4), F.BLURRY),
        (shadow(CLEAN), F.UNEVEN_BACKGROUND),
        (blank_page(), F.BLANK_PAGE),
        (salt_and_pepper(blank_page(level=250), 0.0003), F.BLANK_PAGE),
        (gaussian_noise(blank_page(level=240), 4), F.BLANK_PAGE),
    ],
)
def test_degradations_are_flagged(page: Gray, expected: F) -> None:
    assert expected in _flags(page)


@pytest.mark.parametrize(
    ("page", "absent"),
    [
        (gaussian_noise(CLEAN, 3), F.NOISY),
        (text_page(dpi=200, ink=110), F.POOR_CONTRAST),  # grey but legible ink
        (blur(CLEAN, 0.6), F.BLURRY),
        (rotate(CLEAN, 0.2), F.SKEWED),
    ],
)
def test_mild_conditions_are_not_flagged(page: Gray, absent: F) -> None:
    assert absent not in _flags(page)


def test_blank_page_skips_other_metrics() -> None:
    report, _ = assess(blank_page(), 200)
    assert report.flags == [F.BLANK_PAGE]
    assert report.metrics.sharpness is None
    assert report.metrics.skew_deg is None


def test_low_resolution() -> None:
    assert F.LOW_RESOLUTION in _flags(text_page(dpi=150), dpi=150)


def test_unknown_resolution_with_small_image() -> None:
    small = text_page(dpi=80)
    flags = _flags(small, dpi=None)
    assert {F.RESOLUTION_UNKNOWN, F.LOW_RESOLUTION} <= flags


def test_implausible_dpi_metadata_is_ignored() -> None:
    assert effective_dpi(72, 2000, 3000) is None  # camera default metadata
    assert effective_dpi(72, 600, 800) == 72  # genuinely low-res
    assert effective_dpi(300, 2480, 3508) == 300
    assert effective_dpi(None, 2480, 3508) is None


def test_colour_input_is_accepted() -> None:
    rgb = np.stack([CLEAN] * 3, axis=-1)
    report, _ = assess(rgb, 200)
    assert report.flags == []


def test_assessment_does_not_modify_input() -> None:
    page = gaussian_noise(CLEAN, 10)
    before = page.copy()
    assess(page, 200)
    assert np.array_equal(page, before)
