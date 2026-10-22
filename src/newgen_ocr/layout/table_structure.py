"""Table structure: model tokens to a logical grid, ruling-line check, text assignment.

All functions are deterministic and model-free, so each step can be tested and inspected.
"""

from __future__ import annotations

import html
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from itertools import pairwise

import cv2
import numpy as np
import numpy.typing as npt

from newgen_ocr.ingestion.models import BoundingBox
from newgen_ocr.layout import geometry as g
from newgen_ocr.layout.models import RulingCheck, TableCell
from newgen_ocr.ocr.models import OcrLine

Gray = npt.NDArray[np.uint8]

_SPAN = re.compile(r'\s*(rowspan|colspan)="(\d+)"')


class StructureError(ValueError):
    """The model's structure tokens do not describe a consistent rectangular grid."""


@dataclass(frozen=True)
class LogicalCell:
    row: int
    col: int
    row_span: int = 1
    col_span: int = 1
    header: bool = False


def parse_structure(tokens: Sequence[str]) -> tuple[list[LogicalCell], int, int]:
    """Parse SLANet HTML structure tokens into cells placed on a grid.

    Returns (cells in tag order, rows, cols). Raises StructureError when spans overlap,
    rows have different widths or the tag stream is malformed: such a structure is
    reported as unsupported rather than repaired by guessing.
    """
    cells: list[LogicalCell] = []
    occupied: set[tuple[int, int]] = set()
    row = -1
    col = 0
    in_head = False
    pending: dict[str, int] | None = None
    for tag in tokens:
        if tag in ("<html>", "<body>", "<table>", "</table>", "</body>", "</html>"):
            continue
        if tag in ("<thead>", "</thead>"):
            in_head = tag == "<thead>"
        elif tag in ("<tbody>", "</tbody>"):
            in_head = False
        elif tag == "<tr>":
            row += 1
            col = 0
        elif tag == "</tr>":
            if row < 0:
                raise StructureError("row closed before it was opened")
        elif tag in ("<td>", "<td></td>", "<td"):
            # "<td></td>" is a whole span-free cell (SLANet merges these tokens).
            if row < 0:
                raise StructureError("cell outside a row")
            pending = {"rowspan": 1, "colspan": 1}
            if tag != "<td":
                cells.append(_place(row, col, pending, in_head, occupied))
                col = cells[-1].col + cells[-1].col_span
                pending = None
        elif tag == ">" or tag.startswith(" "):
            if pending is None:
                raise StructureError(f"attribute outside a cell: {tag!r}")
            match = _SPAN.fullmatch(tag)
            if match:
                pending[match.group(1)] = int(match.group(2))
            elif tag == ">":
                cells.append(_place(row, col, pending, in_head, occupied))
                col = cells[-1].col + cells[-1].col_span
                pending = None
            else:
                raise StructureError(f"unexpected attribute tag {tag!r}")
        elif tag == "</td>":
            continue
        else:
            raise StructureError(f"unexpected tag {tag!r}")
    if not cells:
        raise StructureError("no cells")
    rows = max(c.row + c.row_span for c in cells)
    cols = max(c.col + c.col_span for c in cells)
    if len(occupied) != rows * cols:
        raise StructureError(f"grid is not rectangular: {len(occupied)} of {rows}x{cols} filled")
    return cells, rows, cols


def _place(
    row: int, col: int, spans: dict[str, int], header: bool, occupied: set[tuple[int, int]]
) -> LogicalCell:
    while (row, col) in occupied:  # skip positions covered by a rowspan from above
        col += 1
    cell = LogicalCell(row, col, spans["rowspan"], spans["colspan"], header)
    for r in range(row, row + cell.row_span):
        for c in range(col, col + cell.col_span):
            if (r, c) in occupied:
                raise StructureError("overlapping cell spans")
            occupied.add((r, c))
    return cell


def build_grid(cells: Sequence[LogicalCell], rows: int, cols: int) -> list[list[int]]:
    grid = [[-1] * cols for _ in range(rows)]
    for index, cell in enumerate(cells):
        for r in range(cell.row, cell.row + cell.row_span):
            for c in range(cell.col, cell.col + cell.col_span):
                grid[r][c] = index
    return grid


