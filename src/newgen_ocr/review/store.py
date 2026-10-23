"""Review queue and history on the local filesystem (ADR-0028).

Layout under ``data_dir/review``::

    items/<document_id>/<layout_run>.json            current item
    items/<document_id>/<layout_run>.r<N>.json       earlier revisions (kept, never edited)
    history.jsonl                                    append-only, hash-chained events
    .lock                                            inter-process lock

Every change takes an exclusive ``flock`` on ``.lock``, so several API processes on one host
can share the store. Each history event carries the SHA-256 of the previous event and its
own hash over its content: editing, removing or reordering events breaks the chain, which
``verify`` reports. (A chain detects tampering; it cannot prevent someone with write access
from rewriting it entirely. Copy the history off-host for that; OD-34.)
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from newgen_ocr.errors import AppError, ErrorCode
from newgen_ocr.review.models import (
    ChainVerification,
    HistoryEvent,
    ReviewItem,
    ReviewItemStatus,
)

_DOCUMENT_ID = re.compile(r"^[0-9a-f]{32}$")
_RUN_NAME = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
GENESIS = "0" * 64


def event_hash(body: dict[str, Any]) -> str:
    content = {k: v for k, v in body.items() if k != "hash"}
    return hashlib.sha256(
        json.dumps(content, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()


class ReviewStore:
    def __init__(self, data_dir: Path) -> None:
        self.root = data_dir / "review"
        self.items_dir = self.root / "items"
        self.history_path = self.root / "history.jsonl"

    @contextmanager
    def locked(self) -> Iterator[None]:
        self.root.mkdir(mode=0o750, parents=True, exist_ok=True)
        fd = os.open(self.root / ".lock", os.O_RDWR | os.O_CREAT, 0o640)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def _path(self, document_id: str, layout_run: str) -> Path:
        if not _DOCUMENT_ID.fullmatch(document_id) or not _RUN_NAME.fullmatch(layout_run):
            raise AppError(ErrorCode.REVIEW_ITEM_NOT_FOUND, "Review item not found")
        return self.items_dir / document_id / f"{layout_run}.json"

    def get(self, document_id: str, layout_run: str) -> ReviewItem | None:
        path = self._path(document_id, layout_run)
        if not path.is_file():
            return None
        return ReviewItem.model_validate_json(path.read_bytes())

    def read(self, document_id: str, layout_run: str) -> ReviewItem:
        item = self.get(document_id, layout_run)
        if item is None:
            raise AppError(ErrorCode.REVIEW_ITEM_NOT_FOUND, "Review item not found")
        return item

    def write(self, item: ReviewItem) -> None:
        path = self._path(item.document_id, item.layout_run)
        path.parent.mkdir(mode=0o750, parents=True, exist_ok=True)
        tmp = path.with_name(f".{path.name}.tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o640)
        with os.fdopen(fd, "wb") as fh:
            fh.write(item.model_dump_json(indent=2).encode())
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)

    def archive(self, item: ReviewItem) -> None:
        """Keep a superseded revision next to the current item."""
        path = self._path(item.document_id, item.layout_run)
        target = path.with_name(f"{item.layout_run}.r{item.revision}.json")
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o440)
        with os.fdopen(fd, "wb") as fh:
            fh.write(item.model_dump_json(indent=2).encode())

    def items(self) -> Iterator[ReviewItem]:
        if not self.items_dir.is_dir():
            return
        for path in sorted(self.items_dir.glob("*/*.json")):
            if ".r" not in path.stem and not path.name.startswith("."):
                yield ReviewItem.model_validate_json(path.read_bytes())

    def query(
        self,
        statuses: set[ReviewItemStatus],
        schema_id: str | None,
        reason: str | None,
        assigned_to: str | None,
        limit: int,
        offset: int,
    ) -> tuple[int, list[ReviewItem]]:
        """Matching items by priority (highest first), then age, and their total count."""
        matching = [
            i
            for i in self.items()
            if i.status in statuses
            and (schema_id is None or i.schema_id == schema_id)
            and (reason is None or reason in i.reasons)
            and (assigned_to is None or i.assigned_to == assigned_to)
        ]
        matching.sort(key=lambda i: (-i.priority, i.created_at))
        return len(matching), matching[offset : offset + limit]

    # ------------------------------------------------------------ history

    def _tail(self) -> tuple[int, str]:
        """Sequence number and hash of the last event (call under the lock)."""
        if not self.history_path.is_file() or self.history_path.stat().st_size == 0:
            return 0, GENESIS
        with self.history_path.open("rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            block = min(size, 65536)
            while True:
                fh.seek(size - block)
                lines = fh.read(block).splitlines()
                if len(lines) > 1 or block == size:
                    break
                block = min(size, block * 2)
        last = json.loads(lines[-1])
        return int(last["seq"]), str(last["hash"])

    def append(self, event: dict[str, Any]) -> HistoryEvent:
        """Append one event (call under the lock); seq, prev_hash and hash are set here."""
        seq, prev = self._tail()
        body = {**event, "seq": seq + 1, "prev_hash": prev}
        body = HistoryEvent.model_validate({**body, "hash": ""}).model_dump(mode="json")
        body["hash"] = event_hash(body)
        record = HistoryEvent.model_validate(body)
        line = (json.dumps(body, ensure_ascii=False, separators=(",", ":")) + "\n").encode()
        fd = os.open(self.history_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o640)
        try:
            os.write(fd, line)
            os.fsync(fd)
        finally:
            os.close(fd)
        return record

    def history(
        self, document_id: str | None = None, layout_run: str | None = None
    ) -> Iterator[HistoryEvent]:
        if not self.history_path.is_file():
            return
        with self.history_path.open(encoding="utf-8") as fh:
            for line in fh:
                event = HistoryEvent.model_validate_json(line)
                if document_id is not None and event.document_id != document_id:
                    continue
                if layout_run is not None and event.layout_run != layout_run:
                    continue
                yield event

    def verify(self) -> ChainVerification:
        prev, count = GENESIS, 0
        if not self.history_path.is_file():
            return ChainVerification(valid=True, events=0, message="no history yet")
        with self.history_path.open(encoding="utf-8") as fh:
            for number, line in enumerate(fh, start=1):
                try:
                    body = json.loads(line)
                    seq = int(body["seq"])
                    ok = (
                        seq == number
                        and body["prev_hash"] == prev
                        and event_hash(body) == body["hash"]
                    )
                except (ValueError, KeyError, TypeError):
                    ok, seq = False, number
                if not ok:
                    return ChainVerification(
                        valid=False,
                        events=count,
                        first_bad_seq=seq,
                        message=f"event {number} does not match the chain",
                    )
                prev, count = body["hash"], count + 1
        return ChainVerification(valid=True, events=count, message="chain intact")
