"""Evaluate classification, field extraction, tables and checks on the synthetic set.

Usage: python scripts/evaluate_extraction.py [--only PREFIX] [--rebuild]
                                             [--output reports/extraction.json]

Full offline pipeline through the API (ingest, preprocessing, OCR, layout, extraction) on
``newgen_ocr.evaluation.extraction_corpus``. Each field is scored against ground truth:
correct, wrong (a different value), **invented** (a value where the page has none), correctly
missing, or missed. Results are synthetic and must be reported as such.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from newgen_ocr.api.app import create_app
from newgen_ocr.config import Settings
from newgen_ocr.evaluation.extraction_corpus import build, load_manifest

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "evaluation" / "extraction" / "generated"


def _out(message: str = "") -> None:
    sys.stdout.write(message + "\n")
    sys.stdout.flush()


def _same(a: object, b: object) -> bool:
    def norm(v: object) -> str:
        return re.sub(r"\s+", " ", str(v)).strip().casefold()

    return norm(a) == norm(b)


def score_fields(truth: dict[str, str | None], fields: list[dict[str, Any]]) -> Counter[str]:
    counts: Counter[str] = Counter()
    by_name = {f["name"]: f for f in fields}
    for name, expected in truth.items():
        got = by_name.get(name, {"status": "missing", "value": None})
        extracted = got["status"] == "extracted"
        if expected is None:
            counts["invented" if extracted else "correctly_missing"] += 1
        elif not extracted:
            counts["missed"] += 1
        elif _same(got["value"], expected):
            counts["correct"] += 1
        else:
            counts["wrong"] += 1
    return counts


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", default="")
    parser.add_argument("--rebuild", action="store_true")
    parser.add_argument("--output", type=Path, default=ROOT / "reports" / "extraction.json")
    args = parser.parse_args()
    if args.rebuild or not (CORPUS / "manifest.json").is_file():
        build(CORPUS)
    data_dir = ROOT / "reports" / "eval-extraction-data"
    shutil.rmtree(data_dir, ignore_errors=True)
    settings = Settings(  # type: ignore[call-arg]
        data_dir=data_dir,
        models_dir=ROOT / "models",
        models_lock=ROOT / "models.lock.json",
        log_level="WARNING",
        _env_file=None,
    )
    client = TestClient(create_app(settings))
    rows = []
    totals: Counter[str] = Counter()
    for sample in load_manifest(CORPUS):
        if not sample.id.startswith(args.only):
            continue
        image = CORPUS / sample.image
        doc = client.post(
            "/api/v1/documents", files={"file": (image.name, image.read_bytes(), "image/png")}
        ).json()["document_id"]
        client.post(f"/api/v1/documents/{doc}/ocr", json={"languages": [sample.language]})
        client.post(f"/api/v1/documents/{doc}/layout", json={})
        start = time.perf_counter()
        response = client.post(f"/api/v1/documents/{doc}/extraction", json={})
        seconds = time.perf_counter() - start
        result = response.json()
        counts = score_fields(sample.fields, result.get("fields", []))
        totals += counts
        checks = {c["id"]: c["status"] for c in result.get("checks", [])}
        check_ok = sum(1 for k, v in sample.checks.items() if checks.get(k) == v)
        tables = {t["name"]: len(t["records"]) for t in result.get("tables", [])}
        table_ok = sum(1 for k, v in sample.tables.items() if tables.get(k) == v)
        row = {
            "sample": sample.id,
            "expected_type": sample.document_type,
            "type": result.get("schema_id"),
            "classification": result["classification"]["status"],
            "type_ok": result.get("schema_id") == sample.document_type,
            "status": result.get("status"),
            "fields": dict(counts),
            "checks_ok": f"{check_ok}/{len(sample.checks)}",
            "check_mismatches": {
                k: f"{checks.get(k)} (expected {v})"
                for k, v in sample.checks.items()
                if checks.get(k) != v
            },
            "tables_ok": f"{table_ok}/{len(sample.tables)}",
            "field_errors": {
                f["name"]: f"{f['status']}: {f.get('value')!r}"
                for f in result.get("fields", [])
                if f["name"] in sample.fields
                and not (
                    (f["status"] == "extracted" and _same(f["value"], sample.fields[f["name"]]))
                    or (f["status"] != "extracted" and sample.fields[f["name"]] is None)
                )
            },
            "review": result.get("review", {}).get("reasons", []),
            "seconds": round(seconds, 2),
        }
        rows.append(row)
        _out(
            f"{sample.id:28s} type={row['type']!s:15s} ok={row['type_ok']!s:5s} "
            f"fields={dict(counts)} checks={row['checks_ok']} tables={row['tables_ok']} "
            f"status={row['status']}"
        )
        for name, err in row["field_errors"].items():
            _out(f"    {name}: {err} (truth {sample.fields[name]!r})")
        for name, err in row["check_mismatches"].items():
            _out(f"    check {name}: {err}")
    scored = sum(totals.values())
    _out(f"\nfields: {dict(totals)} of {scored}")
    _out(f"classification: {sum(r['type_ok'] for r in rows)}/{len(rows)} correct")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps({"totals": dict(totals), "rows": rows}, indent=2))
    _out(f"results written to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
