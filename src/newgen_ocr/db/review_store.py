"""Review items and history in PostgreSQL (service mode, ADR-0029).

Same interface and behaviour as the file store (``newgen_ocr.review.store``). ``locked``
is one transaction holding a transaction-level advisory lock, so every change (history
first, then the item) commits together or not at all, across all API processes. The
history table is append-only (database trigger) and keeps the SHA-256 chain.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from sqlalchemy import ColumnElement, func, select, text
from sqlalchemy.orm import Session

from newgen_ocr.db.engine import Database
from newgen_ocr.db.models import ReviewHistoryRow, ReviewItemRow, ReviewRevision
from newgen_ocr.errors import AppError, ErrorCode
from newgen_ocr.review.models import ChainVerification, HistoryEvent, ReviewItem, ReviewItemStatus
from newgen_ocr.review.store import GENESIS, ReviewStore, event_hash

# Arbitrary constant naming the review lock among advisory locks.
_LOCK_KEY = 0x4E474F435252  # "NGOCRR"


class SqlReviewStore(ReviewStore):
    def __init__(self, db: Database) -> None:  # the file paths of the base are not used
        self.db = db
        self._local = threading.local()

    @contextmanager
    def locked(self) -> Iterator[None]:
        with self.db.session() as session:
            session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": _LOCK_KEY})
            self._local.session = session
            try:
                yield
            finally:
                self._local.session = None

    @contextmanager
    def _session(self) -> Iterator[Session]:
        current: Session | None = getattr(self._local, "session", None)
        if current is not None:
            yield current
            return
        with self.db.session() as session:
            yield session

    def get(self, document_id: str, layout_run: str) -> ReviewItem | None:
        with self._session() as session:
            row = session.get(ReviewItemRow, (document_id, layout_run))
            return ReviewItem.model_validate(row.item) if row is not None else None

    def write(self, item: ReviewItem) -> None:
        body = item.model_dump(mode="json")
        with self._session() as session:
            row = session.get(ReviewItemRow, (item.document_id, item.layout_run))
            values: dict[str, Any] = {
                "status": item.status.value,
                "priority": item.priority,
                "schema_id": item.schema_id,
                "assigned_to": item.assigned_to,
                "revision": item.revision,
                "version": item.version,
                "created_at": item.created_at,
                "updated_at": item.updated_at,
                "item": body,
            }
            if row is None:
                session.add(
                    ReviewItemRow(
                        document_id=item.document_id, layout_run=item.layout_run, **values
                    )
                )
            else:
                for key, value in values.items():
                    setattr(row, key, value)
            session.flush()

    def archive(self, item: ReviewItem) -> None:
        with self._session() as session:
            session.add(
                ReviewRevision(
                    document_id=item.document_id,
                    layout_run=item.layout_run,
                    revision=item.revision,
                    item=item.model_dump(mode="json"),
                )
            )
            session.flush()

    def items(self) -> Iterator[ReviewItem]:
        with self._session() as session:
            rows = session.scalars(select(ReviewItemRow.item)).all()
        for item in rows:
            yield ReviewItem.model_validate(item)

    def query(
        self,
        statuses: set[ReviewItemStatus],
        schema_id: str | None,
        reason: str | None,
        assigned_to: str | None,
        limit: int,
        offset: int,
    ) -> tuple[int, list[ReviewItem]]:
        where: list[ColumnElement[bool]] = [ReviewItemRow.status.in_([s.value for s in statuses])]
        if schema_id is not None:
            where.append(ReviewItemRow.schema_id == schema_id)
        if assigned_to is not None:
            where.append(ReviewItemRow.assigned_to == assigned_to)
        if reason is not None:
            where.append(ReviewItemRow.item["reasons"].contains([reason]))
        with self._session() as session:
            total = session.scalar(select(func.count()).select_from(ReviewItemRow).where(*where))
            rows = session.scalars(
                select(ReviewItemRow.item)
                .where(*where)
                .order_by(ReviewItemRow.priority.desc(), ReviewItemRow.created_at)
                .limit(limit)
                .offset(offset)
            ).all()
        return int(total or 0), [ReviewItem.model_validate(r) for r in rows]

    # ------------------------------------------------------------ history

    def _tail(self) -> tuple[int, str]:
        with self._session() as session:
            row = session.execute(
                select(ReviewHistoryRow.seq, ReviewHistoryRow.hash)
                .order_by(ReviewHistoryRow.seq.desc())
                .limit(1)
            ).first()
        return (int(row[0]), str(row[1])) if row is not None else (0, GENESIS)

    def append(self, event: dict[str, Any]) -> HistoryEvent:
        if getattr(self._local, "session", None) is None:
            raise RuntimeError("history events are appended under the review lock")
        seq, prev = self._tail()
        body = {**event, "seq": seq + 1, "prev_hash": prev}
        body = HistoryEvent.model_validate({**body, "hash": ""}).model_dump(mode="json")
        body["hash"] = event_hash(body)
        record = HistoryEvent.model_validate(body)
        with self._session() as session:
            session.add(
                ReviewHistoryRow(
                    seq=record.seq,
                    ts=record.ts,
                    kind=record.kind.value,
                    actor=record.actor,
                    document_id=record.document_id,
                    layout_run=record.layout_run,
                    body=body,
                    prev_hash=record.prev_hash,
                    hash=record.hash,
                )
            )
            session.flush()
        return record

    def history(
        self, document_id: str | None = None, layout_run: str | None = None
    ) -> Iterator[HistoryEvent]:
        query = select(ReviewHistoryRow.body).order_by(ReviewHistoryRow.seq)
        if document_id is not None:
            query = query.where(ReviewHistoryRow.document_id == document_id)
        if layout_run is not None:
            query = query.where(ReviewHistoryRow.layout_run == layout_run)
        with self._session() as session:
            rows = session.scalars(query).all()
        for body in rows:
            yield HistoryEvent.model_validate(body)

    def verify(self) -> ChainVerification:
        prev, count = GENESIS, 0
        with self._session() as session:
            rows = session.scalars(
                select(ReviewHistoryRow.body).order_by(ReviewHistoryRow.seq)
            ).all()
        for number, body in enumerate(rows, start=1):
            ok = (
                body.get("seq") == number
                and body.get("prev_hash") == prev
                and event_hash(body) == body.get("hash")
            )
            if not ok:
                return ChainVerification(
                    valid=False,
                    events=count,
                    first_bad_seq=int(body.get("seq", number)),
                    message=f"event {number} does not match the chain",
                )
            prev, count = body["hash"], count + 1
        return ChainVerification(
            valid=True, events=count, message="chain intact" if count else "no history yet"
        )

    def read(self, document_id: str, layout_run: str) -> ReviewItem:
        item = self.get(document_id, layout_run)
        if item is None:
            raise AppError(ErrorCode.REVIEW_ITEM_NOT_FOUND, "Review item not found")
        return item
