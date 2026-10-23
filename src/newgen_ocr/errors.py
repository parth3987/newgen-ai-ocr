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
    # Preprocessing
    UNKNOWN_PROFILE = "UNKNOWN_PROFILE"
    PREPROCESSING_NOT_FOUND = "PREPROCESSING_NOT_FOUND"
    PREPROCESSED_IMAGE_NOT_AVAILABLE = "PREPROCESSED_IMAGE_NOT_AVAILABLE"
    # OCR
    UNSUPPORTED_LANGUAGE = "UNSUPPORTED_LANGUAGE"
    UNKNOWN_ENGINE = "UNKNOWN_ENGINE"
    OCR_ENGINE_UNAVAILABLE = "OCR_ENGINE_UNAVAILABLE"
    MODEL_NOT_AVAILABLE = "MODEL_NOT_AVAILABLE"
    MODEL_INTEGRITY_ERROR = "MODEL_INTEGRITY_ERROR"
    OCR_FAILED = "OCR_FAILED"
    OCR_TIMEOUT = "OCR_TIMEOUT"
    OCR_RUN_NOT_FOUND = "OCR_RUN_NOT_FOUND"
    # Layout
    LAYOUT_UNAVAILABLE = "LAYOUT_UNAVAILABLE"
    LAYOUT_FAILED = "LAYOUT_FAILED"
    LAYOUT_RUN_NOT_FOUND = "LAYOUT_RUN_NOT_FOUND"
    # Extraction
    UNKNOWN_DOCUMENT_TYPE = "UNKNOWN_DOCUMENT_TYPE"
    EXTRACTION_NOT_FOUND = "EXTRACTION_NOT_FOUND"
    UNSUPPORTED_EXPORT = "UNSUPPORTED_EXPORT"


class AppError(Exception):
    """An expected, client-reportable error. ``message`` must not contain document content."""

    def __init__(self, code: ErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
