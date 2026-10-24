"""Release evaluation (ADR-0033): every evaluation cohort, every metric, one report each.

Usage::

    python scripts/evaluate_release.py                    # every evaluation cohort available
    python scripts/evaluate_release.py --cohorts eval-tables,eval-printed-hi
    python scripts/evaluate_release.py --rebuild          # regenerate the synthetic cohorts

1. Loads ``evaluation/cohorts.json``. Synthetic evaluation cohorts are generated into
   ``evaluation/cohorts/generated/<cohort>/`` (git-ignored) from their fixed seeds; real
   ones are read from ``evaluation/real/<dataset>/`` if present (evaluation/real/README.md)
   and reported as *not measured* otherwise.
2. **Separation:** builds the manifests of every tuning cohort and refuses to run if any
   evaluation document is a tuning document, by file or by truth text.
3. Runs each document through the full offline pipeline in local mode (ingest,
   preprocessing, OCR, layout, extraction, review assessment with the shipped calibration),
   one page at a time, timing each stage and measuring the process's CPU time and memory.
4. Writes ``reports/evaluation/<cohort>.json`` (metadata: size, languages, document types,
   conditions, data kind, independence, environment; metrics with sample sizes and 95%
   intervals; slices by language, condition and document type; every document's row) and
   ``reports/evaluation/summary.json``.

Performance figures from this script are for the machine it runs on and are labelled with
it; the release gates accept performance only from the service benchmark on the reference
server (``ngocrctl benchmark``).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
import os
import platform
import resource
import shutil
import subprocess  # nosec B404 - git, fixed arguments
import sys
import time
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from newgen_ocr import __version__
from newgen_ocr.api.app import create_app
from newgen_ocr.config import Settings
from newgen_ocr.evaluation.client import authorized
from newgen_ocr.evaluation.cohort_data import GENERATORS
from newgen_ocr.evaluation.cohorts import (
    Cohort,
    DataKind,
    DocumentRecord,
    Role,
    check_separation,
    file_hash,
    load_registry,
    read_manifest,
    text_hash,
    write_manifest,
)
from newgen_ocr.evaluation.layout_metrics import match_table, shift, table_scores
from newgen_ocr.evaluation.metrics import edit_distance, normalize
from newgen_ocr.evaluation.release_metrics import (
    field_scores,
    field_summary,
    performance_summary,
    review_summary,
    same_value,
    table_summary,
    text_errors,
)
from newgen_ocr.extraction.models import ExtractionResult
from newgen_ocr.layout.models import LayoutRun
from newgen_ocr.ocr.models import OcrRun
from newgen_ocr.review.service import ReviewService

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "evaluation" / "cohorts.json"
GENERATED = ROOT / "evaluation" / "cohorts" / "generated"
REAL = ROOT / "evaluation" / "real"
TEXT_DIR = ROOT / "evaluation" / "corpus" / "text"
CALIBRATION = ROOT / "evaluation" / "calibration" / "synthetic-calibration.json"
OUT = ROOT / "reports" / "evaluation"


def _out(message: str = "") -> None:
    sys.stdout.write(message + "\n")
    sys.stdout.flush()


# ----------------------------------------------------------------------------- documents
def documents(cohort: Cohort, rebuild: bool) -> tuple[Path, list[DocumentRecord]] | None:
    """The cohort's directory and records; None for a real cohort with no data yet."""
    if cohort.kind == DataKind.REAL:
        folder = REAL / str(cohort.source["dataset"])
        if not folder.is_dir():
            return None
        return folder, real_records(cohort, folder)
    folder = GENERATED / cohort.id
    manifest = folder / "manifest.json"
    if rebuild or not manifest.is_file():
        shutil.rmtree(folder, ignore_errors=True)
        folder.mkdir(parents=True)
        generator = GENERATORS[str(cohort.source["generator"])]
        write_manifest(manifest, generator(folder, cohort.id, cohort.source, TEXT_DIR))
    return folder, read_manifest(manifest)


def real_records(cohort: Cohort, folder: Path) -> list[DocumentRecord]:
    records = []
    for truth_file in sorted(folder.glob("*.truth.json")):
        truth = json.loads(truth_file.read_text())
        doc_id = truth_file.name.removesuffix(".truth.json")
        image = next(p for p in folder.iterdir() if p.stem == doc_id and p.suffix != ".json")
        records.append(
            DocumentRecord(
                cohort=cohort.id,
                id=doc_id,
                file_sha256=file_hash(image),
                text_sha256=text_hash(truth.get("text") or ""),
                language=truth["language"],
                document_type=truth["document_type"],
                condition=truth.get("condition", "as-received"),
            )
        )
    return records


