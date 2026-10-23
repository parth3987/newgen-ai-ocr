"""Labelled documents for calibration: extraction results paired with ground truth.

Used by ``scripts/calibrate_review.py``. Ground truth comes from a labelled dataset or from
approved review items (the approved value is the truth for what was extracted).
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

from newgen_ocr.extraction.models import ExtractionResult
from newgen_ocr.extraction.schema import SchemaRegistry
from newgen_ocr.review.acceptance import THRESHOLD_REASONS, observations
from newgen_ocr.review.calibration import LabelledDocument
from newgen_ocr.review.corrections import reviewed
from newgen_ocr.review.models import Assessment, ReviewItemStatus
from newgen_ocr.review.store import ReviewStore
from newgen_ocr.storage.local import DocumentStore


def labelled(
    sample_id: str,
    split: str,
    truth_type: str | None,
    truth: dict[str, Any],
    result: ExtractionResult,
    assessment: Assessment,
) -> LabelledDocument:
    """One document's observations. A misclassified document contributes none (its fields
    belong to another type) and counts as not correct in the held-out evaluation."""
    type_ok = result.schema_id == truth_type
    obs = observations(result, assessment, truth) if type_ok else []
    other = sum(1 for d in assessment.fields if set(d.reasons) - THRESHOLD_REASONS)
    return LabelledDocument(
        sample_id=sample_id,
        split=split,
        schema_id=result.schema_id,
        schema_version=result.schema_version,
        type_correct=type_ok,
        document_ok=not assessment.reasons and all(t.verified for t in assessment.tables),
        other_blocked=other,
        observations=tuple(obs),
    )


def from_reviews(data_dir: Path) -> list[LabelledDocument]:
    """Approved, current review items as labelled documents (a third of them, chosen by
    document id, form the test split)."""
    store = DocumentStore(data_dir)
    registry = SchemaRegistry()
    docs = []
    for item in ReviewStore(data_dir).items():
        if item.status is not ReviewItemStatus.APPROVED or item.schema_id is None:
            continue
        result = store.read_extraction(item.document_id, item.layout_run)
        if result.created_at != item.extraction_created_at:
            continue
        final = reviewed(result, item, registry.get(item.schema_id), date.today())
        truth = {name: v.value for name, v in final.fields.items()}
        split = "test" if int(item.document_id[:8], 16) % 3 == 2 else "calibration"
        docs.append(
            labelled(
                f"{item.document_id}/{item.layout_run}",
                split,
                item.schema_id,
                truth,
                result,
                item.assessment,
            )
        )
    return docs
