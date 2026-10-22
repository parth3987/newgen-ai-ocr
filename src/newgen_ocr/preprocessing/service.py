"""Run a preprocessing profile over every page of a stored document.

Reads source page images from the document store, writes outputs to a separate
per-profile tree and records decisions, quality and timings in ``run.json``.
"""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime

import numpy as np
from PIL import Image

from newgen_ocr import __version__
from newgen_ocr.audit import AuditEvent, AuditLog
from newgen_ocr.errors import AppError, ErrorCode
from newgen_ocr.imaging.quality import assess
from newgen_ocr.imaging.runtime import runtime_info
from newgen_ocr.preprocessing.models import PagePreprocessing, PageStatus, PreprocessingRun
from newgen_ocr.preprocessing.pipeline import preprocess_page
from newgen_ocr.preprocessing.profiles import ProfileConfig
from newgen_ocr.storage.local import DocumentStore

logger = logging.getLogger(__name__)


class PreprocessingService:
    def __init__(
        self, store: DocumentStore, audit: AuditLog, profiles: dict[str, ProfileConfig]
    ) -> None:
        self.store = store
        self.audit = audit
        self.profiles = profiles

    def profile(self, name: str) -> ProfileConfig:
        try:
            return self.profiles[name]
        except KeyError:
            raise AppError(
                ErrorCode.UNKNOWN_PROFILE, f"Unknown preprocessing profile: {name[:40]!r}"
            ) from None

    def run(self, document_id: str, profile_name: str) -> PreprocessingRun:
        profile = self.profile(profile_name)
        manifest = self.store.read_manifest(document_id)
        start = time.perf_counter()
        staged = self.store.stage_run(document_id)
        committed = False
        try:
            pages = []
            for page in manifest.pages:
                if page.image is None:
                    pages.append(
                        PagePreprocessing(
                            page_number=page.page_number,
                            status=PageStatus.SKIPPED,
                            skip_reason="no_page_image",
                        )
                    )
                    continue
                path = self.store.page_image_path(document_id, page.page_number)
                with Image.open(path) as img:
                    pixels = np.asarray(img)
                quality = page.quality or assess(pixels, page.image.dpi)[0]
                output, record = preprocess_page(
                    page.page_number, pixels, page.image.dpi, profile, quality
                )
                if output is not None:
                    staged.write_page_image(page.page_number, output)
                pages.append(record)

            run = PreprocessingRun(
                document_id=document_id,
                profile=profile,
                created_at=datetime.now(tz=UTC),
                pipeline_version=__version__,
                runtime=runtime_info(),
                total_ms=round((time.perf_counter() - start) * 1000, 2),
                pages=pages,
            )
            staged.write_run(run)
            self.store.commit_run(document_id, profile.name, staged)
            committed = True
        finally:
            if not committed:
                self.store.discard_run(staged)

        processed = sum(1 for p in pages if p.status is PageStatus.PROCESSED)
        self.audit.record(
            AuditEvent.PREPROCESSING_COMPLETED,
            document_id=document_id,
            profile=profile.name,
            pages_processed=processed,
            total_ms=round(run.total_ms),
        )
        logger.info(
            "preprocessing completed",
            extra={
                "document_id": document_id,
                "profile": profile.name,
                "pages_processed": processed,
                "total_ms": run.total_ms,
            },
        )
        return run

    def run_after_ingest(self, document_id: str, profile_name: str) -> None:
        """Best-effort run after upload: the document is already stored, so a failure here is
        logged and audited rather than failing the upload. The run can be repeated via the API.
        """
        try:
            self.run(document_id, profile_name)
        except Exception as exc:
            code = exc.code if isinstance(exc, AppError) else ErrorCode.INTERNAL_ERROR
            logger.exception("preprocessing failed", extra={"document_id": document_id})
            self.audit.record(
                AuditEvent.PREPROCESSING_FAILED,
                document_id=document_id,
                profile=profile_name,
                error_code=code.value,
            )
