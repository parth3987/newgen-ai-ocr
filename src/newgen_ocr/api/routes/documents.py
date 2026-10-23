"""Document upload, listing, status and retrieval.

Every endpoint needs a bearer token; submitters reach only their own documents (ADR-0029).
Local mode processes an upload in the request (``201`` with the manifest); service mode
checks it, queues the pipeline and returns ``202`` with the job. Handlers are synchronous
so blocking file and PDF work runs in the thread pool.
"""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, File, Form, Path, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse

from newgen_ocr.api.auth import principal, require_document
from newgen_ocr.api.routes.jobs import QUEUED, IdempotencyKey, Limit, Offset, accepted, jobs_service
from newgen_ocr.auth.tokens import Principal
from newgen_ocr.config import Settings
from newgen_ocr.errors import AppError, ErrorCode
from newgen_ocr.ingestion.models import DocumentManifest, PageText
from newgen_ocr.ingestion.service import IngestionService, UploadInfo
from newgen_ocr.jobs.models import DocumentSummary, JobParams, Page
from newgen_ocr.jobs.service import sees_everything
from newgen_ocr.preprocessing.service import PreprocessingService
from newgen_ocr.storage.local import DocumentStore

router = APIRouter(
    prefix="/api/v1/documents", tags=["documents"], dependencies=[Depends(require_document())]
)

DocumentId = Annotated[str, Path(pattern=r"^[0-9a-f]{32}$")]
PageNumber = Annotated[int, Path(ge=1)]
Who = Annotated[Principal, Depends(principal)]


def _service(request: Request) -> IngestionService:
    service: IngestionService = request.app.state.ingestion
    return service


def _preprocessing(request: Request) -> PreprocessingService:
    service: PreprocessingService = request.app.state.preprocessing
    return service


def _store(request: Request) -> DocumentStore:
    store: DocumentStore = request.app.state.store
    return store


def _languages(value: str | None) -> list[str] | None:
    if value is None or not value.strip():
        return None
    return [code.strip() for code in value.split(",") if code.strip()]


@router.post("", status_code=201, response_model=DocumentManifest, responses=QUEUED)
def upload_document(
    request: Request,
    who: Who,
    file: Annotated[UploadFile, File()],
    profile: Annotated[str | None, Form(max_length=32)] = None,
    engine: Annotated[Literal["auto", "paddle", "tesseract"], Form()] = "auto",
    languages: Annotated[str | None, Form(max_length=64)] = None,
    handwriting: Annotated[bool, Form()] = False,
    compare: Annotated[bool, Form()] = True,
    document_type: Annotated[str, Form(pattern=r"^[a-z][a-z0-9_]{0,47}$")] = "auto",
    force: Annotated[bool, Form()] = False,
    idempotency_key: IdempotencyKey = None,
) -> DocumentManifest | JSONResponse:
    """Upload one PDF, JPEG, PNG or TIFF file.

    Local mode: stores and preprocesses it with ``profile`` and returns the manifest
    (``201``). Service mode: queues the whole pipeline (ingest, preprocessing, OCR with
    ``engine``/``languages`` (comma-separated, or ``auto``), layout, extraction as
    ``document_type``, then review) and returns the job (``202``). The same file from the
    same user returns the earlier job unless ``force`` is true.
    """
    upload = UploadInfo(filename=file.filename, content_type=file.content_type)
    jobs = jobs_service(request)
    if jobs is not None:
        params = JobParams(
            profile=profile,
            engine=engine,
            languages=_languages(languages),
            compare=compare,
            handwriting=handwriting,
            document_type=document_type,
        )
        return accepted(jobs.submit_upload(file.file, upload, params, who, idempotency_key, force))
    settings: Settings = request.app.state.settings
    preprocessing = _preprocessing(request)
    # Validate the profile before storing anything.
    selected = preprocessing.profile(profile or settings.default_preprocessing_profile)
    manifest = _service(request).ingest(file.file, upload, submitted_by=who.user)
    preprocessing.run_after_ingest(manifest.document_id, selected.name)
    return manifest


@router.get("", response_model=Page[DocumentSummary])
def list_documents(
    request: Request,
    who: Who,
    status: Annotated[Literal["received", "ready", "rejected"] | None, Query()] = None,
    limit: Limit = 50,
    offset: Offset = 0,
) -> Page[DocumentSummary]:
    """Newest first. Submitters see their own documents; other roles see every document."""
    jobs = jobs_service(request)
    if jobs is not None:
        return jobs.documents(who, status, limit, offset)
    summaries = [
        _local_summary(m)
        for m in _store(request).manifests()
        if sees_everything(who) or m.submitted_by == who.user
    ]
    summaries = [s for s in summaries if status in (None, s.status)]
    summaries.sort(key=lambda s: s.created_at, reverse=True)
    return Page[DocumentSummary](
        items=summaries[offset : offset + limit], total=len(summaries), limit=limit, offset=offset
    )


@router.get("/{document_id}/status", response_model=DocumentSummary)
def get_status(request: Request, document_id: DocumentId, who: Who) -> DocumentSummary:
    """Whether the document is stored and processed, with its latest job (service mode)."""
    jobs = jobs_service(request)
    if jobs is not None:
        return jobs.document(document_id, who)
    return _local_summary(_store(request).read_manifest(document_id))


@router.get("/{document_id}", response_model=DocumentManifest)
def get_document(request: Request, document_id: DocumentId) -> DocumentManifest:
    store = _store(request)
    if jobs_service(request) is not None and not store.has_document(document_id):
        # Accepted but not yet ingested by a worker (or rejected): see /status.
        summary = request.app.state.jobs.repo.document(document_id)
        if summary is not None:
            raise AppError(ErrorCode.DOCUMENT_NOT_READY, f"The document is {summary.status}")
    return store.read_manifest(document_id)


@router.get("/{document_id}/pages/{page_number}/text", response_model=PageText)
def get_page_text(request: Request, document_id: DocumentId, page_number: PageNumber) -> PageText:
    """Embedded PDF text with segment bounding boxes (points, top-left origin)."""
    return _store(request).read_page_text(document_id, page_number)


@router.get(
    "/{document_id}/pages/{page_number}/image",
    response_class=FileResponse,
    responses={200: {"content": {"image/png": {}}}},
)
def get_page_image(
    request: Request, document_id: DocumentId, page_number: PageNumber
) -> FileResponse:
    """Rendered (PDF) or normalised (image upload) page image, as PNG."""
    path = _store(request).page_image_path(document_id, page_number)
    return FileResponse(path, media_type="image/png")


def _local_summary(manifest: DocumentManifest) -> DocumentSummary:
    return DocumentSummary(
        id=manifest.document_id,
        status="ready",
        sha256=manifest.sha256,
        size_bytes=manifest.size_bytes,
        format=manifest.format.value,
        page_count=manifest.page_count,
        submitted_by=manifest.submitted_by or "",
        error_code=None,
        created_at=manifest.created_at,
        updated_at=manifest.created_at,
    )
