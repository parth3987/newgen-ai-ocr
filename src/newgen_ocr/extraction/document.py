"""A document as extraction sees it: lines in reading order, tables and key-value pairs.

Built from a stored layout run and its OCR run. Pages the layout skipped because they are
native-text PDF pages are read from their embedded text layer instead (lines rebuilt from
PDF text segments, coordinates in points); every line keeps the link to its source.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from newgen_ocr.errors import AppError
from newgen_ocr.extraction.models import Evidence, EvidenceSource
from newgen_ocr.ingestion.models import BoundingBox, PageText, TextSegment
from newgen_ocr.layout import geometry as g
from newgen_ocr.layout.models import KeyValuePair, LayoutRun, PageLayoutStatus, Table
from newgen_ocr.ocr.models import OcrRun
from newgen_ocr.storage.local import DocumentStore


@dataclass
class Line:
    page: int
    text: str
    bbox: BoundingBox
    source: EvidenceSource
    indices: list[int]  # OCR line indices, or PDF text segment indices
    confidence: float | None = None
    region_id: int | None = None
    units: str = "px"
    position: int = 0  # global reading-order position

    def evidence(self, text: str | None = None) -> Evidence:
        return Evidence(
            page_number=self.page,
            source=self.source,
            text=self.text if text is None else text,
            bbox=self.bbox,
            units=self.units,
            line_indices=list(self.indices),
            region_id=self.region_id,
            confidence=self.confidence,
        )


@dataclass
class PageTable:
    page: int
    table: Table
    lines_confidence: dict[int, float | None] = field(default_factory=dict)


@dataclass
class DocumentView:
    lines: list[Line] = field(default_factory=list)
    tables: list[PageTable] = field(default_factory=list)
    key_values: list[tuple[int, KeyValuePair]] = field(default_factory=list)
    page_sizes: dict[int, tuple[float, float, str]] = field(default_factory=dict)
    # Pages whose OCR or layout result was routed to review (evidence there is weaker).
    pages_needing_review: set[int] = field(default_factory=set)
    notes: list[str] = field(default_factory=list)

    @property
    def text(self) -> str:
        return "\n".join(line.text for line in self.lines)


def build_view(
    store: DocumentStore, document_id: str, ocr: OcrRun, layout: LayoutRun
) -> DocumentView:
    view = DocumentView()
    ocr_pages = {p.page_number: p for p in ocr.pages}
    for page in layout.pages:
        number = page.page_number
        ocr_page = ocr_pages.get(number)
        if page.status is PageLayoutStatus.OK and ocr_page is not None:
            view.page_sizes[number] = (
                float(page.image_width or 0),
                float(page.image_height or 0),
                "px",
            )
            if page.review.needs_review or ocr_page.review.needs_review:
                view.pages_needing_review.add(number)
            for region in page.regions:
                for index in region.line_indices:
                    line = ocr_page.lines[index]
                    view.lines.append(
                        Line(
                            page=number,
                            text=line.text,
                            bbox=line.bbox,
                            source=EvidenceSource.OCR_LINE,
                            indices=[index],
                            confidence=line.confidence,
                            region_id=region.id,
                        )
                    )
            confidences = {i: ln.confidence for i, ln in enumerate(ocr_page.lines)}
            for table in page.tables:
                view.tables.append(PageTable(number, table, confidences))
            view.key_values += [(number, kv) for kv in page.key_values]
        elif page.skip_reason == "native_text":
            try:
                text = store.read_page_text(document_id, number)
            except AppError:
                view.notes.append(f"page {number}: native text page without a text layer file")
                continue
            view.page_sizes[number] = (text.page_width, text.page_height, "pt")
            view.lines += pdf_lines(text)
            view.notes.append(
                f"page {number}: embedded PDF text used (no layout regions or tables)"
            )
        else:
            reason = page.skip_reason or page.error_code or page.status.value
            view.notes.append(f"page {number}: no text available ({reason})")
    for position, item in enumerate(view.lines):
        item.position = position
    return view


def pdf_lines(text: PageText) -> list[Line]:
    """Group PDF text segments that share a baseline into lines, left to right."""
    segments = [(i, s) for i, s in enumerate(text.segments) if s.text.strip()]
    segments.sort(key=lambda item: ((item[1].bbox.y0 + item[1].bbox.y1) / 2, item[1].bbox.x0))
    rows: list[list[tuple[int, TextSegment]]] = []
    for index, segment in segments:
        centre = (segment.bbox.y0 + segment.bbox.y1) / 2
        height = max(segment.bbox.y1 - segment.bbox.y0, 1.0)
        if rows:
            last = rows[-1][-1][1]
            last_centre = (last.bbox.y0 + last.bbox.y1) / 2
            if abs(centre - last_centre) <= height / 2:
                rows[-1].append((index, segment))
                continue
        rows.append([(index, segment)])
    lines = []
    for row in rows:
        row.sort(key=lambda item: item[1].bbox.x0)
        parts = [re.sub(r"\s+", " ", s.text).strip() for _, s in row]
        lines.append(
            Line(
                page=text.page_number,
                text=" ".join(p for p in parts if p),
                bbox=g.union(s.bbox for _, s in row),
                source=EvidenceSource.PDF_TEXT,
                indices=[i for i, _ in row],
                units="pt",
            )
        )
    return lines
