"""Layout, reading order, tables and key-value pairs for a stored OCR run (ADR-0025).

The layout stage reads the exact image each OCR page was recognised from (source or
preprocessed, after the recorded rotation), so OCR line coordinates and layout coordinates
share one pixel space. OCR lines are never modified or dropped: every line ends up in exactly
one region (an ``unclassified`` one if no detected region holds it).

Evidence rules:
- a line belongs to the region covering at least half of it; tables take precedence; a line
  that two other regions both cover is ambiguous and routes the page to review;
- table structure comes from SLANet_plus; a structure that is not a consistent grid is
  ``unsupported`` (no cells are returned; its text stays in the region); on wired tables the
  structure is cross-checked against the traced ruling lines, and a disagreement routes the
  table to review;
- a page line spanning several cells is never split by guessing: those cells are re-read
  from their own crops with the same engine, or flagged when that is not possible;
- formulas, charts and seals are recorded as unsupported structures.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime

import cv2
import numpy as np
from PIL import Image

from newgen_ocr import __version__
from newgen_ocr.audit import AuditEvent, AuditLog
from newgen_ocr.errors import AppError, ErrorCode
from newgen_ocr.imaging.runtime import runtime_info
from newgen_ocr.ingestion.models import BoundingBox
from newgen_ocr.layout import geometry as g
from newgen_ocr.layout import key_value, overlay
from newgen_ocr.layout.detector import DetectedRegion, LayoutModels
from newgen_ocr.layout.models import (
    CellBoxSource,
    CellTextSource,
    LayoutRegion,
    LayoutReview,
    LayoutReviewReason,
    LayoutRun,
    PageLayout,
    PageLayoutStatus,
    RegionType,
    RulingCheck,
    Table,
    TableCell,
    TableKind,
    TableStatus,
    UnsupportedKind,
    UnsupportedStructure,
)
from newgen_ocr.layout.reading_order import column_count, order_regions
from newgen_ocr.layout.table_structure import (
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
from newgen_ocr.ocr.engine import OcrEngine, reading_order
from newgen_ocr.ocr.models import OcrLine, OcrPage, OcrRun, PageOcrStatus
from newgen_ocr.ocr.script_detect import rotate_clockwise
from newgen_ocr.storage.local import DocumentStore

logger = logging.getLogger(__name__)

# A line belongs to a region covering at least this share of its box.
MIN_LINE_COVERAGE = 0.5
# Same-type regions mostly inside a higher-scoring one are duplicates of it.
DUPLICATE_COVERAGE = 0.9
# Different-type regions overlapping this much are one region labelled twice.
DUPLICATE_IOU = 0.8
TABLE_CROP_PAD_PX = 5
RULING_CROP_PAD_PX = 15
CELL_CROP_PAD_PX = 3
# A cell with at least this many dark pixels (outside ruling lines) has unread content.
INK_LEVEL = 128
MIN_INK_PX = 30
MIN_INK_SHARE = 0.002
UNSUPPORTED_TYPES = {
    RegionType.FORMULA: UnsupportedKind.FORMULA,
    RegionType.CHART: UnsupportedKind.CHART,
    RegionType.SEAL: UnsupportedKind.SEAL,
}


@dataclass(frozen=True)
class LayoutPolicy:
    min_table_score: float = 0.90
    ruling_check: bool = True


@dataclass
class _Page:
    """Mutable working state for one page."""

    ocr: OcrPage
    image: np.ndarray
    _gray: np.ndarray | None = None
    regions: list[LayoutRegion] = field(default_factory=list)
    tables: list[Table] = field(default_factory=list)
    unsupported: list[UnsupportedStructure] = field(default_factory=list)
    review: list[tuple[LayoutReviewReason, str]] = field(default_factory=list)

    def gray(self) -> np.ndarray:
        if self._gray is None:
            self._gray = (
                self.image
                if self.image.ndim == 2
                else np.asarray(cv2.cvtColor(self.image, cv2.COLOR_RGB2GRAY), dtype=np.uint8)
            )
        return self._gray


class LayoutService:
    def __init__(
        self,
        store: DocumentStore,
        audit: AuditLog,
        models: LayoutModels,
        engines: dict[str, OcrEngine],
        policy: LayoutPolicy | None = None,
    ) -> None:
        self.store = store
        self.audit = audit
        self.models = models
        self.engines = engines
        self.policy = policy or LayoutPolicy()

    def status(self) -> str | None:
        return self.models.unavailable_reason()

    # ------------------------------------------------------------ run

    def analyze(self, document_id: str, ocr_run: str) -> LayoutRun:
        reason = self.models.unavailable_reason()
        if reason is not None:
            raise AppError(ErrorCode.LAYOUT_UNAVAILABLE, f"Layout analysis unavailable: {reason}")
        run = self.store.read_ocr_run(document_id, ocr_run)
        start = time.perf_counter()
        pages = [self._page(document_id, run, page) for page in run.pages]
        result = LayoutRun(
            document_id=document_id,
            ocr_run=ocr_run,
            models=self.models.info(),
            parameters={
                "min_table_score": self.policy.min_table_score,
                "ruling_check": self.policy.ruling_check,
            },
            created_at=datetime.now(tz=UTC),
            pipeline_version=__version__,
            runtime=runtime_info(),
            total_ms=round((time.perf_counter() - start) * 1000, 2),
            pages_needing_review=sum(1 for p in pages if p.review.needs_review),
            pages=pages,
        )
        self.store.write_layout_run(document_id, result)
        self.audit.record(
            AuditEvent.LAYOUT_COMPLETED,
            document_id=document_id,
            ocr_run=ocr_run,
            pages_ok=sum(1 for p in pages if p.status is PageLayoutStatus.OK),
            pages_failed=sum(1 for p in pages if p.status is PageLayoutStatus.FAILED),
            tables=sum(len(p.tables) for p in pages),
            pages_needing_review=result.pages_needing_review,
            total_ms=round(result.total_ms),
        )
        logger.info(
            "layout completed",
            extra={
                "document_id": document_id,
                "ocr_run": ocr_run,
                "pages_needing_review": result.pages_needing_review,
                "total_ms": result.total_ms,
            },
        )
        return result

    def _image(
        self,
        document_id: str,
        page_number: int,
        source: str | None,
        rotation: int,
        size: tuple[int | None, int | None],
    ) -> np.ndarray:
        """The exact image the OCR engine read for this page."""
        if (source or "").startswith("preprocessed:"):
            profile = str(source).split(":", 1)[1]
            path = self.store.preprocessed_image_path(document_id, profile, page_number)
        else:
            path = self.store.page_image_path(document_id, page_number)
        with Image.open(path) as img:
            image = np.asarray(img)
        image = rotate_clockwise(image, rotation)
        if (image.shape[1], image.shape[0]) != size:
            raise AppError(ErrorCode.LAYOUT_FAILED, "Page image does not match the OCR run")
        return image

    def overlay(self, document_id: str, ocr_run: str, page_number: int) -> bytes:
        """Inspection PNG of one analysed page."""
        run = self.store.read_layout_run(document_id, ocr_run)
        page = next((p for p in run.pages if p.page_number == page_number), None)
        if page is None:
            raise AppError(ErrorCode.PAGE_NOT_FOUND, "Page not found")
        if page.status is not PageLayoutStatus.OK:
            raise AppError(ErrorCode.PAGE_IMAGE_NOT_AVAILABLE, "Page has no layout to draw")
        image = self._image(
            document_id,
            page_number,
            page.image_source,
            page.image_rotation_deg,
            (page.image_width, page.image_height),
        )
        return overlay.render(image, page)

    def _page(self, document_id: str, run: OcrRun, page: OcrPage) -> PageLayout:
        number = page.page_number
        if page.status is not PageOcrStatus.OK:
            reason = page.skip_reason or f"ocr_{page.status.value}"
            return PageLayout(
                page_number=number, status=PageLayoutStatus.SKIPPED, skip_reason=reason
            )
        start = time.perf_counter()
        try:
            image = self._image(
                document_id,
                number,
                page.image_source,
                page.image_rotation_deg,
                (page.image_width, page.image_height),
            )
            state = _Page(ocr=page, image=image)
            layout = self._analyze(state)
        except AppError as exc:
            logger.warning("layout page failed", extra={"error_code": exc.code.value})
            return PageLayout(
                page_number=number, status=PageLayoutStatus.FAILED, error_code=exc.code.value
            )
        layout.duration_ms = round((time.perf_counter() - start) * 1000, 2)
        return layout

    # ------------------------------------------------------------ page analysis

    def _analyze(self, state: _Page) -> PageLayout:
        page = state.ocr
        height, width = state.image.shape[:2]
        detected = _deduplicate(
            [
                DetectedRegion(r.label, r.type, r.score, g.clip(r.bbox, width, height))
                for r in self.models.detect(state.image)
            ]
        )
        state.regions = [
            LayoutRegion(id=i, type=r.type, label=r.label, score=r.score, bbox=r.bbox)
            for i, r in enumerate(detected)
        ]
        self._assign_lines(state)
        for region in state.regions:
            if region.type is RegionType.TABLE:
                state.tables.append(self._table(state, region, len(state.tables)))
        self._record_unsupported(state)

        heights = sorted(ln.bbox.y1 - ln.bbox.y0 for ln in page.lines)
        order = order_regions(
            [r.bbox for r in state.regions],
            [r.type for r in state.regions],
            heights[len(heights) // 2] if heights else 0.0,
        )
        state.regions = [state.regions[i] for i in order]
        id_map = {r.id: new for new, r in enumerate(state.regions)}
        for new, region in enumerate(state.regions):
            region.id = new
            region.order = new
        for table in state.tables:
            table.region_id = id_map[table.region_id]
        for item in state.unsupported:
            item.region_id = id_map[item.region_id]
        tables_by_region = {t.region_id: t for t in state.tables}
        for region in state.regions:
            found = tables_by_region.get(region.id)
            region.text = (
                _table_text(found)
                if found is not None and found.status is not TableStatus.UNSUPPORTED
                else "\n".join(page.lines[i].text for i in region.line_indices)
            )

        pairs = []
        for region in state.regions:
            if region.type is not RegionType.TABLE:
                pairs += key_value.from_lines(page.lines, region.line_indices, region.id)
        for table in state.tables:
            pairs += key_value.from_table(table)

        reasons = state.review
        return PageLayout(
            page_number=page.page_number,
            status=PageLayoutStatus.OK,
            image_source=page.image_source,
            image_rotation_deg=page.image_rotation_deg,
            image_width=width,
            image_height=height,
            column_count=column_count(
                [r.bbox for r in state.regions], [r.type for r in state.regions]
            ),
            regions=state.regions,
            tables=state.tables,
            key_values=pairs,
            unsupported=state.unsupported,
            text="\n\n".join(r.text for r in state.regions if r.text),
            review=LayoutReview(
                needs_review=bool(reasons),
                reasons=[r for r, _ in reasons],
                details=[d for _, d in reasons],
            ),
        )

    def _assign_lines(self, state: _Page) -> None:
        lines = state.ocr.lines
        members: dict[int, list[int]] = {r.id: [] for r in state.regions}
        ambiguous = 0
        unassigned: list[int] = []
        for index, line in enumerate(lines):
            covering = [
                (g.coverage(line.bbox, r.bbox), r)
                for r in state.regions
                if g.coverage(line.bbox, r.bbox) >= MIN_LINE_COVERAGE
            ]
            tables = [(c, r) for c, r in covering if r.type is RegionType.TABLE]
            candidates = tables or covering
            if not candidates:
                unassigned.append(index)
                continue
            if not tables and len(candidates) > 1:
                ambiguous += 1
            # Most coverage; on a tie the smaller (more specific) region.
            best = max(candidates, key=lambda cr: (round(cr[0], 3), -g.area(cr[1].bbox)))[1]
            members[best.id].append(index)
        for region in state.regions:
            region.line_indices = _ordered(lines, members[region.id])
        for index in unassigned:
            state.regions.append(
                LayoutRegion(
                    id=len(state.regions),
                    type=RegionType.UNCLASSIFIED,
                    label=None,
                    score=None,
                    bbox=lines[index].bbox,
                    line_indices=[index],
                )
            )
        if ambiguous:
            state.review.append(
                (
                    LayoutReviewReason.OVERLAPPING_REGIONS,
                    f"{ambiguous} lines in overlapping regions",
                )
            )
        if unassigned:
            state.review.append(
                (
                    LayoutReviewReason.UNCLASSIFIED_TEXT,
                    f"{len(unassigned)} lines outside detected regions",
                )
            )

    def _record_unsupported(self, state: _Page) -> None:
        for region in state.regions:
            kind = UNSUPPORTED_TYPES.get(region.type)
            if kind is not None:
                state.unsupported.append(
                    UnsupportedStructure(
                        kind=kind,
                        region_id=region.id,
                        detail=f"{region.type.value} is not recognised; OCR text kept as-is",
                    )
                )
                if region.line_indices:
                    state.review.append(
                        (
                            LayoutReviewReason.UNSUPPORTED_STRUCTURE,
                            f"text inside {region.type.value}",
                        )
                    )
            elif region.type is RegionType.FIGURE and region.line_indices:
                state.unsupported.append(
                    UnsupportedStructure(
                        kind=UnsupportedKind.FIGURE_TEXT,
                        region_id=region.id,
                        detail="text inside a figure is returned without structure",
                    )
                )

    # ------------------------------------------------------------ tables

    def _table(self, state: _Page, region: LayoutRegion, table_id: int) -> Table:
        height, width = state.image.shape[:2]
        crop_box = g.clip(
            g.box(
                region.bbox.x0 - TABLE_CROP_PAD_PX,
                region.bbox.y0 - TABLE_CROP_PAD_PX,
                region.bbox.x1 + TABLE_CROP_PAD_PX,
                region.bbox.y1 + TABLE_CROP_PAD_PX,
            ),
            width,
            height,
        )
        x0, y0 = int(crop_box.x0), int(crop_box.y0)
        crop = np.ascontiguousarray(state.image[y0 : int(crop_box.y1), x0 : int(crop_box.x1)])
        region.table_id = table_id
        table = Table(id=table_id, region_id=region.id, bbox=region.bbox, status=TableStatus.OK)
        table.kind, table.kind_score = self.models.classify_table(crop)
        structure = self.models.table_structure(crop)
        table.structure_score = structure.score
        try:
            cells, rows, cols = parse_structure(structure.tokens)
            if len(structure.cell_boxes) != len(cells):
                raise StructureError(
                    f"{len(structure.cell_boxes)} cell boxes for {len(cells)} cells"
                )
        except StructureError as exc:
            table.status = TableStatus.UNSUPPORTED
            table.issues.append(f"structure: {exc}")
            state.unsupported.append(
                UnsupportedStructure(
                    kind=UnsupportedKind.TABLE_STRUCTURE,
                    region_id=region.id,
                    detail=f"table structure not recoverable: {exc}",
                )
            )
            state.review.append((LayoutReviewReason.TABLE_STRUCTURE_INVALID, f"table {table_id}"))
            return table

        review: list[tuple[LayoutReviewReason, str]] = []
        table.rows, table.cols = rows, cols
        table.grid = build_grid(cells, rows, cols)
        boxes: list[BoundingBox | None] = [
            g.translate(b, x0, y0) if g.area(b) > 0 else None for b in structure.cell_boxes
        ]
        table.cells = [
            TableCell(
                row=c.row,
                col=c.col,
                row_span=c.row_span,
                col_span=c.col_span,
                header=c.header,
                bbox=boxes[i],
            )
            for i, c in enumerate(cells)
        ]
        if structure.score < self.policy.min_table_score:
            review.append(
                (LayoutReviewReason.TABLE_LOW_CONFIDENCE, f"table {table_id}: {structure.score}")
            )
        if not self.policy.ruling_check:
            table.ruling_check = RulingCheck.DISABLED
        elif table.kind is TableKind.WIRED:
            # A wider crop than the model's: a tight region box can clip the outer border.
            trace_box = g.clip(
                g.box(
                    region.bbox.x0 - RULING_CROP_PAD_PX,
                    region.bbox.y0 - RULING_CROP_PAD_PX,
                    region.bbox.x1 + RULING_CROP_PAD_PX,
                    region.bbox.y1 + RULING_CROP_PAD_PX,
                ),
                width,
                height,
            )
            tx, ty = int(trace_box.x0), int(trace_box.y0)
            gray = state.gray()[ty : int(trace_box.y1), tx : int(trace_box.x1)]
            traced = trace_rulings(np.ascontiguousarray(gray))
            table.ruling_check = compare_with_rulings(cells, rows, cols, traced)
            if traced is not None:
                table.ruling_rows, table.ruling_cols = traced.rows, traced.cols
            if table.ruling_check is RulingCheck.VERIFIED and traced is not None:
                # Same cells as the model: use the exact traced cell interiors (no borders,
                # no overlap) instead of the model's approximate boxes.
                exact = {
                    (c.row, c.col, c.row_span, c.col_span): b
                    for c, b in zip(traced.cells, traced.boxes, strict=True)
                }
                for cell in table.cells:
                    key = (cell.row, cell.col, cell.row_span, cell.col_span)
                    cell.bbox = g.translate(exact[key], tx, ty)
                table.cell_boxes = CellBoxSource.RULINGS
            elif table.ruling_check is RulingCheck.MISMATCH:
                review.append(
                    (
                        LayoutReviewReason.TABLE_STRUCTURE_MISMATCH,
                        f"table {table_id}: model {rows}x{cols}, "
                        f"rulings {traced.rows if traced else 0}x{traced.cols if traced else 0}",
                    )
                )
            elif table.ruling_check is RulingCheck.INCONCLUSIVE:
                table.issues.append("ruling lines could not be traced into closed cells")
        else:
            table.ruling_check = RulingCheck.NOT_APPLICABLE
        self._fill_cells(state, region, table, review)
        if review:
            table.status = TableStatus.NEEDS_REVIEW
            table.issues += [d for _, d in review]
            state.review += review
        table.html = to_html(table.cells, rows)
        return table

    def _fill_cells(
        self,
        state: _Page,
        region: LayoutRegion,
        table: Table,
        review: list[tuple[LayoutReviewReason, str]],
    ) -> None:
        lines = state.ocr.lines
        assignment = assign_lines(lines, region.line_indices, [c.bbox for c in table.cells])
        for index, cell in enumerate(table.cells):
            members = _ordered(lines, assignment.cell_lines.get(index, []))
            cell.line_indices = members
            if members:
                cell.text = " ".join(lines[i].text for i in members)
                cell.text_source = CellTextSource.OCR_LINES
                cell.confidence = _mean_confidence([lines[i] for i in members])

        # Structure evidence from the OCR lines (catches wrong column splits on wireless
        # tables, which have no rulings to check against).
        merged = [i for i, members in assignment.cell_lines.items() if side_by_side(lines, members)]
        gray = state.gray()
        cuts = [
            line
            for line, cells in assignment.spanning.items()
            if any(
                cuts_through_text(gray, lines[line].bbox, x)
                for x in cell_boundaries([table.cells[c] for c in cells])
            )
        ]
        if merged or cuts:
            review.append(
                (
                    LayoutReviewReason.TABLE_STRUCTURE_MISMATCH,
                    f"table {table.id}: {len(merged)} cells hold side-by-side text, "
                    f"{len(cuts)} lines cut by cell boundaries",
                )
            )

        spanning = sorted(assignment.spanning)
        span_cells = {c for cells in assignment.spanning.values() for c in cells}
        # Empty cells are re-read too: an engine can miss text inside ruled grids.
        empty = {i for i, c in enumerate(table.cells) if not c.line_indices and c.bbox}
        reread = sorted(span_cells | empty)
        if reread and self._reread_cells(state, table, reread):
            for cell_index in span_cells:
                touching = [i for i in spanning if cell_index in assignment.spanning[i]]
                cell = table.cells[cell_index]
                cell.line_indices = sorted(set(cell.line_indices) | set(touching))
            filled = sum(1 for i in empty if table.cells[i].text)
            if spanning:
                table.issues.append(f"{len(spanning)} lines spanned cells; cells re-read")
            if filled:
                table.issues.append(f"{filled} cells without page OCR text re-read from crops")
        elif spanning:
            review.append(
                (
                    LayoutReviewReason.TABLE_CELL_TEXT_AMBIGUOUS,
                    f"table {table.id}: {len(spanning)} lines span several cells",
                )
            )
        unread = [
            i
            for i in sorted(empty)
            if not table.cells[i].text and _has_ink(gray, table.cells[i].bbox)
        ]
        if unread:
            review.append(
                (
                    LayoutReviewReason.TABLE_CELL_UNREAD,
                    f"table {table.id}: {len(unread)} cells contain marks but no text was read",
                )
            )
        if assignment.unassigned:
            review.append(
                (
                    LayoutReviewReason.TABLE_TEXT_UNASSIGNED,
                    f"table {table.id}: {len(assignment.unassigned)} lines outside every cell",
                )
            )

    def _reread_cells(self, state: _Page, table: Table, cell_indices: list[int]) -> bool:
        """Re-read cells from their crops with the page's engine. False if impossible."""
        page = state.ocr
        engine = self.engines.get(page.engine or "")
        if engine is None or engine.unavailable_reason() is not None or not page.languages:
            return False
        height, width = state.image.shape[:2]
        texts: dict[int, tuple[str, float | None]] = {}
        for index in cell_indices:
            bbox = table.cells[index].bbox
            if bbox is None:
                return False
            # Traced interiors are exact (padding would add borders); model boxes are loose.
            pad = 0 if table.cell_boxes is CellBoxSource.RULINGS else CELL_CROP_PAD_PX
            b = g.clip(
                g.box(bbox.x0 - pad, bbox.y0 - pad, bbox.x1 + pad, bbox.y1 + pad), width, height
            )
            crop = np.ascontiguousarray(state.image[int(b.y0) : int(b.y1), int(b.x0) : int(b.x1)])
            try:
                found = engine.recognize(crop, page.languages)
            except AppError:
                return False
            texts[index] = (" ".join(line.text for line in found), _mean_confidence(found))
        for index, (text, confidence) in texts.items():
            cell = table.cells[index]
            cell.text = text
            cell.confidence = confidence
            cell.text_source = CellTextSource.CELL_OCR if text else CellTextSource.EMPTY
        return True


