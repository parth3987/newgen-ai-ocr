"""Job and document state in PostgreSQL (ADR-0029).

Every transition is one transaction with the job row locked (``SELECT … FOR UPDATE``) or a
single conditional ``UPDATE``, so two workers, a worker and the reaper, or a worker and a
cancel request can never both move a job. Constraints in the database (one active job per
document, one job per idempotency key) hold even if the application is wrong.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import ColumnElement, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from newgen_ocr.db.engine import Database
from newgen_ocr.db.models import Document, ExtractionRecord, Job, JobEvent, Page
from newgen_ocr.errors import AppError, ErrorCode
from newgen_ocr.extraction.models import ExtractionResult
from newgen_ocr.ingestion.models import DocumentManifest
from newgen_ocr.jobs.models import (
    ACTIVE,
    STAGES,
    DocumentFilter,
    DocumentSummary,
    JobKind,
    JobParams,
    JobStatus,
)
from newgen_ocr.jobs.models import Job as JobView
from newgen_ocr.jobs.models import JobEvent as JobEventView


def now() -> datetime:
    return datetime.now(tz=UTC)


def like_escape(term: str) -> str:
    """A user's search text as a literal LIKE pattern (``%`` and ``_`` match themselves)."""
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def request_hash(kind: JobKind, params: JobParams, content_sha256: str | None) -> str:
    """Fingerprint of what a request asks for (an idempotency key must not be reused for a
    different request)."""
    body = {"kind": kind.value, "params": params.model_dump(mode="json"), "content": content_sha256}
    return hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()


def view(job: Job) -> JobView:
    return JobView(
        id=job.id,
        document_id=job.document_id,
        kind=JobKind(job.kind),
        status=JobStatus(job.status),
        params=JobParams.model_validate(job.params),
        stages=list(job.stages),
        completed_stages=list(job.completed_stages),
        current_stage=job.current_stage,
        attempts=job.attempts,
        max_attempts=job.max_attempts,
        cancel_requested=job.cancel_requested,
        error_code=job.error_code,
        error_message=job.error_message,
        submitted_by=job.submitted_by,
        idempotency_key=job.idempotency_key,
        created_at=job.created_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
        updated_at=job.updated_at,
    )


def _event(session: Session, job: Job, event: str, stage: str | None = None, **detail: Any) -> None:
    session.add(
        JobEvent(
            job_id=job.id,
            ts=now(),
            event=event,
            stage=stage,
            attempt=job.attempts,
            detail={k: v for k, v in detail.items() if v is not None},
        )
    )


