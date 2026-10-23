"""Extraction result schema (docs/EXTRACTION.md; JSON Schema in docs/schemas/).

Every extracted value carries its evidence: page, source, OCR line indices and box. A value
that is not found stays ``null`` with status ``missing``; values that cannot be decided are
``ambiguous`` or ``conflict`` with every candidate listed and ``value`` left ``null``.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel

from newgen_ocr.ingestion.models import BoundingBox

Value = str | int | bool | None


class EvidenceSource(StrEnum):
    OCR_LINE = "ocr_line"  # a line of the OCR run (layout run's page)
    PDF_TEXT = "pdf_text"  # the embedded text layer of a native PDF page
    TABLE_CELL = "table_cell"  # a cell of a layout table
    TEMPLATE_REGION = "template_region"  # lines inside a template's region for the field


class Evidence(BaseModel):
    page_number: int
    source: EvidenceSource
    text: str  # the source text (masked for sensitive fields)
    bbox: BoundingBox
    units: str = "px"  # "px" (OCR image of the page) or "pt" (PDF page, top-left origin)
    line_indices: list[int] = []  # OcrPage.lines indices, or PDF text segment indices
    region_id: int | None = None
    table_id: int | None = None
    row: int | None = None
    col: int | None = None
    confidence: float | None = None  # OCR confidence of the source text


class CandidateMethod(StrEnum):
    TEMPLATE = "template"  # inside the matched template's region for the field
    LABEL = "label"  # after one of the field's labels
    TABLE_ROW = "table_row"  # a (label, value) table row
    SECTION = "section"  # an unlabelled line under a section heading ("Bill To:")
    PATTERN = "pattern"  # found by format, attributed to the field by its section
    UNATTRIBUTED = "unattributed"  # found by format or shared label; field undecidable


class Candidate(BaseModel):
    value: Value
    raw: str
    method: CandidateMethod
    evidence: list[Evidence]
    alternatives: list[str] = []  # other readings when the text is ambiguous
    errors: list[str] = []


class FieldStatus(StrEnum):
    EXTRACTED = "extracted"
    MISSING = "missing"  # no candidate found: value is null, never invented
    AMBIGUOUS = "ambiguous"  # text has several readings, or the field cannot be attributed
    CONFLICT = "conflict"  # different valid values found for the field
    INVALID = "invalid"  # found, but fails its format or checksum rules


class FieldResult(BaseModel):
    name: str
    type: str
    required: bool
    status: FieldStatus
    value: Value = None
    raw: str | None = None
    method: CandidateMethod | None = None
    confidence: float | None = None  # lowest OCR confidence among the value's evidence
    evidence: list[Evidence] = []
    candidates: list[Candidate] = []  # every candidate considered (for review)
    notes: list[str] = []  # assumptions, e.g. "numeric date read day-first"
    errors: list[str] = []
    sensitive: bool = False


class CellValue(BaseModel):
    status: FieldStatus
    value: Value = None
    raw: str = ""
    evidence: Evidence | None = None
    notes: list[str] = []
    errors: list[str] = []


class TableRecord(BaseModel):
    index: int
    page_number: int
    table_id: int
    row: int
    values: dict[str, CellValue]
    errors: list[str] = []


class TableResult(BaseModel):
    name: str
    status: str  # "extracted", "not_found"
    columns: list[str]
    column_types: dict[str, str] = {}
    records: list[TableRecord] = []
    summary_rows: list[TableRecord] = []  # e.g. "Total" rows, kept out of the records
    sources: list[dict[str, int | str | list[str] | None]] = []  # mapped layout tables
    issues: list[str] = []


class ClassificationStatus(StrEnum):
    CLASSIFIED = "classified"
    TEMPLATE = "template"  # a template's anchors all matched
    REQUESTED = "requested"  # the caller named the document type
    AMBIGUOUS = "ambiguous"
    UNKNOWN = "unknown"


class Classification(BaseModel):
    status: ClassificationStatus
    document_type: str | None
    template: str | None = None
    score: float = 0.0
    scores: dict[str, float] = {}
    evidence: dict[str, list[str]] = {}  # per type: matched keywords / patterns / headers


class CheckStatus(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    NOT_APPLICABLE = "not_applicable"  # inputs missing or not valid: nothing was assumed


class CheckResult(BaseModel):
    id: str
    kind: str
    status: CheckStatus
    message: str
    fields: list[str] = []
    expected: str | None = None
    actual: str | None = None
    difference: str | None = None
    rows: list[int] = []  # failing record indices for row-level checks


class ValidationIssue(BaseModel):
    code: str  # "missing_required", "invalid_value", "type_error", …
    location: list[str | int]  # ["fields", name] or ["tables", table, index, column]
    message: str


class ValidationReport(BaseModel):
    valid: bool
    issues: list[ValidationIssue] = []


class ExtractionReviewReason(StrEnum):
    DOCUMENT_TYPE_UNKNOWN = "document_type_unknown"
    DOCUMENT_TYPE_AMBIGUOUS = "document_type_ambiguous"
    REQUIRED_FIELD_MISSING = "required_field_missing"
    FIELD_AMBIGUOUS = "field_ambiguous"
    FIELD_CONFLICT = "field_conflict"
    FIELD_INVALID = "field_invalid"
    TABLE_NOT_FOUND = "table_not_found"
    TABLE_VALUE_INVALID = "table_value_invalid"
    CHECK_FAILED = "check_failed"
    LOW_CONFIDENCE_EVIDENCE = "low_confidence_evidence"
    SOURCE_NEEDS_REVIEW = "source_needs_review"  # OCR or layout flagged an evidence page


class ExtractionReview(BaseModel):
    needs_review: bool
    reasons: list[ExtractionReviewReason] = []
    details: list[str] = []


class ExtractionStatus(StrEnum):
    COMPLETE = "complete"  # valid, all checks passed or not applicable, no review needed
    NEEDS_REVIEW = "needs_review"
    UNCLASSIFIED = "unclassified"  # no document type: nothing extracted


class ExtractionResult(BaseModel):
    document_id: str
    layout_run: str
    ocr_run: str
    schema_id: str | None
    schema_version: str | None
    status: ExtractionStatus
    classification: Classification
    fields: list[FieldResult] = []
    tables: list[TableResult] = []
    # Field name -> normalised value (null when not extracted), after Pydantic validation.
    record: dict[str, Value] = {}
    validation: ValidationReport
    checks: list[CheckResult] = []
    review: ExtractionReview
    source_notes: list[str] = []  # e.g. pages without text, unsupported structures
    created_at: datetime
    pipeline_version: str
    total_ms: float
