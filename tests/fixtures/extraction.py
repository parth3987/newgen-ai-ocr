"""Builders for extraction tests: documents from text lines, as views or stored runs."""

from __future__ import annotations

import io
from datetime import UTC, datetime

import numpy as np
from PIL import Image

from newgen_ocr.audit import AuditLog
from newgen_ocr.config import Settings
from newgen_ocr.extraction.document import DocumentView, Line, PageTable
from newgen_ocr.extraction.models import EvidenceSource
from newgen_ocr.imaging.runtime import runtime_info
from newgen_ocr.ingestion.service import IngestionService, UploadInfo
from newgen_ocr.layout import geometry as g
from newgen_ocr.layout.models import (
    LayoutRegion,
    LayoutRun,
    PageLayout,
    PageLayoutStatus,
    RegionType,
    Table,
    TableCell,
    TableStatus,
)
from newgen_ocr.ocr.models import OcrLine, OcrPage, OcrRun, PageOcrStatus
from newgen_ocr.storage.local import DocumentStore

# A "block" is one layout region: a list of line texts.
Blocks = list[list[str]]


def table(
    rows: list[list[str]], header: bool = True, status: TableStatus = TableStatus.OK
) -> Table:
    cells = [
        TableCell(
            row=r,
            col=c,
            header=header and r == 0,
            bbox=g.box(100 + 150 * c, 1000 + 40 * r, 250 + 150 * c, 1040 + 40 * r),
            text=text,
        )
        for r, row in enumerate(rows)
        for c, text in enumerate(row)
    ]
    grid = [[r * len(rows[0]) + c for c in range(len(rows[0]))] for r in range(len(rows))]
    return Table(
        id=0,
        region_id=99,
        bbox=g.box(100, 1000, 100 + 150 * len(rows[0]), 1000 + 40 * len(rows)),
        status=status,
        rows=len(rows),
        cols=len(rows[0]),
        cells=cells,
        grid=grid,
    )


def view(
    blocks: Blocks, tables: list[Table] | None = None, confidence: float = 0.95
) -> DocumentView:
    out = DocumentView()
    y = 50
    for region, block in enumerate(blocks):
        for text in block:
            out.lines.append(
                Line(
                    page=1,
                    text=text,
                    bbox=g.box(100, y, 900, y + 30),
                    source=EvidenceSource.OCR_LINE,
                    indices=[len(out.lines)],
                    confidence=confidence,
                    region_id=region,
                    position=len(out.lines),
                )
            )
            y += 40
        y += 40
    out.tables = [PageTable(1, t) for t in tables or []]
    out.page_sizes = {1: (1000.0, 1400.0, "px")}
    return out


def store_document(
    settings: Settings, store: DocumentStore, blocks: Blocks, tables: list[Table] | None = None
) -> str:
    """Ingest a blank page and store an OCR run + layout run holding these lines/tables."""
    buf = io.BytesIO()
    Image.fromarray(np.full((1400, 1000), 255, np.uint8)).save(buf, "PNG")
    manifest = IngestionService(settings, store, AuditLog(settings.data_dir)).ingest(
        io.BytesIO(buf.getvalue()), UploadInfo("doc.png", None)
    )
    doc = manifest.document_id
    built = view(blocks, tables)
    lines = [OcrLine(text=ln.text, confidence=ln.confidence, bbox=ln.bbox) for ln in built.lines]
    regions = [
        LayoutRegion(
            id=r,
            type=RegionType.TEXT,
            label="text",
            score=0.9,
            bbox=g.union(ln.bbox for ln in built.lines if ln.region_id == r),
            order=r,
            line_indices=[i for i, ln in enumerate(built.lines) if ln.region_id == r],
        )
        for r in range(len(blocks))
    ]
    now = datetime.now(tz=UTC)
    ocr = OcrRun(
        document_id=doc,
        requested_engine="auto",
        engines=[],
        languages=["en"],
        preprocessing_profile=None,
        created_at=now,
        pipeline_version="test",
        runtime=runtime_info(),
        total_ms=0.0,
        pages=[
            OcrPage(
                page_number=1,
                status=PageOcrStatus.OK,
                engine="fake",
                lines=lines,
                image_source="source",
                image_width=1000,
                image_height=1400,
            )
        ],
    )
    layout = LayoutRun(
        document_id=doc,
        ocr_run="auto",
        models=[],
        parameters={},
        created_at=now,
        pipeline_version="test",
        runtime=runtime_info(),
        total_ms=0.0,
        pages=[
            PageLayout(
                page_number=1,
                status=PageLayoutStatus.OK,
                image_width=1000,
                image_height=1400,
                regions=regions,
                tables=list(tables or []),
            )
        ],
    )
    store.write_ocr_run(doc, "auto", ocr)
    store.write_layout_run(doc, layout)
    return doc


INVOICE: Blocks = [
    ["TAX INVOICE"],
    ["Invoice No: INV-7", "Invoice Date: 14/03/2024"],
    ["Seller:", "Northwind Supplies", "GSTIN: 27AAACN1234A1Z7", "Plot 12, Mumbai 400093"],
    ["Bill To:", "Sharma Traders", "GSTIN: 27AABCS5678K1ZS", "45 FC Road, Pune 411004"],
    ["Taxable Value: 1,000.00", "CGST @ 9%: 90.00", "SGST @ 9%: 90.00", "Grand Total: 1,180.00"],
]
ITEMS = [
    ["Description", "HSN", "Qty", "Rate", "Amount"],
    ["Paper", "4802", "2", "250.00", "500.00"],
    ["Toner", "8443", "1", "500.00", "500.00"],
]
