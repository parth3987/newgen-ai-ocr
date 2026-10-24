"""Web sign-in (ADR-0030): password login, the current session, logout.

The session cookie is HttpOnly (scripts cannot read it), SameSite=Strict (other sites
cannot send it) and Secure outside development. Every refused login gets the same
response, so it does not reveal whether a name exists or is locked; the audit log records
the actual reason.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from newgen_ocr.api.auth import SESSION_COOKIE, require
from newgen_ocr.audit import AuditEvent, AuditLog
from newgen_ocr.auth.tokens import Principal, Role
from newgen_ocr.auth.users import LoginResult, UserStore, csrf_token
from newgen_ocr.config import Settings
from newgen_ocr.errors import AppError, ErrorCode

router = APIRouter(prefix="/api/v1/auth", tags=["sign-in"])

Anyone = Annotated[Principal, Depends(require(Role.SUBMITTER))]


class LoginRequest(BaseModel):
    user: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class SessionInfo(BaseModel):
    user: str
    role: Role
    # Send back in X-CSRF-Token on every request that changes something. Null when the
    # caller used a bearer token (no CSRF token is needed then).
    csrf_token: str | None
    expires_at: datetime | None = None


def _users(request: Request) -> UserStore:
    users: UserStore | None = request.app.state.users
    if users is None:
        raise AppError(ErrorCode.SIGN_IN_UNAVAILABLE, "Sign-in needs the database (service mode)")
    return users


@router.post("/login", response_model=SessionInfo)
async def login(request: Request, response: Response, body: LoginRequest) -> SessionInfo:
    """Sign in with a user name and password; sets the session cookie."""
    users = _users(request)
    settings: Settings = request.app.state.settings
    audit: AuditLog = request.app.state.audit
    name = body.user.strip().lower()
    outcome: LoginResult | str = await run_in_threadpool(users.login, name, body.password)
    if not isinstance(outcome, LoginResult):
        await run_in_threadpool(
            audit.record,
            AuditEvent.LOGIN_FAILED,
            actor=name if outcome != "unknown" else "unknown",
            reason=outcome,
        )
        raise AppError(ErrorCode.LOGIN_FAILED, "Wrong user name or password, or account locked")
    await run_in_threadpool(audit.record, AuditEvent.LOGIN_SUCCEEDED, actor=name)
    response.set_cookie(
        SESSION_COOKIE,
        outcome.session_value,
        max_age=settings.session_max_hours * 3600,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="strict",
        path="/",
    )
    return SessionInfo(
        user=outcome.principal.user,
        role=outcome.principal.role,
        csrf_token=csrf_token(outcome.session_value),
        expires_at=outcome.expires_at,
    )


@router.get("/session", response_model=SessionInfo)
def current(request: Request, who: Anyone) -> SessionInfo:
    """Who is signed in (also with a bearer token), and the CSRF token for this session."""
    cookie = request.cookies.get(SESSION_COOKIE)
    csrf = csrf_token(cookie) if cookie and "authorization" not in request.headers else None
    return SessionInfo(user=who.user, role=who.role, csrf_token=csrf)


@router.post("/logout", status_code=204)
async def logout(request: Request, response: Response, who: Anyone) -> Response:
    """End this session (needs the CSRF token, like any change)."""
    users = _users(request)
    audit: AuditLog = request.app.state.audit
    await run_in_threadpool(users.logout, request.cookies.get(SESSION_COOKIE))
    await run_in_threadpool(audit.record, AuditEvent.LOGOUT, actor=who.user)
    response = Response(status_code=204)
    response.delete_cookie(SESSION_COOKIE, path="/", samesite="strict")
    return response


class PasswordChange(BaseModel):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=1, max_length=256)


@router.post("/password", status_code=204)
async def change_password(request: Request, who: Anyone, body: PasswordChange) -> Response:
    """Change your own password (web users). Ends every session of yours, including this
    one: sign in again."""
    users = _users(request)
    audit: AuditLog = request.app.state.audit
    checked = await run_in_threadpool(users.login, who.user, body.current_password)
    if not isinstance(checked, LoginResult):
        raise AppError(ErrorCode.LOGIN_FAILED, "The current password is wrong")
    await run_in_threadpool(lambda: users.update(who.user, password=body.new_password))
    await run_in_threadpool(
        audit.record, AuditEvent.USER_UPDATED, user=who.user, password_changed=True
    )
    response = Response(status_code=204)
    response.delete_cookie(SESSION_COOKIE, path="/", samesite="strict")
    return response
