"""Authentication and role checks for the API (ADR-0027, ADR-0029, ADR-0030).

Every endpoint except the health probes, sign-in and the static web application needs a
caller: either a bearer token (integrations, scripts) or the web application's session
cookie. Requests that change something with a session cookie must also carry the
session's CSRF token in ``X-CSRF-Token``; bearer requests need not, as browsers never send
them on their own. The dependencies are ``async`` so the principal they set in
``actor_var`` is seen by the handler (FastAPI copies the request context into the thread
that runs a sync handler); lookups run in the thread pool.
"""

from __future__ import annotations

import hmac
from collections.abc import Awaitable, Callable

from fastapi import Request
from fastapi.concurrency import run_in_threadpool

from newgen_ocr.audit import AuditEvent, AuditLog, actor_var
from newgen_ocr.auth.tokens import AuthFailure, Principal, Role, token_id_of
from newgen_ocr.auth.users import UserStore, csrf_token
from newgen_ocr.errors import AppError, ErrorCode

SESSION_COOKIE = "ngocr_session"
CSRF_HEADER = "X-CSRF-Token"
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

# Who submitted a document (None: unknown, or a document from before ownership was kept).
OwnerLookup = Callable[[str], str | None]


def _bearer(request: Request) -> str | None:
    header = request.headers.get("Authorization", "")
    scheme, _, value = header.partition(" ")
    return value.strip() if scheme.lower() == "bearer" else None


async def _from_session(request: Request, value: str) -> Principal | AuthFailure:
    users: UserStore | None = request.app.state.users
    if users is None:
        return AuthFailure.UNKNOWN
    who = await run_in_threadpool(users.authenticate, value)
    if who is None:
        return AuthFailure.EXPIRED
    if request.method not in _SAFE_METHODS:
        sent = request.headers.get(CSRF_HEADER, "")
        if not hmac.compare_digest(sent.encode(), csrf_token(value).encode()):
            audit: AuditLog = request.app.state.audit
            await run_in_threadpool(
                audit.record, AuditEvent.CSRF_FAILED, actor=who.user, path=request.url.path
            )
            raise AppError(ErrorCode.CSRF_FAILED, "Missing or wrong CSRF token; reload the page")
    return who


async def _authenticate(request: Request, role: Role) -> Principal:
    audit: AuditLog = request.app.state.audit
    token = _bearer(request)
    cookie = request.cookies.get(SESSION_COOKIE)
    outcome: Principal | AuthFailure
    if token is None and cookie:
        outcome = await _from_session(request, cookie)
    else:
        outcome = await run_in_threadpool(request.app.state.tokens.authenticate, token)
    if not isinstance(outcome, Principal):
        # The token itself is never logged; only its public id, when well-formed.
        await run_in_threadpool(
            audit.record,
            AuditEvent.AUTH_FAILED,
            reason=outcome.value,
            token_id=token_id_of(token),
            path=request.url.path,
        )
        raise AppError(ErrorCode.UNAUTHENTICATED, "A valid bearer token is required")
    actor_var.set(outcome.user)
    request.state.principal = outcome
    if not outcome.role.allows(role):
        await run_in_threadpool(
            audit.record,
            AuditEvent.AUTH_DENIED,
            actor=outcome.user,
            role=outcome.role.value,
            required=role.value,
            path=request.url.path,
        )
        raise AppError(ErrorCode.FORBIDDEN, f"Requires the {role.value} role")
    return outcome


def require(role: Role) -> Callable[[Request], Awaitable[Principal]]:
    """FastAPI dependency: the caller's principal, if it holds at least ``role``."""

    async def dependency(request: Request) -> Principal:
        return await _authenticate(request, role)

    return dependency


def require_document(role: Role = Role.SUBMITTER) -> Callable[[Request], Awaitable[Principal]]:
    """As ``require``, and when the path names a document, a submitter must own it.

    Viewers and above see every document. A document a submitter may not see is reported
    as not found, so its existence is not revealed.
    """

    async def dependency(request: Request) -> Principal:
        who = await _authenticate(request, role)
        document_id = request.path_params.get("document_id")
        if document_id is None or who.role.allows(Role.VIEWER):
            return who
        owner_of: OwnerLookup = request.app.state.owner_of
        if await run_in_threadpool(owner_of, str(document_id)) != who.user:
            raise AppError(ErrorCode.DOCUMENT_NOT_FOUND, "Document not found")
        return who

    return dependency


def principal(request: Request) -> Principal:
    """The principal a router-level dependency already authenticated."""
    who: Principal = request.state.principal
    return who
