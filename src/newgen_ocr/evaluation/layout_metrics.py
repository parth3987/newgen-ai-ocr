"""Layout and table metrics against ``layout_corpus`` ground truth.

Tables:
- ``structure_exact``: the predicted grid has exactly the true cells, spans included;
- ``association`` (OCR-independent): predicted cells are matched to true cells by position,
  then the true neighbour relations (cell A immediately left of / above cell B, as in the
  ICDAR 2013 adjacency-relation metric) are compared as precision / recall / F1;
- ``content_association``: the same relations keyed by normalised cell text, so OCR errors
  count too (the original ICDAR 2013 formulation);
- ``cell_cer``: character error rate of cell text, micro-averaged over true cells.

Pages: reading order of flow blocks (pairwise order accuracy over OCR lines), page
furniture (header / footer / page number) recall and precision, column count, and
key-value precision / recall.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from newgen_ocr.evaluation.metrics import edit_distance, normalize
from newgen_ocr.ingestion.models import BoundingBox
from newgen_ocr.layout import geometry as g
from newgen_ocr.layout.models import PageLayout, RegionType, Table
from newgen_ocr.ocr.models import OcrLine


def _bbox(values: Sequence[float]) -> BoundingBox:
    return g.box(*values)


def shift(item: dict[str, Any], dx: float, dy: float) -> dict[str, Any]:
    """Copy of a truth table or block with every box translated by (dx, dy)."""

    def move(b: list[float]) -> list[float]:
        return [b[0] + dx, b[1] + dy, b[2] + dx, b[3] + dy]

    out = {**item, "bbox": move(item["bbox"])}
    if "cells" in item:
        out["cells"] = [{**c, "bbox": move(c["bbox"])} for c in item["cells"]]
    return out


def grid_relations(grid: list[list[int]]) -> set[tuple[int, int, str]]:
    """(a, b, "h"|"v"): cell b is the immediate right / lower neighbour of cell a."""
    relations: set[tuple[int, int, str]] = set()
    for r, row in enumerate(grid):
        for c, cell in enumerate(row):
            if c + 1 < len(row) and row[c + 1] != cell:
                relations.add((cell, row[c + 1], "h"))
            if r + 1 < len(grid) and grid[r + 1][c] != cell:
                relations.add((cell, grid[r + 1][c], "v"))
    return relations


def truth_grid(table: dict[str, Any]) -> list[list[int]]:
    grid = [[-1] * table["cols"] for _ in range(table["rows"])]
    for index, cell in enumerate(table["cells"]):
        for r in range(cell["row"], cell["row"] + cell["row_span"]):
            for c in range(cell["col"], cell["col"] + cell["col_span"]):
                grid[r][c] = index
    return grid


def prf(predicted: set[Any], truth: set[Any]) -> dict[str, float]:
    hits = len(predicted & truth)
    precision = hits / len(predicted) if predicted else (1.0 if not truth else 0.0)
    recall = hits / len(truth) if truth else 1.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {"precision": round(precision, 4), "recall": round(recall, 4), "f1": round(f1, 4)}


def _cell_point(table: Table, index: int, lines: Sequence[OcrLine]) -> tuple[float, float] | None:
    cell = table.cells[index]
    if cell.bbox is not None:
        return g.centre(cell.bbox)
    if cell.line_indices:
        return g.centre(g.union(lines[i].bbox for i in cell.line_indices))
    return None


def match_table(truth: dict[str, Any], tables: Sequence[Table]) -> Table | None:
    target = _bbox(truth["bbox"])
    best = max(tables, key=lambda t: g.iou(t.bbox, target), default=None)
    return best if best is not None and g.iou(best.bbox, target) >= 0.5 else None


def table_scores(
    truth: dict[str, Any], table: Table | None, lines: Sequence[OcrLine]
) -> dict[str, Any]:
    t_cells = truth["cells"]
    t_relations = grid_relations(truth_grid(truth))
    t_text = [normalize(c["text"]) for c in t_cells]
    t_content = {(t_text[a], t_text[b], d) for a, b, d in t_relations}
    total_chars = sum(len(t) for t in t_text)
    if table is None or not table.cells:
        return {
            "detected": table is not None,
            "status": table.status.value if table is not None else None,
            "structure_exact": False,
            "association": prf(set(), t_relations),
            "content_association": prf(set(), t_content),
            "cell_cer": 1.0,
        }
    structure = {(c.row, c.col, c.row_span, c.col_span) for c in table.cells}
    true_structure = {(c["row"], c["col"], c["row_span"], c["col_span"]) for c in t_cells}
    # Predicted cell -> true cell containing its position.
    mapping: dict[int, int] = {}
    for index in range(len(table.cells)):
        point = _cell_point(table, index, lines)
        if point is None:
            continue
        for t_index, cell in enumerate(t_cells):
            if g.contains_point(_bbox(cell["bbox"]), *point):
                mapping[index] = t_index
                break
    predicted = grid_relations(table.grid)
    mapped = {
        (mapping[a], mapping[b], d)
        for a, b, d in predicted
        if a in mapping and b in mapping and mapping[a] != mapping[b]
    }
    unmapped = {(a, b, d) for a, b, d in predicted if a not in mapping or b not in mapping}
    p_text = [normalize(c.text) for c in table.cells]
    content = {(p_text[a], p_text[b], d) for a, b, d in predicted}
    by_truth: dict[int, list[int]] = {}
    for p_index, t_index in mapping.items():
        by_truth.setdefault(t_index, []).append(p_index)
    errors = 0
    for t_index, text in enumerate(t_text):
        found = " ".join(p_text[i] for i in sorted(by_truth.get(t_index, [])))
        errors += edit_distance([ord(ch) for ch in text], [ord(ch) for ch in found])
    return {
        "detected": True,
        "status": table.status.value,
        "ruling_check": table.ruling_check.value,
        "structure_exact": structure == true_structure,
        "rows_cols": [table.rows, table.cols],
        "association": prf(mapped | unmapped, t_relations),
        "content_association": prf(content, t_content),
        "cell_cer": round(errors / total_chars, 4) if total_chars else 0.0,
    }


def reading_order_accuracy(
    blocks: Sequence[dict[str, Any]], layout: PageLayout, lines: Sequence[OcrLine]
) -> float:
    """Pairwise order accuracy of flow blocks, located through the OCR lines they contain."""
    position: dict[int, tuple[int, int]] = {}
    for region in layout.regions:
        for rank, index in enumerate(region.line_indices):
            position[index] = (region.order, rank)
    keys = []
    for block in blocks:
        if block["kind"] not in ("title", "paragraph"):
            continue
        box = _bbox(block["bbox"])
        inside = [
            position[i]
            for i, line in enumerate(lines)
            if i in position and g.contains_point(box, *g.centre(line.bbox))
        ]
        if inside:
            keys.append(min(inside))
    pairs = [(a, b) for i, a in enumerate(keys) for b in keys[i + 1 :]]
    if not pairs:
        return 0.0
    return round(sum(1 for a, b in pairs if a < b) / len(pairs), 4)


FURNITURE = {
    "header": {RegionType.HEADER},
    "footer": {RegionType.FOOTER, RegionType.PAGE_NUMBER},
    "page_number": {RegionType.PAGE_NUMBER, RegionType.FOOTER},
}


def furniture_scores(blocks: Sequence[dict[str, Any]], layout: PageLayout) -> dict[str, float]:
    truth = [b for b in blocks if b["kind"] in FURNITURE]
    predicted = [
        r
        for r in layout.regions
        if r.type in (RegionType.HEADER, RegionType.FOOTER, RegionType.PAGE_NUMBER)
    ]
    found = sum(
        1
        for b in truth
        if any(
            r.type in FURNITURE[b["kind"]] and g.coverage(_bbox(b["bbox"]), r.bbox) >= 0.5
            for r in predicted
        )
    )
    correct = sum(
        1
        for r in predicted
        if any(
            r.type in FURNITURE[b["kind"]] and g.coverage(_bbox(b["bbox"]), r.bbox) >= 0.5
            for b in truth
        )
    )
    return {
        "recall": round(found / len(truth), 4) if truth else 1.0,
        "precision": round(correct / len(predicted), 4) if predicted else 1.0,
    }


def key_value_scores(truth: Sequence[Sequence[str]], layout: PageLayout) -> dict[str, float]:
    predicted = {(normalize(p.key), normalize(p.value)) for p in layout.key_values}
    expected = {(normalize(k), normalize(v)) for k, v in truth}
    return prf(predicted, expected)
