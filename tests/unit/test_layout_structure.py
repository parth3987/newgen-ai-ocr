"""Table structure parsing, ruling-line tracing and text assignment (no models)."""

from __future__ import annotations

import numpy as np
import pytest
from PIL import Image, ImageDraw

from newgen_ocr.layout import geometry as g
from newgen_ocr.layout.models import RulingCheck, TableCell
from newgen_ocr.layout.table_structure import (
    LogicalCell,
    StructureError,
    assign_lines,
    build_grid,
    cell_boundaries,
    compare_with_rulings,
    cuts_through_text,
    parse_structure,
    side_by_side,
    to_html,
    trace_rulings,
)
from tests.fixtures.layout import COLS, ROWS, draw_page, line, tokens


def test_parse_simple_grid_with_merged_td_tokens() -> None:
    cells, rows, cols = parse_structure(tokens([["", ""], ["", ""]]))
    assert (rows, cols) == (2, 2)
    assert [(c.row, c.col) for c in cells] == [(0, 0), (0, 1), (1, 0), (1, 1)]


def test_parse_colspan_and_rowspan() -> None:
    # Two-level header: "Item" spans 2 rows, "Q1" spans 2 columns.
    cells, rows, cols = parse_structure(
        tokens([[' rowspan="2"', ' colspan="2"'], ["", ""], ["", "", ""]])
    )
    assert (rows, cols) == (3, 3)
    assert cells[0] == LogicalCell(0, 0, 2, 1)
    assert cells[1] == LogicalCell(0, 1, 1, 2)
    # Row 1 starts after the rowspan from above.
    assert (cells[2].row, cells[2].col) == (1, 1)
    assert build_grid(cells, rows, cols)[1] == [0, 2, 3]


def test_parse_plain_td_and_thead() -> None:
    head = ["<thead>", "<tr>", "<td>", "</td>", "</tr>", "</thead>"]
    body = ["<tbody>", "<tr>", "<td>", "</td>", "</tr>", "</tbody>"]
    cells, _, _ = parse_structure([*head, *body])
    assert [c.header for c in cells] == [True, False]


@pytest.mark.parametrize(
    "bad",
    [
        tokens([["", ""], [""]]),  # ragged rows
        tokens([[' rowspan="2"', ""], ['colspan="2"']]),  # malformed attribute
        [*tokens([[' colspan="2"', ""], ["", ' rowspan="2"']]), "<tr>", "<td></td>"],
        ["<td></td>"],  # cell outside a row
        ["<tr>", "<td", ">", "</td>", ">", "</tr>"],  # stray ">"
        ["<tr>", "<span>", "</tr>"],
        ["<tr>", "</tr>"],  # no cells
        ["</tr>"],
    ],
)
def test_inconsistent_structures_are_rejected(bad: list[str]) -> None:
    with pytest.raises(StructureError):
        parse_structure(bad)


def test_overlapping_spans_are_rejected() -> None:
    with pytest.raises(StructureError):
        parse_structure(tokens([["", ' rowspan="2"'], [' colspan="2"']]))


def _crop(merge_first_row: bool = False) -> np.ndarray:
    page = draw_page([], merge_first_row=merge_first_row)
    return np.ascontiguousarray(page[ROWS[0] - 5 : ROWS[-1] + 5, COLS[0] - 5 : COLS[-1] + 5])


def test_trace_rulings_recovers_grid() -> None:
    grid = trace_rulings(_crop())
    assert grid is not None
    assert (grid.rows, grid.cols) == (3, 3)
    assert len(grid.cells) == 9


def test_trace_rulings_recovers_colspan() -> None:
    grid = trace_rulings(_crop(merge_first_row=True))
    assert grid is not None
    assert (grid.rows, grid.cols) == (3, 3)
    assert LogicalCell(0, 0, 1, 3) in grid.cells


def test_trace_rulings_inconclusive_without_closed_cells() -> None:
    page = draw_page([], ruled=False)
    assert trace_rulings(page[150:500, 50:750]) is None
    assert trace_rulings(np.full((10, 10), 255, np.uint8)) is None


def test_compare_with_rulings() -> None:
    grid = trace_rulings(_crop())
    cells, rows, cols = parse_structure(tokens([["", "", ""]] * 3))
    assert compare_with_rulings(cells, rows, cols, grid) is RulingCheck.VERIFIED
    cells, rows, cols = parse_structure(tokens([["", ""]] * 3))
    assert compare_with_rulings(cells, rows, cols, grid) is RulingCheck.MISMATCH
    assert compare_with_rulings(cells, rows, cols, None) is RulingCheck.INCONCLUSIVE


def test_assign_lines_to_cells() -> None:
    cells = [g.box(0, 0, 100, 50), g.box(100, 0, 200, 50)]
    lines = [
        line("left", g.box(10, 10, 60, 40)),
        line("both", g.box(50, 10, 150, 40)),  # half in each cell: spanning
        line("mostly right", g.box(95, 10, 195, 40)),  # 95% inside the right cell
        line("outside", g.box(10, 100, 60, 130)),
    ]
    result = assign_lines(lines, [0, 1, 2, 3], [*cells, None])
    assert result.cell_lines == {0: [0], 1: [2]}
    assert result.spanning == {1: [0, 1]}
    assert result.unassigned == [3]


def test_side_by_side_lines_in_one_cell() -> None:
    lines = [
        line("Paper A4", g.box(0, 0, 100, 30)),
        line("20", g.box(300, 2, 330, 30)),  # same row, gap of 200 px > 1.5 x height
        line("wrapped", g.box(0, 40, 90, 70)),  # next text row: fine
    ]
    assert side_by_side(lines, [0, 1])
    assert not side_by_side(lines, [0, 2])


def test_cuts_through_text() -> None:
    img = Image.new("L", (400, 40), 255)
    draw = ImageDraw.Draw(img)
    draw.rectangle([10, 10, 150, 30], fill=0)  # a solid "word"
    draw.rectangle([250, 10, 390, 30], fill=0)
    gray = np.asarray(img, dtype=np.uint8)
    text = g.box(10, 10, 390, 30)
    assert cuts_through_text(gray, text, 80)  # inside the first word
    assert not cuts_through_text(gray, text, 200)  # in the gap
    assert not cuts_through_text(gray, text, 395)  # beyond the line


def test_cell_boundaries() -> None:
    cells = [
        TableCell(row=0, col=1, bbox=g.box(100, 0, 200, 10)),
        TableCell(row=0, col=0, bbox=g.box(0, 0, 90, 10)),
        TableCell(row=0, col=2, bbox=None),
    ]
    assert cell_boundaries(cells) == [95.0]


def test_html_is_escaped_and_spans_kept() -> None:
    cells = [
        TableCell(row=0, col=0, col_span=2, header=True, bbox=None, text="<b>&"),
        TableCell(row=1, col=0, bbox=None, text="a"),
        TableCell(row=1, col=1, row_span=1, bbox=None, text="b"),
    ]
    html = to_html(cells, 2)
    assert '<th colspan="2">&lt;b&gt;&amp;</th>' in html
    assert "<td>a</td><td>b</td>" in html
