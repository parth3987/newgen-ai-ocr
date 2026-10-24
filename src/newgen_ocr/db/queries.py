"""Read-only queries for search and administration (service mode; ADR-0030).

Extraction search, the operations overview and the audit log. None of these write, and
none return more than identifiers, counts, codes and the values a role may already read.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import ColumnElement, String, cast, func, select

from newgen_ocr.db.engine import Database
from newgen_ocr.db.models import AuditEventRow, Document, ExtractionRecord, Job, ReviewItemRow
from newgen_ocr.jobs.models import Job as JobView
from newgen_ocr.jobs.models import JobStatus, Page
from newgen_ocr.jobs.repo import like_escape, view

_DAY = timedelta(days=1)


class ExtractionFilter(BaseModel):
    submitted_by: str | None = None
    schema_id: str | None = None
    status: str | None = None  # complete, needs_review, unclassified
    review_status: str | None = None  # pending, in_review, approved, rejected, ...
    # Matches the filename, or any extracted value (case-insensitive substring).
    q: str | None = Field(default=None, max_length=100)
    created_from: datetime | None = None
    created_to: datetime | None = None


class ExtractionSummary(BaseModel):
    document_id: str
    layout_run: str
    schema_id: str | None
    status: str
    review_status: str | None
    filename: str | None
    submitted_by: str
    created_at: datetime
    # Up to six extracted field values, for the list (masked as stored).
    preview: dict[str, Any]


class RunningJob(BaseModel):
    id: str
    document_id: str
    kind: str
    current_stage: str | None
    worker: str | None
    attempts: int
    started_at: datetime | None
    lease_expires_at: datetime | None
    submitted_by: str


class Overview(BaseModel):
    generated_at: datetime
    jobs: dict[str, int]  # by status
    oldest_queued_s: float | None
    running: list[RunningJob]
    workers: dict[str, int]  # worker -> running jobs
    recent_failures: list[JobView]
    failures_24h: dict[str, int]  # by error code
    documents: dict[str, int]  # by status
    review: dict[str, int]  # by status
    active_sessions: int = 0


class AuditFilter(BaseModel):
    event: str | None = None
    actor: str | None = None
    document_id: str | None = None
    since: datetime | None = None
    until: datetime | None = None


class AuditEntry(BaseModel):
    id: int
    ts: datetime
    event: str
    actor: str
    request_id: str | None
    document_id: str | None
    fields: dict[str, Any]


def _preview(result: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for field in result.get("fields", []):
        if field.get("value") is not None:
            out[field["name"]] = field["value"]
        if len(out) == 6:
            break
    return out


class Queries:
    def __init__(self, db: Database) -> None:
        self.db = db

    def extractions(
        self, search: ExtractionFilter, limit: int, offset: int
    ) -> Page[ExtractionSummary]:
        where: list[ColumnElement[bool]] = []
        if search.submitted_by is not None:
            where.append(Document.submitted_by == search.submitted_by)
        if search.schema_id is not None:
            where.append(ExtractionRecord.schema_id == search.schema_id)
        if search.status is not None:
            where.append(ExtractionRecord.status == search.status)
        if search.review_status is not None:
            where.append(ReviewItemRow.status == search.review_status)
        if search.q:
            term = f"%{like_escape(search.q)}%"
            where.append(
                Document.filename.ilike(term, escape="\\")
                | cast(ExtractionRecord.result["fields"], String).ilike(term, escape="\\")
            )
        if search.created_from is not None:
            where.append(ExtractionRecord.created_at >= search.created_from)
        if search.created_to is not None:
            where.append(ExtractionRecord.created_at < search.created_to)
        base = (
            select(ExtractionRecord, Document.filename, Document.submitted_by, ReviewItemRow.status)
            .join(Document, Document.id == ExtractionRecord.document_id)
            .outerjoin(
                ReviewItemRow,
                (ReviewItemRow.document_id == ExtractionRecord.document_id)
                & (ReviewItemRow.layout_run == ExtractionRecord.layout_run),
            )
            .where(*where)
        )
        with self.db.session() as session:
            total = session.scalar(select(func.count()).select_from(base.subquery()))
            rows = session.execute(
                base.order_by(ExtractionRecord.created_at.desc(), ExtractionRecord.document_id)
                .limit(limit)
                .offset(offset)
            ).all()
            items = [
                ExtractionSummary(
                    document_id=record.document_id,
                    layout_run=record.layout_run,
                    schema_id=record.schema_id,
                    status=record.status,
                    review_status=review_status,
                    filename=filename,
                    submitted_by=submitted_by,
                    created_at=record.created_at,
                    preview=_preview(record.result),
                )
                for record, filename, submitted_by, review_status in rows
            ]
        return Page[ExtractionSummary](
            items=items, total=int(total or 0), limit=limit, offset=offset
        )

    def overview(self) -> Overview:
        ts = datetime.now(tz=UTC)
        with self.db.session() as session:
            jobs = dict(
                session.execute(select(Job.status, func.count()).group_by(Job.status)).all()
            )
            oldest = session.scalar(
                select(func.min(Job.created_at)).where(Job.status == JobStatus.QUEUED)
            )
            running_rows = session.scalars(
                select(Job).where(Job.status == JobStatus.RUNNING).order_by(Job.started_at)
            ).all()
            failures = session.scalars(
                select(Job)
                .where(Job.status == JobStatus.FAILED)
                .order_by(Job.finished_at.desc())
                .limit(10)
            ).all()
            by_code = dict(
                session.execute(
                    select(Job.error_code, func.count())
                    .where(
                        Job.status == JobStatus.FAILED,
                        Job.finished_at >= ts - _DAY,
                    )
                    .group_by(Job.error_code)
                ).all()
            )
            documents = dict(
                session.execute(
                    select(Document.status, func.count()).group_by(Document.status)
                ).all()
            )
            review = dict(
                session.execute(
                    select(ReviewItemRow.status, func.count()).group_by(ReviewItemRow.status)
                ).all()
            )
            running = [
                RunningJob(
                    id=j.id,
                    document_id=j.document_id,
                    kind=j.kind,
                    current_stage=j.current_stage,
                    worker=j.worker,
                    attempts=j.attempts,
                    started_at=j.started_at,
                    lease_expires_at=j.lease_expires_at,
                    submitted_by=j.submitted_by,
                )
                for j in running_rows
            ]
            workers: dict[str, int] = {}
            for job in running:
                workers[job.worker or "unknown"] = workers.get(job.worker or "unknown", 0) + 1
            return Overview(
                generated_at=ts,
                jobs={s.value: int(jobs.get(s.value, 0)) for s in JobStatus},
                oldest_queued_s=(ts - oldest).total_seconds() if oldest else None,
                running=running,
                workers=workers,
                recent_failures=[view(j) for j in failures],
                failures_24h={str(k or "unknown"): int(v) for k, v in by_code.items()},
                documents={str(k): int(v) for k, v in documents.items()},
                review={str(k): int(v) for k, v in review.items()},
            )

    def audit(self, search: AuditFilter, limit: int, offset: int) -> Page[AuditEntry]:
        where: list[ColumnElement[bool]] = []
        if search.event:
            # "auth." matches every auth event; a full name matches one.
            if search.event.endswith("."):
                where.append(AuditEventRow.event.startswith(search.event, autoescape=True))
            else:
                where.append(AuditEventRow.event == search.event)
        if search.actor:
            where.append(AuditEventRow.actor == search.actor)
        if search.document_id:
            where.append(AuditEventRow.document_id == search.document_id)
        if search.since is not None:
            where.append(AuditEventRow.ts >= search.since)
        if search.until is not None:
            where.append(AuditEventRow.ts < search.until)
        with self.db.session() as session:
            total = session.scalar(select(func.count()).select_from(AuditEventRow).where(*where))
            rows = session.scalars(
                select(AuditEventRow)
                .where(*where)
                .order_by(AuditEventRow.id.desc())
                .limit(limit)
                .offset(offset)
            ).all()
            items = [
                AuditEntry(
                    id=r.id,
                    ts=r.ts,
                    event=r.event,
                    actor=r.actor,
                    request_id=r.request_id,
                    document_id=r.document_id,
                    fields=r.fields,
                )
                for r in rows
            ]
        return Page[AuditEntry](items=items, total=int(total or 0), limit=limit, offset=offset)
