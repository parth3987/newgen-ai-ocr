"""Local filesystem document store.

Layout under ``data_dir``::

    quarantine/<upload_id>.part              incoming bytes, not yet validated
    originals/<document_id>/original.<ext>   accepted originals (read-only)
    derived/<document_id>/manifest.json      metadata (written last, atomically)
    derived/<document_id>/pages/0001.png     rendered / normalised page images
    derived/<document_id>/text/0001.json     embedded PDF text with evidence
    audit/audit.jsonl                        append-only audit events

Originals and derived artefacts live in separate trees. Derived output is built in a
staging directory and renamed into place, so a document is either fully visible or absent.
Stored names are generated; client filenames never reach the filesystem.
"""

from __future__ import annotations

import os
import re
import shutil
import uuid
from pathlib import Path

from PIL import Image

from newgen_ocr.errors import AppError, ErrorCode
from newgen_ocr.ingestion.formats import EXTENSIONS, DocumentFormat
from newgen_ocr.ingestion.models import DocumentManifest, PageText

_DOCUMENT_ID = re.compile(r"^[0-9a-f]{32}$")
_DIR_MODE = 0o750
_FILE_MODE = 0o640
_ORIGINAL_MODE = 0o440


def new_document_id() -> str:
    return uuid.uuid4().hex


def is_valid_document_id(document_id: str) -> bool:
    return bool(_DOCUMENT_ID.fullmatch(document_id))


def _page_name(page_number: int) -> str:
    return f"{page_number:04d}"


def _write_atomic(path: Path, data: bytes) -> None:
    tmp = path.with_name(f".{path.name}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, _FILE_MODE)
    with os.fdopen(fd, "wb") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


class StagedDocument:
    """Derived artefacts under construction; invisible to readers until committed."""

    def __init__(self, root: Path) -> None:
        self.root = root
        (root / "pages").mkdir(mode=_DIR_MODE, parents=True)
        (root / "text").mkdir(mode=_DIR_MODE)

    def write_page_image(self, page_number: int, image: Image.Image) -> None:
        path = self.root / "pages" / f"{_page_name(page_number)}.png"
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, _FILE_MODE)
        with os.fdopen(fd, "wb") as fh:
            image.save(fh, format="PNG")

    def write_page_text(self, text: PageText) -> None:
        path = self.root / "text" / f"{_page_name(text.page_number)}.json"
        _write_atomic(path, text.model_dump_json().encode())

    def write_manifest(self, manifest: DocumentManifest) -> None:
        _write_atomic(self.root / "manifest.json", manifest.model_dump_json(indent=2).encode())


class DocumentStore:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self.quarantine_dir = data_dir / "quarantine"
        self.originals_dir = data_dir / "originals"
        self.derived_dir = data_dir / "derived"
        self.staging_dir = data_dir / "staging"

    def _ensure(self, path: Path) -> Path:
        path.mkdir(mode=_DIR_MODE, parents=True, exist_ok=True)
        return path

    # ------------------------------------------------------------ write side

    def new_quarantine_path(self) -> Path:
        return self._ensure(self.quarantine_dir) / f"{uuid.uuid4().hex}.part"

    def stage(self, document_id: str) -> StagedDocument:
        return StagedDocument(self._ensure(self.staging_dir) / document_id)

    def commit(
        self,
        document_id: str,
        quarantined: Path,
        fmt: DocumentFormat,
        staged: StagedDocument,
    ) -> None:
        """Move the validated original and its derived artefacts into place."""
        original_dir = self._ensure(self.originals_dir) / document_id
        original_dir.mkdir(mode=_DIR_MODE)
        original = original_dir / f"original{min(EXTENSIONS[fmt])}"
        os.replace(quarantined, original)
        os.chmod(original, _ORIGINAL_MODE)
        # Manifest is inside the staged tree, so it becomes visible with its pages.
        os.replace(staged.root, self._ensure(self.derived_dir) / document_id)

    def discard(self, quarantined: Path | None, staged: StagedDocument | None) -> None:
        if quarantined is not None:
            quarantined.unlink(missing_ok=True)
        if staged is not None:
            shutil.rmtree(staged.root, ignore_errors=True)

    # ------------------------------------------------------------ read side

    def _document_dir(self, document_id: str) -> Path:
        if not is_valid_document_id(document_id):
            raise AppError(ErrorCode.DOCUMENT_NOT_FOUND, "Document not found")
        root = self.derived_dir / document_id
        if not (root / "manifest.json").is_file():
            raise AppError(ErrorCode.DOCUMENT_NOT_FOUND, "Document not found")
        return root

    def read_manifest(self, document_id: str) -> DocumentManifest:
        path = self._document_dir(document_id) / "manifest.json"
        return DocumentManifest.model_validate_json(path.read_bytes())

    def _check_page(self, manifest: DocumentManifest, page_number: int) -> None:
        if not 1 <= page_number <= manifest.page_count:
            raise AppError(ErrorCode.PAGE_NOT_FOUND, "Page not found")

    def page_image_path(self, document_id: str, page_number: int) -> Path:
        manifest = self.read_manifest(document_id)
        self._check_page(manifest, page_number)
        if manifest.pages[page_number - 1].image is None:
            raise AppError(ErrorCode.PAGE_IMAGE_NOT_AVAILABLE, "No image exists for this page")
        return self.derived_dir / document_id / "pages" / f"{_page_name(page_number)}.png"

    def read_page_text(self, document_id: str, page_number: int) -> PageText:
        manifest = self.read_manifest(document_id)
        self._check_page(manifest, page_number)
        path = self.derived_dir / document_id / "text" / f"{_page_name(page_number)}.json"
        if not path.is_file():
            # Pages without an embedded text layer (scans, image uploads).
            page = manifest.pages[page_number - 1]
            return PageText(
                page_number=page_number,
                page_width=page.width_pt or 0.0,
                page_height=page.height_pt or 0.0,
                text="",
                segments=[],
            )
        return PageText.model_validate_json(path.read_bytes())
