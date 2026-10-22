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


# Calibrated on the corpus: correct OSD orientations had confidence >= 5.0; noise 0.79.
MIN_ORIENTATION_CONFIDENCE = 2.0


class DetectionStatus(StrEnum):
    DETECTED = "detected"
    UNCERTAIN = "uncertain"  # OSD answered with low confidence
    UNDETERMINED = "undetermined"  # OSD could not decide (e.g. too little text)
    UNAVAILABLE = "unavailable"  # tesseract or osd model missing


class ScriptDetection(BaseModel):
    """Page-level script and orientation (Tesseract OSD; ADR-0023)."""

    status: DetectionStatus
    script: str | None = None  # decided dominant script (supported, or a named unsupported one)
    script_confidence: float | None = None  # census share, or OSD confidence if unsupported
    secondary_scripts: list[str] = []  # other supported scripts with >= 10% of characters
    osd_script: str | None = None
    osd_script_confidence: float | None = None
    census: dict[str, float] = {}  # script shares of the all-languages recognition pass
    census_mean_confidence: float | None = None
    orientation_deg: int | None = None  # clockwise rotation that makes the page upright
    orientation_confidence: float | None = None
    method: str = "tesseract-osd"
    duration_ms: float = 0.0

    @property
    def orientation_reliable(self) -> bool:
        return (
            self.orientation_deg is not None
            and (self.orientation_confidence or 0.0) >= MIN_ORIENTATION_CONFIDENCE
        )


class LanguageGuess(BaseModel):
    """Text-based script census and language identification of recognised text."""

    languages: list[str]  # most likely first
    scripts: dict[str, float]  # share of letters per script
    mixed_script: bool
    uncertain: bool
    reason: str


class PageOcrStatus(StrEnum):
    OK = "ok"
    EMPTY = "empty"  # engine ran and found no text
    SKIPPED = "skipped"  # not applicable, see skip_reason (e.g. native text)
    UNSUPPORTED = "unsupported"  # cannot be recognised reliably; see review.reasons
    FAILED = "failed"  # see error_code


class ReviewReason(StrEnum):
    """Why a page needs human review (ADR-0023). A page may have several."""

    UNSUPPORTED_SCRIPT = "unsupported_script"
    SCRIPT_UNCERTAIN = "script_uncertain"
    SCRIPT_MISMATCH = "script_mismatch"  # detected script differs from the requested language
    LANGUAGE_NOT_ENABLED = "language_not_enabled"
    HANDWRITING_UNSUPPORTED = "handwriting_unsupported"
    HANDWRITING_UNVERIFIED = "handwriting_unverified"
    MIXED_SCRIPT = "mixed_script"
    LOW_CONFIDENCE = "low_confidence"
    LOW_CONFIDENCE_LINES = "low_confidence_lines"
    EMPTY_RESULT = "empty_result"
    OCR_FAILED = "ocr_failed"
    LOW_QUALITY = "low_quality"
    ENGINE_DISAGREEMENT = "engine_disagreement"


class PageReview(BaseModel):
    needs_review: bool
    reasons: list[ReviewReason] = []
    details: list[str] = []


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
    languages: list[str] = []  # languages the engine was asked to read for this page
    handwriting: bool = False
    # Clockwise rotation applied before OCR (from script detection); coordinates refer to
    # the rotated image.
    image_rotation_deg: int = 0
    script: ScriptDetection | None = None
    detected_language: LanguageGuess | None = None
    review: PageReview = PageReview(needs_review=False)


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
    handwriting: bool = False
    pages_needing_review: int = 0
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