class JobRepo:
    def __init__(self, db: Database, max_attempts: int = 3) -> None:
        self.db = db
        self.max_attempts = max_attempts

    # ------------------------------------------------------------ documents

    def find_document_by_content(self, submitted_by: str, sha256: str) -> str | None:
        """An earlier, not rejected upload of the same file by the same submitter."""
        with self.db.session() as session:
            return session.scalar(
                select(Document.id)
                .where(
                    Document.submitted_by == submitted_by,
                    Document.sha256 == sha256,
                    Document.status != "rejected",
                )
                .order_by(Document.created_at)
                .limit(1)
            )

    def owner(self, document_id: str) -> str | None:
        with self.db.session() as session:
            return session.scalar(select(Document.submitted_by).where(Document.id == document_id))

    def document_ready(self, document_id: str, manifest: DocumentManifest) -> None:
        with self.db.session() as session:
            doc = session.get(Document, document_id, with_for_update=True)
            if doc is None:
                return
            doc.status = "ready"
            doc.format = manifest.format.value
            doc.page_count = manifest.page_count
            doc.manifest = manifest.model_dump(mode="json")
            doc.updated_at = now()
            session.query(Page).filter(Page.document_id == document_id).delete()
            for page in manifest.pages:
                session.add(
                    Page(
                        document_id=document_id,
                        page_number=page.page_number,
                        source=page.source.value,
                        needs_ocr=page.needs_ocr,
                        width_px=page.image.width_px if page.image else None,
                        height_px=page.image.height_px if page.image else None,
                        quality=page.quality.model_dump(mode="json") if page.quality else None,
                    )
                )

    def document_rejected(self, document_id: str, error_code: str) -> None:
        with self.db.session() as session:
            doc = session.get(Document, document_id, with_for_update=True)
            if doc is not None and doc.status == "received":
                doc.status = "rejected"
                doc.error_code = error_code
                doc.updated_at = now()

    def save_extraction(self, result: ExtractionResult) -> None:
        with self.db.session() as session:
            row = session.get(ExtractionRecord, (result.document_id, result.layout_run))
            values = {
                "schema_id": result.schema_id,
                "status": result.status.value,
                "created_at": result.created_at,
                "result": result.model_dump(mode="json"),
            }
            if row is None:
                session.add(
                    ExtractionRecord(
                        document_id=result.document_id, layout_run=result.layout_run, **values
                    )
                )
            else:
                for key, value in values.items():
                    setattr(row, key, value)

    def documents(
        self, search: DocumentFilter, limit: int, offset: int
    ) -> tuple[int, list[DocumentSummary]]:
        where: list[ColumnElement[bool]] = []
        if search.submitted_by is not None:
            where.append(Document.submitted_by == search.submitted_by)
        if search.status is not None:
            where.append(Document.status == search.status)
        if search.q:
            term = like_escape(search.q)
            where.append(
                Document.filename.ilike(f"%{term}%", escape="\\")
                | Document.id.startswith(search.q.lower(), autoescape=True)
            )
        if search.created_from is not None:
            where.append(Document.created_at >= search.created_from)
        if search.created_to is not None:
            where.append(Document.created_at < search.created_to)
        with self.db.session() as session:
            total = session.scalar(select(func.count()).select_from(Document).where(*where))
            rows = session.scalars(
                select(Document)
                .where(*where)
                .order_by(Document.created_at.desc(), Document.id)
                .limit(limit)
                .offset(offset)
            ).all()
            out = []
            for doc in rows:
                latest = session.scalar(
                    select(Job)
                    .where(Job.document_id == doc.id)
                    .order_by(Job.created_at.desc())
                    .limit(1)
                )
                out.append(_summary(doc, latest))
        return int(total or 0), out

    def document(self, document_id: str) -> DocumentSummary | None:
        with self.db.session() as session:
            doc = session.get(Document, document_id)
            if doc is None:
                return None
            latest = session.scalar(
                select(Job)
                .where(Job.document_id == document_id)
                .order_by(Job.created_at.desc())
                .limit(1)
            )
            return _summary(doc, latest)

    # ------------------------------------------------------------ submission

    def submit(
        self,
        *,
        kind: JobKind,
        params: JobParams,
        submitted_by: str,
        idempotency_key: str | None,
        fingerprint: str,
        document: dict[str, Any] | None,
        document_id: str,
        max_queued: int,
        max_active_per_user: int,
    ) -> tuple[JobView, bool]:
        """Create a job (and, for an upload, its document row) atomically.

        Returns ``(job, duplicate)``. A repeated idempotency key returns the original job,
        or fails if it was used for a different request. Admission limits are checked in
        the same transaction.
        """
        if idempotency_key is not None:
            existing = self._by_key(submitted_by, idempotency_key)
            if existing is not None:
                return self._replay(existing, fingerprint), True
        try:
            with self.db.session() as session:
                queued = session.scalar(
                    select(func.count()).select_from(Job).where(Job.status == JobStatus.QUEUED)
                )
                if (queued or 0) >= max_queued:
                    raise AppError(ErrorCode.QUEUE_FULL, "The job queue is full; retry later")
                active = session.scalar(
                    select(func.count())
                    .select_from(Job)
                    .where(Job.submitted_by == submitted_by, Job.status.in_(list(ACTIVE)))
                )
                if (active or 0) >= max_active_per_user:
                    raise AppError(
                        ErrorCode.TOO_MANY_JOBS, "Too many active jobs for this user; retry later"
                    )
                ts = now()
                if document is not None:
                    session.add(
                        Document(
                            id=document_id,
                            status="received",
                            submitted_by=submitted_by,
                            created_at=ts,
                            updated_at=ts,
                            **document,
                        )
                    )
                    session.flush()
                job = Job(
                    id=uuid.uuid4().hex,
                    document_id=document_id,
                    kind=kind.value,
                    params=params.model_dump(mode="json"),
                    status=JobStatus.QUEUED.value,
                    stages=STAGES[kind],
                    completed_stages=[],
                    attempts=0,
                    max_attempts=self.max_attempts,
                    cancel_requested=False,
                    submitted_by=submitted_by,
                    idempotency_key=idempotency_key,
                    request_hash=fingerprint,
                    created_at=ts,
                    updated_at=ts,
                )
                session.add(job)
                session.flush()
                _event(session, job, "submitted", kind=kind.value)
                return view(job), False
        except IntegrityError as exc:
            constraint = getattr(getattr(exc.orig, "diag", None), "constraint_name", "")
            if constraint == "uq_jobs_idempotency" and idempotency_key is not None:
                existing = self._by_key(submitted_by, idempotency_key)
                if existing is not None:
                    return self._replay(existing, fingerprint), True
            if constraint == "uq_jobs_active_document":
                active_id = self.active_job(document_id)
                raise AppError(
                    ErrorCode.JOB_ALREADY_ACTIVE,
                    f"Document already has an active job ({active_id}); wait for it or cancel it",
                ) from None
            raise

    def replay(self, submitted_by: str, key: str, fingerprint: str) -> JobView | None:
        """The job an earlier request with this Idempotency-Key created, if any."""
        existing = self._by_key(submitted_by, key)
        return self._replay(existing, fingerprint) if existing is not None else None

    def latest_job(self, document_id: str) -> JobView | None:
        with self.db.session() as session:
            job = session.scalar(
                select(Job)
                .where(Job.document_id == document_id)
                .order_by(Job.created_at.desc())
                .limit(1)
            )
            return view(job) if job is not None else None

    def _by_key(self, submitted_by: str, key: str) -> Job | None:
        with self.db.session() as session:
            return session.scalar(
                select(Job).where(Job.submitted_by == submitted_by, Job.idempotency_key == key)
            )

    @staticmethod
    def _replay(job: Job, fingerprint: str) -> JobView:
        if job.request_hash != fingerprint:
            raise AppError(
                ErrorCode.IDEMPOTENCY_CONFLICT,
                "This Idempotency-Key was already used for a different request",
            )
        return view(job)

    def active_job(self, document_id: str) -> str | None:
        with self.db.session() as session:
            return session.scalar(
                select(Job.id).where(Job.document_id == document_id, Job.status.in_(list(ACTIVE)))
            )

    # ------------------------------------------------------------ execution

    def claim(self, job_id: str, worker: str, lease_s: int) -> JobView | None:
        """Start an attempt: only a queued job, or a running one whose lease expired (its
        worker died), whose backoff has passed and that has attempts left. Atomic."""
        ts = now()
        with self.db.session() as session:
            row = session.execute(
                update(Job)
                .where(
                    Job.id == job_id,
                    Job.cancel_requested.is_(False),
                    Job.attempts < Job.max_attempts,
                    (Job.status == JobStatus.QUEUED)
                    | ((Job.status == JobStatus.RUNNING) & (Job.lease_expires_at < ts)),
                    (Job.not_before.is_(None)) | (Job.not_before <= ts),
                )
                .values(
                    status=JobStatus.RUNNING.value,
                    attempts=Job.attempts + 1,
                    lease_expires_at=ts + timedelta(seconds=lease_s),
                    worker=worker[:128],
                    started_at=func.coalesce(Job.started_at, ts),
                    not_before=None,
                    updated_at=ts,
                )
                .returning(Job)
            ).scalar_one_or_none()
            if row is None:
                return None
            _event(session, row, "started", worker=worker[:128])
            return view(row)

    def heartbeat(self, job_id: str, worker: str, lease_s: int) -> bool:
        ts = now()
        with self.db.session() as session:
            result = session.execute(
                update(Job)
                .where(Job.id == job_id, Job.worker == worker[:128], Job.status == "running")
                .values(lease_expires_at=ts + timedelta(seconds=lease_s), updated_at=ts)
            )
            return bool(result.rowcount)  # type: ignore[attr-defined]

    @staticmethod
    def _owned(session: Session, job_id: str, worker: str) -> Job | None:
        """The job, locked, if this worker still holds it (running, its lease not taken over)."""
        job = session.get(Job, job_id, with_for_update=True)
        if job is None or job.status != JobStatus.RUNNING or job.worker != worker[:128]:
            return None
        return job

    def stage_started(self, job_id: str, stage: str, worker: str) -> str:
        """Record the stage. Returns "ok"; "cancelled" when cancellation was requested (do
        not start it); or "lost" when another worker or the reaper has taken the job."""
        with self.db.session() as session:
            job = self._owned(session, job_id, worker)
            if job is None:
                return "lost"
            if job.cancel_requested:
                return "cancelled"
            job.current_stage = stage
            job.updated_at = now()
            _event(session, job, "stage_started", stage)
            return "ok"

    def stage_completed(self, job_id: str, stage: str, worker: str, **detail: Any) -> bool:
        with self.db.session() as session:
            job = self._owned(session, job_id, worker)
            if job is None:
                return False
            if stage not in job.completed_stages:
                job.completed_stages = [*job.completed_stages, stage]
            job.current_stage = None
            job.updated_at = now()
            _event(session, job, "stage_completed", stage, **detail)
            return True

    def finish(
        self,
        job_id: str,
        status: JobStatus,
        error_code: str | None = None,
        error_message: str | None = None,
        *,
        worker: str,
    ) -> JobView | None:
        """End the attempt (None when this worker no longer holds the job)."""
        with self.db.session() as session:
            job = self._owned(session, job_id, worker)
            if job is None:
                return None
            ts = now()
            job.status = status.value
            job.error_code = error_code
            job.error_message = (error_message or "")[:300] or None
            job.finished_at = ts
            job.updated_at = ts
            job.lease_expires_at = None
            job.current_stage = None
            _event(session, job, status.value, error_code=error_code)
            return view(job)

    def schedule_retry(
        self, job_id: str, delay_s: int, error_code: str, message: str, *, worker: str
    ) -> bool:
        """Back to queued after a transient failure; False when no attempts are left (or
        this worker no longer holds the job)."""
        with self.db.session() as session:
            job = self._owned(session, job_id, worker)
            if job is None or job.attempts >= job.max_attempts:
                return False
            ts = now()
            job.status = JobStatus.QUEUED.value
            job.lease_expires_at = None
            job.current_stage = None
            job.not_before = ts + timedelta(seconds=delay_s)
            job.error_code = error_code
            job.error_message = message[:300]
            job.updated_at = ts
            _event(session, job, "retry_scheduled", delay_s=delay_s, error_code=error_code)
            return True

    # ------------------------------------------------------------ control

    def get(self, job_id: str) -> JobView | None:
        with self.db.session() as session:
            job = session.get(Job, job_id)
            return view(job) if job is not None else None

    def events(self, job_id: str) -> list[JobEventView]:
        with self.db.session() as session:
            rows = session.scalars(
                select(JobEvent).where(JobEvent.job_id == job_id).order_by(JobEvent.id)
            ).all()
            return [
                JobEventView(
                    ts=r.ts, event=r.event, stage=r.stage, attempt=r.attempt, detail=r.detail
                )
                for r in rows
            ]

    def jobs(
        self,
        submitted_by: str | None,
        statuses: list[JobStatus] | None,
        document_id: str | None,
        limit: int,
        offset: int,
    ) -> tuple[int, list[JobView]]:
        where: list[ColumnElement[bool]] = []
        if submitted_by is not None:
            where.append(Job.submitted_by == submitted_by)
        if statuses:
            where.append(Job.status.in_([s.value for s in statuses]))
        if document_id is not None:
            where.append(Job.document_id == document_id)
        with self.db.session() as session:
            total = session.scalar(select(func.count()).select_from(Job).where(*where))
            rows = session.scalars(
                select(Job)
                .where(*where)
                .order_by(Job.created_at.desc(), Job.id)
                .limit(limit)
                .offset(offset)
            ).all()
            return int(total or 0), [view(j) for j in rows]

    def cancel(self, job_id: str) -> JobView:
        """Queued: cancelled at once. Running: stops before its next stage."""
        with self.db.session() as session:
            job = session.get(Job, job_id, with_for_update=True)
            if job is None:
                raise AppError(ErrorCode.JOB_NOT_FOUND, "Job not found")
            if job.status in (JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELLED):
                raise AppError(ErrorCode.JOB_CONFLICT, f"The job is already {job.status}")
            ts = now()
            job.cancel_requested = True
            job.updated_at = ts
            if job.status == JobStatus.QUEUED:
                job.status = JobStatus.CANCELLED.value
                job.finished_at = ts
                _event(session, job, "cancelled")
            else:
                _event(session, job, "cancel_requested")
            return view(job)

    def retry(self, job_id: str) -> JobView:
        """A failed or cancelled job runs again with fresh attempts, resuming after its
        completed stages."""
        try:
            with self.db.session() as session:
                job = session.get(Job, job_id, with_for_update=True)
                if job is None:
                    raise AppError(ErrorCode.JOB_NOT_FOUND, "Job not found")
                if job.status not in (JobStatus.FAILED, JobStatus.CANCELLED):
                    raise AppError(
                        ErrorCode.JOB_CONFLICT, "Only failed or cancelled jobs can be retried"
                    )
                doc = session.get(Document, job.document_id)
                if doc is not None and doc.status == "rejected":
                    raise AppError(
                        ErrorCode.JOB_CONFLICT,
                        "The document was rejected at ingestion; upload a valid file",
                    )
                job.status = JobStatus.QUEUED.value
                job.attempts = 0
                job.cancel_requested = False
                job.error_code = None
                job.error_message = None
                job.finished_at = None
                job.not_before = None
                job.updated_at = now()
                session.flush()
                _event(session, job, "retried")
                return view(job)
        except IntegrityError:
            raise AppError(
                ErrorCode.JOB_ALREADY_ACTIVE, "Document already has an active job"
            ) from None

    def reap(
        self, stale_queued_s: int = 120, include_stale: bool = True
    ) -> tuple[list[str], list[str]]:
        """Recover jobs whose worker died (lease expired) and, with ``include_stale`` (the
        caller saw no messages waiting in the broker), queued jobs whose message was lost.
        Returns (jobs to dispatch again, jobs failed). Safe to run from several workers."""
        ts = now()
        redispatch: list[str] = []
        failed: list[str] = []
        with self.db.session() as session:
            expired = session.scalars(
                select(Job)
                .where(Job.status == JobStatus.RUNNING, Job.lease_expires_at < ts)
                .with_for_update(skip_locked=True)
            ).all()
            for job in expired:
                _event(session, job, "lease_expired", worker=job.worker)
                if job.cancel_requested:
                    job.status = JobStatus.CANCELLED.value
                    job.finished_at = ts
                    _event(session, job, "cancelled")
                elif job.attempts >= job.max_attempts:
                    job.status = JobStatus.FAILED.value
                    job.error_code = ErrorCode.WORKER_LOST.value
                    job.error_message = "The worker stopped during the last allowed attempt"
                    job.finished_at = ts
                    failed.append(job.id)
                    _event(session, job, "failed", error_code=job.error_code)
                else:
                    job.status = JobStatus.QUEUED.value
                    redispatch.append(job.id)
                job.lease_expires_at = None
                job.current_stage = None
                job.updated_at = ts
            if include_stale:
                stale = session.scalars(
                    select(Job)
                    .where(
                        Job.status == JobStatus.QUEUED,
                        Job.updated_at < ts - timedelta(seconds=stale_queued_s),
                        (Job.not_before.is_(None)) | (Job.not_before <= ts),
                    )
                    .with_for_update(skip_locked=True)
                ).all()
                for job in stale:
                    job.updated_at = ts  # dispatched again now; not again for a while
                    _event(session, job, "redispatched")
                    if job.id not in redispatch:
                        redispatch.append(job.id)
        return redispatch, failed


def _summary(doc: Document, latest: Job | None) -> DocumentSummary:
    return DocumentSummary(
        id=doc.id,
        status=doc.status,
        filename=doc.filename,
        sha256=doc.sha256,
        size_bytes=doc.size_bytes,
        format=doc.format,
        page_count=doc.page_count,
        submitted_by=doc.submitted_by,
        error_code=doc.error_code,
        created_at=doc.created_at,
        updated_at=doc.updated_at,
        latest_job=view(latest) if latest is not None else None,
    )
