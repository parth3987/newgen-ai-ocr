"""Region reading order by recursive XY-cut, and column counting.

Page furniture is taken out of the flow first: headers are read first and footers / page
numbers last, each left to right. The remaining regions are split recursively, preferring a
vertical cut (a whitespace gully no region crosses), so multi-column text is read column by
column; a region spanning the gully (a full-width title or table) prevents the vertical cut,
and the block is cut horizontally instead, keeping consecutive bands that share a gully
together when they hold multi-line blocks. So aligned multi-column paragraphs are read
column by column, while rows of one-line blocks (forms, unruled tables) are read row by
row.
"""

from __future__ import annotations

from collections.abc import Sequence

from newgen_ocr.ingestion.models import BoundingBox
from newgen_ocr.layout.models import PAGE_FURNITURE_BOTTOM, PAGE_FURNITURE_TOP, RegionType

# Gaps narrower than this (pixels) do not separate columns or blocks.
MIN_GAP_PX = 4.0

# A block at least this many line heights tall is a paragraph, not a single line.
MULTI_LINE_BLOCK = 2.5

FLOW_TEXT = frozenset({RegionType.TEXT, RegionType.SECTION_TITLE, RegionType.FOOTNOTE})


def order_regions(
    boxes: Sequence[BoundingBox], types: Sequence[RegionType], line_height: float = 0.0
) -> list[int]:
    """Indices of ``boxes`` in reading order; ``line_height`` is the median OCR line height."""
    top = [i for i, t in enumerate(types) if t in PAGE_FURNITURE_TOP]
    bottom = [i for i, t in enumerate(types) if t in PAGE_FURNITURE_BOTTOM]
    furniture = set(top) | set(bottom)
    flow = [i for i in range(len(boxes)) if i not in furniture]

    def by_position(items: list[int]) -> list[int]:
        return sorted(items, key=lambda i: (boxes[i].y0, boxes[i].x0))

    return by_position(top) + _xy_cut(boxes, flow, line_height) + by_position(bottom)


def _xy_cut(boxes: Sequence[BoundingBox], items: list[int], line_height: float) -> list[int]:
    if len(items) <= 1:
        return list(items)
    columns = _split(boxes, items, "x")
    if len(columns) > 1:
        return [i for group in columns for i in _xy_cut(boxes, group, line_height)]
    bands = _merge_column_bands(boxes, _split(boxes, items, "y"), line_height)
    if len(bands) > 1:
        return [i for group in bands for i in _xy_cut(boxes, group, line_height)]
    return sorted(items, key=lambda i: (boxes[i].y0, boxes[i].x0))


def _merge_column_bands(
    boxes: Sequence[BoundingBox], bands: list[list[int]], line_height: float
) -> list[list[int]]:
    """Rejoin consecutive horizontal bands of multi-line blocks that share a column gutter.

    Without this, paragraph gaps that line up across columns would cut the columns into
    rows and the page would be read row by row. Bands of one-line blocks (form rows, rows
    of an unruled table) are not joined: they are read row by row.
    """

    def multi_line(band: list[int]) -> bool:
        tallest = max(boxes[i].y1 - boxes[i].y0 for i in band)
        return tallest >= MULTI_LINE_BLOCK * line_height

    merged: list[list[int]] = []
    for band in bands:
        if (
            merged
            and multi_line(merged[-1])
            and multi_line(band)
            and len(_split(boxes, merged[-1] + band, "x")) > 1
        ):
            merged[-1] = merged[-1] + band
        else:
            merged.append(list(band))
    return merged


def _split(boxes: Sequence[BoundingBox], items: list[int], axis: str) -> list[list[int]]:
    """Group items separated by gaps along an axis (x: columns, y: horizontal bands)."""

    def span(i: int) -> tuple[float, float]:
        b = boxes[i]
        return (b.x0, b.x1) if axis == "x" else (b.y0, b.y1)

    ordered = sorted(items, key=lambda i: span(i)[0])
    groups: list[list[int]] = [[ordered[0]]]
    reach = span(ordered[0])[1]
    for i in ordered[1:]:
        start, end = span(i)
        if start - reach >= MIN_GAP_PX:
            groups.append([i])
        else:
            groups[-1].append(i)
        reach = max(reach, end)
    return groups


def column_count(boxes: Sequence[BoundingBox], types: Sequence[RegionType]) -> int:
    """Most side-by-side flow-text regions crossed by one horizontal line."""
    text = [b for b, t in zip(boxes, types, strict=True) if t in FLOW_TEXT]
    best = 0
    for b in text:
        y = (b.y0 + b.y1) / 2
        crossing = [o for o in text if o.y0 <= y <= o.y1]
        columns = len(_split(crossing, list(range(len(crossing))), "x")) if crossing else 0
        best = max(best, columns)
    return best
