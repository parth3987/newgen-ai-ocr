"""Stable error codes and the API error envelope.

Error codes are part of the public API contract (docs/API_SPECIFICATION.md):
never rename or reuse a code; add new ones instead.
"""

from __future__ import annotations

import logging
from enum import StrEnum
from typing import cast

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger(__name__)


class ErrorCode(StrEnum):
    INTERNAL_ERROR = "INTERNAL_ERROR"
    NOT_FOUND = "NOT_FOUND"
    METHOD_NOT_ALLOWED = "METHOD_NOT_ALLOWED"
    VALIDATION_ERROR = "VALIDATION_ERROR"
    HTTP_ERROR = "HTTP_ERROR"


class AppError(Exception):
    """Base class for expected, client-reportable application errors."""

    def __init__(self, code: ErrorCode, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


def _request_id(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


def error_response(request: Request, status_code: int, code: str, message: str) -> JSONResponse:
    request_id = _request_id(request)
    headers = {"X-Request-ID": request_id} if request_id else None
    body = {"error": {"code": code, "message": message, "request_id": request_id}}
    return JSONResponse(status_code=status_code, content=body, headers=headers)


async def _app_error_handler(request: Request, exc: Exception) -> JSONResponse:
    err = cast(AppError, exc)
    logger.info("application error", extra={"error_code": err.code.value})
    return error_response(request, err.status_code, err.code, err.message)


async def _http_error_handler(request: Request, exc: Exception) -> JSONResponse:
    err = cast(StarletteHTTPException, exc)
    code = {404: ErrorCode.NOT_FOUND, 405: ErrorCode.METHOD_NOT_ALLOWED}.get(
        err.status_code, ErrorCode.HTTP_ERROR
    )
    return error_response(request, err.status_code, code, str(err.detail))


async def _validation_error_handler(request: Request, exc: Exception) -> JSONResponse:
    err = cast(RequestValidationError, exc)
    # Report only locations and messages; never echo submitted input values back.
    problems = "; ".join(f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in err.errors())
    return error_response(request, 422, ErrorCode.VALIDATION_ERROR, problems)


async def _unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.error("unhandled exception", exc_info=exc)
    return error_response(request, 500, ErrorCode.INTERNAL_ERROR, "Internal server error")


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AppError, _app_error_handler)
    app.add_exception_handler(StarletteHTTPException, _http_error_handler)
    app.add_exception_handler(RequestValidationError, _validation_error_handler)
    app.add_exception_handler(Exception, _unhandled_error_handler)
