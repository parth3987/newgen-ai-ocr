"""Background job schemas (ADR-0029; API_SPECIFICATION.md "Jobs")."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Generic, Literal, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")
RUN_NAME = r"^[a-z][a-z0-9_]{0,31}$"
NAME = r"^[a-z][a-z0-9_]{0,47}$"


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


ACTIVE = (JobStatus.QUEUED, JobStatus.RUNNING)
TERMINAL = (JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELLED)


class JobKind(StrEnum):
    PIPELINE = "pipeline"  # a new upload: ingest -> preprocessing -> ocr -> layout -> extraction
    PREPROCESSING = "preprocessing"
    OCR = "ocr"  # re-run OCR and everything after it
    LAYOUT = "layout"  # re-run layout and extraction
    EXTRACTION = "extraction"


STAGES: dict[JobKind, list[str]] = {
    JobKind.PIPELINE: ["ingest", "preprocessing", "ocr", "layout", "extraction"],
    JobKind.PREPROCESSING: ["preprocessing"],
    JobKind.OCR: ["ocr", "layout", "extraction"],
    JobKind.LAYOUT: ["layout", "extraction"],
    JobKind.EXTRACTION: ["extraction"],
}


class JobParams(BaseModel):
    """What a job runs with. Defaults come from the service configuration."""

    profile: str | None = Field(default=None, pattern=RUN_NAME)  # preprocessing profile
    engine: Literal["auto", "paddle", "tesseract"] = "auto"
    languages: list[str] | None = Field(default=None, max_length=6)
    # Run the second OCR engine for the field-level cross-check (ADR-0028).
    compare: bool = True
    handwriting: bool = False
    document_type: str = Field(default="auto", pattern=NAME)
    # Upload only (set by the API): the client's filename and declared type, for checks.
    filename: str | None = Field(default=None, max_length=255)
    content_type: str | None = Field(default=None, max_length=128)


class Job(BaseModel):
    id: str
    document_id: str
    kind: JobKind
    status: JobStatus
    params: JobParams
    stages: list[str]
    completed_stages: list[str]
    current_stage: str | None
    attempts: int
    max_attempts: int
    cancel_requested: bool
    error_code: str | None
    error_message: str | None
    submitted_by: str
    idempotency_key: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    updated_at: datetime


class JobEvent(BaseModel):
    ts: datetime
    event: str  # submitted, started, stage_completed, retry_scheduled, lease_expired, ...
    stage: str | None
    attempt: int
    detail: dict[str, Any]


class JobSubmitted(BaseModel):
    job: Job
    # True when an earlier identical request (same idempotency key, or the same file from
    # the same submitter) is returned instead of a new job.
    duplicate: bool = False


class StageRequest(BaseModel):
    """Re-run part of the pipeline for a stored document."""

    kind: Literal["preprocessing", "ocr", "layout", "extraction"]
    params: JobParams = JobParams()


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    limit: int
    offset: int


class DocumentSummary(BaseModel):
    id: str
    status: str  # received, ready, rejected
    filename: str | None = None
    sha256: str
    size_bytes: int
    format: str | None
    page_count: int | None
    submitted_by: str
    error_code: str | None
    created_at: datetime
    updated_at: datetime
    latest_job: Job | None = None


class DocumentFilter(BaseModel):
    """Document search (all optional). ``q`` matches the filename (case-insensitive
    substring) or the start of the document id."""

    submitted_by: str | None = None
    status: str | None = None
    q: str | None = Field(default=None, max_length=100)
    created_from: datetime | None = None
    created_to: datetime | None = None
