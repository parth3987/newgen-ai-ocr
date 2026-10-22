"""Evaluate handwriting recognition (ADR-0024).

Usage:
    python scripts/evaluate_handwriting.py                     # synthetic font proxy
    python scripts/evaluate_handwriting.py --dataset DIR       # real samples (see
                                                               # evaluation/handwriting/README.md)

Synthetic mode renders corpus text in OFL handwriting-style fonts (evaluation/fonts.lock.json).
Font renders are far easier than real handwriting: these numbers are a weak proxy and can
never mark a language as verified. Only real-sample results can (via
NGOCR_OCR_HANDWRITING_VERIFIED_LANGUAGES, after review).
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from newgen_ocr.api.app import build_engines
from newgen_ocr.config import Settings
from newgen_ocr.evaluation.corpus import load_lines, render_page
from newgen_ocr.evaluation.metrics import cer, wer

ROOT = Path(__file__).resolve().parents[1]
TEXT = ROOT / "evaluation" / "corpus" / "text"
FONTS = ROOT / "evaluation" / "fonts" / "handwriting"
# (language, font file, engines to measure); Devanagari is informational only.
SYNTHETIC = [
    ("en", "Caveat[wght].ttf", ("paddle", "paddle_handwriting", "tesseract")),
    ("en", "IndieFlower-Regular.ttf", ("paddle", "paddle_handwriting", "tesseract")),
    ("en", "PatrickHand-Regular.ttf", ("paddle", "paddle_handwriting", "tesseract")),
    ("en", "ShadowsIntoLight.ttf", ("paddle", "paddle_handwriting", "tesseract")),
    ("en", "Kalam-Regular.ttf", ("paddle", "paddle_handwriting", "tesseract")),
    ("hi", "Kalam-Regular.ttf", ("paddle", "tesseract")),
]


def _out(message: str = "") -> None:
    sys.stdout.write(message + "\n")
    sys.stdout.flush()


def _samples(dataset: Path | None) -> list[tuple[str, str, np.ndarray, str, tuple[str, ...]]]:
    """(id, language, image, truth, engines)."""
    samples = []
    if dataset is None:
        for language, font, engines in SYNTHETIC:
            lines = load_lines(TEXT, language)[:8]
            for dpi in (200, 300):
                image = render_page(lines, FONTS / font, dpi)
                samples.append(
                    (
                        f"{language}-{font.split('-')[0]}-{dpi}",
                        language,
                        image,
                        "\n".join(lines),
                        engines,
                    )
                )
        return samples
    for truth_file in sorted(dataset.glob("*.txt")):
        stem = truth_file.stem
        image_file = next((p for p in dataset.glob(f"{stem}.*") if p.suffix != ".txt"), None)
        if image_file is None:
            continue
        language = stem.split("_", 1)[0]
        with Image.open(image_file) as img:
            image = np.asarray(img.convert("L"))
        engines = (
            ("paddle_handwriting", "paddle", "tesseract") if language == "en" else ("tesseract",)
        )
        samples.append((stem, language, image, truth_file.read_text(encoding="utf-8"), engines))
    return samples


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, default=None)
    parser.add_argument("--output", type=Path, default=ROOT / "reports" / "handwriting.json")
    args = parser.parse_args()
    settings = Settings(  # type: ignore[call-arg]
        models_dir=ROOT / "models",
        models_lock=ROOT / "models.lock.json",
        data_dir=ROOT / "reports" / "eval-data",
        _env_file=None,
    )
    engines = build_engines(settings)
    rows: list[dict[str, Any]] = []
    for sample_id, language, image, truth, names in _samples(args.dataset):
        for name in names:
            engine = engines[name]
            if engine.unavailable_reason() is not None or language not in engine.languages():
                continue
            engine.recognize(image, [language])  # warm-up (model load)
            start = time.perf_counter()
            lines = engine.recognize(image, [language])
            seconds = time.perf_counter() - start
            text = "\n".join(line.text for line in lines)
            row = {
                "sample": sample_id,
                "language": language,
                "engine": name,
                "cer": round(cer(truth, text), 4),
                "wer": round(wer(truth, text), 4),
                "seconds": round(seconds, 2),
            }
            rows.append(row)
            _out(
                f"{sample_id:28s} {name:20s} CER {row['cer']:.3f} WER {row['wer']:.3f} "
                f"{seconds:5.2f}s"
            )
    _out("\n| Language | Engine | Samples | CER mean | CER worst | WER mean |")
    _out("|---|---|---|---|---|---|")
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in rows:
        groups.setdefault((row["language"], row["engine"]), []).append(row)
    for (language, name), items in sorted(groups.items()):
        _out(
            f"| {language} | {name} | {len(items)} | "
            f"{statistics.mean(r['cer'] for r in items):.3f} | "
            f"{max(r['cer'] for r in items):.3f} | {statistics.mean(r['wer'] for r in items):.3f} |"
        )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    mode = "real" if args.dataset else "synthetic-font-proxy"
    args.output.write_text(json.dumps({"mode": mode, "rows": rows}, indent=2))
    _out(f"mode: {mode}; results written to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