def tuning_records(cohort: Cohort) -> list[DocumentRecord]:
    """Every document of a tuning cohort (its files must exist: they are regenerated by the
    phase that owns them, e.g. `make eval` or `make calibrate`)."""
    if cohort.kind == DataKind.REAL:
        folder = REAL / str(cohort.source["dataset"])
        return real_records(cohort, folder) if folder.is_dir() else []
    folder = ROOT / str(cohort.source["path"])
    if not folder.is_dir():
        raise SystemExit(
            f"tuning cohort {cohort.id} is not generated at {folder}: the separation check "
            "needs it (build it with the evaluation that owns it)"
        )
    records = []
    for image in sorted(folder.glob("*.png")):
        text_file = image.with_suffix(".txt")
        text = text_file.read_text() if text_file.is_file() else ""
        records.append(
            DocumentRecord(
                cohort=cohort.id,
                id=image.stem,
                file_sha256=file_hash(image),
                text_sha256=text_hash(text),
                language="",
                document_type="",
                condition="",
            )
        )
    return records


# ----------------------------------------------------------------------------- processing
def _cpu() -> float:
    own = resource.getrusage(resource.RUSAGE_SELF)
    children = resource.getrusage(resource.RUSAGE_CHILDREN)  # Tesseract runs as a subprocess
    return own.ru_utime + own.ru_stime + children.ru_utime + children.ru_stime


def _rss_mib() -> float:
    for line in Path("/proc/self/status").read_text().splitlines():
        if line.startswith("VmRSS:"):
            return int(line.split()[1]) / 1024
    return 0.0


def text_row(truth: str, hypothesis: str) -> dict[str, int]:
    ref, hyp = normalize(truth), normalize(hypothesis)
    vocabulary: dict[str, int] = {}
    ref_words = [vocabulary.setdefault(w, len(vocabulary)) for w in ref.split()]
    hyp_words = [vocabulary.setdefault(w, len(vocabulary)) for w in hyp.split()]
    return {
        "char_edits": edit_distance([ord(c) for c in ref], [ord(c) for c in hyp]),
        "chars": len(ref),
        "word_edits": edit_distance(ref_words, hyp_words),
        "words": len(ref_words),
    }


def process(
    client: TestClient,
    review: ReviewService,
    cohort: Cohort,
    folder: Path,
    record: DocumentRecord,
) -> dict[str, Any]:
    truth = json.loads((folder / f"{record.id}.truth.json").read_text())
    image = next(p for p in folder.iterdir() if p.stem == record.id and p.suffix != ".json")
    row: dict[str, Any] = {
        "id": record.id,
        "language": record.language,
        "document_type": record.document_type,
        "condition": record.condition,
    }
    start, cpu = time.perf_counter(), _cpu()
    try:
        upload = client.post(
            "/api/v1/documents",
            files={"file": (image.name, image.read_bytes(), _media_type(image))},
        )
        upload.raise_for_status()
        doc = upload.json()["document_id"]
        request: dict[str, Any] = {"languages": truth.get("languages", [record.language])}
        if "language_id" in cohort.metrics:
            request = {"languages": ["auto"]}
        if truth.get("handwriting"):
            request["handwriting"] = True
        ocr = client.post(f"/api/v1/documents/{doc}/ocr", json=request)
        ocr.raise_for_status()
        run = OcrRun.model_validate(ocr.json())
        page = run.pages[0]
        hypothesis = "\n".join(line.text for line in page.lines)
        row["ocr"] = {
            "engine": page.engine,
            "languages": page.languages,
            "status": page.status,
            "needs_review": page.review.needs_review,
            "review_reasons": [str(r) for r in page.review.reasons],
        }
        if truth.get("text"):
            row["text"] = text_row(truth["text"], hypothesis)
        if truth.get("handwriting"):
            row["handwriting_routed_to_review"] = page.review.needs_review or str(page.status) in (
                "unsupported",
            )
        if truth.get("tables") or truth.get("fields") is not None:
            layout = client.post(f"/api/v1/documents/{doc}/layout", json={})
            layout.raise_for_status()
            if truth.get("tables"):
                layout_page = LayoutRun.model_validate(layout.json()).pages[0]
                # Deskew rotates onto an enlarged canvas, centred: shift the truth by half
                # the growth (as scripts/evaluate_layout.py does).
                size = _image_size(image) or (page.image_width or 0, page.image_height or 0)
                dx = ((page.image_width or 0) - size[0]) / 2
                dy = ((page.image_height or 0) - size[1]) / 2
                row["tables"] = [
                    table_scores(t, match_table(t, layout_page.tables), page.lines)
                    for t in (shift(t, dx, dy) for t in truth["tables"])
                ]
        if truth.get("fields") is not None:
            extraction = client.post(f"/api/v1/documents/{doc}/extraction", json={})
            extraction.raise_for_status()
            result = ExtractionResult.model_validate(extraction.json())
            extracted = {f.name: {"status": str(f.status), "value": f.value} for f in result.fields}
            type_correct = result.schema_id == record.document_type
            counts = (
                field_scores(truth["fields"], extracted)
                if type_correct
                else field_scores(truth["fields"], {})
            )
            row["type_correct"] = type_correct
            row["fields"] = counts
            row["review"] = review_counts(review, result, truth["fields"], type_correct, counts)
    except Exception as exc:  # noqa: BLE001 - every failure is recorded and counted
        row["error"] = f"{type(exc).__name__}: {str(exc)[:200]}"
    row["seconds"] = round(time.perf_counter() - start, 3)
    row["cpu_seconds"] = round(_cpu() - cpu, 3)
    row["rss_mib"] = round(_rss_mib(), 1)
    return row


