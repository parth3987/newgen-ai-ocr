"""Document ingestion: receive → quarantine → validate → extract/render → commit.

Runs synchronously for now; it moves behind the job queue in Phase 5 (ADR-0012).
On any failure, quarantined bytes and staged artefacts are removed and a
``document.rejected`` audit event is recorded with the error code.
"""

from __future__ import annotations

import hashlib
import logging
import os
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, BinaryIO

import cv2
import numpy as np
import PIL
from PIL import Image

from newgen_ocr import __version__
from newgen_ocr.audit import AuditEvent, AuditLog
from newgen_ocr.config import Settings
from newgen_ocr.errors import AppError, ErrorCode
from newgen_ocr.imaging import loader
from newgen_ocr.imaging.quality import QualityReport, assess
from newgen_ocr.ingestion.formats import (
    MEDIA_TYPES,
    SIGNATURE_BYTES,
    DocumentFormat,
    check_extension,
    detect_format,
    safe_filename,
)
from newgen_ocr.ingestion.models import (
    DocumentManifest,
    OcrReason,
    PageImageInfo,
    PageManifest,
    PageSource,
    ProcessingInfo,
)
from newgen_ocr.pdf import processor as pdf
from newgen_ocr.storage.local import DocumentStore, StagedDocument, new_document_id

logger = logging.getLogger(__name__)

_CHUNK = 1024 * 1024


@dataclass(frozen=True)
class UploadInfo:
    filename: str | None
    content_type: str | None


@dataclass(frozen=True)
class ReceivedFile:
    path: Path
    size: int
    sha256: str
    head: bytes


def receive(source: BinaryIO, dest: Path, max_bytes: int) -> ReceivedFile:
    """Copy ``source`` to ``dest`` (created exclusively, 0600), enforcing the size limit."""
    digest = hashlib.sha256()
    size = 0
    head = b""
    fd = os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as out:
        while chunk := source.read(_CHUNK):
            size += len(chunk)
            if size > max_bytes:
                raise AppError(
                    ErrorCode.FILE_TOO_LARGE, f"File exceeds the {max_bytes}-byte upload limit"
                )
            if len(head) < SIGNATURE_BYTES:
                head += chunk[: SIGNATURE_BYTES - len(head)]
            digest.update(chunk)
            out.write(chunk)
        out.flush()
        os.fsync(out.fileno())
    if size == 0:
        raise AppError(ErrorCode.EMPTY_FILE, "File is empty")
    return ReceivedFile(dest, size, digest.hexdigest(), head)


def _quality(image: Image.Image, dpi: int | None) -> QualityReport:
    report, _ = assess(np.asarray(image), dpi)
    return report


def _write_pages(staged: StagedDocument, pages: Iterable[pdf.ProcessedPage]) -> list[PageManifest]:
    manifests = []
    for page in pages:
        manifest = page.manifest
        if page.image is not None:
            staged.write_page_image(manifest.page_number, page.image)
            dpi = manifest.image.dpi if manifest.image else None
            manifest = manifest.model_copy(update={"quality": _quality(page.image, dpi)})
        if page.text is not None:
            staged.write_page_text(page.text)
        manifests.append(manifest)
    return manifests


def _process_pdf(
    path: Path, staged: StagedDocument, settings: Settings
) -> tuple[list[PageManifest], bool]:
    limits = pdf.PdfLimits(
        max_pages=settings.max_pages,
        max_page_pixels=settings.max_page_pixels,
        render_dpi=settings.render_dpi,
        native_text_min_chars=settings.native_text_min_chars,
    )
    with pdf.open_pdf(path) as document:
        pdf.check_pdf(document, limits)
        encrypted = pdf.is_encrypted(document)
        return _write_pages(staged, pdf.process_pages(document, limits)), encrypted


def _image_dpi(info: Mapping[Any, object]) -> int | None:
    dpi = info.get("dpi")
    if isinstance(dpi, tuple) and dpi and isinstance(dpi[0], int | float) and dpi[0] > 0:
        return round(dpi[0])
    return None


