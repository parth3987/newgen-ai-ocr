from __future__ import annotations

import numpy as np
import pytest

from newgen_ocr.imaging import filters
from newgen_ocr.imaging.filters import DenoiseMethod
from newgen_ocr.imaging.quality import assess
from tests.fixtures.pages import gaussian_noise, salt_and_pepper, shadow, text_page

CLEAN = text_page(dpi=200)


@pytest.mark.parametrize("method", list(DenoiseMethod))
def test_denoise_reduces_gaussian_noise(method: DenoiseMethod) -> None:
    noisy = gaussian_noise(CLEAN, 20)
    before = assess(noisy, 200)[0].metrics.noise_sigma
    after = assess(filters.denoise(noisy, method, 5), 200)[0].metrics.noise_sigma
    assert before is not None and after is not None
    assert after < before * 0.6


def test_median_removes_salt_and_pepper() -> None:
    noisy = salt_and_pepper(CLEAN, 0.02)
    before = assess(noisy, 200)[0].metrics.impulse_noise_ratio
    after = assess(filters.denoise(noisy, DenoiseMethod.MEDIAN, 3), 200)[0].metrics
    assert after.impulse_noise_ratio < before / 10


def test_clahe_raises_contrast_of_faded_page() -> None:
    faded = text_page(dpi=200, ink=150, background=200)
    before = assess(faded, 200)[0].metrics.text_contrast
    after = assess(filters.enhance_contrast(faded, 3.0, 8), 200)[0].metrics.text_contrast
    assert after > before * 1.5


def test_illumination_normalisation_flattens_background() -> None:
    shaded = shadow(CLEAN)
    before = assess(shaded, 200)[0].metrics.background_variation
    after = assess(filters.normalize_illumination(shaded, 200), 200)[0].metrics
    assert after.background_variation < before / 4


def test_adaptive_threshold_is_binary() -> None:
    out = filters.adaptive_threshold(shadow(CLEAN), 200, 0.1, 10)
    assert set(np.unique(out)) <= {0, 255}
    assert out.shape == CLEAN.shape


def test_filters_do_not_modify_input() -> None:
    page = gaussian_noise(CLEAN, 10)
    before = page.copy()
    filters.denoise(page, DenoiseMethod.MEDIAN, 3)
    filters.enhance_contrast(page, 2.0, 8)
    filters.normalize_illumination(page, 200)
    filters.adaptive_threshold(page, 200, 0.1, 10)
    assert np.array_equal(page, before)


@pytest.mark.parametrize("pct", [0.5, 5, 50, 95, 99.5])
def test_histogram_percentile_matches_numpy(pct: float) -> None:
    page = gaussian_noise(CLEAN, 30)
    expected = np.percentile(page, pct, method="inverted_cdf")
    assert filters.u8_percentile(page, pct) == expected
