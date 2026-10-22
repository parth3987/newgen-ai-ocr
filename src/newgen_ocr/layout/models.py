"""Layout and table result schema (docs/API_SPECIFICATION.md, ADR-0025).

A layout run is derived from one stored OCR run: it adds regions, reading order, tables and
key-value pairs on top of the OCR lines, without changing them. Coordinates are pixels in the
image the OCR engine read (``OcrPage.image_source`` after ``image_rotation_deg``), so every
region, cell and pair links back to the OCR lines (by index) and to the source page.

Nothing is inferred that the evidence does not support: structures the models or rules cannot
represent are listed in ``unsupported`` and routed to review, never approximated.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel

from newgen_ocr.ingestion.models import BoundingBox
from newgen_ocr.ocr.models import EngineInfo
from newgen_ocr.preprocessing.models import RuntimeInfo


class RegionType(StrEnum):
    """Stable region types; ``LayoutRegion.label`` keeps the model's own label."""

    TITLE = "title"
    SECTION_TITLE = "section_title"
    TEXT = "text"
    CAPTION = "caption"
    HEADER = "header"
    FOOTER = "footer"
    PAGE_NUMBER = "page_number"
    FOOTNOTE = "footnote"
    ASIDE = "aside"
    TABLE = "table"
    FIGURE = "figure"
    CHART = "chart"
    FORMULA = "formula"
    SEAL = "seal"
    # OCR text outside every detected region (kept, never dropped), or an unknown label.
    UNCLASSIFIED = "unclassified"


# Regions outside the main reading flow (read first or last, in their own order).
PAGE_FURNITURE_TOP = frozenset({RegionType.HEADER})
PAGE_FURNITURE_BOTTOM = frozenset({RegionType.FOOTER, RegionType.PAGE_NUMBER})


class LayoutRegion(BaseModel):
    id: int
    type: RegionType
    label: str | None  # raw model label; None for UNCLASSIFIED
    score: float | None  # detector confidence; None for UNCLASSIFIED
    bbox: BoundingBox
    # Position in the page reading order (0-based); every region has one.
    order: int = 0
    line_indices: list[int] = []  # indices into OcrPage.lines, in reading order
    text: str = ""
    table_id: int | None = None


class TableKind(StrEnum):
    WIRED = "wired"  # ruled cell borders
    WIRELESS = "wireless"  # whitespace-separated columns


class RulingCheck(StrEnum):
    """Deterministic cross-check of the model's structure against drawn cell borders."""

    VERIFIED = "verified"  # ruling lines give exactly the same rows, columns and spans
    MISMATCH = "mismatch"  # they disagree: the table is routed to review
    INCONCLUSIVE = "inconclusive"  # borders could not be traced into closed cells
    NOT_APPLICABLE = "not_applicable"  # wireless table: no borders to check against
    DISABLED = "disabled"


class TableStatus(StrEnum):
    OK = "ok"
    NEEDS_REVIEW = "needs_review"  # structure returned, with issues listed
    UNSUPPORTED = "unsupported"  # no reliable structure: no cells returned, text kept


class CellBoxSource(StrEnum):
    MODEL = "model"  # SLANet's approximate cell boxes
    RULINGS = "rulings"  # exact interiors traced from verified ruling lines


class CellTextSource(StrEnum):
    OCR_LINES = "ocr_lines"  # the page's OCR lines that fall inside the cell
    CELL_OCR = "cell_ocr"  # re-read from the cell crop (a page line spanned several cells)
    EMPTY = "empty"


class TableCell(BaseModel):
    row: int  # 0-based
    col: int
    row_span: int = 1
    col_span: int = 1
    header: bool = False  # in the model's <thead>
    bbox: BoundingBox | None  # page pixels; None if the model gave no box for this cell
    text: str = ""
    text_source: CellTextSource = CellTextSource.EMPTY
    line_indices: list[int] = []
    confidence: float | None = None  # mean OCR confidence of the cell text


