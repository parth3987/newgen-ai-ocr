"""Confidence thresholds fitted on held-out ground truth (ADR-0028).

An observation is one field of one labelled document, with the signals the acceptance
policy uses. Only observations that pass every gate other than confidence are counted
(those are the only ones a threshold could accept). For each field, the threshold is the
lowest OCR confidence at which the values at or above it are correct with a one-sided
Wilson lower bound of at least ``target_precision``, over at least ``min_samples`` values.
Fields with too little data are pooled by field type, but only when the field's own data
shows no error above the pooled threshold. Otherwise there is no threshold and the field is
never verified automatically.

"Missing" judgements (an optional field reported absent) are calibrated the same way,
without a confidence: the share of reported-absent values that really are absent.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from itertools import pairwise
from pathlib import Path
from statistics import NormalDist

from newgen_ocr import __version__
from newgen_ocr.extraction.models import Value
from newgen_ocr.review.models import (
    Calibration,
    CalibrationDataset,
    CalibrationEntry,
    CalibrationKind,
    CalibrationStatus,
    ReliabilityBin,
    SplitEvaluation,
)

BIN_EDGES = (0.0, 0.5, 0.7, 0.8, 0.85, 0.9, 0.95, 0.98, 1.0001)


@dataclass(frozen=True)
class Observation:
    schema_id: str
    field: str
    type: str
    kind: CalibrationKind
    gated: bool  # passes every gate except the confidence threshold
    confidence: float | None
    correct: bool


def same_value(a: Value, b: Value) -> bool:
    def norm(v: Value) -> str:
        return re.sub(r"\s+", " ", str(v)).strip().casefold()

    return norm(a) == norm(b)


def wilson_lower(correct: int, n: int, confidence: float) -> float:
    if n == 0:
        return 0.0
    z = NormalDist().inv_cdf(confidence)
    p = correct / n
    centre = p + z * z / (2 * n)
    margin = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return max(0.0, (centre - margin) / (1 + z * z / n))


def _reliability(obs: list[Observation]) -> list[ReliabilityBin]:
    bins = []
    for low, high in pairwise(BIN_EDGES):
        inside = [o for o in obs if o.confidence is not None and low <= o.confidence < high]
        if inside:
            bins.append(
                ReliabilityBin(
                    low=low,
                    high=min(high, 1.0),
                    n=len(inside),
                    correct=sum(o.correct for o in inside),
                )
            )
    return bins


def _fit_values(
    key: str,
    level: str,
    obs: list[Observation],
    target: float,
    confidence: float,
    min_samples: int,
) -> CalibrationEntry:
    scored = sorted(
        (o for o in obs if o.gated and o.confidence is not None),
        key=lambda o: o.confidence or 0.0,
        reverse=True,
    )
    entry = CalibrationEntry(
        key=key,
        kind=CalibrationKind.VALUE,
        level=level,
        status=CalibrationStatus.INSUFFICIENT_DATA,
        observations=len(scored),
        reliability=_reliability(scored),
    )
    if len(scored) < min_samples:
        return entry
    best: tuple[float, int, int, float] | None = None
    correct = 0
    for i, o in enumerate(scored):
        correct += o.correct
        n = i + 1
        # Only cut between distinct confidences: equal scores are accepted together.
        if i + 1 < len(scored) and scored[i + 1].confidence == o.confidence:
            continue
        bound = wilson_lower(correct, n, confidence)
        if n >= min_samples and bound >= target:
            best = (o.confidence or 0.0, n, correct, bound)
    if best is None:
        # Without a single error the bound only lacks samples: pooling by type may still
        # apply. With errors, the field has shown it cannot meet the target.
        errors = sum(not o.correct for o in scored)
        entry.status = (
            CalibrationStatus.TARGET_NOT_MET if errors else CalibrationStatus.INSUFFICIENT_DATA
        )
        entry.accepted = len(scored)
        entry.correct = sum(o.correct for o in scored)
        entry.precision = round(entry.correct / len(scored), 4)
        entry.lower_bound = round(wilson_lower(entry.correct, len(scored), confidence), 4)
        return entry
    threshold, n, correct, bound = best
    entry.status = CalibrationStatus.CALIBRATED
    entry.threshold = threshold
    entry.accepted = n
    entry.correct = correct
    entry.precision = round(correct / n, 4)
    entry.lower_bound = round(bound, 4)
    entry.coverage = round(n / len(scored), 4)
    entry.errors_above_threshold = n - correct
    return entry


def _fit_missing(
    key: str, level: str, obs: list[Observation], target: float, confidence: float, minimum: int
) -> CalibrationEntry:
    gated = [o for o in obs if o.gated]
    correct = sum(o.correct for o in gated)
    entry = CalibrationEntry(
        key=key,
        kind=CalibrationKind.MISSING,
        level=level,
        status=CalibrationStatus.INSUFFICIENT_DATA,
        observations=len(gated),
        accepted=len(gated),
        correct=correct,
    )
    if not gated:
        return entry
    bound = wilson_lower(correct, len(gated), confidence)
    entry.precision = round(correct / len(gated), 4)
    entry.lower_bound = round(bound, 4)
    entry.errors_above_threshold = len(gated) - correct
    if bound >= target and len(gated) >= minimum:
        entry.status = CalibrationStatus.CALIBRATED
        entry.coverage = 1.0
    elif entry.errors_above_threshold and len(gated) >= minimum:
        entry.status = CalibrationStatus.TARGET_NOT_MET
        entry.coverage = 1.0 if entry.status is CalibrationStatus.CALIBRATED else None
    return entry


def fit(
    observations: list[Observation],
    dataset: CalibrationDataset,
    schema_versions: dict[str, str],
    target_precision: float = 0.95,
    confidence_level: float = 0.95,
    min_samples: int = 50,
) -> Calibration:
    groups: dict[tuple[CalibrationKind, str], list[Observation]] = defaultdict(list)
    for o in observations:
        groups[(o.kind, f"{o.schema_id}.{o.field}")].append(o)
        groups[(o.kind, f"type:{o.type}")].append(o)
    entries: dict[str, CalibrationEntry] = {}
    # Types first: a field without enough data of its own is checked against its type's
    # threshold (errors_above_threshold), which decides whether pooling may apply.
    for (kind, name), obs in sorted(groups.items(), key=lambda g: not g[0][1].startswith("type:")):
        level = "type" if name.startswith("type:") else "field"
        key = f"{kind.value}:{name}"
        if kind is CalibrationKind.VALUE:
            entry = _fit_values(key, level, obs, target_precision, confidence_level, min_samples)
            pooled = entries.get(f"value:type:{obs[0].type}")
            if (
                level == "field"
                and entry.status is CalibrationStatus.INSUFFICIENT_DATA
                and pooled is not None
                and pooled.threshold is not None
            ):
                # Errors at or above the pooled threshold forbid pooling for this field.
                entry.errors_above_threshold = sum(
                    1
                    for o in obs
                    if o.gated
                    and not o.correct
                    and o.confidence is not None
                    and o.confidence >= pooled.threshold
                )
        else:
            entry = _fit_missing(key, level, obs, target_precision, confidence_level, min_samples)
        entries[key] = entry
    entries = dict(sorted(entries.items()))
    calibration = Calibration(
        id="",
        created_at=datetime.now(tz=UTC),
        pipeline_version=__version__,
        dataset=dataset,
        target_precision=target_precision,
        confidence_level=confidence_level,
        min_samples=min_samples,
        schema_versions=schema_versions,
        entries=entries,
    )
    calibration.id = content_id(calibration)
    return calibration


def content_id(calibration: Calibration) -> str:
    body = calibration.model_dump(mode="json", exclude={"id", "created_at"})
    return hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()[:16]


@dataclass(frozen=True)
class Threshold:
    key: str | None
    threshold: float | None  # None: not calibrated (value) / not supported (missing)
    detail: str


def lookup(
    calibration: Calibration | None,
    schema_id: str,
    schema_version: str | None,
    field: str,
    field_type: str,
    kind: CalibrationKind,
) -> Threshold:
    """The calibrated threshold for a field (``0.0`` for a supported missing judgement)."""
    if calibration is None:
        return Threshold(None, None, "no calibration loaded")
    if calibration.schema_versions.get(schema_id) != schema_version:
        return Threshold(None, None, f"calibration is for another {schema_id} schema version")
    own = calibration.entries.get(f"{kind.value}:{schema_id}.{field}")
    if own is not None and own.status is CalibrationStatus.CALIBRATED:
        return Threshold(own.key, own.threshold if kind is CalibrationKind.VALUE else 0.0, "field")
    if own is not None and own.status is CalibrationStatus.TARGET_NOT_MET:
        return Threshold(own.key, None, "target precision not met for this field")
    pooled = calibration.entries.get(f"{kind.value}:type:{field_type}")
    if pooled is None or pooled.status is not CalibrationStatus.CALIBRATED:
        return Threshold(None, None, "not enough evaluation data for this field or type")
    if own is not None and own.errors_above_threshold:
        return Threshold(None, None, "field has errors above the pooled threshold")
    return Threshold(
        pooled.key, pooled.threshold if kind is CalibrationKind.VALUE else 0.0, "pooled by type"
    )


def load_calibration(path: Path | None) -> Calibration | None:
    """Read a calibration file; its id must match its content (no hand-edited thresholds)."""
    if path is None:
        return None
    calibration = Calibration.model_validate_json(path.read_bytes())
    if content_id(calibration) != calibration.id:
        raise ValueError(f"calibration {path} was modified after it was made (id mismatch)")
    return calibration


@dataclass(frozen=True)
class LabelledDocument:
    """One labelled document's observations, kept so thresholds can be refitted or tested."""

    sample_id: str
    split: str
    schema_id: str | None
    schema_version: str | None
    type_correct: bool
    document_ok: bool  # no document-level reason and every table verified
    other_blocked: int  # fields needing a person for reasons a threshold cannot change
    observations: tuple[Observation, ...]


