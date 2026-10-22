"""Append-only audit trail of document lifecycle events.

Events carry identifiers, hashes, sizes, formats and outcome codes, never document
content or client filenames. One JSON object per line in ``audit/audit.jsonl``.
"""

from __future__ import annotations

import json
import os
import threading
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

from newgen_ocr.logging_config import request_id_var


class AuditEvent(StrEnum):
    DOCUMENT_ACCEPTED = "document.accepted"
    DOCUMENT_REJECTED = "document.rejected"


AuditValue = str | int | bool | None


class AuditLog:
    def __init__(self, data_dir: Path) -> None:
        self.path = data_dir / "audit" / "audit.jsonl"
        self._lock = threading.Lock()

    def record(self, event: AuditEvent, **fields: AuditValue) -> None:
        entry: dict[str, AuditValue] = {
            "ts": datetime.now(tz=UTC).isoformat(),
            "event": event.value,
            "request_id": request_id_var.get(),
            # Placeholder until authentication exists (DECISIONS.md OD-6).
            "actor": "anonymous",
            **fields,
        }
        line = (json.dumps(entry, ensure_ascii=False) + "\n").encode()
        with self._lock:
            self.path.parent.mkdir(mode=0o750, parents=True, exist_ok=True)
            fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o640)
            try:
                os.write(fd, line)
                os.fsync(fd)
            finally:
                os.close(fd)