class Table(BaseModel):
    id: int
    region_id: int
    bbox: BoundingBox
    status: TableStatus
    kind: TableKind | None = None
    kind_score: float | None = None
    structure_score: float | None = None  # SLANet's own structure confidence
    rows: int = 0
    cols: int = 0
    cells: list[TableCell] = []
    # rows x cols of indices into ``cells``; a spanned cell repeats its index.
    grid: list[list[int]] = []
    ruling_check: RulingCheck = RulingCheck.DISABLED
    cell_boxes: CellBoxSource = CellBoxSource.MODEL
    ruling_rows: int | None = None
    ruling_cols: int | None = None
    issues: list[str] = []
    html: str = ""  # escaped HTML rendering of cells (empty when unsupported)


class KeyValueSource(StrEnum):
    SEPARATOR = "separator"  # "Key: value" on one line
    TABLE_ROW = "table_row"  # a two-column table row


class KeyValuePair(BaseModel):
    """Heuristic pair; only emitted on explicit evidence (ADR-0025)."""

    key: str
    value: str
    source: KeyValueSource
    region_id: int
    # SEPARATOR: the one line holding both (no per-word boxes are estimated).
    line_index: int | None = None
    bbox: BoundingBox
    # TABLE_ROW: exact cell boxes.
    key_bbox: BoundingBox | None = None
    value_bbox: BoundingBox | None = None
    table_id: int | None = None
    row: int | None = None


class UnsupportedKind(StrEnum):
    FORMULA = "formula"
    CHART = "chart"
    SEAL = "seal"
    FIGURE_TEXT = "figure_text"
    TABLE_STRUCTURE = "table_structure"


class UnsupportedStructure(BaseModel):
    kind: UnsupportedKind
    region_id: int
    detail: str


class LayoutReviewReason(StrEnum):
    TABLE_STRUCTURE_MISMATCH = "table_structure_mismatch"
    TABLE_STRUCTURE_INVALID = "table_structure_invalid"
    TABLE_LOW_CONFIDENCE = "table_low_confidence"
    TABLE_TEXT_UNASSIGNED = "table_text_unassigned"
    TABLE_CELL_TEXT_AMBIGUOUS = "table_cell_text_ambiguous"
    TABLE_CELL_UNREAD = "table_cell_unread"  # a cell has marks but no text was read
    UNSUPPORTED_STRUCTURE = "unsupported_structure"
    OVERLAPPING_REGIONS = "overlapping_regions"
    UNCLASSIFIED_TEXT = "unclassified_text"


class LayoutReview(BaseModel):
    needs_review: bool
    reasons: list[LayoutReviewReason] = []
    details: list[str] = []


class PageLayoutStatus(StrEnum):
    OK = "ok"
    SKIPPED = "skipped"  # see skip_reason
    FAILED = "failed"  # see error_code


class PageLayout(BaseModel):
    page_number: int
    status: PageLayoutStatus
    skip_reason: str | None = None  # "native_text", "no_ocr_result", ...
    error_code: str | None = None
    image_source: str | None = None
    image_rotation_deg: int = 0
    image_width: int | None = None
    image_height: int | None = None
    column_count: int = 0  # side-by-side text columns in the main flow
    regions: list[LayoutRegion] = []  # sorted by ``order``
    tables: list[Table] = []
    key_values: list[KeyValuePair] = []
    unsupported: list[UnsupportedStructure] = []
    text: str = ""  # page text in reading order (tables row by row, cells tab-separated)
    review: LayoutReview = LayoutReview(needs_review=False)
    duration_ms: float = 0.0


class LayoutRun(BaseModel):
    document_id: str
    ocr_run: str  # name of the stored OCR run this layout was derived from
    models: list[EngineInfo]
    parameters: dict[str, str | int | float | bool]
    created_at: datetime
    pipeline_version: str
    runtime: RuntimeInfo
    total_ms: float
    pages_needing_review: int = 0
    pages: list[PageLayout]
