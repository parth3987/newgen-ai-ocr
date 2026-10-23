"""Structured extraction for a stored layout run (ADR-0026). Rules only: no model, no LLM.

Pipeline: build the document view (layout + OCR lines, tables; embedded text for native PDF
pages) → classify (template or rules) → extract fields and tables with evidence → validate
with Pydantic → run consistency checks → decide review → store.
"""

from __future__ import annotations

import logging
import time
from datetime import UTC, date, datetime

from newgen_ocr import __version__
from newgen_ocr.audit import AuditEvent, AuditLog
from newgen_ocr.errors import AppError, ErrorCode
from newgen_ocr.extraction.checks import run_checks
from newgen_ocr.extraction.classify import classify
from newgen_ocr.extraction.document import DocumentView, build_view
from newgen_ocr.extraction.extract import Options, extract_fields, mask_text
from newgen_ocr.extraction.models import (
    CheckResult,
    CheckStatus,
    ClassificationStatus,
    ExtractionResult,
    ExtractionReview,
    ExtractionReviewReason,
    ExtractionStatus,
    FieldResult,
    FieldStatus,
    TableResult,
    ValidationReport,
)
from newgen_ocr.extraction.schema import FieldType, SchemaRegistry
from newgen_ocr.extraction.validation import validate
from newgen_ocr.storage.local import DocumentStore

logger = logging.getLogger(__name__)

AUTO = "auto"
Reasons = list[tuple[ExtractionReviewReason, str]]


