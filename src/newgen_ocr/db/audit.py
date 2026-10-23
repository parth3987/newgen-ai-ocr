"""Audit events in PostgreSQL (service mode, ADR-0029). Same content rules as the file
log: identifiers, hashes, sizes and outcome codes only. The table is append-only."""

from __future__ import annotations

from datetime import datetime

from newgen_ocr.audit import AuditEvent, AuditLog, AuditValue
from newgen_ocr.db.engine import Database
from newgen_ocr.db.models import AuditEventRow


class DbAuditLog(AuditLog):
    def __init__(self, db: Database) -> None:
        self.db = db

    def record(self, event: AuditEvent, **fields: AuditValue) -> None:
        entry = self.entry(event, fields)
        ts, actor = entry.pop("ts"), entry.pop("actor")
        entry.pop("event")
        request_id = entry.pop("request_id")
        document_id = entry.get("document_id")
        with self.db.session() as session:
            session.add(
                AuditEventRow(
                    ts=datetime.fromisoformat(str(ts)),
                    event=event.value,
                    actor=str(actor),
                    request_id=request_id if isinstance(request_id, str) else None,
                    document_id=document_id if isinstance(document_id, str) else None,
                    fields=entry,
                )
            )
