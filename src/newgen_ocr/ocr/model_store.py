"""Local OCR model files: resolution and integrity verification.

The application never downloads models. ``models.lock.json`` lists every model, its pinned
source revision and the SHA-256 of each file; files are fetched at staging time by
``scripts/fetch_models.py``. Here they are only located and verified (once per process).
"""

from __future__ import annotations

import hashlib
import json
import threading
from pathlib import Path

from pydantic import BaseModel

from newgen_ocr.errors import AppError, ErrorCode
from newgen_ocr.ocr.models import ModelRef


class ModelSource(BaseModel):
    type: str
    repo: str
    revision: str


class ModelEntry(BaseModel):
    id: str
    engine: str
    name: str
    kind: str  # detection | recognition | textline_orientation
    languages: list[str]
    license: str
    training_data: str
    source: ModelSource
    dir: str
    files: dict[str, str | None]

    def ref(self) -> ModelRef:
        return ModelRef(
            id=self.id,
            revision=self.source.revision,
            files={name: digest or "" for name, digest in self.files.items()},
        )


class ModelStore:
    def __init__(self, models_dir: Path, lock_file: Path) -> None:
        self.models_dir = models_dir
        try:
            data = json.loads(lock_file.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise AppError(
                ErrorCode.MODEL_NOT_AVAILABLE, "Model lock file is missing or invalid"
            ) from exc
        self.entries = [ModelEntry.model_validate(m) for m in data["models"]]
        self._verified: set[str] = set()
        self._lock = threading.Lock()

    def find(self, engine: str, kind: str, *, name: str | None = None) -> list[ModelEntry]:
        return [
            e
            for e in self.entries
            if e.engine == engine and e.kind == kind and (name is None or e.name == name)
        ]

    def for_language(self, engine: str, language: str) -> ModelEntry | None:
        for entry in self.find(engine, "recognition"):
            if language in entry.languages:
                return entry
        return None

    def path(self, entry: ModelEntry) -> Path:
        return self.models_dir / entry.dir

    def is_present(self, entry: ModelEntry) -> bool:
        root = self.path(entry)
        return all((root / name).is_file() for name in entry.files)

    def verify(self, entry: ModelEntry) -> Path:
        """Return the model directory after checking every file against its pinned hash."""
        root = self.path(entry)
        with self._lock:
            if entry.id in self._verified:
                return root
            for name, expected in entry.files.items():
                path = root / name
                if not path.is_file():
                    raise AppError(
                        ErrorCode.MODEL_NOT_AVAILABLE,
                        f"Model file missing for {entry.id}; fetch models at staging",
                    )
                if not expected:
                    raise AppError(
                        ErrorCode.MODEL_INTEGRITY_ERROR, f"Model {entry.id} has no pinned hash"
                    )
                digest = hashlib.sha256()
                with path.open("rb") as fh:
                    for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                        digest.update(chunk)
                if digest.hexdigest() != expected:
                    raise AppError(
                        ErrorCode.MODEL_INTEGRITY_ERROR, f"Model file hash mismatch for {entry.id}"
                    )
            self._verified.add(entry.id)
        return root
