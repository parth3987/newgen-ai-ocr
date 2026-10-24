"""Operations: ``python -m newgen_ocr.ops verify|retention`` (service mode; ADR-0031).

``verify`` checks that the database and the data volume agree, for example after a
restore. It reads:
- the schema version;
- the review history's hash chain;
- every ready document's manifest and original on the volume;
- every extraction result's file.

It prints a JSON report with counts and a fingerprint of what is stored, so a report taken
before a backup can be compared with one taken after a restore. It exits 1 on any problem.

``retention`` applies the retention rules (``NGOCR_RETENTION_*``). It is a dry run unless
``--apply`` is given:
- documents older than ``NGOCR_RETENTION_DOCUMENT_DAYS`` (unset: kept) are deleted, with
  their files, pages, jobs, results and review items. A document with an active job or an
  open review item is kept;
- events of finished jobs older than ``NGOCR_RETENTION_JOB_EVENT_DAYS`` are deleted;
- ended sessions older than a week are deleted;
- uploads left in quarantine for more than a day by documents that are no longer queued
  are deleted.
Audit events and the review history are append-only and are never deleted (OD-48).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import time
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, func, select

from newgen_ocr.audit import AuditEvent
from newgen_ocr.config import Settings, get_settings
from newgen_ocr.db.__main__ import schema_is_current
from newgen_ocr.db.models import (
    AuditEventRow,
    Document,
    ExtractionRecord,
    Job,
    JobEvent,
    ReviewHistoryRow,
    ReviewItemRow,
    ReviewRevision,
    UserSession,
)
from newgen_ocr.jobs.models import ACTIVE
from newgen_ocr.review.models import OPEN
from newgen_ocr.services import Services, build_services
from newgen_ocr.storage.local import is_valid_document_id


def verify(services: Services) -> dict[str, Any]:
    if services.db is None:
        raise ValueError("verify needs the database (service mode)")
    db, store = services.db, services.store
    problems: list[str] = []
    report: dict[str, Any] = {"database": db.ping()}
    report["schema_current"] = report["database"] and schema_is_current(db)
    if not report["schema_current"]:
        problems.append("database unreachable or not at the latest migration")
    chain = services.review.reviews.verify()
    report["review_history"] = {"valid": chain.valid, "events": chain.events}
    if not chain.valid:
        problems.append(f"review history: {chain.message}")
    fingerprint = hashlib.sha256()
    with db.session() as session:
        documents = session.execute(
            select(Document.id, Document.status, Document.sha256).order_by(Document.id)
        ).all()
        results = session.execute(
            select(ExtractionRecord.document_id, ExtractionRecord.layout_run).order_by(
                ExtractionRecord.document_id, ExtractionRecord.layout_run
            )
        ).all()
        report["counts"] = {
            "documents": len(documents),
            "jobs": session.scalar(select(func.count()).select_from(Job)),
            "extraction_results": len(results),
            "review_items": session.scalar(select(func.count()).select_from(ReviewItemRow)),
            "review_history": session.scalar(select(func.count()).select_from(ReviewHistoryRow)),
            "audit_events": session.scalar(select(func.count()).select_from(AuditEventRow)),
        }
    for document_id, status, sha256 in documents:
        fingerprint.update(f"{document_id}:{status}:{sha256}\n".encode())
        if status != "ready":
            continue
        try:
            manifest = store.read_manifest(document_id)
            if manifest.sha256 != sha256:
                problems.append(f"document {document_id}: manifest hash differs from database")
        except Exception:  # noqa: BLE001 - reported, not raised
            problems.append(f"document {document_id}: manifest missing or unreadable")
            continue
        original_dir = store.originals_dir / document_id
        if not original_dir.is_dir() or not any(original_dir.iterdir()):
            problems.append(f"document {document_id}: original missing")
    for document_id, layout_run in results:
        try:
            stored = store.read_extraction(document_id, layout_run)
            fingerprint.update(f"{document_id}/{layout_run}:{stored.created_at}\n".encode())
        except Exception:  # noqa: BLE001 - reported, not raised
            problems.append(f"extraction {document_id}/{layout_run}: file missing")
    report["fingerprint"] = fingerprint.hexdigest()
    report["problems"] = problems
    report["ok"] = not problems
    return report


def retention(services: Services, settings: Settings, apply: bool) -> dict[str, Any]:
    if services.db is None:
        raise ValueError("retention needs the database (service mode)")
    db, store = services.db, services.store
    now = datetime.now(tz=UTC)
    plan: dict[str, Any] = {"applied": apply, "documents": [], "kept_open": 0}
    with db.session() as session:
        if settings.retention_document_days is not None:
            cutoff = now - timedelta(days=settings.retention_document_days)
            old = session.scalars(select(Document.id).where(Document.created_at < cutoff)).all()
            busy = set(
                session.scalars(
                    select(Job.document_id).where(Job.status.in_([s.value for s in ACTIVE]))
                ).all()
            ) | set(
                session.scalars(
                    select(ReviewItemRow.document_id).where(
                        ReviewItemRow.status.in_([s.value for s in OPEN])
                    )
                ).all()
            )
            plan["documents"] = [d for d in old if d not in busy]
            plan["kept_open"] = len([d for d in old if d in busy])
        event_cutoff = now - timedelta(days=settings.retention_job_event_days)
        finished = select(Job.id).where(
            Job.finished_at.is_not(None), Job.finished_at < event_cutoff
        )
        plan["job_events"] = session.scalar(
            select(func.count()).select_from(JobEvent).where(JobEvent.job_id.in_(finished))
        )
        ended = (UserSession.revoked_at.is_not(None)) | (UserSession.expires_at < now)
        plan["sessions"] = session.scalar(
            select(func.count())
            .select_from(UserSession)
            .where(ended, UserSession.created_at < now - timedelta(days=7))
        )
        queued = set(
            session.scalars(select(Document.id).where(Document.status == "received")).all()
        )
    leftovers = []
    if store.quarantine_dir.is_dir():
        for path in store.quarantine_dir.iterdir():
            stem = path.name.removesuffix(".part")
            age = time.time() - path.stat().st_mtime
            if age > 86400 and not (is_valid_document_id(stem) and stem in queued):
                leftovers.append(path)
    plan["quarantine_files"] = len(leftovers)
    if not apply:
        return plan
    for document_id in plan["documents"]:
        with db.session() as session:
            session.execute(delete(ReviewRevision).where(ReviewRevision.document_id == document_id))
            session.execute(delete(Document).where(Document.id == document_id))  # cascades
        for root in (store.derived_dir, store.originals_dir):
            shutil.rmtree(root / document_id, ignore_errors=True)
        services.audit.record(
            AuditEvent.DOCUMENT_DELETED, document_id=document_id, reason="retention"
        )
    with db.session() as session:
        session.execute(delete(JobEvent).where(JobEvent.job_id.in_(finished)))
        session.execute(
            delete(UserSession).where(ended, UserSession.created_at < now - timedelta(days=7))
        )
    for path in leftovers:
        path.unlink(missing_ok=True)
    return plan


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m newgen_ocr.ops")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("verify", help="check that the database and the data volume agree")
    keep = sub.add_parser("retention", help="apply the retention rules (dry run by default)")
    keep.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    settings = get_settings()
    if not settings.service_mode:
        parser.error("needs the database and broker (service mode)")
    services = build_services(settings, pool_size=1)
    if args.command == "verify":
        report = verify(services)
        sys.stdout.write(json.dumps(report, indent=2, default=str) + "\n")
        return 0 if report["ok"] else 1
    plan = retention(services, settings, args.apply)
    sys.stdout.write(json.dumps(plan, indent=2, default=str) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