def evaluate(documents: list[LabelledDocument], calibration: Calibration) -> SplitEvaluation:
    """Apply fitted thresholds to documents that were not used to fit them."""
    out = SplitEvaluation(
        samples=len(documents),
        documents_eligible=0,
        documents_eligible_correct=0,
        values_verified=0,
        values_verified_correct=0,
        values_total=0,
        missing_verified=0,
        missing_verified_correct=0,
    )
    for doc in documents:
        verified_all = doc.document_ok and doc.other_blocked == 0 and doc.schema_id is not None
        correct_all = doc.type_correct
        for o in doc.observations:
            found = lookup(calibration, o.schema_id, doc.schema_version, o.field, o.type, o.kind)
            ok = (
                o.gated
                and found.threshold is not None
                and (o.kind is CalibrationKind.MISSING or (o.confidence or 0.0) >= found.threshold)
            )
            correct_all = correct_all and o.correct
            if o.kind is CalibrationKind.VALUE:
                out.values_total += 1
                out.values_verified += ok
                out.values_verified_correct += ok and o.correct
            else:
                out.missing_verified += ok
                out.missing_verified_correct += ok and o.correct
            verified_all = verified_all and ok
        out.documents_eligible += verified_all
        out.documents_eligible_correct += verified_all and correct_all
    return out
