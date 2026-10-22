"""Stable error codes and the domain error type.

Error codes are part of the public API contract (docs/API_SPECIFICATION.md):
never rename or reuse a code; add new ones instead. This module must stay free of
web-framework imports; HTTP status mapping lives in ``newgen_ocr.api.errors``.
"""

from __future__ import annotations

from enum import StrEnum


class ErrorCode(StrEnum):
    INTERNAL_ERROR = "INTERNAL_ERROR"
    NOT_FOUND = "NOT_FOUND"
    METHOD_NOT_ALLOWED = "METHOD_NOT_ALLOWED"
    VALIDATION_ERROR = "VALIDATION_ERROR"
    HTTP_ERROR = "HTTP_ERROR"
    # Ingestion
    EMPTY_FILE = "EMPTY_FILE"
    FILE_TOO_LARGE = "FILE_TOO_LARGE"
    UNSUPPORTED_FORMAT = "UNSUPPORTED_FORMAT"
    FORMAT_MISMATCH = "FORMAT_MISMATCH"
    CORRUPT_DOCUMENT = "CORRUPT_DOCUMENT"
    ENCRYPTED_DOCUMENT = "ENCRYPTED_DOCUMENT"
    TOO_MANY_PAGES = "TOO_MANY_PAGES"
    PAGE_TOO_LARGE = "PAGE_TOO_LARGE"
    # Retrieval
    DOCUMENT_NOT_FOUND = "DOCUMENT_NOT_FOUND"
    PAGE_NOT_FOUND = "PAGE_NOT_FOUND"
    PAGE_IMAGE_NOT_AVAILABLE = "PAGE_IMAGE_NOT_AVAILABLE"


class AppError(Exception):
    """An expected, client-reportable error. ``message`` must not contain document content."""

    def __init__(self, code: ErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
