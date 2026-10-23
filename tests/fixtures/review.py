"""Builders for review tests: calibrations, second-engine runs, services and principals."""

from __future__ import annotations

from datetime import UTC, date, datetime

from newgen_ocr.audit import AuditLog
from newgen_ocr.auth.tokens import Principal, Role
from newgen_ocr.config import Settings
from newgen_ocr.extraction.extract import Options
from newgen_ocr.extraction.models import ExtractionResult
from newgen_ocr.extraction.schema import FieldType, SchemaRegistry
from newgen_ocr.extraction.service import ExtractionService
from newgen_ocr.ocr.models import OcrComparison, OcrLine, PageComparison
from newgen_ocr.review.acceptance import Policy
from newgen_ocr.review.calibration import content_id
from newgen_ocr.review.models import (
    Calibration,
    CalibrationDataset,
    CalibrationEntry,
    CalibrationKind,
    CalibrationStatus,
)
from newgen_ocr.review.service import ReviewService
from newgen_ocr.review.store import ReviewStore
from newgen_ocr.storage.local import DocumentStore

TODAY = date(2024, 6, 1)


def calibration(threshold: float = 0.9, missing: bool = True) -> Calibration:
    """Type-level thresholds for every field type (and calibrated absence when ``missing``)."""
    registry = SchemaRegistry()
    entries: dict[str, CalibrationEntry] = {}
    for kind in FieldType:
        key = f"value:type:{kind.value}"
        entries[key] = CalibrationEntry(
            key=key,
            kind=CalibrationKind.VALUE,
            level="type",
            status=CalibrationStatus.CALIBRATED,
            threshold=threshold,
            observations=100,
            accepted=90,
            correct=90,
            precision=1.0,
            lower_bound=0.97,
            coverage=0.9,
        )
        if missing:
            key = f"missing:type:{kind.value}"
            entries[key] = CalibrationEntry(
                key=key,
                kind=CalibrationKind.MISSING,
                level="type",
                status=CalibrationStatus.CALIBRATED,
                threshold=0.0,
                observations=80,
                accepted=80,
                correct=80,
            )
    out = Calibration(
        id="",
        created_at=datetime.now(tz=UTC),
        pipeline_version="test",
        dataset=CalibrationDataset(name="test", kind="synthetic", samples=1),
        target_precision=0.95,
        confidence_level=0.95,
        min_samples=50,
        schema_versions={s.id: s.version for s in registry.schemas.values()},
        entries=entries,
    )
    out.id = content_id(out)
    return out


def second_engine(
    store: DocumentStore, document_id: str, replace: dict[str, str], tables: bool = True
) -> None:
    """Store a 'tesseract' run reading the same lines and table cells (with substitutions)
    plus the comparison that links it to the 'auto' run."""
    primary = store.read_ocr_run(document_id, "auto")
    other = primary.model_copy(deep=True)
    other.requested_engine = "tesseract"
    for page in other.pages:
        page.engine = "tesseract"
        sources = [(line.text, line.bbox) for line in page.lines]
        if tables:
            layout = store.read_layout_run(document_id, "auto")
            sources += [(c.text, c.bbox) for t in layout.pages[0].tables for c in t.cells if c.bbox]
        lines = []
        for text, bbox in sources:
            for old, new in replace.items():
                text = text.replace(old, new)
            lines.append(OcrLine(text=text, confidence=0.9, bbox=bbox))
        page.lines = lines
    store.write_ocr_run(document_id, "tesseract", other)
    store.write_ocr_comparison(
        document_id,
        OcrComparison(
            document_id=document_id,
            primary="auto",
            secondary="tesseract",
            threshold=0.85,
            created_at=datetime.now(tz=UTC),
            pages=[
                PageComparison(page_number=1, agreement=0.99, needs_review=False, reason="agree")
            ],
        ),
    )


def extract(settings: Settings, store: DocumentStore, document_id: str) -> ExtractionResult:
    service = ExtractionService(store, AuditLog(settings.data_dir), SchemaRegistry(), Options())
    return service.extract(document_id, "auto", today=TODAY)


def review_service(
    settings: Settings,
    store: DocumentStore,
    cal: Calibration | None = None,
    auto_accept: bool = False,
    second_person: bool = True,
) -> ReviewService:
    return ReviewService(
        store,
        ReviewStore(settings.data_dir),
        AuditLog(settings.data_dir),
        SchemaRegistry(),
        Policy(calibration=cal, auto_accept=auto_accept),
        require_second_person=second_person,
    )


def who(user: str, role: Role) -> Principal:
    return Principal(user=user, role=role, token_id=f"{0:012x}")  # a fake public token id
