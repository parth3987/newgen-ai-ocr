"""Layout, reading order, tables and key-value pairs derived from a stored OCR run (ADR-0025).

Local mode runs in the request (ADR-0012); service mode queues layout and extraction as a
job (``202``; ADR-0029). Every endpoint needs a bearer token; submitters reach only their
own documents.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from newgen_ocr.api.auth import principal, require_document
from newgen_ocr.api.routes.jobs import QUEUED, IdempotencyKey, accepted, jobs_service
from newgen_ocr.auth.tokens import Principal
from newgen_ocr.errors import AppError, ErrorCode
from newgen_ocr.jobs.models import JobParams, StageRequest
from newgen_ocr.layout.models import LayoutRun
from newgen_ocr.layout.service import LayoutService
from newgen_ocr.ocr.models import EngineInfo
from newgen_ocr.storage.local import DocumentStore

router = APIRouter(prefix="/api/v1", tags=["layout"], dependencies=[Depends(require_document())])

DocumentId = Annotated[str, Path(pattern=r"^[0-9a-f]{32}$")]
RunName = Annotated[str, Path(pattern=r"^[a-z][a-z0-9_]{0,31}$")]
PageNumber = Annotated[int, Path(ge=1)]


class LayoutRequest(BaseModel):
    # Name of the stored OCR run to analyse (the engine it was requested with).
    ocr_run: str = Field(default="auto", pattern=r"^[a-z][a-z0-9_]{0,31}$")


class LayoutStatus(BaseModel):
    available: bool
    reason: str | None
    models: list[EngineInfo]


def _service(request: Request) -> LayoutService:
    service: LayoutService = request.app.state.layout
    return service


def _store(request: Request) -> DocumentStore:
    store: DocumentStore = request.app.state.store
    return store


@router.get("/layout/status", response_model=LayoutStatus)
def layout_status(request: Request) -> LayoutStatus:
    service = _service(request)
    reason = service.status()
    return LayoutStatus(
        available=reason is None,
        reason=reason,
        models=service.models.info() if reason is None else [],
    )


@router.post(
    "/documents/{document_id}/layout", status_code=201, response_model=LayoutRun, responses=QUEUED
)
def run_layout(
    request: Request,
    document_id: DocumentId,
    body: LayoutRequest,
    who: Annotated[Principal, Depends(principal)],
    idempotency_key: IdempotencyKey = None,
) -> LayoutRun | JSONResponse:
    """Analyse layout and tables. Replaces any earlier layout of the same OCR run. In service
    mode the job also re-runs extraction on the new layout."""
    jobs = jobs_service(request)
    if jobs is not None:
        if body.ocr_run not in ("auto", "paddle", "tesseract"):
            raise AppError(
                ErrorCode.OCR_RUN_NOT_FOUND, "Jobs use the auto, paddle or tesseract run"
            )
        params = JobParams(engine=body.ocr_run)
        stage = StageRequest(kind="layout", params=params)
        return accepted(jobs.submit_stage(document_id, stage, who, idempotency_key))
    return _service(request).analyze(document_id, body.ocr_run)


@router.get("/documents/{document_id}/layout", response_model=list[str])
def list_layouts(request: Request, document_id: DocumentId) -> list[str]:
    return _store(request).list_layout_runs(document_id)


@router.get("/documents/{document_id}/layout/{ocr_run}", response_model=LayoutRun)
def get_layout(request: Request, document_id: DocumentId, ocr_run: RunName) -> LayoutRun:
    return _store(request).read_layout_run(document_id, ocr_run)


@router.get(
    "/documents/{document_id}/layout/{ocr_run}/pages/{page_number}/overlay",
    response_class=Response,
    responses={200: {"content": {"image/png": {}}}},
)
def get_overlay(
    request: Request, document_id: DocumentId, ocr_run: RunName, page_number: PageNumber
) -> Response:
    """The page image with regions, reading order and table cells drawn (for inspection)."""
    png = _service(request).overlay(document_id, ocr_run, page_number)
    return Response(content=png, media_type="image/png")
