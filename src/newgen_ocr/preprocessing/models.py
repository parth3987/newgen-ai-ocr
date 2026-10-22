"""Preprocessing run records: what was done to each page, why, and how long it took."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel

from newgen_ocr.imaging.quality import QualityFlag, QualityReport
from newgen_ocr.ingestion.models import PageImageInfo
from newgen_ocr.preprocessing.profiles import ProfileConfig

StepParam = float | int | str | bool


class StepRecord(BaseModel):
    name: str
    applied: bool
    reason: str  # e.g. "flag:noisy", "always", "not_flagged", "disabled", "below_min_angle"
    params: dict[str, StepParam] = {}
    duration_ms: float = 0.0


class PageStatus(StrEnum):
    PROCESSED = "processed"  # a derived image was written
    UNCHANGED = "unchanged"  # no step applied; OCR uses the source page image
    SKIPPED = "skipped"  # not applicable (see skip_reason)


class PagePreprocessing(BaseModel):
    page_number: int
    status: PageStatus
    skip_reason: str | None = None  # "no_page_image" (native PDF text) or "blank_page"
    steps: list[StepRecord] = []
    rotation_applied_deg: int = 0  # clockwise quarter turn applied for vertical text
    skew_detected_deg: float | None = None  # counter-clockwise positive; None if unreliable
    skew_corrected_deg: float = 0.0
    quality_flags_before: list[QualityFlag] = []
    output: PageImageInfo | None = None  # None: use the source page image unchanged
    quality_after: QualityReport | None = None
    duration_ms: float = 0.0


class RuntimeInfo(BaseModel):
    opencv_version: str
    numpy_version: str
    cpu_model: str
    logical_cpus: int
    opencv_threads: int


class PreprocessingRun(BaseModel):
    document_id: str
    profile: ProfileConfig
    created_at: datetime
    pipeline_version: str
    runtime: RuntimeInfo
    total_ms: float
    pages: list[PagePreprocessing]
