"""Measure OCR accuracy (CER/WER) and CPU throughput on the baseline corpus.

Usage:
    python scripts/evaluate_ocr.py --build                  # (re)generate the corpus first
    python scripts/evaluate_ocr.py [--engines paddle,tesseract] [--languages en,hi]
        [--conditions clean,scan,lowres] [--preprocess standard] [--threads N]
        [--output reports/ocr_eval.json]

Runs fully offline with the locally fetched, hash-verified models. The first call per
engine and language loads models; that time is reported separately and excluded from
per-page throughput. Results feed docs/ACCURACY_EVALUATION.md and docs/PERFORMANCE.md.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from newgen_ocr.api.app import build_engines
from newgen_ocr.config import Settings
from newgen_ocr.evaluation.corpus import build, load_manifest
from newgen_ocr.evaluation.metrics import cer, wer
from newgen_ocr.imaging.quality import assess
from newgen_ocr.imaging.runtime import runtime_info
from newgen_ocr.preprocessing.pipeline import preprocess_page
from newgen_ocr.preprocessing.profiles import BUILTIN_PROFILES

ROOT = Path(__file__).resolve().parents[1]

CORPUS_TEXT = ROOT / "evaluation" / "corpus" / "text"
CORPUS_DIR = ROOT / "evaluation" / "corpus" / "generated"


def _out(message: str = "") -> None:
    sys.stdout.write(message + "\n")
    sys.stdout.flush()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--build", action="store_true")
    parser.add_argument("--engines", default="paddle,tesseract")
    parser.add_argument("--languages", default="")
    parser.add_argument("--conditions", default="")
    parser.add_argument("--preprocess", default="", help="profile to apply before OCR")
    parser.add_argument("--threads", type=int, default=None)
    parser.add_argument("--output", type=Path, default=ROOT / "reports" / "ocr_eval.json")
    args = parser.parse_args()

    if args.build or not (CORPUS_DIR / "manifest.json").is_file():
        build(CORPUS_TEXT, CORPUS_DIR)
    samples = load_manifest(CORPUS_DIR)
    if args.languages:
        samples = [s for s in samples if s.language in args.languages.split(",")]
    if args.conditions:
        samples = [s for s in samples if s.condition in args.conditions.split(",")]

    settings = Settings(
        models_dir=ROOT / "models",
        models_lock=ROOT / "models.lock.json",
        data_dir=ROOT / "reports" / "eval-data",
        ocr_cpu_threads=args.threads,
        _env_file=None,  # type: ignore[call-arg]
    )
    engines = build_engines(settings)
    profile = BUILTIN_PROFILES[args.preprocess] if args.preprocess else None
    info = runtime_info()
    _out(f"CPU: {info.cpu_model} | logical CPUs: {info.logical_cpus} | threads: {args.threads}")
    _out(f"Samples: {len(samples)} | preprocessing: {args.preprocess or 'none'}")

    rows: list[dict[str, Any]] = []
    load_times: dict[str, float] = {}
    for engine_name in args.engines.split(","):
        engine = engines[engine_name]
        reason = engine.unavailable_reason()
        if reason is not None:
            _out(f"SKIP {engine_name}: {reason}")
            continue
        warmed: set[str] = set()
        for sample in samples:
            if sample.language not in engine.languages():
                _out(f"SKIP {engine_name}/{sample.id}: no model for {sample.language}")
                continue
            with Image.open(CORPUS_DIR / sample.image) as img:
                image = np.asarray(img.convert("L"))
            if profile is not None:
                quality, _ = assess(image, sample.dpi)
                processed, _ = preprocess_page(1, image, sample.dpi, profile, quality)
                image = processed if processed is not None else image
            languages = [sample.language]
            if sample.language not in warmed:
                start = time.perf_counter()
                engine.recognize(image, languages)
                load_times[f"{engine_name}/{sample.language}"] = time.perf_counter() - start
                warmed.add(sample.language)
            start = time.perf_counter()
            lines = engine.recognize(image, languages)
            seconds = time.perf_counter() - start
            truth = (CORPUS_DIR / sample.truth).read_text(encoding="utf-8")
            text = "\n".join(line.text for line in lines)
            row = {
                "engine": engine_name,
                "sample": sample.id,
                "language": sample.language,
                "condition": sample.condition,
                "font": sample.font,
                "dpi": sample.dpi,
                "cer": round(cer(truth, text), 4),
                "wer": round(wer(truth, text), 4),
                "seconds": round(seconds, 3),
                "lines": len(lines),
            }
            rows.append(row)
            _out(
                f"{engine_name:9s} {sample.id:40s} CER {row['cer']:.3f}  WER {row['wer']:.3f}  "
                f"{seconds:6.2f}s"
            )

    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[(row["engine"], row["language"], row["condition"])].append(row)
    summary = [
        {
            "engine": engine,
            "language": language,
            "condition": condition,
            "pages": len(items),
            "cer": round(statistics.mean(r["cer"] for r in items), 4),
            "wer": round(statistics.mean(r["wer"] for r in items), 4),
            "seconds_per_page": round(statistics.mean(r["seconds"] for r in items), 2),
        }
        for (engine, language, condition), items in sorted(groups.items())
    ]
    _out("\n| Engine | Lang | Condition | Pages | CER | WER | s/page |")
    _out("|---|---|---|---|---|---|---|")
    for s in summary:
        _out(
            f"| {s['engine']} | {s['language']} | {s['condition']} | {s['pages']} | "
            f"{s['cer']:.3f} | {s['wer']:.3f} | {s['seconds_per_page']:.2f} |"
        )
    for engine_name in sorted({r["engine"] for r in rows}):
        seconds = [r["seconds"] for r in rows if r["engine"] == engine_name]
        _out(
            f"\n{engine_name}: {len(seconds)} pages, mean {statistics.mean(seconds):.2f} s/page "
            f"({60 / statistics.mean(seconds):.1f} pages/min, one process)"
        )
    _out(
        "model load (first call) seconds: "
        + json.dumps({k: round(v, 1) for k, v in load_times.items()})
    )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(
            {
                "runtime": info.model_dump(),
                "threads": args.threads,
                "preprocessing": args.preprocess or None,
                "rows": rows,
                "summary": summary,
                "model_load_seconds": load_times,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    _out(f"results written to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
