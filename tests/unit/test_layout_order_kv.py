"""Reading order (XY-cut), column counting and evidence-only key-value pairs."""

from __future__ import annotations

import pytest

from newgen_ocr.layout import geometry as g
from newgen_ocr.layout.key_value import from_lines, from_table, split_pair
from newgen_ocr.layout.models import KeyValueSource, RegionType, Table, TableCell, TableStatus
from newgen_ocr.layout.reading_order import column_count, order_regions
from tests.fixtures.layout import line

T = RegionType


def test_two_columns_are_read_column_by_column() -> None:
    boxes = [
        g.box(100, 50, 900, 90),  # 0 title, full width
        g.box(100, 120, 480, 300),  # 1 left top
        g.box(520, 120, 900, 300),  # 2 right top
        g.box(100, 320, 480, 500),  # 3 left bottom
        g.box(520, 320, 900, 500),  # 4 right bottom
        g.box(100, 540, 900, 700),  # 5 full-width table below
        g.box(100, 10, 300, 30),  # 6 header
        g.box(450, 760, 550, 780),  # 7 page number
        g.box(700, 10, 900, 30),  # 8 header (right)
    ]
    types = [T.TITLE, T.TEXT, T.TEXT, T.TEXT, T.TEXT, T.TABLE, T.HEADER, T.PAGE_NUMBER, T.HEADER]
    assert order_regions(boxes, types, line_height=30) == [6, 8, 0, 1, 3, 2, 4, 5, 7]
    # Without multi-line evidence, aligned rows are read row by row.
    assert order_regions(boxes, types, line_height=100) == [6, 8, 0, 1, 2, 3, 4, 5, 7]
    assert column_count(boxes, types) == 2


def test_single_column_is_top_to_bottom() -> None:
    boxes = [g.box(100, 300, 800, 400), g.box(100, 100, 800, 200), g.box(100, 500, 800, 600)]
    types = [T.TEXT] * 3
    assert order_regions(boxes, types) == [1, 0, 2]
    assert column_count(boxes, types) == 1
    assert column_count([], []) == 0


def test_one_line_rows_under_a_full_width_block_are_read_row_by_row() -> None:
    # Label / value blocks, one line each, in aligned rows.
    boxes = [g.box(100, 100, 300, 130), g.box(500, 100, 800, 130)]
    boxes += [g.box(100, 160, 300, 190), g.box(500, 160, 800, 190)]
    types = [T.TEXT] * 4
    # With nothing spanning the gutter, the vertical cut wins: column by column.
    assert order_regions(boxes, types, line_height=30) == [0, 2, 1, 3]
    wide = [g.box(100, 20, 800, 50), *boxes]
    assert order_regions(wide, [T.TITLE, *types], line_height=30) == [0, 1, 2, 3, 4]


def test_overlapping_boxes_fall_back_to_position() -> None:
    boxes = [g.box(100, 100, 500, 300), g.box(300, 50, 800, 250)]
    assert order_regions(boxes, [T.TEXT, T.TEXT]) == [1, 0]


@pytest.mark.parametrize(
    ("text", "pair"),
    [
        ("Invoice No: INV-2024-0042", ("Invoice No", "INV-2024-0042")),
        ("Date : 14 March 2024", ("Date", "14 March 2024")),
        ("दिनांक: 14/03/2024", ("दिनांक", "14/03/2024")),
        ("GSTIN\uff1a29ABCDE1234F1Z5", ("GSTIN", "29ABCDE1234F1Z5")),
        ("Delivery window 10:30 to 12:00", None),  # a time
        ("See https://example.org/terms", None),  # a URL
        ("Terms and conditions apply", None),  # no separator
        ("Note:", None),  # no value
        ("123: 456 units", None),  # key without letters
        ("A very long label with far too many words in it: x", None),
    ],
)
def test_split_pair(text: str, pair: tuple[str, str] | None) -> None:
    assert split_pair(text) == pair


def test_pairs_from_lines_carry_the_line_box() -> None:
    lines = [
        line("Customer: Sharma Traders", g.box(0, 0, 300, 30)),
        line("hello", g.box(0, 40, 50, 70)),
    ]
    pairs = from_lines(lines, [0, 1], region_id=3)
    assert len(pairs) == 1
    assert (pairs[0].key, pairs[0].value, pairs[0].line_index) == ("Customer", "Sharma Traders", 0)
    assert pairs[0].source is KeyValueSource.SEPARATOR
    assert pairs[0].bbox == lines[0].bbox
    assert pairs[0].key_bbox is None  # word boxes are never estimated


def _table(cells: list[TableCell], rows: int, cols: int) -> Table:
    grid = [[-1] * cols for _ in range(rows)]
    for i, c in enumerate(cells):
        for r in range(c.row, c.row + c.row_span):
            for k in range(c.col, c.col + c.col_span):
                grid[r][k] = i
    return Table(
        id=0,
        region_id=2,
        bbox=g.box(0, 0, 200, 100),
        status=TableStatus.OK,
        rows=rows,
        cols=cols,
        cells=cells,
        grid=grid,
    )


def _cell(r: int, c: int, text: str, **kw: object) -> TableCell:
    return TableCell(
        row=r, col=c, text=text, bbox=g.box(c * 100, r * 50, c * 100 + 100, r * 50 + 50), **kw
    )  # type: ignore[arg-type]


def test_pairs_from_two_column_table() -> None:
    table = _table(
        [
            _cell(0, 0, "Field", header=True),
            _cell(0, 1, "Value", header=True),
            _cell(1, 0, "Amount"),
            _cell(1, 1, "11000.00"),
            _cell(2, 0, ""),
            _cell(2, 1, "orphan"),
        ],
        3,
        2,
    )
    pairs = from_table(table)
    assert [(p.key, p.value, p.row) for p in pairs] == [("Amount", "11000.00", 1)]
    assert pairs[0].key_bbox == table.cells[2].bbox
    assert pairs[0].bbox == g.box(0, 50, 200, 100)


def test_no_pairs_from_wider_spanned_or_unsupported_tables() -> None:
    three = _table([_cell(0, c, "x") for c in range(3)], 1, 3)
    assert from_table(three) == []
    spanned = _table([_cell(0, 0, "x", col_span=2)], 1, 2)
    assert from_table(spanned) == []
    unsupported = _table([_cell(0, 0, "a"), _cell(0, 1, "b")], 1, 2)
    unsupported.status = TableStatus.UNSUPPORTED
    assert from_table(unsupported) == []
