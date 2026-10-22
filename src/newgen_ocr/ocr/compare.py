"""Engine cross-checking policy (ADR-0022).

Results from different engines are **never merged**. The primary engine's result is the
result of record (or the fallback's, when the primary failed on that page). A comparison
only measures agreement and routes disagreeing pages to human review.
"""

from __future__ import annotations

from datetime import UTC, datetime

from newgen_ocr.evaluation.metrics import agreement
from newgen_ocr.ocr.models import OcrComparison, OcrPage, OcrRun, PageComparison, PageOcrStatus


def compare_runs(primary: OcrRun, secondary: OcrRun, threshold: float) -> OcrComparison:
    pages: list[PageComparison] = []
    for a, b in zip(primary.pages, secondary.pages, strict=True):
        pages.append(_compare_page(a, b, threshold))
    return OcrComparison(
        document_id=primary.document_id,
        primary=primary.requested_engine,
        secondary=secondary.requested_engine,
        threshold=threshold,
        created_at=datetime.now(tz=UTC),
        pages=pages,
    )


def _compare_page(a: OcrPage, b: OcrPage, threshold: float) -> PageComparison:
    if a.status is PageOcrStatus.SKIPPED:
        return PageComparison(
            page_number=a.page_number,
            agreement=None,
            needs_review=False,
            reason=f"skipped:{a.skip_reason}",
        )
    if PageOcrStatus.FAILED in (a.status, b.status):
        return PageComparison(
            page_number=a.page_number, agreement=None, needs_review=True, reason="engine_failed"
        )
    score = round(agreement(a.text, b.text), 4)
    if score < threshold:
        return PageComparison(
            page_number=a.page_number, agreement=score, needs_review=True, reason="disagreement"
        )
    return PageComparison(
        page_number=a.page_number, agreement=score, needs_review=False, reason="agree"
    )
