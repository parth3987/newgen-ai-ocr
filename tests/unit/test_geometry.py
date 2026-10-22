from __future__ import annotations

import cv2
import numpy as np
import pytest

from newgen_ocr.imaging.geometry import (
    TextAxis,
    detect_axis,
    estimate_skew,
    ink_points,
    rotate_bound,
    rotate_quarter,
)
from newgen_ocr.imaging.quality import analysis_image
from tests.fixtures.pages import Gray, blank_page, quarter_turn, rotate, text_page

PAGE = text_page(dpi=150)


def _points(page: Gray) -> np.ndarray:
    return ink_points(analysis_image(page, 150).ink)


@pytest.mark.parametrize("angle", [-12.0, -5.0, -1.0, -0.4, 0.0, 0.7, 2.5, 9.0])
def test_skew_estimate_matches_applied_rotation(angle: float) -> None:
    estimate = estimate_skew(_points(rotate(PAGE, angle)))
    assert estimate.reliable
    assert estimate.angle_deg == pytest.approx(angle, abs=0.15)


def test_deskew_round_trip() -> None:
    skewed = rotate(PAGE, 4.0)
    corrected = rotate_bound(skewed, -estimate_skew(_points(skewed)).angle_deg)
    assert abs(estimate_skew(_points(corrected)).angle_deg) <= 0.15


def test_rotate_bound_does_not_crop() -> None:
    page = np.zeros((100, 300), dtype=np.uint8)
    rotated = rotate_bound(page, 30)
    # A 300x100 box rotated 30 degrees needs about 310x237.
    assert rotated.shape[1] >= 309
    assert rotated.shape[0] >= 236


@pytest.mark.parametrize(
    ("turns", "axis"),
    [
        (0, TextAxis.HORIZONTAL),
        (1, TextAxis.VERTICAL),
        (2, TextAxis.HORIZONTAL),  # 180 is indistinguishable from 0 (ADR-0016)
        (3, TextAxis.VERTICAL),
    ],
)
def test_text_axis(turns: int, axis: TextAxis) -> None:
    page = PAGE if turns == 0 else quarter_turn(PAGE, turns)
    estimate, _ = detect_axis(_points(page))
    assert estimate.axis is axis


def test_quarter_turn_makes_vertical_text_horizontal() -> None:
    restored = rotate_quarter(quarter_turn(PAGE, 3))
    estimate, _ = detect_axis(_points(restored))
    assert estimate.axis is TextAxis.HORIZONTAL


def test_blank_page_has_no_reliable_skew() -> None:
    estimate = estimate_skew(_points(blank_page(dpi=150)))
    assert not estimate.reliable


def test_non_text_content_has_no_reliable_skew() -> None:
    rng = np.random.default_rng(3)
    blobs = np.full((2338, 1654), 255, dtype=np.uint8)
    for _ in range(300):
        centre = (int(rng.integers(0, 1654)), int(rng.integers(0, 2338)))
        cv2.circle(blobs, centre, int(rng.integers(5, 60)), 0, -1)
    estimate = estimate_skew(ink_points(analysis_image(blobs, 200).ink))
    assert not estimate.reliable


def test_degraded_upscaled_scan_still_gives_reliable_skew() -> None:
    encoded = cv2.imencode(".jpg", rotate(text_page(dpi=100), 4.0), [cv2.IMWRITE_JPEG_QUALITY, 92])
    upscaled = cv2.resize(cv2.imdecode(encoded[1], 0), None, fx=2, fy=2)
    estimate = estimate_skew(ink_points(analysis_image(upscaled, 200).ink))
    assert estimate.reliable
    assert estimate.angle_deg == pytest.approx(4.0, abs=0.15)
