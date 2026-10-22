from __future__ import annotations

from pathlib import Path

import pytest

from newgen_ocr.errors import AppError, ErrorCode
from newgen_ocr.storage.local import DocumentStore, is_valid_document_id, new_document_id


def test_document_ids() -> None:
    doc = new_document_id()
    assert is_valid_document_id(doc)
    assert new_document_id() != doc
    for bad in ("", "../etc", "A" * 32, "0" * 31, "0" * 33, "0" * 31 + "/"):
        assert not is_valid_document_id(bad)


@pytest.mark.parametrize("document_id", ["../../etc/passwd", "0" * 32])
def test_unknown_or_malformed_ids_are_not_found(tmp_path: Path, document_id: str) -> None:
    store = DocumentStore(tmp_path)
    with pytest.raises(AppError) as exc:
        store.read_manifest(document_id)
    assert exc.value.code is ErrorCode.DOCUMENT_NOT_FOUND


def test_staged_document_is_invisible_until_committed(tmp_path: Path) -> None:
    store = DocumentStore(tmp_path)
    doc = new_document_id()
    store.stage(doc)
    with pytest.raises(AppError):
        store.read_manifest(doc)
