"""Verification and human review (ADR-0028). Every endpoint needs a bearer token (ADR-0027).

Roles: viewer reads; reviewer claims, corrects, confirms and rejects; approver approves;
admin also takes over items and verifies the history chain.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Path, Query, Request, Response
from pydantic import BaseModel

from newgen_ocr.api.auth import require
from newgen_ocr.auth.tokens import Principal, Role
from newgen_ocr.errors import AppError, ErrorCode
from newgen_ocr.review.models import (
    ApproveRequest,
    Calibration,
    ChainVerification,
    ClaimRequest,
    CorrectionPatterns,
    CorrectionRequest,
    HistoryEvent,
    RejectRequest,
    ReviewedResult,
    ReviewItem,
    ReviewItemStatus,
    ReviewItemSummary,
    VersionedRequest,
)
from newgen_ocr.review.service import ReviewService

router = APIRouter(prefix="/api/v1", tags=["review"])

DocumentId = Annotated[str, Path(pattern=r"^[0-9a-f]{32}$")]
RunName = Annotated[str, Path(pattern=r"^[a-z][a-z0-9_]{0,31}$")]
Name = Annotated[str | None, Query(pattern=r"^[a-z][a-z0-9_]{0,47}$")]
PNG: dict[int | str, dict[str, Any]] = {200: {"content": {"image/png": {}}}}

Viewer = Annotated[Principal, Depends(require(Role.VIEWER))]
Reviewer = Annotated[Principal, Depends(require(Role.REVIEWER))]
Approver = Annotated[Principal, Depends(require(Role.APPROVER))]
Admin = Annotated[Principal, Depends(require(Role.ADMIN))]

ITEM = "/review/items/{document_id}/{layout_run}"


class Queue(BaseModel):
    total: int
    items: list[ReviewItemSummary]


class Policy(BaseModel):
    auto_accept: bool
    require_second_person: bool
    calibration: Calibration | None


def _service(request: Request) -> ReviewService:
    service: ReviewService = request.app.state.review
    return service


@router.get("/auth/whoami", response_model=Principal)
def whoami(who: Viewer) -> Principal:
    return who


@router.get("/review/policy", response_model=Policy)
def policy(request: Request, who: Viewer) -> Policy:
    """Whether automatic acceptance is on, and the calibration (thresholds) in force."""
    service = _service(request)
    return Policy(
        auto_accept=service.policy.auto_accept,
        require_second_person=service.require_second_person,
        calibration=service.policy.calibration,
    )


@router.get("/review/items", response_model=Queue)
def queue(
    request: Request,
    who: Viewer,
    status: Annotated[list[ReviewItemStatus] | None, Query()] = None,
    schema_id: Name = None,
    reason: Name = None,
    assigned_to: Annotated[str | None, Query(pattern=r"^[a-z0-9][a-z0-9._-]{0,63}$")] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Queue:
    """Items by priority (highest first), then age. Default: pending and in review."""
    statuses = set(status or [ReviewItemStatus.PENDING, ReviewItemStatus.IN_REVIEW])
    total, items = _service(request).queue(statuses, schema_id, reason, assigned_to, limit, offset)
    return Queue(total=total, items=items)


@router.get(ITEM, response_model=ReviewItem)
def get_item(
    request: Request, document_id: DocumentId, layout_run: RunName, who: Viewer
) -> ReviewItem:
    return _service(request).item(document_id, layout_run)


@router.get(f"{ITEM}/result", response_model=ReviewedResult)
def get_result(
    request: Request, document_id: DocumentId, layout_run: RunName, who: Viewer
) -> ReviewedResult:
    """The record with corrections applied, re-validated and re-checked."""
    return _service(request).result(document_id, layout_run)


@router.get(f"{ITEM}/history", response_model=list[HistoryEvent])
def get_history(
    request: Request, document_id: DocumentId, layout_run: RunName, who: Viewer
) -> list[HistoryEvent]:
    return _service(request).history(document_id, layout_run)


@router.post(f"{ITEM}/claim", response_model=ReviewItem)
def claim(
    request: Request,
    document_id: DocumentId,
    layout_run: RunName,
    body: ClaimRequest,
    who: Reviewer,
) -> ReviewItem:
    return _service(request).claim(document_id, layout_run, who, body)


@router.post(f"{ITEM}/release", response_model=ReviewItem)
def release(
    request: Request,
    document_id: DocumentId,
    layout_run: RunName,
    body: VersionedRequest,
    who: Reviewer,
) -> ReviewItem:
    return _service(request).release(document_id, layout_run, who, body)


@router.post(f"{ITEM}/corrections", response_model=ReviewItem)
def correct(
    request: Request,
    document_id: DocumentId,
    layout_run: RunName,
    body: CorrectionRequest,
    who: Reviewer,
) -> ReviewItem:
    """Set, clear or confirm values (all or nothing). Each change needs a reason code."""
    return _service(request).correct(document_id, layout_run, who, body)


@router.post(f"{ITEM}/approve", response_model=ReviewItem)
def approve(
    request: Request,
    document_id: DocumentId,
    layout_run: RunName,
    body: ApproveRequest,
    who: Approver,
) -> ReviewItem:
    return _service(request).approve(document_id, layout_run, who, body)


@router.post(f"{ITEM}/reject", response_model=ReviewItem)
def reject(
    request: Request,
    document_id: DocumentId,
    layout_run: RunName,
    body: RejectRequest,
    who: Reviewer,
) -> ReviewItem:
    return _service(request).reject(document_id, layout_run, who, body)


@router.get(f"{ITEM}/crop", response_class=Response, responses=PNG)
def crop(
    request: Request,
    document_id: DocumentId,
    layout_run: RunName,
    who: Viewer,
    field: Name = None,
    table: Name = None,
    record: Annotated[int | None, Query(ge=0)] = None,
    column: Name = None,
    candidate: Annotated[int | None, Query(ge=0)] = None,
) -> Response:
    """The source area of a field (or one of its candidates) or of a table cell."""
    if (field is None) == (table is None) or (table is not None and None in (record, column)):
        raise AppError(ErrorCode.VALIDATION_ERROR, "give field, or table with record and column")
    png = _service(request).crop(document_id, layout_run, field, table, record, column, candidate)
    return Response(content=png, media_type="image/png")


@router.get(f"{ITEM}/pages/{{page_number}}", response_class=Response, responses=PNG)
def page(
    request: Request,
    document_id: DocumentId,
    layout_run: RunName,
    page_number: Annotated[int, Path(ge=1)],
    who: Viewer,
    field: Name = None,
) -> Response:
    """The page the OCR read, with a field's evidence (red) and other candidates (blue)."""
    png = _service(request).page(document_id, layout_run, page_number, field)
    return Response(content=png, media_type="image/png")


@router.get("/review/patterns", response_model=CorrectionPatterns)
def patterns(request: Request, who: Approver) -> CorrectionPatterns:
    """What reviewers change, where and why: input for rules, schemas and thresholds."""
    return _service(request).patterns()


@router.get("/review/history/verify", response_model=ChainVerification)
def verify(request: Request, who: Admin) -> ChainVerification:
    return _service(request).verify()
