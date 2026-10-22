"""Layout test-set generator and metrics (no models)."""

from __future__ import annotations

from pathlib import Path

import pytest

from newgen_ocr.evaluation.layout_corpus import (
    build,
    columns_page,
    key_value_page,
    load_manifest,
    table_page,
    table_specs,
)
from newgen_ocr.evaluation.layout_metrics import (
    furniture_scores,
    grid_relations,
    key_value_scores,
    match_table,
    prf,
    reading_order_accuracy,
    shift,
    table_scores,
    truth_grid,
)
from newgen_ocr.layout import geometry as g
from newgen_ocr.layout.models import (
    KeyValuePair,
    KeyValueSource,
    LayoutRegion,
    PageLayout,
    PageLayoutStatus,
    RegionType,
    Table,
    TableCell,
    TableStatus,
)
from tests.conftest import ROOT
from tests.fixtures.layout import line

TEXT_DIR = ROOT / "evaluation" / "corpus" / "text"


def test_specs_are_consistent_rectangular_grids() -> None:
    specs = table_specs(TEXT_DIR)
    assert {s.language for s in specs} == {"en", "hi", "mr", "te", "ta", "kn"}
    assert {s.rules for s in specs} == {"full", "header", "none"}
    for spec in specs:
        covered = sum(c.row_span * c.col_span for c in spec.cells)
        assert covered == spec.rows * len(spec.col_widths), spec.id


def test_table_page_truth() -> None:
    spec = next(s for s in table_specs(TEXT_DIR) if s.id == "en-two-level-header")
    image, sample = table_page(spec, TEXT_DIR, 100)
    assert image.shape == (1169, 827)
    table = sample.tables[0]
    assert (table.rows, table.cols) == (5, 5)
    item = next(c for c in table.cells if c.text == "Item")
    assert (item.row_span, item.col_span) == (2, 1)
    assert item.bbox[3] - item.bbox[1] == pytest.approx(2 * 0.42 * 100)


def test_columns_and_key_value_pages() -> None:
    _, columns = columns_page(TEXT_DIR, 100)
    kinds = [b.kind for b in columns.blocks]
    assert kinds.count("paragraph") == 6 and kinds.count("header") == 2
    assert columns.columns == 2
    _, kv = key_value_page(100)
    assert len(kv.key_values) == 5 and len(kv.not_key_values) == 3


def test_build_writes_manifest(tmp_path: Path) -> None:
    samples = build(TEXT_DIR, tmp_path)
    assert len(samples) == 2 * (len(table_specs(TEXT_DIR)) + 2)
    manifest = load_manifest(tmp_path)
    assert manifest[0]["id"] == "en-simple-clean"
    assert all((tmp_path / str(s["image"])).is_file() for s in manifest)


def _truth_2x2() -> dict:  # type: ignore[type-arg]
    cells = [
        {
            "row": r,
            "col": c,
            "row_span": 1,
            "col_span": 1,
            "text": f"{r}{c}",
            "bbox": [c * 100, r * 50, c * 100 + 100, r * 50 + 50],
        }
        for r in range(2)
        for c in range(2)
    ]
    return {"bbox": [0, 0, 200, 100], "rows": 2, "cols": 2, "rules": "full", "cells": cells}


def _table(texts: list[str], grid: list[list[int]], boxes: list[tuple[int, int]]) -> Table:
    cells = [
        TableCell(
            row=r, col=c, text=t, bbox=g.box(c * 100 + 10, r * 50 + 10, c * 100 + 90, r * 50 + 40)
        )
        for t, (r, c) in zip(texts, boxes, strict=True)
    ]
    return Table(
        id=0,
        region_id=0,
        bbox=g.box(0, 0, 200, 100),
        status=TableStatus.OK,
        rows=len(grid),
        cols=len(grid[0]),
        cells=cells,
        grid=grid,
    )


def test_grid_relations_and_prf() -> None:
    assert grid_relations([[0, 1], [2, 3]]) == {(0, 1, "h"), (2, 3, "h"), (0, 2, "v"), (1, 3, "v")}
    assert grid_relations([[0, 0], [1, 2]]) == {(1, 2, "h"), (0, 1, "v"), (0, 2, "v")}
    assert truth_grid(_truth_2x2()) == [[0, 1], [2, 3]]
    assert prf(set(), set()) == {"precision": 1.0, "recall": 1.0, "f1": 1.0}
    assert prf({1}, {2})["f1"] == 0.0


def test_perfect_table_scores() -> None:
    truth = _truth_2x2()
    table = _table(["00", "01", "10", "11"], [[0, 1], [2, 3]], [(0, 0), (0, 1), (1, 0), (1, 1)])
    assert match_table(truth, [table]) is table
    scores = table_scores(truth, table, [])
    assert scores["structure_exact"] and scores["cell_cer"] == 0.0
    assert scores["association"]["f1"] == scores["content_association"]["f1"] == 1.0


def test_merged_columns_lose_associations() -> None:
    truth = _truth_2x2()
    # One cell per row covering both true cells: its centre lands in neither... or one.
    table = _table(["00 01", "10 11"], [[0], [1]], [(0, 0), (1, 0)])
    scores = table_scores(truth, table, [])
    assert not scores["structure_exact"]
    assert scores["association"]["recall"] < 1.0
    assert scores["cell_cer"] > 0.0
    assert table_scores(truth, None, [])["association"]["f1"] == 0.0
    assert match_table(truth, []) is None


def test_page_scores() -> None:
    blocks = [
        {"kind": "paragraph", "text": "a", "bbox": [0, 0, 100, 40]},
        {"kind": "paragraph", "text": "b", "bbox": [200, 0, 300, 40]},
        {"kind": "header", "text": "h", "bbox": [0, -50, 100, -30]},
    ]
    lines = [line("a", g.box(10, 10, 90, 30)), line("b", g.box(210, 10, 290, 30))]
    regions = [
        LayoutRegion(
            id=0,
            type=RegionType.HEADER,
            label="header",
            score=0.9,
            bbox=g.box(0, -52, 110, -28),
            order=0,
        ),
        LayoutRegion(
            id=1,
            type=RegionType.TEXT,
            label="text",
            score=0.9,
            bbox=g.box(0, 0, 100, 40),
            order=1,
            line_indices=[0],
        ),
        LayoutRegion(
            id=2,
            type=RegionType.TEXT,
            label="text",
            score=0.9,
            bbox=g.box(200, 0, 300, 40),
            order=2,
            line_indices=[1],
        ),
    ]
    page = PageLayout(page_number=1, status=PageLayoutStatus.OK, regions=regions)
    assert reading_order_accuracy(blocks, page, lines) == 1.0
    page.regions[1].order, page.regions[2].order = 2, 1
    assert reading_order_accuracy(blocks, page, lines) == 0.0
    assert furniture_scores(blocks, page) == {"recall": 1.0, "precision": 1.0}
    page.key_values = [
        KeyValuePair(
            key="Date",
            value="1 May",
            source=KeyValueSource.SEPARATOR,
            region_id=1,
            bbox=g.box(0, 0, 1, 1),
        )
    ]
    assert key_value_scores([["Date", "1 May"], ["No", "7"]], page)["recall"] == 0.5
    moved = shift(_truth_2x2(), 10, 5)
    assert moved["bbox"] == [10, 5, 210, 105] and moved["cells"][0]["bbox"][0] == 10
