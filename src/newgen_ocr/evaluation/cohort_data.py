"""Synthetic evaluation cohorts (ADR-0033): rendered pages with exact ground truth.

Each generator writes ``<id>.png`` and ``<id>.truth.json`` per document and returns their
:class:`~newgen_ocr.evaluation.cohorts.DocumentRecord`s. The truth holds whatever applies:
``text`` (for CER and WER), ``fields`` and ``document_type`` (for field and document exact
match, and auto-accept), ``tables`` (for table structure).

Independence from the tuning corpora, which the reports repeat:
- **seeds** no tuning set uses (checked by the registry);
- **pages composed afresh**: random word sequences, not the tuning corpus sentences (the
  vocabulary is the same);
- **fonts the tuning corpus never rendered**: DejaVu Sans and Nimbus Roman for English,
  and the Bold weights of Noto Sans and Noto Serif for the Indian scripts (the only
  families available offline for them);
- the business-document and table **templates** are the tuning generators' own, with new
  values.

So these cohorts check the measurement machinery and catch regressions; they are not
evidence about real documents. Real cohorts are (evaluation/real/README.md).
"""

from __future__ import annotations

import io
import json
import random
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path
from typing import Any

from PIL import Image, ImageFont

from newgen_ocr.evaluation import calibration_corpus as business
from newgen_ocr.evaluation.cohorts import DocumentRecord, file_hash, text_hash
from newgen_ocr.evaluation.corpus import degrade_scan, load_lines, render_page
from newgen_ocr.evaluation.layout_corpus import CellSpec, TableSpec, table_page

Gray = Any  # numpy uint8 array (kept loose: generators come from several modules)

FONT_ROOT = Path("/usr/share/fonts")
EVALUATION_FONTS: dict[str, tuple[str, ...]] = {
    "en": ("truetype/dejavu/DejaVuSans.ttf", "opentype/urw-base35/NimbusRoman-Regular.otf"),
    "hi": (
        "truetype/noto/NotoSansDevanagari-Bold.ttf",
        "truetype/noto/NotoSerifDevanagari-Bold.ttf",
    ),
    "mr": (
        "truetype/noto/NotoSansDevanagari-Bold.ttf",
        "truetype/noto/NotoSerifDevanagari-Bold.ttf",
    ),
    "te": ("truetype/noto/NotoSansTelugu-Bold.ttf", "truetype/noto/NotoSerifTelugu-Bold.ttf"),
    "ta": ("truetype/noto/NotoSansTamil-Bold.ttf", "truetype/noto/NotoSerifTamil-Bold.ttf"),
    "kn": ("truetype/noto/NotoSansKannada-Bold.ttf", "truetype/noto/NotoSerifKannada-Bold.ttf"),
}
# condition -> render DPI (and degradation)
CONDITIONS = {"clean": 300, "scan": 200, "heavy": 150}


def degrade(page: Gray, condition: str, seed: int) -> Gray:
    if condition == "scan":
        return degrade_scan(page, seed=seed)
    if condition == "heavy":
        return business.degrade_heavy(page, seed=seed)
    return page


def vocabulary(text_dir: Path, language: str) -> list[str]:
    words: list[str] = []
    for line in load_lines(text_dir, language):
        for word in line.replace("।", " ").replace(",", " ").replace(".", " ").split():
            if word not in words and len(word) >= 2:
                words.append(word)
    return words


class MissingGlyphsError(RuntimeError):
    """A font has no glyph for some text: the page would show boxes, not the truth."""


def missing_glyphs(font: Path, text: str) -> list[str]:
    """Characters the font renders as its 'missing' glyph."""
    loaded = ImageFont.truetype(str(font), 40)

    def shape(ch: str) -> tuple[tuple[int, int], bytes]:
        mask = loaded.getmask(ch)
        return mask.size, bytes(mask)

    notdef = shape("\U000f0000")  # a private-use code point no font here covers
    return sorted({ch for ch in text if not ch.isspace() and shape(ch) == notdef})


