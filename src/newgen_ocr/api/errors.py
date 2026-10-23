"""HTTP mapping for error codes and the API error envelope."""

from __future__ import annotations

import logging
from typing import cast

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from newgen_ocr.errors import AppError, ErrorCode

logger = logging.getLogger(__name__)

HTTP_STATUS: dict[ErrorCode, int] = {
    ErrorCode.INTERNAL_ERROR: 500,
    ErrorCode.NOT_FOUND: 404,
    ErrorCode.METHOD_NOT_ALLOWED: 405,
    ErrorCode.VALIDATION_ERROR: 422,
    ErrorCode.HTTP_ERROR: 400,
    ErrorCode.EMPTY_FILE: 422,
    ErrorCode.FILE_TOO_LARGE: 413,
    ErrorCode.UNSUPPORTED_FORMAT: 415,
    ErrorCode.FORMAT_MISMATCH: 415,
    ErrorCode.CORRUPT_DOCUMENT: 422,
    ErrorCode.ENCRYPTED_DOCUMENT: 422,
    ErrorCode.TOO_MANY_PAGES: 422,
    ErrorCode.PAGE_TOO_LARGE: 422,
    ErrorCode.DOCUMENT_NOT_FOUND: 404,
    ErrorCode.PAGE_NOT_FOUND: 404,
    ErrorCode.PAGE_IMAGE_NOT_AVAILABLE: 404,
    ErrorCode.UNKNOWN_PROFILE: 422,
    ErrorCode.PREPROCESSING_NOT_FOUND: 404,
    ErrorCode.PREPROCESSED_IMAGE_NOT_AVAILABLE: 404,
    ErrorCode.UNSUPPORTED_LANGUAGE: 422,
    ErrorCode.UNKNOWN_ENGINE: 422,
    ErrorCode.OCR_ENGINE_UNAVAILABLE: 503,
    ErrorCode.MODEL_NOT_AVAILABLE: 503,
    ErrorCode.MODEL_INTEGRITY_ERROR: 503,
    ErrorCode.OCR_FAILED: 500,
    ErrorCode.OCR_TIMEOUT: 504,
    ErrorCode.OCR_RUN_NOT_FOUND: 404,
    ErrorCode.LAYOUT_UNAVAILABLE: 503,
    ErrorCode.LAYOUT_FAILED: 500,
    ErrorCode.LAYOUT_RUN_NOT_FOUND: 404,
    ErrorCode.UNKNOWN_DOCUMENT_TYPE: 422,
    ErrorCode.EXTRACTION_NOT_FOUND: 404,
    ErrorCode.UNSUPPORTED_EXPORT: 422,
}

_CODE_FOR_HTTP_STATUS = {
    404: ErrorCode.NOT_FOUND,
    405: ErrorCode.METHOD_NOT_ALLOWED,
    413: ErrorCode.FILE_TOO_LARGE,
}


def _request_id(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


def error_body(code: str, message: str, request_id: str | None) -> dict[str, object]:
    return {"error": {"code": code, "message": message, "request_id": request_id}}


def error_response(request: Request, status_code: int, code: str, message: str) -> JSONResponse:
    request_id = _request_id(request)
    headers = {"X-Request-ID": request_id} if request_id else None
    return JSONResponse(
        status_code=status_code, content=error_body(code, message, request_id), headers=headers
    )


async def _app_error_handler(request: Request, exc: Exception) -> JSONResponse:
    err = cast(AppError, exc)
    logger.info("application error", extra={"error_code": err.code.value})
    return error_response(request, HTTP_STATUS[err.code], err.code, err.message)


async def _http_error_handler(request: Request, exc: Exception) -> JSONResponse:
    err = cast(StarletteHTTPException, exc)
    code = _CODE_FOR_HTTP_STATUS.get(err.status_code, ErrorCode.HTTP_ERROR)
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
