"""Background jobs (service mode, ADR-0029): status, events, cancellation and retry.

Mounted only when the database and broker are configured. Uploads (``POST
/api/v1/documents``) and stage runs (``POST .../ocr`` and the like) then return ``202``
with the job; poll ``GET /api/v1/jobs/{id}`` or ``GET /api/v1/documents/{id}/status``.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Path, Query, Request
from fastapi.responses import JSONResponse

from newgen_ocr.api.auth import require, require_document
from newgen_ocr.auth.tokens import Principal, Role
from newgen_ocr.jobs.models import Job, JobEvent, JobStatus, JobSubmitted, Page, StageRequest
from newgen_ocr.jobs.service import JobService

router = APIRouter(prefix="/api/v1", tags=["jobs"])

JobId = Annotated[str, Path(pattern=r"^[0-9a-f]{32}$")]
DocumentId = Annotated[str, Path(pattern=r"^[0-9a-f]{32}$")]
Submitter = Annotated[Principal, Depends(require(Role.SUBMITTER))]
DocumentSubmitter = Annotated[Principal, Depends(require_document(Role.SUBMITTER))]
# Same key, same user, same request: the original job is returned instead of a new one.
IdempotencyKey = Annotated[
    str | None,
    Header(alias="Idempotency-Key", pattern=r"^[A-Za-z0-9._:-]{1,128}$"),
]
Limit = Annotated[int, Query(ge=1, le=200)]
Offset = Annotated[int, Query(ge=0, le=1_000_000)]

QUEUED: dict[int | str, dict[str, Any]] = {
    202: {"model": JobSubmitted, "description": "Queued (service mode)"}
}


def jobs_service(request: Request) -> JobService | None:
    """The job service in service mode; None in local mode (run synchronously)."""
    service: JobService | None = getattr(request.app.state, "jobs", None)
    return service


def _jobs(request: Request) -> JobService:
    service = jobs_service(request)
    if service is None:  # pragma: no cover - the router is mounted only in service mode
        raise AssertionError("jobs router mounted without the job service")
    return service


def accepted(submitted: JobSubmitted) -> JSONResponse:
    """202 with the job and where to poll it (also for a duplicate, flagged in the body)."""
    return JSONResponse(
        submitted.model_dump(mode="json"),
        status_code=202,
        headers={"Location": f"/api/v1/jobs/{submitted.job.id}"},
    )


@router.post(
    "/documents/{document_id}/jobs",
    status_code=202,
    response_model=JobSubmitted,
)
def submit_stage(
    request: Request,
    document_id: DocumentId,
    body: StageRequest,
    who: DocumentSubmitter,
    idempotency_key: IdempotencyKey = None,
) -> JSONResponse:
    """Re-run part of the pipeline for a stored document (``ocr`` re-runs OCR, layout and
    extraction; ``layout`` re-runs layout and extraction; and so on)."""
    return accepted(_jobs(request).submit_stage(document_id, body, who, idempotency_key))


@router.get("/jobs", response_model=Page[Job])
def list_jobs(
    request: Request,
    who: Submitter,
    status: Annotated[list[JobStatus] | None, Query()] = None,
    document_id: Annotated[str | None, Query(pattern=r"^[0-9a-f]{32}$")] = None,
    limit: Limit = 50,
    offset: Offset = 0,
) -> Page[Job]:
    """Newest first. Submitters see their own jobs; other roles see every job."""
    return _jobs(request).jobs(who, status, document_id, limit, offset)


@router.get("/jobs/{job_id}", response_model=Job)
def get_job(request: Request, job_id: JobId, who: Submitter) -> Job:
    return _jobs(request).get(job_id, who)


@router.get("/jobs/{job_id}/events", response_model=list[JobEvent])
def get_events(request: Request, job_id: JobId, who: Submitter) -> list[JobEvent]:
    """What happened to the job, oldest first: attempts, stages, retries, lease expiry."""
    return _jobs(request).events(job_id, who)


@router.post("/jobs/{job_id}/cancel", response_model=Job)
def cancel_job(request: Request, job_id: JobId, who: Submitter) -> Job:
    """A queued job is cancelled at once; a running one stops before its next stage
    (stages already done keep their results)."""
    return _jobs(request).cancel(job_id, who)


@router.post("/jobs/{job_id}/retry", response_model=Job)
def retry_job(request: Request, job_id: JobId, who: Submitter) -> Job:
    """Run a failed or cancelled job again with fresh attempts, from the first stage it
    did not complete."""
    return _jobs(request).retry(job_id, who)