def render_fitting(
    rng: random.Random,
    pools: list[list[str]],
    font: Path,
    dpi: int,
    line_fonts: list[Path] | None = None,
) -> tuple[list[str], Gray]:
    """One random line per word pool, fitted to the page width (long words in wide fonts
    get fewer words per line)."""
    for pool, path in zip(pools, line_fonts or [font] * len(pools), strict=True):
        missing = missing_glyphs(path, " ".join(pool))
        if missing:
            raise MissingGlyphsError(f"{path.name} has no glyph for {''.join(missing[:10])!r}")
    for most in range(7, 1, -1):
        least = min(4, most)
        lines = [" ".join(rng.choice(p) for _ in range(rng.randint(least, most))) for p in pools]
        try:
            return lines, render_page(lines, font, dpi, line_fonts)
        except ValueError:
            continue
    raise ValueError(f"cannot fit lines in {font.name}")


def _save(
    out: Path,
    cohort: str,
    doc_id: str,
    page: Gray,
    dpi: int,
    truth: dict[str, Any],
) -> DocumentRecord:
    buffer = io.BytesIO()
    Image.fromarray(page).save(buffer, "PNG", dpi=(dpi, dpi))
    image = out / f"{doc_id}.png"
    image.write_bytes(buffer.getvalue())
    truth = {"id": doc_id, **truth}
    (out / f"{doc_id}.truth.json").write_text(json.dumps(truth, ensure_ascii=False, indent=1))
    return DocumentRecord(
        cohort=cohort,
        id=doc_id,
        file_sha256=file_hash(image),
        text_sha256=text_hash(truth.get("text") or ""),
        language=truth["language"],
        document_type=truth["document_type"],
        condition=truth["condition"],
    )


# ----------------------------------------------------------------------------- generators
def printed_text(
    out: Path, cohort: str, source: dict[str, Any], text_dir: Path
) -> list[DocumentRecord]:
    """Pages of random word lines in one or more languages and conditions."""
    rng = random.Random(int(source["seed"]))  # noqa: S311  # nosec B311 - synthetic data
    records = []
    for language in source["languages"]:
        words = vocabulary(text_dir, language)
        for condition in source["conditions"]:
            for i in range(int(source["count"])):
                font = FONT_ROOT / rng.choice(EVALUATION_FONTS[language])
                dpi = CONDITIONS[condition]
                lines, page = render_fitting(rng, [words] * rng.randint(8, 14), font, dpi)
                page = degrade(page, condition, rng.randrange(1 << 30))
                truth = {
                    "language": language,
                    "languages": [language],
                    "document_type": "text",
                    "condition": condition,
                    "font": font.name,
                    "text": "\n".join(lines),
                }
                records.append(
                    _save(out, cohort, f"{language}-{condition}-{i:03d}", page, dpi, truth)
                )
    return records


def mixed_text(
    out: Path, cohort: str, source: dict[str, Any], text_dir: Path
) -> list[DocumentRecord]:
    """An Indian language with English lines between, read in automatic language mode."""
    rng = random.Random(int(source["seed"]))  # noqa: S311  # nosec B311 - synthetic data
    english = vocabulary(text_dir, "en")
    records = []
    for language in source["languages"]:
        words = vocabulary(text_dir, language)
        for i in range(int(source["count"])):
            english_lines = [n % 3 == 2 for n in range(rng.randint(8, 12))]
            pools = [english if e else words for e in english_lines]
            font = FONT_ROOT / EVALUATION_FONTS[language][i % 2]
            latin = FONT_ROOT / EVALUATION_FONTS["en"][i % 2]  # Indian-script fonts lack Latin
            fonts = [latin if e else font for e in english_lines]
            lines, page = render_fitting(rng, pools, font, 300, fonts)
            truth = {
                "language": language,
                "languages": [language, "en"],
                "document_type": "text",
                "condition": "clean",
                "font": font.name,
                "text": "\n".join(lines),
            }
            records.append(_save(out, cohort, f"{language}+en-{i:03d}", page, 300, truth))
    return records


