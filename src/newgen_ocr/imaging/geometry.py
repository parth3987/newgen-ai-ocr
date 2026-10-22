"""Text-line axis (coarse orientation) and skew estimation, and the matching transforms.

Both estimators use projection profiles of ink pixels: when text lines are aligned with an
axis, the ink histogram across that axis has sharp peaks (lines) and gaps (leading). The
score is the mean squared difference between adjacent histogram bins.

Limitation (ADR-0016): projection profiles cannot tell 0° from 180° (or 90° from 270°).
The axis is detected; the remaining 180° ambiguity is resolved at the OCR stage.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

import cv2
import numpy as np
import numpy.typing as npt

GrayImage = npt.NDArray[np.uint8]

MAX_SKEW_DEG = 15.0
_COARSE_STEP = 0.5
_FINE_STEP = 0.05
_MAX_POINTS = 40_000
# Peak score relative to the median over all candidate angles; below this the profile has
# no dominant line direction and the estimate is unreliable. Calibrated on synthetic pages:
# non-text content (blobs, smooth photo, pure noise) ~1.1; text 3.3 (heavy blur) to 115.
MIN_SKEW_CONFIDENCE = 2.0
# Ratio of row-profile to column-profile score. Measured on synthetic pages: ~9-12 for
# horizontal text, ~0.08 for text rotated by 90 degrees.
HORIZONTAL_AXIS_RATIO = 2.0
VERTICAL_AXIS_RATIO = 0.5


class TextAxis(StrEnum):
    HORIZONTAL = "horizontal"
    VERTICAL = "vertical"
    UNDETERMINED = "undetermined"


@dataclass(frozen=True)
class SkewEstimate:
    angle_deg: float  # page rotation, counter-clockwise positive; correct by rotating -angle
    confidence: float

    @property
    def reliable(self) -> bool:
        return (
            self.confidence >= MIN_SKEW_CONFIDENCE
            and abs(self.angle_deg) < MAX_SKEW_DEG - _COARSE_STEP
        )


@dataclass(frozen=True)
class AxisEstimate:
    axis: TextAxis
    ratio: float


def ink_points(binary_ink: npt.NDArray[np.bool_], seed: int = 0) -> npt.NDArray[np.float64]:
    """(N, 2) array of centred x, y coordinates of ink pixels, deterministically subsampled."""
    ys, xs = np.nonzero(binary_ink)
    points = np.column_stack((xs, ys)).astype(np.float64)
    if len(points) > _MAX_POINTS:
        rng = np.random.default_rng(seed)
        points = points[rng.choice(len(points), _MAX_POINTS, replace=False)]
    if len(points):
        points -= points.mean(axis=0)
    return points


def _profile_score(coords: npt.NDArray[np.float64]) -> float:
    bins = np.round(coords - coords.min()).astype(np.int64)
    hist = np.bincount(bins).astype(np.float64)
    return float(np.sum(np.diff(hist) ** 2)) / len(coords)


def _score(points: npt.NDArray[np.float64], angle_deg: float, rows: bool) -> float:
    t = np.deg2rad(angle_deg)
    x, y = points[:, 0], points[:, 1]
    # Coordinate across text lines after rotating the page by -angle (image y points down).
    coords = (y * np.cos(t) - x * np.sin(t)) if rows else (x * np.cos(t) + y * np.sin(t))
    return _profile_score(coords)


def estimate_skew(points: npt.NDArray[np.float64], rows: bool = True) -> SkewEstimate:
    if len(points) < 100:
        return SkewEstimate(0.0, 0.0)
    coarse = np.arange(-MAX_SKEW_DEG, MAX_SKEW_DEG + 1e-9, _COARSE_STEP)
    coarse_scores = np.array([_score(points, a, rows) for a in coarse])
    best = float(coarse[int(np.argmax(coarse_scores))])
    fine = np.arange(best - _COARSE_STEP, best + _COARSE_STEP + 1e-9, _FINE_STEP)
    fine_scores = np.array([_score(points, a, rows) for a in fine])
    best_fine = float(fine[int(np.argmax(fine_scores))])
    median = float(np.median(coarse_scores))
    confidence = float(fine_scores.max()) / median if median > 0 else 0.0
    # The search angle de-rotates the content: a page rotated counter-clockwise by a degrees
    # scores best at -a in image coordinates.
    return SkewEstimate(angle_deg=-round(best_fine, 2), confidence=round(confidence, 2))


def detect_axis(points: npt.NDArray[np.float64]) -> tuple[AxisEstimate, SkewEstimate]:
    """Text-line axis plus the skew measured relative to that axis."""
    row_skew = estimate_skew(points, rows=True)
    col_skew = estimate_skew(points, rows=False)
    row_best = _score(points, -row_skew.angle_deg, rows=True) if len(points) >= 100 else 0.0
    col_best = _score(points, -col_skew.angle_deg, rows=False) if len(points) >= 100 else 0.0
    ratio = row_best / col_best if col_best > 0 else 0.0
    if ratio >= HORIZONTAL_AXIS_RATIO:
        return AxisEstimate(TextAxis.HORIZONTAL, round(ratio, 2)), row_skew
    if 0 < ratio <= VERTICAL_AXIS_RATIO:
        return AxisEstimate(TextAxis.VERTICAL, round(ratio, 2)), col_skew
    return AxisEstimate(TextAxis.UNDETERMINED, round(ratio, 2)), row_skew


def rotate_quarter(image: GrayImage) -> GrayImage:
    """Rotate 90 degrees clockwise (direction is arbitrary; see module docstring)."""
    return np.ascontiguousarray(cv2.rotate(image, cv2.ROTATE_90_CLOCKWISE))


def rotate_bound(image: GrayImage, angle_deg: float, border: int = 255) -> GrayImage:
    """Rotate counter-clockwise by ``angle_deg``, enlarging the canvas so nothing is cropped."""
    h, w = image.shape[:2]
    matrix = cv2.getRotationMatrix2D((w / 2, h / 2), angle_deg, 1.0)
    cos, sin = abs(matrix[0, 0]), abs(matrix[0, 1])
    new_w, new_h = round(h * sin + w * cos), round(h * cos + w * sin)
    matrix[0, 2] += new_w / 2 - w / 2
    matrix[1, 2] += new_h / 2 - h / 2
    rotated = cv2.warpAffine(
        image, matrix, (new_w, new_h), flags=cv2.INTER_LINEAR, borderValue=border
    )
    return np.asarray(rotated, dtype=np.uint8)
