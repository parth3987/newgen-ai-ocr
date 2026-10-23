"""Fit field-specific review thresholds on held-out ground truth (ADR-0028).

Usage:
  python scripts/calibrate_review.py [--per-type 60] [--rebuild]      synthetic set
  python scripts/calibrate_review.py --dataset DIR                    real labelled documents
  python scripts/calibrate_review.py --from-reviews DATA_DIR          approved review items
  python scripts/calibrate_review.py --refit                          refit saved observations
  python scripts/calibrate_review.py --resume                         continue an interrupted run
  python scripts/calibrate_review.py --reassess                       re-gate stored results
Options: --target 0.95 --confidence 0.95 --min-samples 50
         --output reports/calibration/calibration.json --report reports/calibration/report.json

Each document runs through the whole offline pipeline (ingest, OCR with the second engine
for cross-checking, layout, extraction) and the review assessment *without* thresholds.
Its fields become observations: the value's confidence, whether it passed every other gate,
and whether it matched the ground truth. Thresholds are fitted on the calibration split
and then measured, unchanged, on the test split.

A dataset directory holds images and a ``manifest.json`` list of
``{"id", "image", "document_type", "language", "fields": {name: value or null}, "split"}``
(split "calibration" or "test"; without it, every third document is test). Values use the
normalised forms of docs/EXTRACTION.md (ISO dates, "1180.00", masked Aadhaar).

``--from-reviews`` takes approved items of a data directory as ground truth (no OCR re-run):
the approved value of each field is the truth for what was extracted. Thresholds fitted
this way describe the documents that were reviewed; see docs/REVIEW.md.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from collections import Counter
from dataclasses import asdict
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from newgen_ocr.api.app import create_app
from newgen_ocr.config import Settings
from newgen_ocr.evaluation.calibration_corpus import build, load_manifest
from newgen_ocr.evaluation.client import authorized
from newgen_ocr.extraction.models import ExtractionResult
from newgen_ocr.extraction.schema import SchemaRegistry
from newgen_ocr.review.calibration import (
    LabelledDocument,
    Observation,
    content_id,
    evaluate,
    fit,
)
from newgen_ocr.review.labelled import from_reviews, labelled
from newgen_ocr.review.models import (
    CalibrationDataset,
    CalibrationKind,
)
from newgen_ocr.review.service import ReviewService
from newgen_ocr.storage.local import DocumentStore

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "evaluation" / "calibration" / "generated"
OUT = ROOT / "reports" / "calibration"


def _out(message: str = "") -> None:
    sys.stdout.write(message + "\n")
    sys.stdout.flush()


def run_pipeline(
    samples: list[Any], corpus: Path, data_dir: Path, journal: Path, resume: bool
) -> list[LabelledDocument]:
    """Each document is appended to ``journal`` as soon as it is done; with ``resume``,
    documents already in it are not run again."""
    docs = _load(journal) if resume and journal.is_file() else []
    done = {d.sample_id for d in docs}
    if not resume:
        shutil.rmtree(data_dir, ignore_errors=True)
        journal.parent.mkdir(parents=True, exist_ok=True)
        journal.write_text("", encoding="utf-8")
    settings = Settings(  # type: ignore[call-arg]
        data_dir=data_dir,
        models_dir=ROOT / "models",
        models_lock=ROOT / "models.lock.json",
        log_level="WARNING",
        _env_file=None,
    )
    app = create_app(settings)
    client = authorized(TestClient(app))
    review: ReviewService = app.state.review
    for n, sample in enumerate(samples, start=1):
        if sample.id in done:
            continue
        image = corpus / sample.image
        doc = client.post(
            "/api/v1/documents", files={"file": (image.name, image.read_bytes(), "image/png")}
        ).json()["document_id"]
        ocr = client.post(
            f"/api/v1/documents/{doc}/ocr",
            json={"languages": [sample.language], "compare": True},
        )
        if ocr.status_code != 201:
            ocr = client.post(f"/api/v1/documents/{doc}/ocr", json={"languages": [sample.language]})
        client.post(f"/api/v1/documents/{doc}/layout", json={})
        result = ExtractionResult.model_validate(
            client.post(f"/api/v1/documents/{doc}/extraction", json={}).json()
        )
        assessment = review.assess(result)
        labelled_doc = labelled(
            sample.id, sample.split, sample.document_type, sample.fields, result, assessment
        )
        docs.append(labelled_doc)
        with journal.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(asdict(labelled_doc), default=str) + "\n")
        wrong = [o.field for o in labelled_doc.observations if not o.correct]
        _out(
            f"[{n}/{len(samples)}] {sample.id:32s} {sample.split:11s} "
            f"type={'ok' if labelled_doc.type_correct else result.schema_id} "
            f"obs={len(labelled_doc.observations)} blocked={labelled_doc.other_blocked} "
            f"wrong={wrong}"
        )
    return docs


def reassess(
    samples: list[Any], corpus: Path, data_dir: Path, journal: Path
) -> list[LabelledDocument]:
    """Assess the extraction results a previous run stored, matched to samples by hash."""
    settings = Settings(data_dir=data_dir, log_level="WARNING", _env_file=None)  # type: ignore[call-arg]
    app = create_app(settings)
    review: ReviewService = app.state.review
    store = DocumentStore(data_dir)
    by_hash = {
        store.read_manifest(p.name).sha256: p.name
        for p in (data_dir / "derived").iterdir()
        if (p / "manifest.json").is_file()
    }
    docs = []
    for sample in samples:
        digest = hashlib.sha256((corpus / sample.image).read_bytes()).hexdigest()
        result = store.read_extraction(by_hash[digest], "auto")
        docs.append(
            labelled(
                sample.id,
                sample.split,
                sample.document_type,
                sample.fields,
                result,
                review.assess(result),
            )
        )
    journal.write_text(
        "".join(json.dumps(asdict(d), default=str) + "\n" for d in docs), encoding="utf-8"
    )
    return docs


def _load(path: Path) -> list[LabelledDocument]:
    docs = []
    for line in path.read_text(encoding="utf-8").splitlines():
        body = json.loads(line)
        body["observations"] = tuple(
            Observation(**{**o, "kind": CalibrationKind(o["kind"])}) for o in body["observations"]
        )
        docs.append(LabelledDocument(**body))
    return docs


def main() -> int:
    parser = argparse.ArgumentParser()
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--dataset", type=Path)
    source.add_argument("--from-reviews", type=Path)
    source.add_argument("--refit", action="store_true")
    parser.add_argument("--per-type", type=int, default=60)
    parser.add_argument("--rebuild", action="store_true")
    parser.add_argument("--resume", action="store_true", help="continue an interrupted run")
    parser.add_argument(
        "--reassess",
        action="store_true",
        help="re-run only the review assessment on the stored pipeline results (after a "
        "change to the gates; no OCR)",
    )
    parser.add_argument("--target", type=float, default=0.95)
    parser.add_argument("--confidence", type=float, default=0.95)
    parser.add_argument("--min-samples", type=int, default=50)
    parser.add_argument("--output", type=Path, default=OUT / "calibration.json")
    parser.add_argument("--report", type=Path, default=OUT / "report.json")
    args = parser.parse_args()
    observations_path = args.report.with_name("observations.jsonl")

    manifest_sha = None
    if args.refit:
        docs = _load(observations_path)
        meta = json.loads(args.report.read_text(encoding="utf-8"))["dataset"]
        dataset = CalibrationDataset(**meta)
    elif args.from_reviews:
        docs = from_reviews(args.from_reviews)
        dataset = CalibrationDataset(
            name=f"approved reviews in {args.from_reviews.name}",
            kind="reviews",
            samples=len(docs),
            description="approved review items; truth is the approved value",
        )
    else:
        corpus = args.dataset or CORPUS
        if args.dataset is None and (args.rebuild or not (corpus / "manifest.json").is_file()):
            build(corpus, per_type=args.per_type)
        samples = load_manifest(corpus)
        manifest_sha = hashlib.sha256((corpus / "manifest.json").read_bytes()).hexdigest()
        if args.reassess:
            docs = reassess(samples, corpus, OUT / "data", observations_path)
        else:
            docs = run_pipeline(samples, corpus, OUT / "data", observations_path, args.resume)
        dataset = CalibrationDataset(
            name=corpus.name if args.dataset else "synthetic calibration set",
            kind="real" if args.dataset else "synthetic",
            samples=len(docs),
            description=(
                "labelled documents"
                if args.dataset
                else "seeded synthetic documents (newgen_ocr.evaluation.calibration_corpus)"
            ),
            manifest_sha256=manifest_sha,
        )

    fit_docs = [d for d in docs if d.split == "calibration"]
    test_docs = [d for d in docs if d.split == "test"]
    registry = SchemaRegistry()
    calibration = fit(
        [o for d in fit_docs for o in d.observations],
        dataset,
        {s.id: s.version for s in registry.schemas.values()},
        target_precision=args.target,
        confidence_level=args.confidence,
        min_samples=args.min_samples,
    )
    calibration.test = evaluate(test_docs, calibration)
    calibration.notes.append(
        f"fitted on {len(fit_docs)} documents, tested on {len(test_docs)} held-out documents"
    )
    if dataset.kind == "synthetic":
        calibration.notes.append(
            "synthetic data: demonstrates the method; fit on real documents for production"
        )
    calibration.id = content_id(calibration)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(calibration.model_dump_json(indent=2), encoding="utf-8")

    calibrated = sorted(k for k, e in calibration.entries.items() if e.status.value == "calibrated")
    values = [o for d in docs for o in d.observations if o.kind is CalibrationKind.VALUE]
    report = {
        "dataset": dataset.model_dump(),
        "documents": {"calibration": len(fit_docs), "test": len(test_docs)},
        "classification_correct": sum(d.type_correct for d in docs),
        "values": dict(Counter("correct" if o.correct else "wrong" for o in values)),
        "calibrated": calibrated,
        "test": calibration.test.model_dump() if calibration.test else None,
        "calibration_id": calibration.id,
    }
    args.report.write_text(json.dumps(report, indent=2), encoding="utf-8")
    _out(f"\ncalibration {calibration.id} written to {args.output}")
    _out(f"calibrated entries: {len(calibrated)} of {len(calibration.entries)}")
    for key, entry in calibration.entries.items():
        _out(
            f"  {key:45s} {entry.status.value:17s} t={entry.threshold} "
            f"n={entry.observations} acc={entry.accepted} correct={entry.correct} "
            f"lb={entry.lower_bound} cov={entry.coverage}"
        )
    _out(f"test split: {calibration.test}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
