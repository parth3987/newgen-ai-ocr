"""Structured extraction from a stored layout run, and its exports (ADR-0026).

Rule- and template-based only: no LLM or external service is involved. Runs synchronously
in the request in local mode (ADR-0012) and as a job (``202``) in service mode (ADR-0029).
Every endpoint needs a bearer token; submitters reach only their own documents.
"""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Path, Query, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from newgen_ocr.api.auth import principal, require_document
from newgen_ocr.api.routes.jobs import QUEUED, IdempotencyKey, accepted, jobs_service
from newgen_ocr.audit import AuditEvent, AuditLog
from newgen_ocr.auth.tokens import Principal
from newgen_ocr.errors import AppError, ErrorCode
from newgen_ocr.extraction import export
from newgen_ocr.extraction.models import ExtractionResult
from newgen_ocr.extraction.schema import DocumentSchema
from newgen_ocr.extraction.service import ExtractionService
from newgen_ocr.jobs.models import JobParams, StageRequest
from newgen_ocr.review.service import ReviewService
from newgen_ocr.storage.local import DocumentStore

router = APIRouter(
    prefix="/api/v1", tags=["extraction"], dependencies=[Depends(require_document())]
)

DocumentId = Annotated[str, Path(pattern=r"^[0-9a-f]{32}$")]
RunName = Annotated[str, Path(pattern=r"^[a-z][a-z0-9_]{0,31}$")]
SchemaId = Annotated[str, Path(pattern=r"^[a-z][a-z0-9_]{0,47}$")]


class ExtractionRequest(BaseModel):
    layout_run: str = Field(default="auto", pattern=r"^[a-z][a-z0-9_]{0,31}$")
    # A schema id, or "auto" to classify the document.
    document_type: str = Field(default="auto", pattern=r"^[a-z][a-z0-9_]{0,47}$")


class SchemaSummary(BaseModel):
    id: str
    version: str
    title: str
    fields: list[str]
    tables: list[str]


def _service(request: Request) -> ExtractionService:
    service: ExtractionService = request.app.state.extraction
    return service


def _store(request: Request) -> DocumentStore:
    store: DocumentStore = request.app.state.store
    return store


@router.get("/extraction/schemas", response_model=list[SchemaSummary])
def list_schemas(request: Request) -> list[SchemaSummary]:
    return [
        SchemaSummary(
            id=s.id,
            version=s.version,
            title=s.title,
            fields=[f.name for f in s.fields],
            tables=[t.name for t in s.tables],
        )
        for s in _service(request).registry.schemas.values()
    ]


@router.get("/extraction/schemas/{schema_id}", response_model=DocumentSchema)
def get_schema(request: Request, schema_id: SchemaId) -> DocumentSchema:
    schema = _service(request).registry.get(schema_id)
    if schema is None:
        raise AppError(ErrorCode.UNKNOWN_DOCUMENT_TYPE, "Unknown document type")
    return schema


@router.post(
    "/documents/{document_id}/extraction",
    status_code=201,
    response_model=ExtractionResult,
    responses=QUEUED,
)
def run_extraction(
    request: Request,
    document_id: DocumentId,
    body: ExtractionRequest,
    who: Annotated[Principal, Depends(principal)],
    idempotency_key: IdempotencyKey = None,
) -> ExtractionResult | JSONResponse:
    """Classify and extract. Replaces any earlier extraction of the same layout run, and
    queues the result for verification and review (``/api/v1/review/items/...``)."""
    jobs = jobs_service(request)
    if jobs is not None:
        if body.layout_run not in ("auto", "paddle", "tesseract"):
            raise AppError(
                ErrorCode.LAYOUT_RUN_NOT_FOUND, "Jobs use the auto, paddle or tesseract run"
            )
        params = JobParams(
            engine=body.layout_run,
            document_type=body.document_type,
        )
        stage = StageRequest(kind="extraction", params=params)
        return accepted(jobs.submit_stage(document_id, stage, who, idempotency_key))
    result = _service(request).extract(document_id, body.layout_run, body.document_type)
    reviews: ReviewService = request.app.state.review
    reviews.enqueue(result)
    return result


@router.get("/documents/{document_id}/extraction", response_model=list[str])
def list_extractions(request: Request, document_id: DocumentId) -> list[str]:
    return _store(request).list_extractions(document_id)


@router.get("/documents/{document_id}/extraction/{layout_run}", response_model=ExtractionResult)
def get_extraction(
    request: Request, document_id: DocumentId, layout_run: RunName
) -> ExtractionResult:
    return _store(request).read_extraction(document_id, layout_run)


_MEDIA = {
    "json": "application/json",
    "csv": "text/csv; charset=utf-8",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}


@router.get(
    "/documents/{document_id}/extraction/{layout_run}/export",
    response_class=Response,
    responses={200: {"content": {m: {} for m in _MEDIA.values()}}},
)
def export_extraction(
    request: Request,
    document_id: DocumentId,
    layout_run: RunName,
    format: Literal["json", "csv", "xlsx"] = "json",
    table: Annotated[str | None, Query(pattern=r"^[a-z][a-z0-9_]{0,47}$")] = None,
) -> Response:
    """Download the result: JSON (complete), CSV (fields, or one table with ``table=``), or
    Excel (summary, fields, tables, checks and validation sheets)."""
    result = _store(request).read_extraction(document_id, layout_run)
    name = f"{document_id}-{layout_run}"
    if format == "json":
        content = export.to_json(result)
    elif format == "xlsx":
        content = export.to_xlsx(result)
    elif table is None:
        content = export.fields_csv(result)
    else:
        found = export.table_csv(result, table)
        if found is None:
            raise AppError(ErrorCode.UNSUPPORTED_EXPORT, "No such table in this result")
        content, name = found, f"{name}-{table}"
    audit: AuditLog = request.app.state.audit
    audit.record(
        AuditEvent.EXTRACTION_EXPORTED,
        document_id=document_id,
        layout_run=layout_run,
        format=format,
        table=table,
    )
    return Response(
        content=content,
        media_type=_MEDIA[format],
        headers={"Content-Disposition": f'attachment; filename="{name}.{format}"'},
    )
