"""Local filesystem document store.

Layout under ``data_dir``::

    quarantine/<upload_id>.part              incoming bytes, not yet validated
    originals/<document_id>/original.<ext>   accepted originals (read-only)
    derived/<document_id>/manifest.json      metadata (written last, atomically)
    derived/<document_id>/pages/0001.png     rendered / normalised page images
    derived/<document_id>/text/0001.json     embedded PDF text with evidence
    derived/<document_id>/preprocessed/<profile>/run.json        preprocessing record
    derived/<document_id>/preprocessed/<profile>/pages/0001.png  preprocessed images
    derived/<document_id>/ocr/<engine>.json                      OCR run (per requested engine)
    derived/<document_id>/ocr/comparison.json                    engine cross-check
    derived/<document_id>/layout/<ocr_run>.json                  layout + tables of an OCR run
    derived/<document_id>/extraction/<layout_run>.json           structured extraction
    audit/audit.jsonl                        append-only audit events

Originals and derived artefacts live in separate trees. Derived output is built in a
staging directory and renamed into place, so a document is either fully visible or absent.
Stored names are generated; client filenames never reach the filesystem. Preprocessing
never overwrites source page images; each profile's run is swapped in atomically.
"""

from __future__ import annotations

import os
import re
import shutil
import threading
import uuid
from pathlib import Path

import numpy as np
import numpy.typing as npt
from PIL import Image

from newgen_ocr.errors import AppError, ErrorCode
from newgen_ocr.extraction.models import ExtractionResult
from newgen_ocr.ingestion.formats import EXTENSIONS, DocumentFormat
from newgen_ocr.ingestion.models import DocumentManifest, PageText
from newgen_ocr.layout.models import LayoutRun
from newgen_ocr.ocr.models import OcrComparison, OcrRun
from newgen_ocr.preprocessing.models import PreprocessingRun

_DOCUMENT_ID = re.compile(r"^[0-9a-f]{32}$")
_PROFILE_NAME = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
_RUN_NAME = _PROFILE_NAME
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


# zlib level 3: lossless like every level; measured 43% faster than the default (6) on
# 300 DPI pages, with equal or smaller files (docs/PERFORMANCE.md).
PNG_COMPRESS_LEVEL = 3


def _write_png(path: Path, image: Image.Image) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, _FILE_MODE)
    with os.fdopen(fd, "wb") as fh:
        image.save(fh, format="PNG", compress_level=PNG_COMPRESS_LEVEL)


class StagedDocument:
    """Derived artefacts under construction; invisible to readers until committed."""

    def __init__(self, root: Path) -> None:
        self.root = root
        (root / "pages").mkdir(mode=_DIR_MODE, parents=True)
        (root / "text").mkdir(mode=_DIR_MODE)

    def write_page_image(self, page_number: int, image: Image.Image) -> None:
        _write_png(self.root / "pages" / f"{_page_name(page_number)}.png", image)

    def write_page_text(self, text: PageText) -> None:
        path = self.root / "text" / f"{_page_name(text.page_number)}.json"
        _write_atomic(path, text.model_dump_json().encode())

    def write_manifest(self, manifest: DocumentManifest) -> None:
        _write_atomic(self.root / "manifest.json", manifest.model_dump_json(indent=2).encode())


class StagedRun:
    """A preprocessing run under construction, inside the document's derived tree."""

    def __init__(self, root: Path) -> None:
        self.root = root
        (root / "pages").mkdir(mode=_DIR_MODE, parents=True)

    def write_page_image(self, page_number: int, image: npt.NDArray[np.uint8]) -> None:
        _write_png(self.root / "pages" / f"{_page_name(page_number)}.png", Image.fromarray(image))

    def write_run(self, run: PreprocessingRun) -> None:
        _write_atomic(self.root / "run.json", run.model_dump_json(indent=2).encode())