def _media_type(path: Path) -> str:
    return mimetypes.guess_type(path.name)[0] or "application/octet-stream"


def _image_size(image: Path) -> tuple[int, int] | None:
    """A raster's size (truth boxes are in its pixels); None for a PDF (page coordinates)."""
    from PIL import Image, UnidentifiedImageError

    try:
        with Image.open(image) as img:
            return img.size
    except UnidentifiedImageError:
        return None


def review_counts(
    review: ReviewService,
    result: ExtractionResult,
    truth: dict[str, str | None],
    type_correct: bool,
    counts: dict[str, int],
) -> dict[str, Any]:
    """What the review gate accepted without a person, and whether it was right."""
    assessment = review.assess(result)
    values = {f.name: f for f in result.fields}
    total = verified = verified_correct = 0
    for decision in assessment.fields:
        field = values.get(decision.field)
        if field is None or str(field.status) != "extracted":
            continue
        total += 1
        if decision.verified:
            verified += 1
            expected = truth.get(decision.field)
            verified_correct += int(
                type_correct and expected not in (None, "") and same_value(field.value, expected)
            )
    document_correct = (
        type_correct and counts["values_correct"] == counts["values"] and counts["invented"] == 0
    )
    return {
        "values_total": total,
        "values_verified": verified,
        "values_verified_correct": verified_correct,
        "document_eligible": assessment.eligible,
        "document_correct": document_correct,
        "reasons": assessment.reasons,
    }


