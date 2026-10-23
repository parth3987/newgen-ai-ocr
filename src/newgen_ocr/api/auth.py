"""Bearer-token authentication and role checks for the API (ADR-0027)."""

from __future__ import annotations

from collections.abc import Callable

from fastapi import Request

from newgen_ocr.audit import AuditEvent, AuditLog
from newgen_ocr.auth.tokens import Principal, Role, TokenStore, token_id_of
from newgen_ocr.errors import AppError, ErrorCode


def _bearer(request: Request) -> str | None:
    header = request.headers.get("Authorization", "")
    scheme, _, value = header.partition(" ")
    return value.strip() if scheme.lower() == "bearer" else None


def require(role: Role) -> Callable[[Request], Principal]:
    """FastAPI dependency: the caller's principal, if it holds at least ``role``."""

    def dependency(request: Request) -> Principal:
        tokens: TokenStore = request.app.state.tokens
        audit: AuditLog = request.app.state.audit
        token = _bearer(request)
        outcome = tokens.authenticate(token)
        if not isinstance(outcome, Principal):
            # The token itself is never logged; only its public id, when well-formed.
            audit.record(
                AuditEvent.AUTH_FAILED,
                reason=outcome.value,
                token_id=token_id_of(token),
                path=request.url.path,
            )
            raise AppError(ErrorCode.UNAUTHENTICATED, "A valid bearer token is required")
        if not outcome.role.allows(role):
            audit.record(
                AuditEvent.AUTH_DENIED,
                actor=outcome.user,
                role=outcome.role.value,
                required=role.value,
                path=request.url.path,
            )
            raise AppError(ErrorCode.FORBIDDEN, f"Requires the {role.value} role")
        return outcome

    return dependency
