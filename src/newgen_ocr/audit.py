"""Append-only audit trail of document lifecycle events.

Events carry identifiers, hashes, sizes, formats and outcome codes, never document
content or client filenames. One JSON object per line in ``audit/audit.jsonl``.
"""

from __future__ import annotations

import json
import os
import threading
from contextvars import ContextVar
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

from newgen_ocr.logging_config import request_id_var

# The authenticated user of the current request or job (set by the API and the worker).
actor_var: ContextVar[str | None] = ContextVar("actor", default=None)


class AuditEvent(StrEnum):
    DOCUMENT_ACCEPTED = "document.accepted"
    DOCUMENT_REJECTED = "document.rejected"
    PREPROCESSING_COMPLETED = "preprocessing.completed"
    PREPROCESSING_FAILED = "preprocessing.failed"
    OCR_COMPLETED = "ocr.completed"
    LAYOUT_COMPLETED = "layout.completed"
    EXTRACTION_COMPLETED = "extraction.completed"
    EXTRACTION_EXPORTED = "extraction.exported"
    AUTH_FAILED = "auth.failed"
    AUTH_DENIED = "auth.denied"
    REVIEW_QUEUED = "review.queued"
    REVIEW_AUTO_ACCEPTED = "review.auto_accepted"
    REVIEW_CLAIMED = "review.claimed"
    REVIEW_RELEASED = "review.released"
    REVIEW_CORRECTED = "review.corrected"
    REVIEW_APPROVED = "review.approved"
    REVIEW_REJECTED = "review.rejected"


AuditValue = str | int | bool | None


class AuditLog:
    """File sink (local mode). ``DbAuditLog`` (``newgen_ocr.db.audit``) is the service-mode
    sink with the same interface."""

    def __init__(self, data_dir: Path) -> None:
        self.path = data_dir / "audit" / "audit.jsonl"
        self._lock = threading.Lock()

    @staticmethod
    def entry(event: AuditEvent, fields: dict[str, AuditValue]) -> dict[str, AuditValue]:
        return {
            "ts": datetime.now(tz=UTC).isoformat(),
            "event": event.value,
            "request_id": request_id_var.get(),
            # The authenticated user, when the caller passes it; otherwise the current
            # request's principal (set by the API's authentication dependency).
            "actor": actor_var.get() or "system",
            **fields,
        }

    def record(self, event: AuditEvent, **fields: AuditValue) -> None:
        entry = self.entry(event, fields)
        line = (json.dumps(entry, ensure_ascii=False) + "\n").encode()
        with self._lock:
            self.path.parent.mkdir(mode=0o750, parents=True, exist_ok=True)
            fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o640)
            try:
                os.write(fd, line)
                os.fsync(fd)
            finally:
                os.close(fd)
