"""Evaluation cohorts, and the wall between tuning and final evaluation (ADR-0033).

Every set of documents the project measures on is a *cohort* in ``evaluation/cohorts.json``
with a **role**:

- ``tuning``: used to choose engines, write rules, set preprocessing or fit review
  thresholds. Its results may never be reported as final accuracy.
- ``evaluation``: used only for final measurement. Nothing may be tuned on it.

A cohort is either **synthetic** (rendered by a generator with a fixed seed) or **real**
(a directory of documents with ground truth; see ``evaluation/real/README.md``). Each
document is identified by the SHA-256 of its file and by a hash of its normalised truth
text, and :func:`check_separation` refuses an evaluation that shares either with any
tuning cohort. Synthetic evaluation cohorts use seeds no tuning set uses, but share the
generators' templates and vocabulary with them; the reports say so (``independence``).
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


class Role(StrEnum):
    TUNING = "tuning"
    EVALUATION = "evaluation"


class DataKind(StrEnum):
    SYNTHETIC = "synthetic"
    REAL = "real"


class Cohort(BaseModel):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9_.-]{0,63}$")
    title: str
    role: Role
    kind: DataKind
    # What the documents are: reported with every result.
    languages: list[str]
    document_types: list[str]
    conditions: list[str]
    # Which metric families apply (ocr, fields, tables, review, handwriting, language_id).
    metrics: list[str]
    # synthetic: {"generator": name, "seed": int, "count": int, ...generator options};
    # real: {"dataset": "<directory relative to evaluation/real/>"}.
    source: dict[str, Any]
    # How independent of the tuning data the cohort is, in plain words.
    independence: str = ""
    notes: str = ""


class Registry(BaseModel):
    cohorts: list[Cohort]

    def get(self, cohort_id: str) -> Cohort:
        for cohort in self.cohorts:
            if cohort.id == cohort_id:
                return cohort
        raise KeyError(cohort_id)

    def by_role(self, role: Role) -> list[Cohort]:
        return [c for c in self.cohorts if c.role == role]


def load_registry(path: Path) -> Registry:
    registry = Registry.model_validate_json(path.read_text())
    ids = [c.id for c in registry.cohorts]
    duplicates = {i for i in ids if ids.count(i) > 1}
    if duplicates:
        raise ValueError(f"duplicate cohort ids: {sorted(duplicates)}")
    seeds: dict[tuple[str, int], str] = {}
    for cohort in registry.cohorts:
        if cohort.kind == DataKind.SYNTHETIC:
            key = (str(cohort.source["generator"]), int(cohort.source["seed"]))
            if key in seeds:
                raise ValueError(f"{cohort.id} reuses the generator seed of {seeds[key]}")
            seeds[key] = cohort.id
    return registry


class DocumentRecord(BaseModel):
    """One document of a cohort, as the separation check and the reports see it."""

    cohort: str
    id: str
    file_sha256: str
    text_sha256: str  # of the normalised ground-truth text; "" if the truth has no text
    language: str
    document_type: str
    condition: str


_SPACE = re.compile(r"\s+")


def normalise_text(text: str) -> str:
    return _SPACE.sub(" ", unicodedata.normalize("NFC", text)).strip().casefold()


def text_hash(text: str) -> str:
    normalised = normalise_text(text)
    return hashlib.sha256(normalised.encode()).hexdigest() if normalised else ""


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check_separation(tuning: list[DocumentRecord], evaluation: list[DocumentRecord]) -> list[str]:
    """Problems if any evaluation document is (by content or by truth text) a tuning one."""
    files = {d.file_sha256: d for d in tuning}
    texts = {d.text_sha256: d for d in tuning if d.text_sha256}
    problems = []
    for doc in evaluation:
        if doc.file_sha256 in files:
            other = files[doc.file_sha256]
            problems.append(f"{doc.cohort}/{doc.id} is the same file as {other.cohort}/{other.id}")
        elif doc.text_sha256 and doc.text_sha256 in texts:
            other = texts[doc.text_sha256]
            problems.append(f"{doc.cohort}/{doc.id} has the same text as {other.cohort}/{other.id}")
    return problems


def write_manifest(path: Path, records: list[DocumentRecord]) -> None:
    path.write_text(
        json.dumps([r.model_dump() for r in records], indent=2, ensure_ascii=False) + "\n"
    )


def read_manifest(path: Path) -> list[DocumentRecord]:
    return [DocumentRecord.model_validate(r) for r in json.loads(path.read_text())]


def evaluation_folders(registry: Registry, root: Path) -> dict[Path, str]:
    """The directories holding evaluation cohorts' documents (generated or real)."""
    folders = {}
    for cohort in registry.by_role(Role.EVALUATION):
        if cohort.kind == DataKind.REAL:
            folder = root / "evaluation" / "real" / str(cohort.source["dataset"])
        else:
            folder = root / "evaluation" / "cohorts" / "generated" / cohort.id
        folders[folder.resolve()] = cohort.id
    return folders


def refuse_evaluation_data(path: Path, registry: Registry, root: Path) -> None:
    """Tuning tools call this: nothing may be tuned on an evaluation cohort."""
    target = path.resolve()
    for folder, cohort in evaluation_folders(registry, root).items():
        if target == folder or folder in target.parents or target in folder.parents:
            raise ValueError(
                f"{path} holds evaluation cohort {cohort!r}: tuning on it would invalidate the "
                "final evaluation (register a tuning cohort instead)"
            )
