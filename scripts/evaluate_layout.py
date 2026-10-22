"""Evaluate layout, reading order, tables and key-value pairs on the synthetic layout set.

Usage: python scripts/evaluate_layout.py [--only PREFIX] [--layout-model NAME]
                                         [--output reports/layout.json]

Runs the full offline pipeline (ingest, standard preprocessing, OCR with the configured
engines, layout) through the API on every page of the generated set
(``newgen_ocr.evaluation.layout_corpus``) and scores it against exact ground truth.
Results are synthetic and must be reported as such (docs/ACCURACY_EVALUATION.md).
"""

from __future__ import annotations

import argparse
import json
import shutil
import statistics
import sys
import time
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from PIL import Image

from newgen_ocr.api.app import create_app
from newgen_ocr.config import Settings
from newgen_ocr.evaluation.layout_corpus import build, load_manifest
from newgen_ocr.evaluation.layout_metrics import (
    furniture_scores,
    key_value_scores,
    match_table,
    reading_order_accuracy,
    shift,
    table_scores,
)
from newgen_ocr.layout.models import LayoutRun
from newgen_ocr.ocr.models import OcrRun

ROOT = Path(__file__).resolve().parents[1]
TEXT = ROOT / "evaluation" / "corpus" / "text"
CORPUS = ROOT / "evaluation" / "layout" / "generated"


def _out(message: str = "") -> None:
    sys.stdout.write(message + "\n")
    sys.stdout.flush()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", default="", help="only sample ids starting with this prefix")
    parser.add_argument("--layout-model", default="PP-DocLayout_plus-L")
    parser.add_argument("--rebuild", action="store_true")
    parser.add_argument("--output", type=Path, default=ROOT / "reports" / "layout.json")
    args = parser.parse_args()
    if args.rebuild or not (CORPUS / "manifest.json").is_file():
        build(TEXT, CORPUS)
    data_dir = ROOT / "reports" / "eval-layout-data"
    shutil.rmtree(data_dir, ignore_errors=True)
    settings = Settings(  # type: ignore[call-arg]
        data_dir=data_dir,
        models_dir=ROOT / "models",
        models_lock=ROOT / "models.lock.json",
        layout_model=args.layout_model,
        log_level="WARNING",
        _env_file=None,
    )
    client = TestClient(create_app(settings))
    rows: list[dict[str, Any]] = []
    for sample in load_manifest(CORPUS):
        if not str(sample["id"]).startswith(args.only):
            continue
        image = CORPUS / str(sample["image"])
        with Image.open(image) as img:
            truth_size = img.size
        upload = client.post(
            "/api/v1/documents", files={"file": (image.name, image.read_bytes(), "image/png")}
        )
        document = upload.json()["document_id"]
        ocr = client.post(
            f"/api/v1/documents/{document}/ocr", json={"languages": [sample["language"]]}
        )
        start = time.perf_counter()
        response = client.post(f"/api/v1/documents/{document}/layout", json={"ocr_run": "auto"})
        seconds = time.perf_counter() - start
        if ocr.status_code != 201 or response.status_code != 201:
            rows.append({"sample": sample["id"], "error": response.text[:200]})
            _out(f"{sample['id']:30s} ERROR {ocr.status_code} {response.status_code}")
            continue
        ocr_page = OcrRun.model_validate(ocr.json()).pages[0]
        lines = ocr_page.lines
        page = LayoutRun.model_validate(response.json()).pages[0]
        row: dict[str, Any] = {
            "sample": sample["id"],
            "kind": sample["kind"],
            "language": sample["language"],
            "condition": sample["condition"],
            "layout_seconds": round(seconds, 2),
            "review": [r.value for r in page.review.reasons],
        }
        # Deskew rotates onto an enlarged canvas, centred: shift truth by half the growth.
        dx = ((ocr_page.image_width or 0) - truth_size[0]) / 2
        dy = ((ocr_page.image_height or 0) - truth_size[1]) / 2
        tables = [shift(t, dx, dy) for t in sample["tables"]]  # type: ignore[attr-defined]
        blocks = [shift(b, dx, dy) for b in sample["blocks"]]  # type: ignore[attr-defined]
        if tables:
            row["table"] = table_scores(tables[0], match_table(tables[0], page.tables), lines)
        if sample["kind"] == "columns":
            row["reading_order"] = reading_order_accuracy(blocks, page, lines)
            row["furniture"] = furniture_scores(blocks, page)
            row["columns"] = [page.column_count, sample["columns"]]
        if sample["kind"] in ("key_value", "table"):
            truth = list(sample["key_values"])  # type: ignore[call-overload]
            if sample["id"].startswith("en-field-value"):
                truth = [[c["text"], d["text"]] for c, d in _rows(tables[0])]
            if truth:
                row["key_values"] = key_value_scores(truth, page)
        rows.append(row)
        _out(_summary_line(row))
    _report(rows)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps({"layout_model": args.layout_model, "rows": rows}, indent=2))
    _out(f"results written to {args.output}")
    return 0


def _rows(table: dict[str, Any]) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    cells = table["cells"]
    return [
        (left, right)
        for left in cells
        for right in cells
        if left["col"] == 0 and right["col"] == 1 and left["row"] == right["row"]
    ]


def _summary_line(row: dict[str, Any]) -> str:
    parts = [f"{row['sample']:30s} {row['layout_seconds']:5.2f}s"]
    if "table" in row:
        t = row["table"]
        parts.append(
            f"table={t['status']} exact={t['structure_exact']} "
            f"assoc_f1={t['association']['f1']} content_f1={t['content_association']['f1']} "
            f"cer={t['cell_cer']} ruling={t.get('ruling_check')}"
        )
    if "reading_order" in row:
        parts.append(
            f"order={row['reading_order']} furniture={row['furniture']} cols={row['columns']}"
        )
    if "key_values" in row:
        parts.append(f"kv={row['key_values']}")
    parts.append(f"review={row['review']}")
    return " ".join(parts)


def _silent(table: dict[str, Any]) -> bool:
    """A detected table with a wrong structure that was not routed to review."""
    return bool(table["detected"] and not table["structure_exact"] and table["status"] == "ok")


def _report(rows: list[dict[str, Any]]) -> None:
    tables = [r for r in rows if "table" in r]
    _out(
        "\n| Condition | Tables | Detected | Structure exact | Wrong structure not flagged "
        "| Association F1 | Content F1 | Cell CER | Routed to review |"
    )
    _out("|---|---|---|---|---|---|---|---|---|")
    for condition in ("clean", "scan"):
        items = [r for r in tables if r["condition"] == condition]
        if not items:
            continue
        _out(
            f"| {condition} | {len(items)} | {sum(r['table']['detected'] for r in items)} | "
            f"{sum(r['table']['structure_exact'] for r in items)} | "
            f"{sum(1 for r in items if _silent(r['table']))} | "
            f"{statistics.mean(r['table']['association']['f1'] for r in items):.3f} | "
            f"{statistics.mean(r['table']['content_association']['f1'] for r in items):.3f} | "
            f"{statistics.mean(r['table']['cell_cer'] for r in items):.3f} | "
            f"{sum(1 for r in items if r['table']['status'] not in ('ok', None))} |"
        )


if __name__ == "__main__":
    raise SystemExit(main())
