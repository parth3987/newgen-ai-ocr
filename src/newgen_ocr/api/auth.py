"""Bearer-token authentication and role checks for the API (ADR-0027, ADR-0029).

Every endpoint except the health probes and the static review page needs a token. The
dependencies are ``async`` so the principal they set in ``actor_var`` is seen by the
handler (FastAPI copies the request context into the thread that runs a sync handler);
the token lookup itself runs in the thread pool.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from fastapi import Request
from fastapi.concurrency import run_in_threadpool

from newgen_ocr.audit import AuditEvent, AuditLog, actor_var
from newgen_ocr.auth.tokens import AuthFailure, Principal, Role, token_id_of
from newgen_ocr.errors import AppError, ErrorCode

# Who submitted a document (None: unknown, or a document from before ownership was kept).
OwnerLookup = Callable[[str], str | None]


def _bearer(request: Request) -> str | None:
    header = request.headers.get("Authorization", "")
    scheme, _, value = header.partition(" ")
    return value.strip() if scheme.lower() == "bearer" else None


async def _authenticate(request: Request, role: Role) -> Principal:
    audit: AuditLog = request.app.state.audit
    token = _bearer(request)
    outcome: Principal | AuthFailure = await run_in_threadpool(
        request.app.state.tokens.authenticate, token
    )
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
