"""OCR engines and per-document OCR runs.

Local mode runs OCR in the request (ADR-0012; expect seconds per page on CPU). Service mode
queues it, with layout and extraction after it, as a job (``202``; ADR-0029). Every
endpoint needs a bearer token; submitters reach only their own documents.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from newgen_ocr.api.auth import principal, require_document
from newgen_ocr.api.routes.jobs import QUEUED, IdempotencyKey, accepted, jobs_service
from newgen_ocr.auth.tokens import Principal
from newgen_ocr.config import Settings
from newgen_ocr.errors import AppError, ErrorCode
from newgen_ocr.jobs.models import JobParams, StageRequest
from newgen_ocr.ocr.languages import LANGUAGES, LanguageInfo
from newgen_ocr.ocr.models import OcrComparison, OcrRun
from newgen_ocr.ocr.service import EngineStatus, OcrService
from newgen_ocr.storage.local import DocumentStore

router = APIRouter(prefix="/api/v1", tags=["ocr"], dependencies=[Depends(require_document())])

DocumentId = Annotated[str, Path(pattern=r"^[0-9a-f]{32}$")]
RunName = Annotated[str, Path(pattern=r"^[a-z][a-z0-9_]{0,31}$")]


class OcrRequest(BaseModel):
    engine: str = Field(default="auto", pattern=r"^[a-z][a-z0-9_]{0,31}$")
    # ISO 639-1 codes, or ["auto"] to detect the script per page (ADR-0023).
    languages: list[str] | None = Field(default=None, min_length=1, max_length=6)
    # Use the handwriting engine; languages without one are returned as unsupported.
    handwriting: bool = False
    # Preprocessing profile whose output images to read; None = configured default profile.
    preprocessing_profile: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_]{0,31}$")
    compare: bool = False


def _service(request: Request) -> OcrService:
    service: OcrService = request.app.state.ocr
    return service


def _store(request: Request) -> DocumentStore:
    store: DocumentStore = request.app.state.store
    return store


@router.get("/languages", response_model=list[LanguageInfo])
def list_languages() -> list[LanguageInfo]:
    """The supported-language matrix (docs/LANGUAGE_SUPPORT.md)."""
    return list(LANGUAGES.values())


@router.get("/ocr/engines", response_model=list[EngineStatus])
def list_engines(request: Request) -> list[EngineStatus]:
    return _service(request).status()


@router.post(
    "/documents/{document_id}/ocr", status_code=201, response_model=OcrRun, responses=QUEUED
)
def run_ocr(
    request: Request,
    document_id: DocumentId,
    body: OcrRequest,
    who: Annotated[Principal, Depends(principal)],
    idempotency_key: IdempotencyKey = None,
) -> OcrRun | JSONResponse:
    """Run OCR. Replaces any earlier run for the same engine name. In service mode the job
    also re-runs layout and extraction on the new OCR."""
    jobs = jobs_service(request)
    if jobs is not None:
        if body.engine not in ("auto", "paddle", "tesseract"):
            raise AppError(
                ErrorCode.UNKNOWN_ENGINE, "Jobs run the auto, paddle or tesseract engine"
            )
        params = JobParams(
            profile=body.preprocessing_profile,
            engine=body.engine,
            languages=body.languages,
            compare=body.compare,
            handwriting=body.handwriting,
        )
        stage = StageRequest(kind="ocr", params=params)
        return accepted(jobs.submit_stage(document_id, stage, who, idempotency_key))
    settings: Settings = request.app.state.settings
    return _service(request).run(
        document_id,
        body.engine,
        body.languages or list(settings.ocr_default_languages),
        body.preprocessing_profile or settings.default_preprocessing_profile,
        compare=body.compare,
        handwriting=body.handwriting,
    )


@router.get("/documents/{document_id}/ocr", response_model=list[str])
def list_runs(request: Request, document_id: DocumentId) -> list[str]:
    return _store(request).list_ocr_runs(document_id)


@router.get("/documents/{document_id}/ocr/comparison", response_model=OcrComparison)
def get_comparison(request: Request, document_id: DocumentId) -> OcrComparison:
    return _store(request).read_ocr_comparison(document_id)


@router.get("/documents/{document_id}/ocr/{engine}", response_model=OcrRun)
def get_run(request: Request, document_id: DocumentId, engine: RunName) -> OcrRun:
    return _store(request).read_ocr_run(document_id, engine)
