"""Preprocessing profiles and per-document preprocessing runs.

Every endpoint needs a bearer token; submitters reach only their own documents. In service
mode a run is queued as a job (``202``) instead of running in the request (ADR-0029).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Request
from fastapi.responses import FileResponse, JSONResponse

from newgen_ocr.api.auth import principal, require_document
from newgen_ocr.api.routes.jobs import QUEUED, IdempotencyKey, accepted, jobs_service
from newgen_ocr.auth.tokens import Principal
from newgen_ocr.jobs.models import JobParams, StageRequest
from newgen_ocr.preprocessing.models import PreprocessingRun
from newgen_ocr.preprocessing.profiles import ProfileConfig
from newgen_ocr.preprocessing.service import PreprocessingService
from newgen_ocr.storage.local import DocumentStore

router = APIRouter(
    prefix="/api/v1", tags=["preprocessing"], dependencies=[Depends(require_document())]
)

DocumentId = Annotated[str, Path(pattern=r"^[0-9a-f]{32}$")]
ProfileName = Annotated[str, Path(pattern=r"^[a-z][a-z0-9_]{0,31}$")]
PageNumber = Annotated[int, Path(ge=1)]


def _service(request: Request) -> PreprocessingService:
    service: PreprocessingService = request.app.state.preprocessing
    return service


def _store(request: Request) -> DocumentStore:
    store: DocumentStore = request.app.state.store
    return store


@router.get("/preprocessing/profiles", response_model=list[ProfileConfig])
def list_profiles(request: Request) -> list[ProfileConfig]:
    return list(_service(request).profiles.values())


@router.get("/documents/{document_id}/preprocessing", response_model=list[str])
def list_runs(request: Request, document_id: DocumentId) -> list[str]:
    """Profiles that have been run for this document."""
    return _store(request).list_runs(document_id)


@router.post(
    "/documents/{document_id}/preprocessing/{profile}",
    status_code=201,
    response_model=PreprocessingRun,
    responses=QUEUED,
)
def run_profile(
    request: Request,
    document_id: DocumentId,
    profile: ProfileName,
    who: Annotated[Principal, Depends(principal)],
    idempotency_key: IdempotencyKey = None,
) -> PreprocessingRun | JSONResponse:
    """(Re)run a profile. Replaces any earlier run of the same profile; other profiles and
    the source page images are untouched."""
    jobs = jobs_service(request)
    if jobs is not None:
        stage = StageRequest(kind="preprocessing", params=JobParams(profile=profile))
        return accepted(jobs.submit_stage(document_id, stage, who, idempotency_key))
    return _service(request).run(document_id, profile)


@router.get("/documents/{document_id}/preprocessing/{profile}", response_model=PreprocessingRun)
def get_run(request: Request, document_id: DocumentId, profile: ProfileName) -> PreprocessingRun:
    return _store(request).read_run(document_id, profile)


@router.get(
    "/documents/{document_id}/preprocessing/{profile}/pages/{page_number}/image",
    response_class=FileResponse,
    responses={200: {"content": {"image/png": {}}}},
)
def get_preprocessed_image(
    request: Request, document_id: DocumentId, profile: ProfileName, page_number: PageNumber
) -> FileResponse:
    path = _store(request).preprocessed_image_path(document_id, profile, page_number)
    return FileResponse(path, media_type="image/png")
