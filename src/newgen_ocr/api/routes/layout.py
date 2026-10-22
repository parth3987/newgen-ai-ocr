"""Layout, reading order, tables and key-value pairs derived from a stored OCR run (ADR-0025).

Runs synchronously in the request for now (ADR-0012). No authentication yet (OD-6).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Path, Request, Response
from pydantic import BaseModel, Field

from newgen_ocr.layout.models import LayoutRun
from newgen_ocr.layout.service import LayoutService
from newgen_ocr.ocr.models import EngineInfo
from newgen_ocr.storage.local import DocumentStore

router = APIRouter(prefix="/api/v1", tags=["layout"])

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


@router.post("/documents/{document_id}/layout", status_code=201, response_model=LayoutRun)
def run_layout(request: Request, document_id: DocumentId, body: LayoutRequest) -> LayoutRun:
    """Analyse layout and tables. Replaces any earlier layout of the same OCR run."""
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
