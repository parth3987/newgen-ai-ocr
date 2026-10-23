"""Field-level cross-check against the second OCR engine (ADR-0028).

When OCR ran with ``compare``, the second engine read the same page image. For each
extracted value, the second engine's lines in the evidence area are normalised with the
field's own normaliser; the engines agree when one of those readings equals the value.
Results are never merged: a disagreement only routes the value to a person.
"""

from __future__ import annotations

import re

from newgen_ocr.extraction.extract import Options, mask_text, normalize_field
from newgen_ocr.extraction.models import Evidence, EvidenceSource, Value
from newgen_ocr.extraction.schema import FieldType
from newgen_ocr.ingestion.models import BoundingBox
from newgen_ocr.ocr.models import OcrComparison, OcrPage, OcrRun, PageOcrStatus
from newgen_ocr.review.models import EngineAgreement, EngineCheck
from newgen_ocr.storage.local import DocumentStore

TEXT_TYPES = {FieldType.TEXT, FieldType.NAME, FieldType.ADDRESS}
MAX_WINDOW = 8  # tokens per reading window
_OCR_SOURCES = {EvidenceSource.OCR_LINE, EvidenceSource.TABLE_CELL, EvidenceSource.TEMPLATE_REGION}


def secondary_run(store: DocumentStore, document_id: str, ocr_run: str) -> OcrRun | None:
    """The other engine's run made by ``compare`` for this OCR run, if any."""
    try:
        comparison: OcrComparison = store.read_ocr_comparison(document_id)
    except Exception:  # noqa: BLE001 - no comparison stored
        return None
    if comparison.primary != ocr_run or comparison.secondary == ocr_run:
        return None
    try:
        return store.read_ocr_run(document_id, comparison.secondary)
    except Exception:  # noqa: BLE001 - removed or never written
        return None


def comparable_pages(primary: OcrRun, secondary: OcrRun | None) -> dict[int, OcrPage]:
    """Second-engine pages read from exactly the same image as the primary's."""
    if secondary is None:
        return {}
    out: dict[int, OcrPage] = {}
    first = {p.page_number: p for p in primary.pages}
    for page in secondary.pages:
        mine = first.get(page.page_number)
        if (
            mine is not None
            and page.status is PageOcrStatus.OK
            and mine.status is PageOcrStatus.OK
            and (page.image_source, page.image_rotation_deg, page.image_width, page.image_height)
            == (mine.image_source, mine.image_rotation_deg, mine.image_width, mine.image_height)
        ):
            out[page.page_number] = page
    return out


def _inside(line: BoundingBox, area: BoundingBox) -> bool:
    pad = (area.y1 - area.y0) * 0.25
    cx, cy = (line.x0 + line.x1) / 2, (line.y0 + line.y1) / 2
    return area.x0 - pad <= cx <= area.x1 + pad and area.y0 - pad <= cy <= area.y1 + pad


def _text_in(page: OcrPage, area: BoundingBox) -> str:
    """The second engine's text inside an evidence area. Engines split lines differently
    (Tesseract often joins two columns or a whole table row into one line), so words are
    used when the engine reports them."""
    parts: list[tuple[int, float, str]] = []  # (line, x, text): the engine's line order
    for number, line in enumerate(page.lines):
        if line.words:
            parts += [(number, w.bbox.x0, w.text) for w in line.words if _inside(w.bbox, area)]
        elif _inside(line.bbox, area):
            parts.append((number, line.bbox.x0, line.text))
    parts.sort()
    return " ".join(text for _, _, text in parts)


def _compact(text: str) -> str:
    return re.sub(r"[\W_]+", "", text).casefold()


def readings(text: str, field_type: FieldType, options: Options, pattern: str | None) -> list[str]:
    tokens = text.split()
    found: dict[str, None] = {}
    for start in range(len(tokens)):
        for end in range(start + 1, min(len(tokens), start + MAX_WINDOW) + 1):
            norm = normalize_field(field_type, " ".join(tokens[start:end]), options, pattern)
            if norm.ok:
                found[str(norm.value)] = None
    return list(found)


def check_value(
    value: Value,
    field_type: FieldType,
    evidence: list[Evidence],
    pages: dict[int, OcrPage],
    engine: str | None,
    options: Options,
    pattern: str | None = None,
    mask: bool = False,
) -> EngineCheck:
    """``mask``: Aadhaar numbers in the reported text and readings are masked."""
    ocr_evidence = [e for e in evidence if e.source in _OCR_SOURCES and e.page_number in pages]
    if value is None or not ocr_evidence or len(ocr_evidence) != len(evidence):
        return EngineCheck(status=EngineAgreement.UNAVAILABLE, engine=engine)
    texts = [_text_in(pages[e.page_number], e.bbox) for e in ocr_evidence]
    text = " ".join(t for t in texts if t).strip()
    if not text:
        return EngineCheck(status=EngineAgreement.NO_READING, engine=engine, text="")
    if field_type in TEXT_TYPES:
        agree = _compact(str(value)) in _compact(text)
        found = [str(value)] if agree else []
    else:
        found = readings(text, field_type, options, pattern)
        agree = str(value) in found
    shown = mask_text if mask else str
    return EngineCheck(
        status=EngineAgreement.AGREE if agree else EngineAgreement.DISAGREE,
        engine=engine,
        readings=[shown(r) for r in found[:10]],
        text=shown(text)[:500],
    )
