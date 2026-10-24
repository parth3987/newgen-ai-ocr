"""Release-evaluation metrics (ADR-0033): one definition per metric, with its sample size.

Every proportion is reported with its count and a 95% Wilson score interval, so a "99%"
measured on 40 fields is visibly different from one measured on 4,000. The definitions:

- **CER / WER**: edit distance over the reference length, per page; the cohort figure is
  the *micro* average (total edits over total reference characters or words), so long
  pages weigh more. Character accuracy is 1 - CER.
- **Field exact match**: of the fields whose truth has a value, the share extracted with
  exactly that value (whitespace and case normalised). Fields with no truth value count
  separately: correctly missing, or *invented*.
- **Document exact match**: the document type is right and every field is right
  (including correctly missing).
- **Table structure**: the share of tables whose cell grid (rows, columns, spans) equals
  the truth exactly, and the mean adjacency F1.
- **Auto-accept precision**: of the field values the review gate verified without a
  person, the share that are correct. **Coverage**: the share of all field values it
  verified. **Document auto-accept**: documents with every field and table verified.
  **Manual-review rate**: 1 - document auto-accept rate.
- **Latency**: wall time per page; p50 and p95 by nearest rank. **Pages per minute**: pages
  over the total wall time of the run, one page at a time.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable, Sequence
from typing import Any

Z95 = 1.959963984540054


def wilson(successes: int, total: int) -> dict[str, Any]:
    """A proportion with its 95% Wilson score interval (``None`` when there is no sample)."""
    if total == 0:
        return {"value": None, "successes": 0, "n": 0, "ci95": None}
    p = successes / total
    denominator = 1 + Z95**2 / total
    centre = (p + Z95**2 / (2 * total)) / denominator
    margin = Z95 * math.sqrt(p * (1 - p) / total + Z95**2 / (4 * total**2)) / denominator
    return {
        "value": round(p, 6),
        "successes": successes,
        "n": total,
        "ci95": [round(max(0.0, centre - margin), 6), round(min(1.0, centre + margin), 6)],
    }


def percentile(values: Sequence[float], q: float) -> float | None:
    """Nearest-rank percentile (q in 0..100)."""
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil(q / 100 * len(ordered)))
    return ordered[rank - 1]


def same_value(a: object, b: object) -> bool:
    def norm(v: object) -> str:
        return re.sub(r"\s+", " ", str(v)).strip().casefold()

    return norm(a) == norm(b)


def text_errors(pages: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Micro CER and WER over pages with ``char_edits``, ``chars``, ``word_edits``, ``words``."""
    rows = list(pages)
    chars = sum(r["chars"] for r in rows)
    words = sum(r["words"] for r in rows)
    cer = sum(r["char_edits"] for r in rows) / chars if chars else None
    wer = sum(r["word_edits"] for r in rows) / words if words else None
    per_page = [r["char_edits"] / r["chars"] for r in rows if r["chars"]]
    return {
        "pages": len(rows),
        "reference_characters": chars,
        "reference_words": words,
        "cer": None if cer is None else round(cer, 6),
        "wer": None if wer is None else round(wer, 6),
        "character_accuracy": None if cer is None else round(max(0.0, 1 - cer), 6),
        "page_cer_p50": _round(percentile(per_page, 50)),
        "page_cer_p95": _round(percentile(per_page, 95)),
        "pages_cer_at_most_1pct": wilson(sum(1 for c in per_page if c <= 0.01), len(per_page)),
    }


def field_scores(
    truth: dict[str, str | None], extracted: dict[str, dict[str, Any]]
) -> dict[str, int]:
    """Counts for one document. ``extracted``: field name -> {"status", "value"}."""
    counts = {"values": 0, "values_correct": 0, "absent": 0, "absent_correct": 0, "invented": 0}
    for name, expected in truth.items():
        got = extracted.get(name, {"status": "missing", "value": None})
        found = got.get("status") == "extracted"
        if expected in (None, ""):
            counts["absent"] += 1
            if found:
                counts["invented"] += 1
            else:
                counts["absent_correct"] += 1
        else:
            counts["values"] += 1
            counts["values_correct"] += int(found and same_value(got.get("value"), expected))
    return counts


def field_summary(documents: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Field and document exact match over documents with ``fields`` counts."""
    total = (
        {k: sum(d["fields"][k] for d in documents) for k in documents[0]["fields"]}
        if documents
        else {}
    )
    exact = sum(
        1
        for d in documents
        if d["type_correct"]
        and d["fields"]["values_correct"] == d["fields"]["values"]
        and d["fields"]["invented"] == 0
    )
    return {
        "documents": len(documents),
        "field_exact_match": wilson(total.get("values_correct", 0), total.get("values", 0)),
        "absent_fields_correct": wilson(total.get("absent_correct", 0), total.get("absent", 0)),
        "invented_values": total.get("invented", 0),
        "document_type_correct": wilson(sum(d["type_correct"] for d in documents), len(documents)),
        "document_exact_match": wilson(exact, len(documents)),
    }


def review_summary(documents: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Auto-accept precision and coverage over documents with ``review`` counts."""
    verified = sum(d["review"]["values_verified"] for d in documents)
    verified_ok = sum(d["review"]["values_verified_correct"] for d in documents)
    values = sum(d["review"]["values_total"] for d in documents)
    eligible = [d for d in documents if d["review"]["document_eligible"]]
    eligible_ok = sum(1 for d in eligible if d["review"]["document_correct"])
    return {
        "auto_accept_precision": wilson(verified_ok, verified),
        "auto_accept_coverage": wilson(verified, values),
        "document_auto_accept_rate": wilson(len(eligible), len(documents)),
        "document_auto_accept_precision": wilson(eligible_ok, len(eligible)),
        "manual_review_rate": wilson(len(documents) - len(eligible), len(documents)),
    }


def table_summary(tables: Sequence[dict[str, Any]]) -> dict[str, Any]:
    f1 = [t["association"]["f1"] for t in tables]
    return {
        "tables": len(tables),
        "detected": wilson(sum(1 for t in tables if t["detected"]), len(tables)),
        "structure_exact": wilson(sum(1 for t in tables if t["structure_exact"]), len(tables)),
        "adjacency_f1_mean": _round(sum(f1) / len(f1)) if f1 else None,
        "cell_cer_mean": _round(sum(t["cell_cer"] for t in tables) / len(tables))
        if tables
        else None,
    }


def performance_summary(pages: Sequence[dict[str, Any]], wall_seconds: float) -> dict[str, Any]:
    """Per-page latency, throughput and CPU from ``seconds`` and ``cpu_seconds`` per page."""
    latencies = [p["seconds"] for p in pages]
    cpu = [p["cpu_seconds"] for p in pages]
    return {
        "pages": len(pages),
        "pages_per_minute": round(len(pages) / wall_seconds * 60, 3) if wall_seconds else None,
        "latency_p50_s": _round(percentile(latencies, 50)),
        "latency_p95_s": _round(percentile(latencies, 95)),
        "latency_max_s": _round(max(latencies)) if latencies else None,
        "cpu_seconds_per_page": _round(sum(cpu) / len(cpu)) if cpu else None,
        "failures": wilson(sum(1 for p in pages if p.get("error")), len(pages)),
    }


def _round(value: float | None) -> float | None:
    return None if value is None else round(value, 4)
