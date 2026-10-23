"""PostgreSQL schema (ADR-0029). Changed only through Alembic migrations (``db/migrations``).

Metadata, job state and results live here; originals, page images and the large OCR and
layout runs stay on the data volume (paths are derived from ids, contents hashed in the
manifest). Two tables are append-only, enforced by triggers: ``review_history`` and
``audit_events``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    MetaData,
    String,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# Stable constraint names, so migrations can refer to them.
NAMING = {
    "ix": "ix_%(table_name)s_%(column_0_name)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

Json = dict[str, Any]
Timestamp = DateTime(timezone=True)
ACTIVE_JOB = "status IN ('queued', 'running')"


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING)


class ApiToken(Base):
    __tablename__ = "api_tokens"

    id: Mapped[str] = mapped_column(String(12), primary_key=True)
    user: Mapped[str] = mapped_column(String(64), index=True)
    role: Mapped[str] = mapped_column(String(16))
    sha256: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(Timestamp)
    expires_at: Mapped[datetime | None] = mapped_column(Timestamp)
    revoked_at: Mapped[datetime | None] = mapped_column(Timestamp)


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    # received -> ready | rejected (ingestion failed: see error_code)
    status: Mapped[str] = mapped_column(String(16), index=True)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    format: Mapped[str | None] = mapped_column(String(8))
    page_count: Mapped[int | None] = mapped_column(Integer)
    submitted_by: Mapped[str] = mapped_column(String(64), index=True)
    error_code: Mapped[str | None] = mapped_column(String(48))
    manifest: Mapped[Json | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(Timestamp, index=True)
    updated_at: Mapped[datetime] = mapped_column(Timestamp)


class Page(Base):
    __tablename__ = "pages"

    document_id: Mapped[str] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), primary_key=True
    )
    page_number: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(String(16))
    needs_ocr: Mapped[bool] = mapped_column(Boolean)
    width_px: Mapped[int | None] = mapped_column(Integer)
    height_px: Mapped[int | None] = mapped_column(Integer)
    quality: Mapped[Json | None] = mapped_column(JSONB)


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (
        # At most one active job per document: concurrent requests cannot interleave writes
        # to the same runs.
        Index(
            "uq_jobs_active_document",
            "document_id",
            unique=True,
            postgresql_where=text(ACTIVE_JOB),
        ),
        Index(
            "uq_jobs_idempotency",
            "submitted_by",
            "idempotency_key",
            unique=True,
            postgresql_where=text("idempotency_key IS NOT NULL"),
        ),
        Index("ix_jobs_status_created", "status", "created_at"),
        CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled')", name="status"
        ),
        CheckConstraint("attempts >= 0 AND attempts <= max_attempts", name="attempts"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    document_id: Mapped[str] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(16))  # pipeline, preprocessing, ocr, layout, ...
    params: Mapped[Json] = mapped_column(JSONB)
    # queued -> running -> succeeded | failed | cancelled (running -> queued on retry)
    status: Mapped[str] = mapped_column(String(16))
    stages: Mapped[list[str]] = mapped_column(JSONB)
    completed_stages: Mapped[list[str]] = mapped_column(JSONB)
    current_stage: Mapped[str | None] = mapped_column(String(16))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer)
    lease_expires_at: Mapped[datetime | None] = mapped_column(Timestamp)
    worker: Mapped[str | None] = mapped_column(String(128))
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    error_code: Mapped[str | None] = mapped_column(String(48))
    error_message: Mapped[str | None] = mapped_column(String(300))  # never document content
    submitted_by: Mapped[str] = mapped_column(String(64), index=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(128))
    request_hash: Mapped[str] = mapped_column(String(64))  # what was asked (for idempotency)
    not_before: Mapped[datetime | None] = mapped_column(Timestamp)  # retry backoff
    created_at: Mapped[datetime] = mapped_column(Timestamp)
    started_at: Mapped[datetime | None] = mapped_column(Timestamp)
    finished_at: Mapped[datetime | None] = mapped_column(Timestamp)
    updated_at: Mapped[datetime] = mapped_column(Timestamp)


class JobEvent(Base):
    __tablename__ = "job_events"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    ts: Mapped[datetime] = mapped_column(Timestamp)
    event: Mapped[str] = mapped_column(String(32))
    stage: Mapped[str | None] = mapped_column(String(16))
    attempt: Mapped[int] = mapped_column(Integer)
    detail: Mapped[Json] = mapped_column(JSONB)


class ExtractionRecord(Base):
    __tablename__ = "extraction_results"

    document_id: Mapped[str] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), primary_key=True
    )
    layout_run: Mapped[str] = mapped_column(String(32), primary_key=True)
    schema_id: Mapped[str | None] = mapped_column(String(48), index=True)
    status: Mapped[str] = mapped_column(String(16), index=True)
    created_at: Mapped[datetime] = mapped_column(Timestamp)
    result: Mapped[Json] = mapped_column(JSONB)


class ReviewItemRow(Base):
    __tablename__ = "review_items"

    document_id: Mapped[str] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), primary_key=True
    )
    layout_run: Mapped[str] = mapped_column(String(32), primary_key=True)
    status: Mapped[str] = mapped_column(String(16), index=True)
    priority: Mapped[int] = mapped_column(Integer)
    schema_id: Mapped[str | None] = mapped_column(String(48))
    assigned_to: Mapped[str | None] = mapped_column(String(64))
    revision: Mapped[int] = mapped_column(Integer)
    version: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(Timestamp)
    updated_at: Mapped[datetime] = mapped_column(Timestamp)
    item: Mapped[Json] = mapped_column(JSONB)


class ReviewRevision(Base):
    __tablename__ = "review_item_revisions"

    document_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    layout_run: Mapped[str] = mapped_column(String(32), primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, primary_key=True)
    item: Mapped[Json] = mapped_column(JSONB)


class ReviewHistoryRow(Base):
    __tablename__ = "review_history"

    seq: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    ts: Mapped[datetime] = mapped_column(Timestamp)
    kind: Mapped[str] = mapped_column(String(16))
    actor: Mapped[str] = mapped_column(String(64))
    document_id: Mapped[str] = mapped_column(String(32), index=True)
    layout_run: Mapped[str] = mapped_column(String(32))
    body: Mapped[Json] = mapped_column(JSONB)  # the full HistoryEvent
    prev_hash: Mapped[str] = mapped_column(String(64))
    hash: Mapped[str] = mapped_column(String(64))


class AuditEventRow(Base):
    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    ts: Mapped[datetime] = mapped_column(Timestamp, index=True)
    event: Mapped[str] = mapped_column(String(48), index=True)
    actor: Mapped[str] = mapped_column(String(64))
    request_id: Mapped[str | None] = mapped_column(String(64))
    document_id: Mapped[str | None] = mapped_column(String(32), index=True)
    fields: Mapped[Json] = mapped_column(JSONB)  # identifiers and outcome codes, no content


APPEND_ONLY = ("review_history", "audit_events")
