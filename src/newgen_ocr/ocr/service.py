"""Run OCR over a stored document's pages (ADR-0022).

Page policy:
- native-text PDF pages are not OCRed (their embedded text is used), nor are blank pages;
- the image read is the profile's preprocessed output when one exists, else the source page;
- ``engine="auto"`` uses the primary engine, falling back to the secondary engine per page
  only when the primary is unavailable or fails on that page (recorded on the page);
- an explicitly named engine never falls back;
- ``compare=True`` also runs the secondary engine and records agreement. Results are never
  merged.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Sequence
from datetime import UTC, datetime

import numpy as np
from PIL import Image
from pydantic import BaseModel

from newgen_ocr import __version__
from newgen_ocr.audit import AuditEvent, AuditLog
from newgen_ocr.errors import AppError, ErrorCode
from newgen_ocr.imaging.quality import QualityFlag
from newgen_ocr.imaging.runtime import runtime_info
from newgen_ocr.ingestion.models import DocumentManifest, PageManifest
from newgen_ocr.ocr.compare import compare_runs
from newgen_ocr.ocr.engine import OcrEngine, page_orientation
from newgen_ocr.ocr.models import (
    SUPPORTED_LANGUAGES,
    EngineInfo,
    OcrComparison,
    OcrPage,
    OcrRun,
    PageOcrStatus,
)
from newgen_ocr.storage.local import DocumentStore

logger = logging.getLogger(__name__)

AUTO = "auto"


class EngineStatus(BaseModel):
    name: str
    role: str  # "primary" | "fallback" | "available"
    available: bool
    reason: str | None
    languages: list[str]


class OcrService:
    def __init__(
        self,
        store: DocumentStore,
        audit: AuditLog,
        engines: dict[str, OcrEngine],
        primary: str,
        fallback: str | None,
        disagreement_threshold: float,
        language_primary: dict[str, str] | None = None,
    ) -> None:
        self.store = store
        self.audit = audit
        self.engines = engines
        self.primary = primary
        self.fallback = fallback
        self.threshold = disagreement_threshold
        self.language_primary = dict(language_primary or {})

    def _roles(self, languages: Sequence[str]) -> tuple[str, str | None]:
        """(primary, fallback) for a request; per-language overrides swap the engines."""
        primary = self.language_primary.get(languages[0], self.primary)
        if primary == self.primary:
            return primary, self.fallback
        return primary, self.primary if self.fallback is not None else None

    # ------------------------------------------------------------ engines

    def status(self) -> list[EngineStatus]:
        result = []
        for name, engine in self.engines.items():
            reason = engine.unavailable_reason()
            role = "primary" if name == self.primary else "available"
            if name == self.fallback:
                role = "fallback"
            result.append(
                EngineStatus(
                    name=name,
                    role=role,
                    available=reason is None,
                    reason=reason,
                    languages=sorted(engine.languages()),
                )
            )
        return result

    def _engine(self, name: str) -> OcrEngine:
        if name not in self.engines:
            raise AppError(ErrorCode.UNKNOWN_ENGINE, f"Unknown OCR engine: {name[:40]!r}")
        return self.engines[name]

    def _usable(self, name: str | None, languages: Sequence[str]) -> OcrEngine | None:
        if name is None or name not in self.engines:
            return None
        engine = self.engines[name]
        if engine.unavailable_reason() is not None:
            return None
        if not set(languages[:1]) <= engine.languages():
            return None
        return engine

    @staticmethod
    def _check_languages(languages: Sequence[str]) -> None:
        if not languages:
            raise AppError(ErrorCode.UNSUPPORTED_LANGUAGE, "At least one language is required")
        unknown = [lang for lang in languages if lang not in SUPPORTED_LANGUAGES]
        if unknown:
            raise AppError(
                ErrorCode.UNSUPPORTED_LANGUAGE,
                f"Unsupported language(s): {', '.join(unknown)[:60]}; "
                f"supported: {', '.join(SUPPORTED_LANGUAGES)}",
            )

    # ------------------------------------------------------------ images

    def _image(
        self, document_id: str, page: PageManifest, profile: str | None
    ) -> tuple[np.ndarray, str]:
        if profile is not None and profile in self.store.list_runs(document_id):
            run = self.store.read_run(document_id, profile)
            if run.pages[page.page_number - 1].output is not None:
                path = self.store.preprocessed_image_path(document_id, profile, page.page_number)
                with Image.open(path) as img:
                    return np.asarray(img), f"preprocessed:{profile}"
        path = self.store.page_image_path(document_id, page.page_number)
        with Image.open(path) as img:
            return np.asarray(img), "source"

    # ------------------------------------------------------------ runs

    def _ocr_page(
        self,
        page_number: int,
        image: np.ndarray,
        source: str,
        engine: OcrEngine,
        languages: Sequence[str],
    ) -> OcrPage:
        start = time.perf_counter()
        lines = engine.recognize(image, languages)
        confidences = [line.confidence for line in lines if line.confidence is not None]
        return OcrPage(
            page_number=page_number,
            status=PageOcrStatus.OK if lines else PageOcrStatus.EMPTY,
            engine=engine.name,
            image_source=source,
            image_width=int(image.shape[1]),
            image_height=int(image.shape[0]),
            orientation_deg=page_orientation(lines),
            lines=lines,
            text="\n".join(line.text for line in lines),
            mean_confidence=round(sum(confidences) / len(confidences), 4) if confidences else None,
            duration_ms=round((time.perf_counter() - start) * 1000, 2),
        )

    @staticmethod
    def _skip(page: PageManifest) -> str | None:
        if not page.needs_ocr:
            return "native_text"
        if page.image is None:
            return "no_page_image"
        if page.quality is not None and QualityFlag.BLANK_PAGE in page.quality.flags:
            return "blank_page"
        return None

    def _run_engine(
        self,
        manifest: DocumentManifest,
        requested: str,
        primary: OcrEngine | None,
        fallback: OcrEngine | None,
        primary_down_reason: str | None,
        languages: Sequence[str],
        profile: str | None,
    ) -> OcrRun:
        start = time.perf_counter()
        pages: list[OcrPage] = []
        used: dict[str, EngineInfo] = {}
        for page in manifest.pages:
            skip = self._skip(page)
            if skip is not None:
                pages.append(
                    OcrPage(
                        page_number=page.page_number,
                        status=PageOcrStatus.SKIPPED,
                        skip_reason=skip,
                    )
                )
                continue
            image, source = self._image(manifest.document_id, page, profile)
            result: OcrPage | None = None
            failure: AppError | None = None
            if primary is not None:
                try:
                    result = self._ocr_page(page.page_number, image, source, primary, languages)
                    used.setdefault(primary.name, primary.info(languages))
                except AppError as exc:
                    failure = exc
            if result is None and fallback is not None:
                reason = failure.code.value if failure else (primary_down_reason or "unavailable")
                try:
                    result = self._ocr_page(page.page_number, image, source, fallback, languages)
                    used.setdefault(fallback.name, fallback.info(languages))
                    result.fallback_from = primary.name if primary else self._roles(languages)[0]
                    result.fallback_reason = reason
                except AppError as exc:
                    failure = exc
            if result is None:
                code = failure.code if failure else ErrorCode.OCR_ENGINE_UNAVAILABLE
                logger.warning(
                    "ocr page failed",
                    extra={"document_id": manifest.document_id, "error_code": code.value},
                )
                result = OcrPage(
                    page_number=page.page_number,
                    status=PageOcrStatus.FAILED,
                    error_code=code.value,
                    image_source=source,
                )
            pages.append(result)
        return OcrRun(
            document_id=manifest.document_id,
            requested_engine=requested,
            engines=list(used.values()),
            languages=list(languages),
            preprocessing_profile=profile,
            created_at=datetime.now(tz=UTC),
            pipeline_version=__version__,
            runtime=runtime_info(),
            total_ms=round((time.perf_counter() - start) * 1000, 2),
            pages=pages,
        )

    def run(
        self,
        document_id: str,
        engine: str,
        languages: Sequence[str],
        profile: str | None,
        compare: bool = False,
    ) -> OcrRun:
        self._check_languages(languages)
        manifest = self.store.read_manifest(document_id)
        if profile is not None and profile not in self.store.list_runs(document_id):
            profile = None  # no such run: OCR reads the source pages (recorded as such)

        primary_name, fallback_name = self._roles(languages)
        if engine == AUTO:
            primary = self._usable(primary_name, languages)
            fallback = self._usable(fallback_name, languages)
            if primary is None and fallback is None:
                raise AppError(
                    ErrorCode.OCR_ENGINE_UNAVAILABLE,
                    "No OCR engine is available for the requested languages",
                )
            down = None
            if primary is None:
                down = "primary_unavailable"
            run = self._run_engine(manifest, AUTO, primary, fallback, down, languages, profile)
            secondary_name = fallback_name
        else:
            chosen = self._engine(engine)
            reason = chosen.unavailable_reason()
            if reason is not None:
                raise AppError(ErrorCode.OCR_ENGINE_UNAVAILABLE, f"{engine}: {reason}")
            missing = [lang for lang in languages if lang not in chosen.languages()]
            if missing:
                raise AppError(
                    ErrorCode.UNSUPPORTED_LANGUAGE,
                    f"{engine} has no model for: {', '.join(missing)}",
                )
            run = self._run_engine(manifest, engine, chosen, None, None, languages, profile)
            secondary_name = fallback_name if engine == primary_name else primary_name

        self.store.write_ocr_run(document_id, run.requested_engine, run)
        comparison: OcrComparison | None = None
        if compare:
            secondary = self._usable(secondary_name, languages)
            if secondary is None:
                raise AppError(
                    ErrorCode.OCR_ENGINE_UNAVAILABLE,
                    "Comparison needs a second available engine for these languages",
                )
            other = self._run_engine(
                manifest, secondary.name, secondary, None, None, languages, profile
            )
            self.store.write_ocr_run(document_id, other.requested_engine, other)
            comparison = compare_runs(run, other, self.threshold)
            self.store.write_ocr_comparison(document_id, comparison)

        self.audit.record(
            AuditEvent.OCR_COMPLETED,
            document_id=document_id,
            engine=run.requested_engine,
            engines_used=",".join(e.name for e in run.engines),
            languages=",".join(languages),
            pages_ok=sum(1 for p in run.pages if p.status is PageOcrStatus.OK),
            pages_failed=sum(1 for p in run.pages if p.status is PageOcrStatus.FAILED),
            pages_fallback=sum(1 for p in run.pages if p.fallback_from),
            pages_needing_review=(
                sum(1 for p in comparison.pages if p.needs_review) if comparison else None
            ),
            total_ms=round(run.total_ms),
        )
        logger.info(
            "ocr completed",
            extra={
                "document_id": document_id,
                "engine": run.requested_engine,
                "total_ms": run.total_ms,
            },
        )
        return run