class DocumentStore:
    def __init__(self, data_dir: Path) -> None:
        self._commit_lock = threading.Lock()
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

    # ------------------------------------------------------------ preprocessing runs

    def _run_dir(self, document_id: str, profile: str) -> Path:
        if not _PROFILE_NAME.fullmatch(profile):
            raise AppError(ErrorCode.PREPROCESSING_NOT_FOUND, "Preprocessing run not found")
        return self._document_dir(document_id) / "preprocessed" / profile

    def stage_run(self, document_id: str) -> StagedRun:
        parent = self._ensure(self._document_dir(document_id) / "preprocessed")
        return StagedRun(parent / f".staging-{uuid.uuid4().hex}")

    def commit_run(self, document_id: str, profile: str, staged: StagedRun) -> None:
        """Replace any previous run of this profile atomically; readers never see a mix."""
        target = self._run_dir(document_id, profile)
        with self._commit_lock:
            previous = None
            if target.exists():
                previous = target.with_name(f".old-{uuid.uuid4().hex}")
                os.replace(target, previous)
            os.replace(staged.root, target)
        if previous is not None:
            shutil.rmtree(previous, ignore_errors=True)

    def discard_run(self, staged: StagedRun) -> None:
        shutil.rmtree(staged.root, ignore_errors=True)

    def list_runs(self, document_id: str) -> list[str]:
        root = self._document_dir(document_id) / "preprocessed"
        if not root.is_dir():
            return []
        return sorted(p.name for p in root.iterdir() if (p / "run.json").is_file())

    def read_run(self, document_id: str, profile: str) -> PreprocessingRun:
        path = self._run_dir(document_id, profile) / "run.json"
        if not path.is_file():
            raise AppError(ErrorCode.PREPROCESSING_NOT_FOUND, "Preprocessing run not found")
        return PreprocessingRun.model_validate_json(path.read_bytes())

    def preprocessed_image_path(self, document_id: str, profile: str, page_number: int) -> Path:
        run = self.read_run(document_id, profile)
        if not 1 <= page_number <= len(run.pages):
            raise AppError(ErrorCode.PAGE_NOT_FOUND, "Page not found")
        if run.pages[page_number - 1].output is None:
            raise AppError(
                ErrorCode.PREPROCESSED_IMAGE_NOT_AVAILABLE,
                "No preprocessed image for this page; use the source page image",
            )
        return self._run_dir(document_id, profile) / "pages" / f"{_page_name(page_number)}.png"

    # ------------------------------------------------------------ OCR runs

    def _ocr_dir(self, document_id: str) -> Path:
        return self._document_dir(document_id) / "ocr"

    def write_ocr_run(self, document_id: str, name: str, run: OcrRun) -> None:
        if not _RUN_NAME.fullmatch(name):
            raise ValueError(f"invalid OCR run name {name!r}")
        root = self._ensure(self._ocr_dir(document_id))
        _write_atomic(root / f"{name}.json", run.model_dump_json(indent=2).encode())

    def list_ocr_runs(self, document_id: str) -> list[str]:
        root = self._ocr_dir(document_id)
        if not root.is_dir():
            return []
        return sorted(p.stem for p in root.glob("*.json") if p.stem != "comparison")

    def read_ocr_run(self, document_id: str, name: str) -> OcrRun:
        path = self._ocr_dir(document_id) / f"{name}.json"
        if not _RUN_NAME.fullmatch(name) or name == "comparison" or not path.is_file():
            raise AppError(ErrorCode.OCR_RUN_NOT_FOUND, "OCR run not found")
        return OcrRun.model_validate_json(path.read_bytes())

    def write_ocr_comparison(self, document_id: str, comparison: OcrComparison) -> None:
        root = self._ensure(self._ocr_dir(document_id))
        _write_atomic(root / "comparison.json", comparison.model_dump_json(indent=2).encode())

    def read_ocr_comparison(self, document_id: str) -> OcrComparison:
        path = self._ocr_dir(document_id) / "comparison.json"
        if not path.is_file():
            raise AppError(ErrorCode.OCR_RUN_NOT_FOUND, "No OCR comparison for this document")
        return OcrComparison.model_validate_json(path.read_bytes())

    # ------------------------------------------------------------ layout runs

    def _layout_dir(self, document_id: str) -> Path:
        return self._document_dir(document_id) / "layout"

    def write_layout_run(self, document_id: str, run: LayoutRun) -> None:
        if not _RUN_NAME.fullmatch(run.ocr_run):
            raise ValueError(f"invalid OCR run name {run.ocr_run!r}")
        root = self._ensure(self._layout_dir(document_id))
        _write_atomic(root / f"{run.ocr_run}.json", run.model_dump_json(indent=2).encode())

    def list_layout_runs(self, document_id: str) -> list[str]:
        root = self._layout_dir(document_id)
        if not root.is_dir():
            return []
        return sorted(p.stem for p in root.glob("*.json"))

    def read_layout_run(self, document_id: str, ocr_run: str) -> LayoutRun:
        path = self._layout_dir(document_id) / f"{ocr_run}.json"
        if not _RUN_NAME.fullmatch(ocr_run) or not path.is_file():
            raise AppError(ErrorCode.LAYOUT_RUN_NOT_FOUND, "Layout run not found")
        return LayoutRun.model_validate_json(path.read_bytes())

    # ------------------------------------------------------------ extraction results

    def _extraction_dir(self, document_id: str) -> Path:
        return self._document_dir(document_id) / "extraction"

    def write_extraction(self, document_id: str, result: ExtractionResult) -> None:
        if not _RUN_NAME.fullmatch(result.layout_run):
            raise ValueError(f"invalid layout run name {result.layout_run!r}")
        root = self._ensure(self._extraction_dir(document_id))
        _write_atomic(root / f"{result.layout_run}.json", result.model_dump_json(indent=2).encode())

    def list_extractions(self, document_id: str) -> list[str]:
        root = self._extraction_dir(document_id)
        if not root.is_dir():
            return []
        return sorted(p.stem for p in root.glob("*.json"))

    def read_extraction(self, document_id: str, layout_run: str) -> ExtractionResult:
        path = self._extraction_dir(document_id) / f"{layout_run}.json"
        if not _RUN_NAME.fullmatch(layout_run) or not path.is_file():
            raise AppError(ErrorCode.EXTRACTION_NOT_FOUND, "Extraction result not found")
        return ExtractionResult.model_validate_json(path.read_bytes())
