"""OCR result schema (docs/API_SPECIFICATION.md). Identical for every engine.

Coordinates are image pixels with a top-left origin, in the image the engine actually read
(``image_source``). Confidence is normalised to 0..1, or ``None`` when the engine gives none.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel

from newgen_ocr.ingestion.models import BoundingBox
from newgen_ocr.preprocessing.models import RuntimeInfo

# ISO 639-1 codes accepted by the API.
SUPPORTED_LANGUAGES = ("en", "hi", "mr", "te", "ta", "kn")


class OcrWord(BaseModel):
    text: str
    confidence: float | None
    bbox: BoundingBox


class OcrLine(BaseModel):
    text: str
    confidence: float | None
    bbox: BoundingBox
    polygon: list[tuple[float, float]] | None = None
    words: list[OcrWord] | None = None
    # Text-line orientation as classified by the engine (0 or 180); None if not classified.
    orientation_deg: int | None = None


class ModelRef(BaseModel):
    id: str
    revision: str
    files: dict[str, str]  # filename -> sha256


class EngineInfo(BaseModel):
    name: str
    version: str
    models: list[ModelRef]
    parameters: dict[str, str | int | float | bool]


class PageOcrStatus(StrEnum):
    OK = "ok"
    EMPTY = "empty"  # engine ran and found no text
    SKIPPED = "skipped"  # see skip_reason
    FAILED = "failed"  # see error_code


class OcrPage(BaseModel):
    page_number: int
    status: PageOcrStatus
    skip_reason: str | None = None  # "native_text", "blank_page", "no_page_image"
    error_code: str | None = None
    engine: str | None = None  # engine whose result this is
    fallback_from: str | None = None  # primary engine, when the fallback produced the result
    fallback_reason: str | None = None
    image_source: str | None = None  # "source" or "preprocessed:<profile>"
    image_width: int | None = None
    image_height: int | None = None
    # 180 when most classified lines were upside down (lines are then ordered for the
    # upright page); 0 when upright; None when the engine does not classify orientation.
    orientation_deg: int | None = None
    lines: list[OcrLine] = []
    text: str = ""
    mean_confidence: float | None = None
    duration_ms: float = 0.0


class OcrRun(BaseModel):
    document_id: str
    requested_engine: str  # "auto", "paddle" or "tesseract"
    engines: list[EngineInfo]  # every engine that produced at least one page
    languages: list[str]
    preprocessing_profile: str | None
    created_at: datetime
    pipeline_version: str
    runtime: RuntimeInfo
    total_ms: float
    pages: list[OcrPage]


class PageComparison(BaseModel):
    page_number: int
    agreement: float | None  # 1 - CER between the two engines' texts; None if not comparable
    needs_review: bool
    reason: str


class OcrComparison(BaseModel):
    """Cross-check of two engines. Informational only: results are never merged (ADR-0022)."""

    document_id: str
    primary: str
    secondary: str
    threshold: float
    created_at: datetime
    pages: list[PageComparison]
