"""Submitting and controlling jobs (service mode, ADR-0029).

The API process only receives the upload, checks it cheaply (size, signature, extension),
records the document and its job in one transaction and publishes the job id. Workers do
the rest. The database is the source of truth: a job whose message never reached the
broker stays queued and is dispatched again by the reaper.

Duplicate submissions:

* the same ``Idempotency-Key`` (per user) returns the original job, and is refused for a
  different request (``IDEMPOTENCY_CONFLICT``);
* the same file from the same user returns the earlier document's latest job unless
  ``force`` is set;
* a document has at most one active job (database constraint), so two runs can never
  write the same document's results at once (``JOB_ALREADY_ACTIVE``).

Access: submitters see and control only their own documents and jobs; other roles see
all of them; only admins may cancel or retry someone else's job. Documents and jobs a
submitter may not see are reported as not found.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import BinaryIO

from newgen_ocr.auth.tokens import Principal, Role
from newgen_ocr.errors import AppError, ErrorCode
from newgen_ocr.ingestion.formats import safe_filename
from newgen_ocr.ingestion.service import UploadInfo
from newgen_ocr.jobs.models import (
    DocumentFilter,
    DocumentSummary,
    Job,
    JobEvent,
    JobKind,
    JobParams,
    JobStatus,
    JobSubmitted,
    Page,
    StageRequest,
)
from newgen_ocr.jobs.repo import JobRepo, request_hash
from newgen_ocr.jobs.runner import JobRunner
from newgen_ocr.services import Services

logger = logging.getLogger(__name__)

Dispatch = Callable[[str, int], None]


def sees_everything(who: Principal) -> bool:
    return who.role.allows(Role.VIEWER)


class JobService:
    def __init__(self, services: Services, dispatch: Dispatch | None) -> None:
        """``dispatch`` publishes a job id to the workers; None runs jobs inline (eager)."""
        if services.jobs is None:
            raise ValueError("jobs need the database (service mode)")
        self.services = services
        self.repo: JobRepo = services.jobs
        self.settings = services.settings
        self._dispatch = dispatch
        self._runner = JobRunner(services, lambda _job, _delay: None) if dispatch is None else None

    # ------------------------------------------------------------ submission

    def submit_upload(
        self,
        source: BinaryIO,
        upload: UploadInfo,
        params: JobParams,
        who: Principal,
        idempotency_key: str | None = None,
        force: bool = False,
    ) -> JobSubmitted:
        self._check_params(params)
        params = params.model_copy(
            update={"filename": upload.filename, "content_type": upload.content_type}
        )
        accepted = self.services.ingestion.accept(source, upload)
        keep = False
        try:
            fingerprint = request_hash(JobKind.PIPELINE, params, accepted.sha256)
            if idempotency_key is not None:
                replayed = self.repo.replay(who.user, idempotency_key, fingerprint)
                if replayed is not None:
                    return JobSubmitted(job=replayed, duplicate=True)
            if not force:
                earlier = self.repo.find_document_by_content(who.user, accepted.sha256)
                latest = self.repo.latest_job(earlier) if earlier is not None else None
                if latest is not None:
                    return JobSubmitted(job=latest, duplicate=True)
            job, duplicate = self.repo.submit(
                kind=JobKind.PIPELINE,
                params=params,
                submitted_by=who.user,
                idempotency_key=idempotency_key,
                fingerprint=fingerprint,
                document={
                    "sha256": accepted.sha256,
                    "size_bytes": accepted.size,
                    "format": accepted.format.value,
                    "filename": safe_filename(upload.filename),
                },
                document_id=accepted.document_id,
                max_queued=self.settings.max_queued_jobs,
                max_active_per_user=self.settings.max_active_jobs_per_user,
            )
            keep = not duplicate
        finally:
            if not keep:
                self.services.ingestion.discard(accepted)
        if duplicate:
            return JobSubmitted(job=job, duplicate=True)
        return JobSubmitted(job=self._start(job))

    def submit_stage(
        self,
        document_id: str,
        request: StageRequest,
        who: Principal,
        idempotency_key: str | None = None,
    ) -> JobSubmitted:
        self._check_params(request.params)
        document = self.document(document_id, who)
        if document.status != "ready":
            raise AppError(
                ErrorCode.DOCUMENT_NOT_READY,
                f"The document is {document.status}; it must be ready to run a stage",
            )
        kind = JobKind(request.kind)
        params = request.params.model_copy(update={"filename": None, "content_type": None})
        job, duplicate = self.repo.submit(
            kind=kind,
            params=params,
            submitted_by=who.user,
            idempotency_key=idempotency_key,
            fingerprint=request_hash(kind, params, document.sha256),
            document=None,
            document_id=document_id,
            max_queued=self.settings.max_queued_jobs,
            max_active_per_user=self.settings.max_active_jobs_per_user,
        )
        if duplicate:
            return JobSubmitted(job=job, duplicate=True)
        return JobSubmitted(job=self._start(job))

    def _check_params(self, params: JobParams) -> None:
        """Reject what would certainly fail, before anything is stored or queued."""
        if params.profile is not None:
            self.services.preprocessing.profile(params.profile)
        if params.document_type != "auto" and (
            self.services.extraction.registry.get(params.document_type) is None
        ):
            raise AppError(ErrorCode.UNKNOWN_DOCUMENT_TYPE, "Unknown document type")

    def _start(self, job: Job) -> Job:
        if self._runner is not None:
            # Inline (tests, evaluation): run attempts until the job leaves the queue.
            while self._runner.run(job.id) is JobStatus.QUEUED:
                pass
            return self.repo.get(job.id) or job
        if self._dispatch is None:  # pragma: no cover - one of the two is always set
            raise AssertionError
        try:
            self._dispatch(job.id, 0)
        except Exception:  # noqa: BLE001 - the job is recorded; the reaper dispatches it
            logger.warning("job dispatch failed; the reaper will retry", extra={"job_id": job.id})
        return job

    # ------------------------------------------------------------ reading

    def document(self, document_id: str, who: Principal) -> DocumentSummary:
        document = self.repo.document(document_id)
        if document is None or not (sees_everything(who) or document.submitted_by == who.user):
            raise AppError(ErrorCode.DOCUMENT_NOT_FOUND, "Document not found")
        return document

    def documents(
        self, who: Principal, search: DocumentFilter, limit: int, offset: int
    ) -> Page[DocumentSummary]:
        if not sees_everything(who):
            search = search.model_copy(update={"submitted_by": who.user})
        total, items = self.repo.documents(search, limit, offset)
        return Page[DocumentSummary](items=items, total=total, limit=limit, offset=offset)

    def get(self, job_id: str, who: Principal) -> Job:
        job = self.repo.get(job_id)
        if job is None or not (sees_everything(who) or job.submitted_by == who.user):
            raise AppError(ErrorCode.JOB_NOT_FOUND, "Job not found")
        return job

    def jobs(
        self,
        who: Principal,
        statuses: list[JobStatus] | None,
        document_id: str | None,
        limit: int,
        offset: int,
    ) -> Page[Job]:
        owner = None if sees_everything(who) else who.user
        total, items = self.repo.jobs(owner, statuses, document_id, limit, offset)
        return Page[Job](items=items, total=total, limit=limit, offset=offset)

    def events(self, job_id: str, who: Principal) -> list[JobEvent]:
        self.get(job_id, who)
        return self.repo.events(job_id)

    # ------------------------------------------------------------ control

    def _controllable(self, job_id: str, who: Principal) -> Job:
        job = self.get(job_id, who)
        if job.submitted_by != who.user and not who.role.allows(Role.ADMIN):
            raise AppError(ErrorCode.FORBIDDEN, "Only the submitter or an admin can do this")
        return job

    def cancel(self, job_id: str, who: Principal) -> Job:
        self._controllable(job_id, who)
        return self.repo.cancel(job_id)

    def retry(self, job_id: str, who: Principal) -> Job:
        self._controllable(job_id, who)
        return self._start(self.repo.retry(job_id))