def _process_image(
    path: Path, fmt: DocumentFormat, staged: StagedDocument, settings: Settings
) -> list[PageManifest]:
    limits = loader.ImageLimits(
        max_pages=settings.max_pages, max_page_pixels=settings.max_page_pixels
    )
    manifests = []
    with loader.open_image(path, fmt) as img:
        frames = loader.frame_count(img, limits)
        for index, page in enumerate(loader.iter_pages(img, frames, limits)):
            page_number = index + 1
            staged.write_page_image(page_number, page)
            dpi = _image_dpi(img.info)
            manifests.append(
                PageManifest(
                    page_number=page_number,
                    source=PageSource.IMAGE,
                    needs_ocr=True,
                    ocr_reason=OcrReason.IMAGE_INPUT,
                    image=PageImageInfo(width_px=page.width, height_px=page.height, dpi=dpi),
                    quality=_quality(page, dpi),
                )
            )
    return manifests


class IngestionService:
    def __init__(self, settings: Settings, store: DocumentStore, audit: AuditLog) -> None:
        self.settings = settings
        self.store = store
        self.audit = audit

    def ingest(self, source: BinaryIO, upload: UploadInfo) -> DocumentManifest:
        document_id = new_document_id()
        quarantined = self.store.new_quarantine_path()
        staged: StagedDocument | None = None
        received: ReceivedFile | None = None
        fmt: DocumentFormat | None = None
        try:
            received = receive(source, quarantined, self.settings.max_upload_bytes)
            fmt = detect_format(received.head, self.settings.allowed_formats)
            check_extension(fmt, upload.filename)

            staged = self.store.stage(document_id)
            encrypted = False
            if fmt is DocumentFormat.PDF:
                pages, encrypted = _process_pdf(received.path, staged, self.settings)
            else:
                pages = _process_image(received.path, fmt, staged, self.settings)

            manifest = DocumentManifest(
                document_id=document_id,
                created_at=datetime.now(tz=UTC),
                sha256=received.sha256,
                size_bytes=received.size,
                format=fmt,
                media_type=MEDIA_TYPES[fmt],
                original_filename=safe_filename(upload.filename),
                declared_content_type=(upload.content_type or None),
                pdf_encrypted=encrypted,
                page_count=len(pages),
                pages=pages,
                processing=ProcessingInfo(
                    pipeline_version=__version__,
                    render_dpi=self.settings.render_dpi,
                    native_text_min_chars=self.settings.native_text_min_chars,
                    pdfium_version=pdf.PDFIUM_VERSION if fmt is DocumentFormat.PDF else None,
                    pillow_version=PIL.__version__,
                    opencv_version=cv2.__version__,
                ),
            )
            staged.write_manifest(manifest)
            self.store.commit(document_id, received.path, fmt, staged)
        except BaseException as exc:
            self.store.discard(quarantined, staged)
            code = exc.code if isinstance(exc, AppError) else ErrorCode.INTERNAL_ERROR
            self.audit.record(
                AuditEvent.DOCUMENT_REJECTED,
                error_code=code.value,
                sha256=received.sha256 if received else None,
                size_bytes=received.size if received else None,
                format=fmt.value if fmt else None,
            )
            logger.info("document rejected", extra={"error_code": code.value})
            raise

        self.audit.record(
            AuditEvent.DOCUMENT_ACCEPTED,
            document_id=document_id,
            sha256=manifest.sha256,
            size_bytes=manifest.size_bytes,
            format=fmt.value,
            page_count=manifest.page_count,
            pages_needing_ocr=sum(1 for p in pages if p.needs_ocr),
        )
        logger.info(
            "document accepted",
            extra={
                "document_id": document_id,
                "format": fmt.value,
                "page_count": manifest.page_count,
                "size_bytes": manifest.size_bytes,
            },
        )
        return manifest
