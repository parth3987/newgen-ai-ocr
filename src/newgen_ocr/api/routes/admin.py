"""Administration and search (service mode; ADR-0030).

Admins: the operations overview, the audit log and user accounts. Everyone: extraction
search (submitters see their own documents only). Access is checked here, in the API,
whatever the web application shows.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Path, Query, Request
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from newgen_ocr.api.auth import require
from newgen_ocr.api.routes.jobs import Limit, Offset
from newgen_ocr.audit import AuditEvent, AuditLog
from newgen_ocr.auth.tokens import USER_PATTERN, Principal, Role
from newgen_ocr.auth.users import UserInfo, UserStore
from newgen_ocr.db.queries import (
    AuditEntry,
    AuditFilter,
    ExtractionFilter,
    ExtractionSummary,
    Overview,
    Queries,
)
from newgen_ocr.errors import AppError, ErrorCode
from newgen_ocr.jobs.models import Page
from newgen_ocr.jobs.service import sees_everything

router = APIRouter(prefix="/api/v1", tags=["administration"])

Admin = Annotated[Principal, Depends(require(Role.ADMIN))]
Anyone = Annotated[Principal, Depends(require(Role.SUBMITTER))]
UserName = Annotated[str, Path(pattern=USER_PATTERN)]
Name = Annotated[str | None, Query(pattern=r"^[a-z][a-z0-9_]{0,47}$")]


class NewUser(BaseModel):
    name: str = Field(pattern=USER_PATTERN)
    role: Role
    password: str = Field(min_length=1, max_length=256)


class UserChange(BaseModel):
    role: Role | None = None
    disabled: bool | None = None
    password: str | None = Field(default=None, min_length=1, max_length=256)


def _queries(request: Request) -> Queries:
    queries: Queries = request.app.state.services.queries
    return queries


def _users(request: Request) -> UserStore:
    users: UserStore = request.app.state.users
    return users


@router.get("/extractions", response_model=Page[ExtractionSummary])
def search_extractions(
    request: Request,
    who: Anyone,
    q: Annotated[str | None, Query(max_length=100)] = None,
    schema_id: Name = None,
    status: Annotated[Literal["complete", "needs_review", "unclassified"] | None, Query()] = None,
    review_status: Annotated[str | None, Query(pattern=r"^[a-z_]{1,16}$")] = None,
    submitted_by: Annotated[str | None, Query(pattern=USER_PATTERN)] = None,
    created_from: Annotated[datetime | None, Query()] = None,
    created_to: Annotated[datetime | None, Query()] = None,
    limit: Limit = 50,
    offset: Offset = 0,
) -> Page[ExtractionSummary]:
    """Extraction results, newest first. ``q`` matches the filename or any extracted value
    (substring, any case). Submitters see their own documents only."""
    search = ExtractionFilter(
        submitted_by=submitted_by if sees_everything(who) else who.user,
        schema_id=schema_id,
        status=status,
        review_status=review_status,
        q=q.strip() if q else None,
        created_from=created_from,
        created_to=created_to,
    )
    return _queries(request).extractions(search, limit, offset)


@router.get("/admin/overview", response_model=Overview)
def overview(request: Request, who: Admin) -> Overview:
    """Queue and worker state, recent failures, documents and review items by status."""
    result = _queries(request).overview()
    result.active_sessions = _users(request).active_sessions()
    return result


@router.get("/admin/audit", response_model=Page[AuditEntry])
def audit_log(
    request: Request,
    who: Admin,
    event: Annotated[str | None, Query(pattern=r"^[a-z_]+\.([a-z_]+)?$")] = None,
    actor: Annotated[str | None, Query(max_length=64)] = None,
    document_id: Annotated[str | None, Query(pattern=r"^[0-9a-f]{32}$")] = None,
    since: Annotated[datetime | None, Query()] = None,
    until: Annotated[datetime | None, Query()] = None,
    limit: Limit = 50,
    offset: Offset = 0,
) -> Page[AuditEntry]:
    """Audit events, newest first. ``event`` is a full name (``review.approved``) or a
    prefix ending in a dot (``auth.``)."""
    search = AuditFilter(
        event=event, actor=actor, document_id=document_id, since=since, until=until
    )
    return _queries(request).audit(search, limit, offset)


@router.get("/admin/users", response_model=list[UserInfo])
def list_users(request: Request, who: Admin) -> list[UserInfo]:
    return _users(request).list()


@router.post("/admin/users", status_code=201, response_model=UserInfo)
async def create_user(request: Request, who: Admin, body: NewUser) -> UserInfo:
    """Create a web user. The password must have 12 to 256 characters and must not
    contain the user name; share it privately and ask the user to change it."""
    users, audit = _users(request), request.app.state.audit
    created = await run_in_threadpool(users.create, body.name, body.role, body.password)
    await run_in_threadpool(
        audit.record, AuditEvent.USER_CREATED, user=body.name, role=body.role.value
    )
    return created


@router.patch("/admin/users/{name}", response_model=UserInfo)
async def change_user(request: Request, who: Admin, name: UserName, body: UserChange) -> UserInfo:
    """Change a role, disable or enable, or set a new password. Disabling, a new role or a
    new password ends the user's sessions. Admins cannot disable or demote themselves."""
    if name == who.user and (body.disabled or (body.role is not None and body.role != Role.ADMIN)):
        raise AppError(ErrorCode.FORBIDDEN, "Admins cannot disable or demote themselves")
    users, audit = _users(request), request.app.state.audit
    changed = await run_in_threadpool(
        lambda: users.update(name, role=body.role, disabled=body.disabled, password=body.password)
    )
    auditlog: AuditLog = audit
    await run_in_threadpool(
        auditlog.record,
        AuditEvent.USER_UPDATED,
        user=name,
        role=body.role.value if body.role else None,
        disabled=body.disabled,
        password_changed=body.password is not None,
    )
    return changed
