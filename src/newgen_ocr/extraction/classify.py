"""Rule- and template-based document classification (ADR-0026). No model, no LLM.

1. **Templates** first: a template matches when all its anchor phrases occur; the document
   is then that template's type.
2. **Rules** otherwise: each schema scores the text by weighted keywords, regular-expression
   patterns and table header words. The best type is accepted only if it reaches its
   ``min_score`` and leads the runner-up clearly (``MIN_MARGIN``); otherwise the result is
   ``ambiguous`` or ``unknown``, never a guess.
"""

from __future__ import annotations

import re
import unicodedata

from newgen_ocr.extraction.document import DocumentView
from newgen_ocr.extraction.models import Classification, ClassificationStatus
from newgen_ocr.extraction.normalize import ascii_digits
from newgen_ocr.extraction.schema import DocumentSchema, TemplateSpec

# The winner must score at least this multiple of the runner-up (if the runner-up qualifies).
MIN_MARGIN = 1.5
_WORD = r"\wऀ-෿"


def fold(text: str) -> str:
    """Lower-case, NFC, ASCII digits, curly quotes straightened, whitespace collapsed."""
    text = unicodedata.normalize("NFC", ascii_digits(text)).replace("\u2019", "'").lower()
    return re.sub(r"\s+", " ", text)


def phrase(text: str) -> re.Pattern[str]:
    """Whole-phrase pattern: words separated by spaces or dots, not inside other words."""
    words = [re.escape(w) for w in fold(text).split()]
    return re.compile(rf"(?<![{_WORD}])" + r"[\s.]*".join(words) + rf"\.?(?![{_WORD}])")


def matches_template(template: TemplateSpec, text: str) -> bool:
    folded = fold(text)
    return all(phrase(anchor).search(folded) for anchor in template.anchors)


def score(schema: DocumentSchema, view: DocumentView) -> tuple[float, list[str]]:
    folded = fold(view.text)
    total = 0.0
    evidence: list[str] = []
    for keyword in schema.classification.keywords:
        if phrase(keyword.text).search(folded):
            total += keyword.weight
            evidence.append(f"keyword:{keyword.text}")
    raw = ascii_digits(view.text)
    for rule in schema.classification.patterns:
        if re.search(rule.regex, raw):
            total += rule.weight
            evidence.append(f"pattern:{rule.regex[:30]}")
    headers = " | ".join(
        fold(c.text) for t in view.tables for c in t.table.cells if c.header or c.row == 0
    )
    for keyword in schema.classification.table_headers:
        if phrase(keyword.text).search(headers):
            total += keyword.weight
            evidence.append(f"table_header:{keyword.text}")
    return round(total, 2), evidence


def classify(
    schemas: dict[str, DocumentSchema], view: DocumentView, requested: str | None = None
) -> Classification:
    scores: dict[str, float] = {}
    evidence: dict[str, list[str]] = {}
    for schema_id, schema in schemas.items():
        scores[schema_id], evidence[schema_id] = score(schema, view)
    if requested is not None:
        return Classification(
            status=ClassificationStatus.REQUESTED,
            document_type=requested,
            score=scores.get(requested, 0.0),
            scores=scores,
            evidence=evidence,
        )
    for schema_id, schema in schemas.items():
        for template in schema.templates:
            if matches_template(template, view.text):
                return Classification(
                    status=ClassificationStatus.TEMPLATE,
                    document_type=schema_id,
                    template=template.id,
                    score=scores[schema_id],
                    scores=scores,
                    evidence=evidence,
                )
    qualified = sorted(
        (s for s in scores if scores[s] >= schemas[s].classification.min_score),
        key=lambda s: scores[s],
        reverse=True,
    )
    if not qualified:
        return Classification(
            status=ClassificationStatus.UNKNOWN,
            document_type=None,
            scores=scores,
            evidence=evidence,
        )
    best = qualified[0]
    if len(qualified) > 1 and scores[best] < MIN_MARGIN * scores[qualified[1]]:
        return Classification(
            status=ClassificationStatus.AMBIGUOUS,
            document_type=None,
            score=scores[best],
            scores=scores,
            evidence=evidence,
        )
    return Classification(
        status=ClassificationStatus.CLASSIFIED,
        document_type=best,
        score=scores[best],
        scores=scores,
        evidence=evidence,
    )
