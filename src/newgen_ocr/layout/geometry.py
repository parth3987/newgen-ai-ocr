"""Axis-aligned box helpers shared by the layout modules."""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from newgen_ocr.ingestion.models import BoundingBox


def box(x0: float, y0: float, x1: float, y1: float) -> BoundingBox:
    return BoundingBox(x0=float(x0), y0=float(y0), x1=float(x1), y1=float(y1))


def area(b: BoundingBox) -> float:
    return max(0.0, b.x1 - b.x0) * max(0.0, b.y1 - b.y0)


def intersection(a: BoundingBox, b: BoundingBox) -> float:
    w = min(a.x1, b.x1) - max(a.x0, b.x0)
    h = min(a.y1, b.y1) - max(a.y0, b.y0)
    return w * h if w > 0 and h > 0 else 0.0


def coverage(inner: BoundingBox, outer: BoundingBox) -> float:
    """Fraction of ``inner``'s area that lies inside ``outer``."""
    a = area(inner)
    return intersection(inner, outer) / a if a > 0 else 0.0


def iou(a: BoundingBox, b: BoundingBox) -> float:
    inter = intersection(a, b)
    union = area(a) + area(b) - inter
    return inter / union if union > 0 else 0.0


def centre(b: BoundingBox) -> tuple[float, float]:
    return (b.x0 + b.x1) / 2, (b.y0 + b.y1) / 2


def contains_point(b: BoundingBox, x: float, y: float) -> bool:
    return b.x0 <= x <= b.x1 and b.y0 <= y <= b.y1


def union(boxes: Iterable[BoundingBox]) -> BoundingBox:
    items = list(boxes)
    if not items:
        raise ValueError("union of no boxes")
    return box(
        min(b.x0 for b in items),
        min(b.y0 for b in items),
        max(b.x1 for b in items),
        max(b.y1 for b in items),
    )


def from_points(points: Sequence[float]) -> BoundingBox:
    """Bounding box of a flat [x0, y0, x1, y1, ...] polygon (4 or more values)."""
    xs, ys = points[0::2], points[1::2]
    return box(min(xs), min(ys), max(xs), max(ys))


def translate(b: BoundingBox, dx: float, dy: float) -> BoundingBox:
    return box(b.x0 + dx, b.y0 + dy, b.x1 + dx, b.y1 + dy)


def clip(b: BoundingBox, width: float, height: float) -> BoundingBox:
    return box(
        min(max(b.x0, 0.0), width),
        min(max(b.y0, 0.0), height),
        min(max(b.x1, 0.0), width),
        min(max(b.y1, 0.0), height),
    )
