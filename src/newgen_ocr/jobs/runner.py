"""Run one job attempt: claim it, run its remaining stages, record the outcome (ADR-0029).

Safe to call more than once for the same job (duplicate or redelivered messages): only a
claim that succeeds runs anything, and a running job is claimed again only after its lease
has expired. Each stage replaces its earlier output atomically, so a stage interrupted by a
crash simply runs again; completed stages are skipped.

Failures are either deterministic (the input or request is wrong: the job fails at once,
retrying cannot help) or transient (anything unexpected: retried with backoff until the
attempts run out). Error messages stored on the job are the error code's public message,
never exception text that might quote document content.
"""

from __future__ import annotations

import logging
import os
import socket
import threading
import uuid
from collections.abc import Callable

from newgen_ocr.audit import actor_var
from newgen_ocr.errors import AppError, ErrorCode
from newgen_ocr.ingestion.formats import DocumentFormat
from newgen_ocr.ingestion.service import Accepted, UploadInfo
from newgen_ocr.jobs.models import Job, JobKind, JobStatus
from newgen_ocr.jobs.repo import JobRepo
from newgen_ocr.logging_config import request_id_var
from newgen_ocr.services import Services

logger = logging.getLogger(__name__)

# Errors that may succeed on another attempt. Every other AppError is deterministic.
TRANSIENT = frozenset(
    {
        ErrorCode.INTERNAL_ERROR,
        ErrorCode.OCR_TIMEOUT,
    }
)

Dispatch = Callable[[str, int], None]  # (job id, delay in seconds)


class LeaseLostError(Exception):
    """Another worker (or the reaper) has taken over this job; stop without writing."""


def worker_name() -> str:
    return f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"


def retry_delay(attempt: int, eager: bool) -> int:
    """30 s, 60 s, 120 s ... capped at 10 minutes (0 when jobs run inline)."""
    return 0 if eager else min(30 * 2 ** max(attempt - 1, 0), 600)


class _Heartbeat:
    """Renews the lease while a stage runs; notes when the lease was lost."""

    def __init__(self, repo: JobRepo, job_id: str, worker: str, lease_s: int) -> None:
        self.repo, self.job_id, self.worker, self.lease_s = repo, job_id, worker, lease_s
        self.lost = False
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name=f"heartbeat-{job_id}", daemon=True)

    def _run(self) -> None:
        interval = max(self.lease_s / 3, 1)
        while not self._stop.wait(interval):
            try:
                if not self.repo.heartbeat(self.job_id, self.worker, self.lease_s):
                    self.lost = True
                    return
            except Exception:  # noqa: BLE001 - the database may be briefly unavailable
                logger.warning("lease renewal failed", extra={"job_id": self.job_id})

    def __enter__(self) -> _Heartbeat:
        self._thread.start()
        return self

    def __exit__(self, *_: object) -> None:
        self._stop.set()
        self._thread.join(timeout=5)