# ------------------------------------------------------------ ruling-line check


@dataclass
class RulingGrid:
    """Closed cells traced from drawn borders, placed on a row/column grid."""

    cells: list[LogicalCell] = field(default_factory=list)
    boxes: list[BoundingBox] = field(default_factory=list)  # crop pixels
    rows: int = 0
    cols: int = 0


def trace_rulings(crop: Gray) -> RulingGrid | None:
    """Trace a wired table's ruling lines into closed cells.

    Returns None when the borders do not form closed cells covering most of the table
    (e.g. partially ruled tables); the check is then inconclusive, not failed.
    """
    h, w = crop.shape
    if h < 20 or w < 20:
        return None
    _, inv = cv2.threshold(crop, 0, 255, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)
    horizontal = cv2.morphologyEx(
        inv, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (max(w // 25, 15), 1))
    )
    vertical = cv2.morphologyEx(
        inv, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(h // 25, 15)))
    )
    rulings = cv2.dilate(cv2.bitwise_or(horizontal, vertical), np.ones((3, 3), np.uint8))
    count, _labels, stats, _ = cv2.connectedComponentsWithStats(
        cv2.bitwise_not(rulings), connectivity=4
    )
    boxes: list[BoundingBox] = []
    min_side = 8
    for i in range(1, count):
        x, y, bw, bh, _ = (int(v) for v in stats[i])
        touches_edge = x == 0 or y == 0 or x + bw >= w or y + bh >= h
        if touches_edge or bw < min_side or bh < min_side:
            continue
        boxes.append(g.box(x, y, x + bw, y + bh))
    if len(boxes) < 2:
        return None
    outer = g.union(boxes)
    if sum(g.area(b) for b in boxes) < 0.7 * g.area(outer):
        return None
    tolerance = max(4.0, min(min(b.x1 - b.x0, b.y1 - b.y0) for b in boxes) / 3)
    xs = _cluster([v for b in boxes for v in (b.x0, b.x1)], tolerance)
    ys = _cluster([v for b in boxes for v in (b.y0, b.y1)], tolerance)
    grid = RulingGrid(rows=len(ys) - 1, cols=len(xs) - 1)
    for b in sorted(boxes, key=lambda b: (b.y0, b.x0)):
        r0, r1 = _nearest(ys, b.y0), _nearest(ys, b.y1)
        c0, c1 = _nearest(xs, b.x0), _nearest(xs, b.x1)
        if r1 <= r0 or c1 <= c0:
            return None
        grid.cells.append(LogicalCell(r0, c0, r1 - r0, c1 - c0))
        grid.boxes.append(b)
    covered = sum(c.row_span * c.col_span for c in grid.cells)
    return grid if covered == grid.rows * grid.cols else None


def _cluster(values: list[float], tolerance: float) -> list[float]:
    groups: list[list[float]] = []
    for v in sorted(values):
        if groups and v - groups[-1][-1] <= tolerance:
            groups[-1].append(v)
        else:
            groups.append([v])
    return [sum(grp) / len(grp) for grp in groups]


def _nearest(positions: list[float], value: float) -> int:
    return min(range(len(positions)), key=lambda i: abs(positions[i] - value))


def compare_with_rulings(
    cells: Sequence[LogicalCell], rows: int, cols: int, grid: RulingGrid | None
) -> RulingCheck:
    if grid is None:
        return RulingCheck.INCONCLUSIVE
    model = {(c.row, c.col, c.row_span, c.col_span) for c in cells}
    traced = {(c.row, c.col, c.row_span, c.col_span) for c in grid.cells}
    if (rows, cols) == (grid.rows, grid.cols) and model == traced:
        return RulingCheck.VERIFIED
    return RulingCheck.MISMATCH


# ------------------------------------------------------------ text assignment


# A line at least this much inside one cell belongs to it, whatever else it overlaps.
DOMINANT_SHARE = 0.9


@dataclass
class Assignment:
    cell_lines: dict[int, list[int]] = field(default_factory=dict)  # cell -> line indices
    spanning: dict[int, list[int]] = field(default_factory=dict)  # line -> cells it spans
    unassigned: list[int] = field(default_factory=list)


def assign_lines(
    lines: Sequence[OcrLine], line_indices: Sequence[int], cell_boxes: Sequence[BoundingBox | None]
) -> Assignment:
    """Place each OCR line in the cell that contains it.

    A line overlapping two or more cells substantially (each holding at least 25% of the
    line's width) is recorded as spanning: its text cannot be split without word boxes,
    so those cells are re-read individually (or flagged) by the caller.
    """
    result = Assignment()
    for index in line_indices:
        line = lines[index].bbox
        hits = []
        for c, cell in enumerate(cell_boxes):
            if cell is None:
                continue
            vertical = min(line.y1, cell.y1) - max(line.y0, cell.y0)
            horizontal = min(line.x1, cell.x1) - max(line.x0, cell.x0)
            if vertical <= 0.5 * (line.y1 - line.y0) or horizontal <= 0:
                continue
            hits.append((horizontal / max(line.x1 - line.x0, 1.0), c))
        strong = [c for share, c in hits if share >= 0.25]
        if hits and max(hits)[0] >= DOMINANT_SHARE:
            strong = [max(hits)[1]]  # nearly all inside one cell; loose neighbours overlap
        if len(strong) >= 2:
            result.spanning[index] = sorted(strong)
        elif hits:
            best = max(hits)[1]
            result.cell_lines.setdefault(best, []).append(index)
        else:
            result.unassigned.append(index)
    return result


# A cell holding two OCR lines on the same text row this far apart (in line heights)
# almost certainly spans a missed column boundary.
SIDE_BY_SIDE_GAP = 1.5
# A cell boundary is clean if a blank run this wide (in line heights) lies within half a
# line height of it; otherwise the boundary cuts through a word.
MIN_BLANK_RUN = 0.2


def side_by_side(lines: Sequence[OcrLine], members: Sequence[int]) -> bool:
    """Whether two of the lines share a text row but are separated by a wide gap."""
    boxes = [lines[i].bbox for i in members]
    for i, a in enumerate(boxes):
        for b in boxes[i + 1 :]:
            height = max(min(a.y1 - a.y0, b.y1 - b.y0), 1.0)
            overlap = min(a.y1, b.y1) - max(a.y0, b.y0)
            gap = max(b.x0 - a.x1, a.x0 - b.x1)
            if overlap >= 0.5 * height and gap >= SIDE_BY_SIDE_GAP * height:
                return True
    return False


def cell_boundaries(cells: Sequence[TableCell]) -> list[float]:
    """x positions separating horizontally adjacent cells (left to right)."""
    boxes = sorted((c.bbox for c in cells if c.bbox is not None), key=lambda b: b.x0)
    return [(a.x1 + b.x0) / 2 for a, b in pairwise(boxes)]


def cuts_through_text(gray: Gray, line: BoundingBox, x: float) -> bool:
    """Whether a vertical cut at ``x`` passes through ink inside the line's box."""
    if not line.x0 < x < line.x1:
        return False  # the cut lies outside the line: nothing is split
    height = max(line.y1 - line.y0, 1.0)
    x0 = max(int(line.x0), int(x - height / 2))
    x1 = min(int(line.x1), int(x + height / 2))
    strip = gray[int(line.y0) : int(line.y1), int(line.x0) : int(line.x1)]
    if strip.size == 0:
        return False
    _, inv = cv2.threshold(strip, 0, 255, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)
    ink = np.asarray(inv[:, x0 - int(line.x0) : x1 - int(line.x0)].any(axis=0)).tolist()
    run = best = 0
    for has_ink in ink:
        run = 0 if has_ink else run + 1
        best = max(best, run)
    return best < max(3.0, MIN_BLANK_RUN * height)


def to_html(cells: Sequence[TableCell], rows: int) -> str:
    out = ["<table>"]
    for r in range(rows):
        out.append("<tr>")
        for cell in (c for c in cells if c.row == r):
            tag = "th" if cell.header else "td"
            attrs = ""
            if cell.row_span > 1:
                attrs += f' rowspan="{cell.row_span}"'
            if cell.col_span > 1:
                attrs += f' colspan="{cell.col_span}"'
            out.append(f"<{tag}{attrs}>{html.escape(cell.text)}</{tag}>")
        out.append("</tr>")
    out.append("</table>")
    return "".join(out)