def _has_ink(gray: np.ndarray, bbox: BoundingBox | None) -> bool:
    """Whether a cell holds marks (other than ruling lines) that could be text."""
    if bbox is None:
        return False
    crop = gray[int(bbox.y0) : int(bbox.y1), int(bbox.x0) : int(bbox.x1)]
    if crop.size == 0:
        return False
    # Fixed threshold: Otsu on a near-blank crop would binarise scanner noise.
    ink = np.asarray(crop < INK_LEVEL, dtype=np.uint8) * 255
    h, w = ink.shape
    for size in ((max(w // 2, 3), 1), (1, max(h // 2, 3))):
        ruling = cv2.morphologyEx(
            ink, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, size)
        )
        ink = np.asarray(
            cv2.subtract(ink, cv2.dilate(ruling, np.ones((3, 3), np.uint8))), dtype=np.uint8
        )
    marked = int(np.count_nonzero(ink))
    return bool(marked >= max(MIN_INK_PX, MIN_INK_SHARE * h * w))


def _deduplicate(regions: list[DetectedRegion]) -> list[DetectedRegion]:
    """Drop regions that repeat a higher-scoring one (nested same-type or near-identical)."""
    kept: list[DetectedRegion] = []
    for region in sorted(regions, key=lambda r: -r.score):
        duplicate = any(
            (k.type == region.type and g.coverage(region.bbox, k.bbox) >= DUPLICATE_COVERAGE)
            or g.iou(k.bbox, region.bbox) >= DUPLICATE_IOU
            for k in kept
        )
        if not duplicate:
            kept.append(region)
    return kept


def _ordered(lines: list[OcrLine], indices: list[int]) -> list[int]:
    by_identity = {id(lines[i]): i for i in indices}
    return [by_identity[id(line)] for line in reading_order([lines[i] for i in indices])]


def _mean_confidence(lines: list[OcrLine]) -> float | None:
    values = [line.confidence for line in lines if line.confidence is not None]
    return round(sum(values) / len(values), 4) if values else None


def _table_text(table: Table) -> str:
    rows = []
    for r in range(table.rows):
        cells = [c for c in table.cells if c.row == r]
        rows.append("\t".join(c.text for c in sorted(cells, key=lambda c: c.col)))
    return "\n".join(rows)
