"""Evaluate script/orientation identification, Hindi/Marathi identification and mixed-script
routing on the synthetic corpus (docs/ACCURACY_EVALUATION.md, ADR-0023).

Usage: python scripts/evaluate_language_id.py [--quick] [--output reports/language_id.json]
Runs offline with the pinned models. Requires `make models` and the generated corpus.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from PIL import Image

from newgen_ocr.api.app import build_engines
from newgen_ocr.config import Settings
from newgen_ocr.evaluation.corpus import (
    DEFAULT_FONT_DIR,
    LANGUAGE_FONTS,
    build,
    load_lines,
    load_manifest,
    render_page,
)
from newgen_ocr.evaluation.metrics import cer
from newgen_ocr.ocr.language_id import identify
from newgen_ocr.ocr.languages import LANGUAGES
from newgen_ocr.ocr.model_store import ModelStore
from newgen_ocr.ocr.models import DetectionStatus
from newgen_ocr.ocr.script_detect import ScriptDetector, rotate_clockwise
from newgen_ocr.ocr.service import route_languages

ROOT = Path(__file__).resolve().parents[1]
TEXT = ROOT / "evaluation" / "corpus" / "text"
CORPUS = ROOT / "evaluation" / "corpus" / "generated"
TURN = {90: cv2.ROTATE_90_COUNTERCLOCKWISE, 180: cv2.ROTATE_180, 270: cv2.ROTATE_90_CLOCKWISE}
UNSUPPORTED = {
    "bengali": ("NotoSansBengali-Regular.ttf", "এই চালানটি নিচে তালিকাভুক্ত পণ্য সরবরাহের নিশ্চয়তা দেয়।"),
    "gujarati": ("NotoSansGujarati-Regular.ttf", "આ ઇન્વોઇસ નીચે સૂચિબદ્ધ માલની ડિલિવરીની પુષ્ટિ કરે છે."),
    "arabic": ("NotoSansArabic-Regular.ttf", "تؤكد هذه الفاتورة تسليم البضائع المدرجة أدناه."),
    "russian": (
        "NotoSans-Regular.ttf",
        "Этот счёт подтверждает доставку перечисленных ниже товаров.",
    ),
}


def _out(message: str = "") -> None:
    sys.stdout.write(message + "\n")
    sys.stdout.flush()


def script_identification(detector: ScriptDetector, quick: bool) -> dict[str, Any]:
    rows = []
    for sample in load_manifest(CORPUS):
        page = np.asarray(Image.open(CORPUS / sample.image).convert("L"))
        rotations = [0, 180] + ([90, 270] if sample.condition == "scan" else [])
        if quick:
            rotations = [0]
        for rotation in rotations:
            image = (
                page if rotation == 0 else np.ascontiguousarray(cv2.rotate(page, TURN[rotation]))
            )
            start = time.perf_counter()
            result = detector.detect(image)
            rows.append(
                {
                    "sample": sample.id,
                    "language": sample.language,
                    "rotation": rotation,
                    "script_ok": result.status is DetectionStatus.DETECTED
                    and result.script == LANGUAGES[sample.language].script,
                    "orientation_ok": result.orientation_reliable
                    and (result.orientation_deg or 0) == rotation,
                    "osd_script_ok": result.osd_script == LANGUAGES[sample.language].script,
                    "seconds": round(time.perf_counter() - start, 2),
                }
            )
    unsupported = []
    for name, (font, text) in UNSUPPORTED.items():
        path = DEFAULT_FONT_DIR / font
        if not path.is_file():
            continue
        result = detector.detect(render_page([text] * 10, path, 200))
        routed_supported = result.status is DetectionStatus.DETECTED and result.script in {
            info.script for info in LANGUAGES.values()
        }
        unsupported.append(
            {
                "script": name,
                "status": result.status.value,
                "named": result.script,
                "routed_to_supported": routed_supported,
            }
        )
    blank = detector.detect(np.full((2338, 1654), 255, np.uint8)).status.value
    summary = {
        "pages": len(rows),
        "script_accuracy": round(statistics.mean(r["script_ok"] for r in rows), 4),
        "osd_only_script_accuracy": round(statistics.mean(r["osd_script_ok"] for r in rows), 4),
        "orientation_accuracy": round(statistics.mean(r["orientation_ok"] for r in rows), 4),
        "mean_seconds": round(statistics.mean(r["seconds"] for r in rows), 2),
        "unsupported_routed_to_supported": sum(u["routed_to_supported"] for u in unsupported),
        "blank_page_status": blank,
    }
    return {"summary": summary, "rows": rows, "unsupported": unsupported}


def devanagari_identification(engines: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for sample in load_manifest(CORPUS):
        if sample.language not in ("hi", "mr"):
            continue
        image = np.asarray(Image.open(CORPUS / sample.image).convert("L"))
        for name in ("paddle", "tesseract"):
            text = "\n".join(
                line.text for line in engines[name].recognize(image, ["hi", "mr", "en"])
            )
            guess = identify(text)
            rows.append(
                {
                    "sample": sample.id,
                    "engine": name,
                    "truth": sample.language,
                    "guess": guess.languages,
                    "uncertain": guess.uncertain,
                    "correct": guess.languages == [sample.language],
                }
            )
    return rows


def mixed_scripts(
    detector: ScriptDetector, engines: dict[str, Any], settings: Settings
) -> list[dict[str, Any]]:
    rows = []
    english = load_lines(TEXT, "en")
    for language in ("hi", "mr", "te", "ta", "kn"):
        indic = load_lines(TEXT, language)
        for label, first, second in (
            ("indic-dominant", (language, indic[:6]), ("en", english[:3])),
            ("english-dominant", ("en", english[:6]), (language, indic[:2])),
        ):
            parts = [
                render_page(lines, DEFAULT_FONT_DIR / LANGUAGE_FONTS[lang][0], 200)[
                    150 : 150 + 60 * len(lines) + 40
                ]
                for lang, lines in (first, second)
            ]
            page = np.vstack(parts)
            truth = "\n".join(first[1] + second[1])
            detection = detector.detect(page)
            if detection.status is not DetectionStatus.DETECTED or detection.script is None:
                rows.append({"page": f"{language}/{label}", "routed": None, "cer": None})
                continue
            languages, note = route_languages(detection.script, detection.secondary_scripts)
            engine = settings.ocr_language_primary.get(languages[0], settings.ocr_primary_engine)
            image = (
                rotate_clockwise(page, int(detection.orientation_deg or 0))
                if detection.orientation_reliable
                else page
            )
            text = "\n".join(line.text for line in engines[engine].recognize(image, languages))
            rows.append(
                {
                    "page": f"{language}/{label}",
                    "detected": detection.script,
                    "secondary": detection.secondary_scripts,
                    "routed": languages,
                    "engine": engine,
                    "cer": round(cer(truth, text), 4),
                    "note": note,
                }
            )
    return rows


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--quick", action="store_true", help="upright pages only")
    parser.add_argument("--output", type=Path, default=ROOT / "reports" / "language_id.json")
    args = parser.parse_args()
    if not (CORPUS / "manifest.json").is_file():
        build(TEXT, CORPUS)
    settings = Settings(
        models_dir=ROOT / "models",
        models_lock=ROOT / "models.lock.json",
        data_dir=ROOT / "reports" / "eval-data",
        _env_file=None,
    )  # type: ignore[call-arg]
    detector = ScriptDetector(
        ModelStore(settings.models_dir, settings.models_lock), "tesseract", 300
    )
    engines = build_engines(settings)

    scripts = script_identification(detector, args.quick)
    _out("script/orientation: " + json.dumps(scripts["summary"]))
    for item in scripts["unsupported"]:
        _out(
            f"  unsupported {item['script']:9s} -> {item['status']:12s} named={item['named']} "
            f"routed_to_supported={item['routed_to_supported']}"
        )
    misses = [r for r in scripts["rows"] if not (r["script_ok"] and r["orientation_ok"])]
    for r in misses:
        _out(
            f"  MISS {r['sample']} rot={r['rotation']} script_ok={r['script_ok']} "
            f"orientation_ok={r['orientation_ok']}"
        )

    deva = devanagari_identification(engines)
    _out(
        f"hi/mr identification: {sum(r['correct'] for r in deva)}/{len(deva)} correct, "
        f"{sum(r['uncertain'] for r in deva)} uncertain"
    )
    for r in deva:
        if not r["correct"]:
            _out(f"  {r['sample']} {r['engine']}: guess {r['guess']} uncertain={r['uncertain']}")

    mixed = mixed_scripts(detector, engines, settings)
    _out("mixed-script auto routing:")
    for r in mixed:
        _out(
            f"  {r['page']:22s} detected={r.get('detected')} secondary={r.get('secondary')} "
            f"routed={r['routed']} engine={r.get('engine')} CER={r['cer']}"
        )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps({"scripts": scripts, "devanagari": deva, "mixed": mixed}, indent=2)
    )
    _out(f"results written to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