class JobRunner:
    def __init__(self, services: Services, dispatch: Dispatch, worker: str | None = None) -> None:
        if services.jobs is None:
            raise ValueError("the job runner needs the database (service mode)")
        self.services = services
        self.repo: JobRepo = services.jobs
        self.dispatch = dispatch
        self.worker = worker or worker_name()
        self.settings = services.settings

    def run(self, job_id: str) -> JobStatus | None:
        """One attempt. Returns the job's status afterwards, or None when there was nothing
        to do (not claimable: already running elsewhere, finished, cancelled, or waiting)."""
        job = self.repo.claim(job_id, self.worker, self.settings.job_lease_s)
        if job is None:
            logger.info("job not claimable", extra={"job_id": job_id})
            return None
        actor = actor_var.set(job.submitted_by)
        request = request_id_var.set(f"job-{job.id}")
        logger.info(
            "job started",
            extra={"job_id": job.id, "kind": job.kind.value, "attempt": job.attempts},
        )
        try:
            with _Heartbeat(self.repo, job.id, self.worker, self.settings.job_lease_s) as beat:
                return self._attempt(job, beat)
        finally:
            request_id_var.reset(request)
            actor_var.reset(actor)

    def _attempt(self, job: Job, beat: _Heartbeat) -> JobStatus | None:
        stage = None
        try:
            for stage in job.stages:
                if stage in job.completed_stages:
                    continue
                state = self.repo.stage_started(job.id, stage, self.worker)
                if state == "lost" or beat.lost:
                    raise LeaseLostError
                if state == "cancelled":
                    self.repo.finish(job.id, JobStatus.CANCELLED, worker=self.worker)
                    logger.info("job cancelled", extra={"job_id": job.id, "stage": stage})
                    return JobStatus.CANCELLED
                detail = self._stage(job, stage)
                if beat.lost or not self.repo.stage_completed(job.id, stage, self.worker, **detail):
                    raise LeaseLostError
        except LeaseLostError:
            logger.warning("job lease lost; another worker owns it", extra={"job_id": job.id})
            return None
        except AppError as exc:
            return self._failed(job, stage, exc.code, exc.code in TRANSIENT)
        except Exception:
            logger.exception("job stage failed", extra={"job_id": job.id, "stage": stage})
            return self._failed(job, stage, ErrorCode.INTERNAL_ERROR, transient=True)
        self.repo.finish(job.id, JobStatus.SUCCEEDED, worker=self.worker)
        logger.info("job succeeded", extra={"job_id": job.id})
        return JobStatus.SUCCEEDED

    def _failed(
        self, job: Job, stage: str | None, code: ErrorCode, transient: bool
    ) -> JobStatus | None:
        message = f"Stage {stage} failed ({code.value})"
        extra = {"job_id": job.id, "stage": stage, "error_code": code.value}
        if stage == "ingest" and not transient:
            self.repo.document_rejected(job.document_id, code.value)
        if transient:
            delay = retry_delay(job.attempts, self.settings.jobs_eager)
            if self.repo.schedule_retry(job.id, delay, code.value, message, worker=self.worker):
                logger.warning("job will be retried", extra={**extra, "delay_s": delay})
                self.dispatch(job.id, delay)
                return JobStatus.QUEUED
        done = self.repo.finish(job.id, JobStatus.FAILED, code.value, message, worker=self.worker)
        logger.warning("job failed", extra=extra)
        return JobStatus.FAILED if done is not None else None

    # ------------------------------------------------------------ stages

    def _stage(self, job: Job, stage: str) -> dict[str, str | int | None]:
        s, p, doc = self.services, job.params, job.document_id
        profile = p.profile or self.settings.default_preprocessing_profile
        if stage == "ingest":
            return self._ingest(job)
        if stage == "preprocessing":
            if job.kind is JobKind.PIPELINE:
                # As in local mode: the document is stored; OCR falls back to the page images.
                s.preprocessing.run_after_ingest(doc, profile)
            else:
                s.preprocessing.run(doc, profile)
            return {"profile": profile}
        if stage == "ocr":
            run = s.ocr.run(
                doc,
                p.engine,
                p.languages or list(self.settings.ocr_default_languages),
                profile,
                compare=p.compare and not p.handwriting,
                handwriting=p.handwriting,
                compare_required=False,
            )
            return {"run": p.engine, "pages": len(run.pages)}
        if stage == "layout":
            s.layout.analyze(doc, p.engine)
            return {"run": p.engine}
        if stage == "extraction":
            result = s.extraction.extract(doc, p.engine, p.document_type)
            self.repo.save_extraction(result)
            item = s.review.enqueue(result)
            return {"schema_id": result.schema_id, "review_status": item.status.value}
        raise AppError(ErrorCode.INTERNAL_ERROR, f"unknown stage {stage}")

    def _ingest(self, job: Job) -> dict[str, str | int | None]:
        store, doc = self.services.store, job.document_id
        if not store.has_document(doc):
            store.recover_ingest(doc)
            summary = self.repo.document(doc)
            quarantined = store.quarantine_path(doc)
            if summary is None or summary.format is None or not quarantined.is_file():
                # The upload is gone (removed by hand, or the data volume was replaced).
                raise AppError(ErrorCode.DOCUMENT_NOT_FOUND, "The uploaded file is missing")
            accepted = Accepted(
                doc, quarantined, summary.sha256, summary.size_bytes, DocumentFormat(summary.format)
            )
            upload = UploadInfo(filename=job.params.filename, content_type=job.params.content_type)
            self.services.ingestion.process(
                accepted, upload, job.submitted_by, retain_on_error=True
            )
        manifest = store.read_manifest(doc)
        self.repo.document_ready(doc, manifest)
        return {"pages": manifest.page_count}
