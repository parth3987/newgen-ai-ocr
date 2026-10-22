"""Document upload and retrieval.

No authentication yet (DECISIONS.md OD-6): bind to localhost or a trusted network only.
Handlers are synchronous so blocking file and PDF work runs in the thread pool.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, File, Path, Request, UploadFile
from fastapi.responses import FileResponse

from newgen_ocr.ingestion.models import DocumentManifest, PageText
from newgen_ocr.ingestion.service import IngestionService, UploadInfo
from newgen_ocr.storage.local import DocumentStore

router = APIRouter(prefix="/api/v1/documents", tags=["documents"])

DocumentId = Annotated[str, Path(pattern=r"^[0-9a-f]{32}$")]
PageNumber = Annotated[int, Path(ge=1)]


def _service(request: Request) -> IngestionService:
    service: IngestionService = request.app.state.ingestion
    return service


def _store(request: Request) -> DocumentStore:
    store: DocumentStore = request.app.state.store
    return store


@router.post("", status_code=201, response_model=DocumentManifest)
def upload_document(request: Request, file: Annotated[UploadFile, File()]) -> DocumentManifest:
    """Upload one PDF, JPEG, PNG or TIFF file. Returns the document manifest."""
    return _service(request).ingest(
        file.file, UploadInfo(filename=file.filename, content_type=file.content_type)
    )


@router.get("/{document_id}", response_model=DocumentManifest)
def get_document(request: Request, document_id: DocumentId) -> DocumentManifest:
    return _store(request).read_manifest(document_id)


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