# ----------------------------------------------------------------------------- reports
def summarise(rows: list[dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {"documents": len(rows)}
    texts = [r["text"] for r in rows if "text" in r]
    if texts:
        out["text"] = text_errors(texts)
    with_fields = [r for r in rows if "fields" in r]
    if with_fields:
        out["fields"] = field_summary(with_fields)
        out["review"] = review_summary(with_fields)
    tables = [t for r in rows for t in r.get("tables", [])]
    if tables:
        out["tables"] = table_summary(tables)
    handwriting = [
        r["handwriting_routed_to_review"] for r in rows if "handwriting_routed_to_review" in r
    ]
    if handwriting:
        from newgen_ocr.evaluation.release_metrics import wilson

        out["handwriting_routed_to_review"] = wilson(sum(handwriting), len(handwriting))
    return out


def slices(rows: list[dict[str, Any]], key: str) -> dict[str, Any]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[str(row[key])].append(row)
    return (
        {name: summarise(group) for name, group in sorted(groups.items())}
        if len(groups) > 1
        else {}
    )


def environment() -> dict[str, Any]:
    cpu_model = ""
    for line in Path("/proc/cpuinfo").read_text().splitlines():
        if line.startswith("model name"):
            cpu_model = line.split(":", 1)[1].strip()
            break
    memory = next(
        int(line.split()[1]) // 1024
        for line in Path("/proc/meminfo").read_text().splitlines()
        if line.startswith("MemTotal:")
    )
    git = shutil.which("git") or "git"
    commit = subprocess.run(  # noqa: S603  # nosec B603 - fixed arguments
        [git, "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=False
    ).stdout.strip()
    dirty = bool(
        subprocess.run(  # noqa: S603  # nosec B603 - fixed arguments
            [git, "status", "--porcelain"], cwd=ROOT, capture_output=True, text=True, check=False
        ).stdout.strip()
    )
    return {
        "host": {
            "cpu_model": cpu_model,
            "cpus": os.cpu_count(),
            "memory_mib": memory,
            "os": platform.platform(),
        },
        "mode": "in-process, local mode, one page at a time",
        "version": __version__,
        "commit": commit,
        "dirty": dirty,
        "models_lock_sha256": hashlib.sha256((ROOT / "models.lock.json").read_bytes()).hexdigest(),
        "calibration": CALIBRATION.name,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cohorts", default="", help="comma-separated ids (default: all)")
    parser.add_argument("--rebuild", action="store_true")
    parser.add_argument("--output", type=Path, default=OUT)
    parser.add_argument(
        "--limit", type=int, default=0, help="first N documents per cohort (trial runs only)"
    )
    args = parser.parse_args()
    registry = load_registry(REGISTRY)
    wanted = [c for c in args.cohorts.split(",") if c]
    cohorts = [registry.get(c) for c in wanted] if wanted else registry.by_role(Role.EVALUATION)
    if any(c.role != Role.EVALUATION for c in cohorts):
        raise SystemExit("only evaluation cohorts can be evaluated; tuning results are never final")

    prepared: dict[str, tuple[Path, list[DocumentRecord]] | None] = {
        c.id: documents(c, args.rebuild) for c in cohorts
    }
    tuning = [r for c in registry.by_role(Role.TUNING) for r in tuning_records(c)]
    evaluation = [r for p in prepared.values() if p for r in p[1]]
    problems = check_separation(tuning, evaluation)
    # Evaluation cohorts must not share documents with each other either.
    seen: dict[str, str] = {}
    for record in evaluation:
        other = seen.setdefault(record.file_sha256, f"{record.cohort}/{record.id}")
        if other != f"{record.cohort}/{record.id}":
            problems.append(f"{record.cohort}/{record.id} is the same file as {other}")
    separation = {
        "tuning_documents": len(tuning),
        "evaluation_documents": len(evaluation),
        "problems": problems,
        "passed": not problems,
    }
    _out(f"separation: {len(evaluation)} evaluation vs {len(tuning)} tuning documents, "
         f"{len(problems)} problems")  # fmt: skip
    if problems:
        for problem in problems[:20]:
            _out(f"  {problem}")
        return 2

    data_dir = ROOT / "reports" / "eval-release-data"
    shutil.rmtree(data_dir, ignore_errors=True)
    settings = Settings(  # type: ignore[call-arg]
        data_dir=data_dir,
        models_dir=ROOT / "models",
        models_lock=ROOT / "models.lock.json",
        review_calibration_file=CALIBRATION,
        log_level="WARNING",
        _env_file=None,
    )
    app = create_app(settings)
    client = authorized(TestClient(app))
    review: ReviewService = app.state.review
    env = environment()
    args.output.mkdir(parents=True, exist_ok=True)
    summary: dict[str, Any] = {
        "created": datetime.now(tz=UTC).isoformat(),
        "environment": env,
        "separation": separation,
        "cohorts": {},
    }
    for cohort in cohorts:
        meta = {
            "id": cohort.id,
            "title": cohort.title,
            "kind": cohort.kind.value,
            "languages": cohort.languages,
            "document_types": cohort.document_types,
            "conditions": cohort.conditions,
            "independence": cohort.independence,
            "notes": cohort.notes,
        }
        prepared_cohort = prepared[cohort.id]
        if prepared_cohort is None:
            summary["cohorts"][cohort.id] = {**meta, "status": "not measured (no data)"}
            _out(f"{cohort.id}: not measured (no data in {REAL / str(cohort.source['dataset'])})")
            continue
        folder, records = prepared_cohort
        if args.limit:
            records = records[: args.limit]
        rows = []
        started = time.perf_counter()
        for record in records:
            row = process(client, review, cohort, folder, record)
            rows.append(row)
            _out(_line(cohort.id, row))
        wall = time.perf_counter() - started
        report = {
            **meta,
            "status": "measured" if not args.limit else "trial (limited; not for release gates)",
            "dataset_size": len(records),
            "environment": env,
            "metrics": summarise(rows),
            "performance": performance_summary(rows, wall),
            "peak_rss_mib": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1),
            "by_language": slices(rows, "language"),
            "by_condition": slices(rows, "condition"),
            "by_document_type": slices(rows, "document_type"),
            "documents": rows,
        }
        (args.output / f"{cohort.id}.json").write_text(
            json.dumps(report, indent=2, ensure_ascii=False) + "\n"
        )
        summary["cohorts"][cohort.id] = {k: v for k, v in report.items() if k != "documents"}
    (args.output / "summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n"
    )
    _out(f"reports written to {args.output}")
    return 0


def _line(cohort: str, row: dict[str, Any]) -> str:
    parts = [f"{cohort:32s} {row['id']:24s} {row['seconds']:6.1f}s"]
    if "error" in row:
        parts.append(f"ERROR {row['error']}")
    if "text" in row and row["text"]["chars"]:
        parts.append(f"cer={row['text']['char_edits'] / row['text']['chars']:.3f}")
    if "fields" in row:
        f = row["fields"]
        parts.append(f"fields={f['values_correct']}/{f['values']}")
        parts.append("auto" if row["review"]["document_eligible"] else "review")
    for table in row.get("tables", []):
        parts.append(f"table_exact={table['structure_exact']}")
    if "handwriting_routed_to_review" in row:
        parts.append(f"routed={row['handwriting_routed_to_review']}")
    return " ".join(parts)


if __name__ == "__main__":
    raise SystemExit(main())
