"""Test doubles and pages for layout tests: a fake model set and a drawn table page."""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from datetime import UTC, datetime

import numpy as np
from PIL import Image, ImageDraw

from newgen_ocr.errors import AppError, ErrorCode
from newgen_ocr.imaging.runtime import runtime_info
from newgen_ocr.ingestion.models import BoundingBox
from newgen_ocr.layout import geometry as g
from newgen_ocr.layout.detector import DetectedRegion, TableStructure
from newgen_ocr.layout.models import RegionType, TableKind
from newgen_ocr.layout.service import TABLE_CROP_PAD_PX
from newgen_ocr.ocr.models import EngineInfo, OcrLine, OcrPage, OcrRun, PageOcrStatus

PAGE_W, PAGE_H = 1000, 800
# Ruled 3x3 table: columns 100-300-500-700, rows 200-280-360-440.
COLS = (100, 300, 500, 700)
ROWS = (200, 280, 360, 440)
TABLE = g.box(COLS[0], ROWS[0], COLS[-1], ROWS[-1])
TITLE = g.box(100, 40, 600, 70)


def cell_box(row: int, col: int, row_span: int = 1, col_span: int = 1) -> BoundingBox:
    return g.box(COLS[col], ROWS[row], COLS[col + col_span], ROWS[row + row_span])


def text_box(row: int, col: int, width: int = 80) -> BoundingBox:
    """Where a short word sits inside a cell."""
    return g.box(COLS[col] + 20, ROWS[row] + 25, COLS[col] + 20 + width, ROWS[row] + 55)


def draw_page(
    words: list[BoundingBox], merge_first_row: bool = False, ruled: bool = True
) -> np.ndarray:
    """White page with a title bar, a ruled 3x3 table and a black bar per word box."""
    img = Image.new("L", (PAGE_W, PAGE_H), 255)
    draw = ImageDraw.Draw(img)
    draw.rectangle([TITLE.x0, TITLE.y0 + 5, TITLE.x1, TITLE.y1 - 5], fill=0)
    if ruled:
        for y in ROWS:
            draw.line([(COLS[0], y), (COLS[-1], y)], fill=0, width=2)
        for i, x in enumerate(COLS):
            top = ROWS[1] if merge_first_row and i in (1, 2) else ROWS[0]
            draw.line([(x, top), (x, ROWS[-1])], fill=0, width=2)
    for b in words:
        draw.rectangle([b.x0, b.y0 + 8, b.x1, b.y1 - 8], fill=0)
    return np.asarray(img, dtype=np.uint8)


def png(image: np.ndarray) -> bytes:
    buf = io.BytesIO()
    Image.fromarray(image).save(buf, "PNG")
    return buf.getvalue()


def line(text: str, bbox: BoundingBox, confidence: float = 0.95) -> OcrLine:
    return OcrLine(text=text, confidence=confidence, bbox=bbox)


def ocr_run(document_id: str, lines: list[OcrLine], engine: str = "paddle") -> OcrRun:
    page = OcrPage(
        page_number=1,
        status=PageOcrStatus.OK,
        engine=engine,
        image_source="source",
        image_width=PAGE_W,
        image_height=PAGE_H,
        lines=lines,
        text="\n".join(ln.text for ln in lines),
        languages=["en"],
    )
    return OcrRun(
        document_id=document_id,
        requested_engine="auto",
        engines=[],
        languages=["en"],
        preprocessing_profile=None,
        created_at=datetime.now(tz=UTC),
        pipeline_version="test",
        runtime=runtime_info(),
        total_ms=0.0,
        pages=[page],
    )


def tokens(rows: list[list[str]]) -> list[str]:
    """SLANet-style tokens; each cell is "<td></td>" or e.g. ' colspan="2"'."""
    out = ["<html>", "<body>", "<table>"]
    for row in rows:
        out.append("<tr>")
        for cell in row:
            out += ["<td></td>"] if not cell else ["<td", cell, ">", "</td>"]
        out.append("</tr>")
    return [*out, "</table>", "</body>", "</html>"]


@dataclass
class FakeLayoutModels:
    """Returns configured regions and table structure; table boxes are given in page pixels."""

    regions: list[DetectedRegion] = field(default_factory=list)
    kind: TableKind = TableKind.WIRED
    structure_tokens: list[str] = field(default_factory=list)
    cell_boxes: list[BoundingBox] = field(default_factory=list)  # page pixels
    score: float = 0.99
    unavailable: str | None = None
    fail: bool = False
    crops: list[tuple[int, ...]] = field(default_factory=list)

    def unavailable_reason(self) -> str | None:
        return self.unavailable

    def info(self) -> list[EngineInfo]:
        return [EngineInfo(name="layout_detection", version="fake", models=[], parameters={})]

    def detect(self, image: np.ndarray) -> list[DetectedRegion]:
        if self.fail:
            raise AppError(ErrorCode.LAYOUT_FAILED, "simulated failure")
        return list(self.regions)

    def classify_table(self, crop: np.ndarray) -> tuple[TableKind, float]:
        self.crops.append(tuple(crop.shape))
        return self.kind, 0.95

    def table_structure(self, crop: np.ndarray) -> TableStructure:
        table = next(r for r in self.regions if r.type is RegionType.TABLE)
        dx = max(table.bbox.x0 - TABLE_CROP_PAD_PX, 0)
        dy = max(table.bbox.y0 - TABLE_CROP_PAD_PX, 0)
        return TableStructure(
            tokens=list(self.structure_tokens),
            cell_boxes=[g.translate(b, -dx, -dy) for b in self.cell_boxes],
            score=self.score,
        )


def region(
    kind: RegionType, bbox: BoundingBox, label: str = "", score: float = 0.9
) -> DetectedRegion:
    return DetectedRegion(label=label or kind.value, type=kind, score=score, bbox=bbox)