class ExtractionService:
    def __init__(
        self,
        store: DocumentStore,
        audit: AuditLog,
        registry: SchemaRegistry,
        options: Options | None = None,
        min_evidence_confidence: float = 0.70,
    ) -> None:
        self.store = store
        self.audit = audit
        self.registry = registry
        self.options = options or Options()
        self.min_confidence = min_evidence_confidence

    def extract(
        self,
        document_id: str,
        layout_run: str,
        document_type: str = AUTO,
        today: date | None = None,
    ) -> ExtractionResult:
        start = time.perf_counter()
        if document_type != AUTO and self.registry.get(document_type) is None:
            raise AppError(
                ErrorCode.UNKNOWN_DOCUMENT_TYPE, f"Unknown document type {document_type[:40]!r}"
            )
        layout = self.store.read_layout_run(document_id, layout_run)
        ocr = self.store.read_ocr_run(document_id, layout.ocr_run)
        view = build_view(self.store, document_id, ocr, layout)
        classification = classify(
            self.registry.schemas, view, None if document_type == AUTO else document_type
        )
        reasons: Reasons = []
        schema = self.registry.get(classification.document_type or "")
        fields: list[FieldResult] = []
        tables: list[TableResult] = []
        checks: list[CheckResult] = []
        report = ValidationReport(valid=False)
        record: dict[str, str | int | bool | None] = {}
        if schema is None:
            reason = (
                ExtractionReviewReason.DOCUMENT_TYPE_AMBIGUOUS
                if classification.status is ClassificationStatus.AMBIGUOUS
                else ExtractionReviewReason.DOCUMENT_TYPE_UNKNOWN
            )
            reasons.append((reason, f"scores {classification.scores}"))
        else:
            fields, tables = extract_fields(schema, view, self.options, classification.template)
            report, record = validate(schema, fields, tables)
            checks = run_checks(schema, fields, tables, today or date.today())
            reasons = self._reasons(
                view, fields, tables, checks, {t.name for t in schema.tables if t.required}
            )
        if (
            schema is not None
            and self.options.mask_aadhaar
            and any(f.type is FieldType.AADHAAR for f in schema.fields)
        ):
            _mask(fields, tables)
        if schema is None:
            status = ExtractionStatus.UNCLASSIFIED
        elif reasons or not report.valid:
            status = ExtractionStatus.NEEDS_REVIEW
        else:
            status = ExtractionStatus.COMPLETE
        result = ExtractionResult(
            document_id=document_id,
            layout_run=layout_run,
            ocr_run=layout.ocr_run,
            schema_id=schema.id if schema else None,
            schema_version=schema.version if schema else None,
            status=status,
            classification=classification,
            fields=fields,
            tables=tables,
            record=record,
            validation=report,
            checks=checks,
            review=ExtractionReview(
                needs_review=bool(reasons) or status is not ExtractionStatus.COMPLETE,
                reasons=list(dict.fromkeys(r for r, _ in reasons)),
                details=[d for _, d in reasons],
            ),
            source_notes=view.notes,
            created_at=datetime.now(tz=UTC),
            pipeline_version=__version__,
            total_ms=round((time.perf_counter() - start) * 1000, 2),
        )
        self.store.write_extraction(document_id, result)
        self.audit.record(
            AuditEvent.EXTRACTION_COMPLETED,
            document_id=document_id,
            layout_run=layout_run,
            document_type=result.schema_id,
            classification=classification.status.value,
            status=status.value,
            fields_extracted=sum(1 for f in fields if f.status is FieldStatus.EXTRACTED),
            fields_missing=sum(1 for f in fields if f.status is FieldStatus.MISSING),
            checks_failed=sum(1 for c in checks if c.status is CheckStatus.FAILED),
            total_ms=round(result.total_ms),
        )
        logger.info(
            "extraction completed",
            extra={"document_id": document_id, "status": status.value, "total_ms": result.total_ms},
        )
        return result

    def _reasons(
        self,
        view: DocumentView,
        fields: list[FieldResult],
        tables: list[TableResult],
        checks: list[CheckResult],
        required_tables: set[str],
    ) -> Reasons:
        reasons: Reasons = []
        per_status = {
            FieldStatus.AMBIGUOUS: ExtractionReviewReason.FIELD_AMBIGUOUS,
            FieldStatus.CONFLICT: ExtractionReviewReason.FIELD_CONFLICT,
            FieldStatus.INVALID: ExtractionReviewReason.FIELD_INVALID,
        }
        pages: set[int] = set()
        for f in fields:
            if f.status is FieldStatus.MISSING and f.required:
                reasons.append((ExtractionReviewReason.REQUIRED_FIELD_MISSING, f.name))
            elif f.status in per_status:
                reasons.append((per_status[f.status], f.name))
            elif f.status is FieldStatus.EXTRACTED:
                pages |= {e.page_number for e in f.evidence}
                if f.confidence is not None and f.confidence < self.min_confidence:
                    reasons.append(
                        (ExtractionReviewReason.LOW_CONFIDENCE_EVIDENCE, f"{f.name} {f.confidence}")
                    )
        for table in tables:
            if table.status != "extracted":
                if table.name in required_tables:
                    reasons.append((ExtractionReviewReason.TABLE_NOT_FOUND, table.name))
                continue
            pages |= {r.page_number for r in table.records}
            bad = sum(
                1
                for r in table.records
                for c in r.values.values()
                if c.status in (FieldStatus.AMBIGUOUS, FieldStatus.INVALID)
            )
            if bad:
                reasons.append(
                    (ExtractionReviewReason.TABLE_VALUE_INVALID, f"{table.name}: {bad} cells")
                )
            if table.issues:
                reasons.append(
                    (ExtractionReviewReason.SOURCE_NEEDS_REVIEW, f"{table.name}: layout")
                )
        for check in checks:
            if check.status is CheckStatus.FAILED:
                reasons.append(
                    (ExtractionReviewReason.CHECK_FAILED, f"{check.id}: {check.message}")
                )
        flagged = sorted(pages & view.pages_needing_review)
        if flagged:
            reasons.append(
                (
                    ExtractionReviewReason.SOURCE_NEEDS_REVIEW,
                    f"OCR/layout review on pages {flagged}",
                )
            )
        return reasons


def _mask(fields: list[FieldResult], tables: list[TableResult]) -> None:
    """Mask Aadhaar numbers in every text of the result (values, raws, evidence)."""
    for f in fields:
        if isinstance(f.value, str):
            f.value = mask_text(f.value)
        f.raw = mask_text(f.raw) if f.raw else f.raw
        for e in f.evidence:
            e.text = mask_text(e.text)
        for c in f.candidates:
            c.raw = mask_text(c.raw)
            if isinstance(c.value, str):
                c.value = mask_text(c.value)
            for e in c.evidence:
                e.text = mask_text(e.text)
    for t in tables:
        for r in [*t.records, *t.summary_rows]:
            for cell in r.values.values():
                cell.raw = mask_text(cell.raw)
                if cell.evidence is not None:
                    cell.evidence.text = mask_text(cell.evidence.text)