def handwriting_text(
    out: Path, cohort: str, source: dict[str, Any], text_dir: Path
) -> list[DocumentRecord]:
    """Handwriting-style font renders (a weak proxy, ADR-0024); fonts from `make fonts`."""
    fonts_dir = Path(source.get("fonts_dir", "evaluation/fonts/handwriting"))
    rng = random.Random(int(source["seed"]))  # noqa: S311  # nosec B311 - synthetic data
    records = []
    for language in source["languages"]:
        words = vocabulary(text_dir, language)
        fonts = source["fonts"][language]
        for i in range(int(source["count"])):
            font = fonts_dir / fonts[i % len(fonts)]
            if not font.is_file():
                raise FileNotFoundError(f"{font} (run `make fonts` at staging)")
            lines, page = render_fitting(rng, [words] * rng.randint(5, 8), font, 300)
            truth = {
                "language": language,
                "languages": [language],
                "document_type": "text",
                "condition": "handwriting-font",
                "font": font.name,
                "handwriting": True,
                "text": "\n".join(lines),
            }
            records.append(_save(out, cohort, f"{language}-hw-{i:03d}", page, 300, truth))
    return records


def business_documents(
    out: Path, cohort: str, source: dict[str, Any], text_dir: Path
) -> list[DocumentRecord]:
    """Invoices, receipts, statements and ID cards with known field values."""
    makers: dict[str, Callable[[random.Random, int], tuple[Gray, dict[str, str | None], int]]] = {
        "gst_invoice": business.gst_invoice,
        "bank_statement": business.bank_statement,
        "pan_card": business.pan_card,
        "aadhaar_card": business.aadhaar_card,
    }
    base = int(source["seed"])
    records = []
    for t, doc_type in enumerate(source["document_types"]):
        for condition in source["conditions"]:
            for i in range(int(source["count"])):
                seed = base + t * 100_000 + list(CONDITIONS).index(condition) * 10_000 + i
                rng = random.Random(seed)  # noqa: S311  # nosec B311 - synthetic data
                dpi = CONDITIONS[condition]
                language = "en"
                if doc_type == "receipt":
                    language = "hi" if rng.random() < 0.25 else "en"
                    page, fields, _ = business.receipt(rng, dpi, language)
                else:
                    page, fields, _ = makers[doc_type](rng, dpi)
                page = degrade(page, condition, seed)
                truth = {
                    "language": language,
                    "languages": [language],
                    "document_type": doc_type,
                    "condition": condition,
                    "fields": fields,
                }
                records.append(
                    _save(out, cohort, f"{doc_type}-{condition}-{i:03d}", page, dpi, truth)
                )
    return records


def tables(out: Path, cohort: str, source: dict[str, Any], text_dir: Path) -> list[DocumentRecord]:
    """Pages with a random table: sizes, spans, rulings and contents drawn afresh."""
    rng = random.Random(int(source["seed"]))  # noqa: S311  # nosec B311 - synthetic data
    words = vocabulary(text_dir, "en")
    records = []
    for condition in source["conditions"]:
        for i in range(int(source["count"])):
            spec = random_table(rng, words, f"t{i}")
            dpi = CONDITIONS[condition]
            page, sample = table_page(spec, text_dir, dpi)
            page = degrade(page, condition, rng.randrange(1 << 30))
            truth = {
                "language": "en",
                "languages": ["en"],
                "document_type": "table",
                "condition": condition,
                "rules": spec.rules,
                "tables": [asdict(t) for t in sample.tables],
            }
            records.append(_save(out, cohort, f"table-{condition}-{i:03d}", page, dpi, truth))
    return records


def random_table(rng: random.Random, words: list[str], table_id: str) -> TableSpec:
    rows, cols = rng.randint(4, 8), rng.randint(3, 5)
    cells = [CellSpec(0, c, rng.choice(words).title(), header=True) for c in range(cols)]
    for r in range(1, rows):
        for c in range(cols):
            text = (
                rng.choice(words) if c == 0 else f"{rng.randint(1, 9999)}.{rng.randint(0, 99):02d}"
            )
            cells.append(CellSpec(r, c, text))
    widths = tuple(round(rng.uniform(1.1, 1.8), 2) for _ in range(cols))
    while sum(widths) > 6.2:
        widths = tuple(round(w * 0.9, 2) for w in widths)
    return TableSpec(
        table_id, "en", rows, widths, tuple(cells), rng.choice(["full", "full", "header"])
    )


GENERATORS: dict[str, Callable[[Path, str, dict[str, Any], Path], list[DocumentRecord]]] = {
    "printed_text": printed_text,
    "mixed_text": mixed_text,
    "handwriting_text": handwriting_text,
    "business_documents": business_documents,
    "tables": tables,
}
