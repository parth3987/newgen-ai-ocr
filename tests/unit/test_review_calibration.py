"""Threshold fitting on labelled observations, lookup rules and held-out evaluation."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from newgen_ocr.review.calibration import (
    LabelledDocument,
    Observation,
    evaluate,
    fit,
    load_calibration,
    lookup,
    wilson_lower,
)
from newgen_ocr.review.models import CalibrationDataset, CalibrationKind, CalibrationStatus

DATASET = CalibrationDataset(name="t", kind="synthetic", samples=1)
VERSIONS = {"inv": "1.0"}
VALUE, MISSING = CalibrationKind.VALUE, CalibrationKind.MISSING


def obs(
    field: str,
    confidence: float | None,
    correct: bool,
    type_: str = "amount",
    gated: bool = True,
    kind: CalibrationKind = VALUE,
) -> Observation:
    return Observation("inv", field, type_, kind, gated, confidence, correct)


def test_wilson_lower_bound() -> None:
    assert wilson_lower(0, 0, 0.95) == 0.0
    # All 52 correct is just enough for 0.95 at one-sided 95% (n / (n + z^2) >= 0.95).
    assert wilson_lower(52, 52, 0.95) >= 0.95 > wilson_lower(51, 51, 0.95)
    assert wilson_lower(90, 100, 0.95) < 0.9


def test_threshold_excludes_low_confidence_errors() -> None:
    data = [obs("total", 0.95 + i / 10000, True) for i in range(60)]
    data += [obs("total", 0.60, False), obs("total", 0.62, False), obs("total", 0.70, True)]
    data += [obs("total", 0.99, True, gated=False)] * 5  # blocked by other gates: not counted
    cal = fit(data, DATASET, VERSIONS, min_samples=50)
    entry = cal.entries["value:inv.total"]
    assert entry.status is CalibrationStatus.CALIBRATED
    assert entry.threshold == 0.70  # the lowest score keeping both errors out
    assert (entry.observations, entry.errors_above_threshold) == (63, 0)
    assert entry.lower_bound is not None and entry.lower_bound >= 0.95
    assert sum(b.n for b in entry.reliability) == 63
    found = lookup(cal, "inv", "1.0", "total", "amount", VALUE)
    assert (found.key, found.threshold) == ("value:inv.total", entry.threshold)


def test_target_not_met_and_insufficient_data() -> None:
    noisy = [obs("total", 0.99, i % 10 != 0) for i in range(100)]  # 90% correct everywhere
    cal = fit(noisy + [obs("tax", 0.99, True)] * 10, DATASET, VERSIONS, min_samples=50)
    assert cal.entries["value:inv.total"].status is CalibrationStatus.TARGET_NOT_MET
    assert cal.entries["value:inv.tax"].status is CalibrationStatus.INSUFFICIENT_DATA
    assert lookup(cal, "inv", "1.0", "total", "amount", VALUE).threshold is None
    # The type pools both fields and is still 90% correct: no threshold for "tax" either.
    assert lookup(cal, "inv", "1.0", "tax", "amount", VALUE).threshold is None


def test_pooling_by_type_needs_a_clean_field() -> None:
    pool = [obs("total", 0.97, True) for _ in range(60)]
    clean = [obs("tax", 0.97, True) for _ in range(5)]
    dirty = [obs("cess", 0.97, True) for _ in range(4)] + [obs("cess", 0.98, False)]
    cal = fit(pool + clean + dirty, DATASET, VERSIONS, target_precision=0.9, min_samples=50)
    assert cal.entries["value:type:amount"].status is CalibrationStatus.CALIBRATED
    pooled = lookup(cal, "inv", "1.0", "tax", "amount", VALUE)
    assert pooled.key == "value:type:amount" and pooled.detail == "pooled by type"
    blocked = lookup(cal, "inv", "1.0", "cess", "amount", VALUE)
    assert blocked.threshold is None and "errors" in blocked.detail


def test_missing_judgements_and_schema_version() -> None:
    absent = [obs("due", None, True, "date", kind=MISSING) for _ in range(60)]
    missed = [obs("po", None, i % 4 != 0, "text", kind=MISSING) for i in range(60)]
    cal = fit(absent + missed, DATASET, VERSIONS, min_samples=50)
    assert lookup(cal, "inv", "1.0", "due", "date", MISSING).threshold == 0.0
    assert lookup(cal, "inv", "1.0", "po", "text", MISSING).threshold is None
    assert lookup(cal, "inv", "2.0", "due", "date", MISSING).threshold is None
    assert lookup(None, "inv", "1.0", "due", "date", MISSING).detail == "no calibration loaded"


def test_load_rejects_edited_files(tmp_path: Path) -> None:
    cal = fit([obs("total", 0.99, True)] * 60, DATASET, VERSIONS, min_samples=50)
    path = tmp_path / "cal.json"
    path.write_text(cal.model_dump_json())
    assert load_calibration(path) == cal
    assert load_calibration(None) is None
    data = json.loads(path.read_text())
    data["entries"]["value:inv.total"]["threshold"] = 0.1
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="modified"):
        load_calibration(path)


def test_evaluate_held_out_documents() -> None:
    cal = fit([obs("total", 0.97, True)] * 60, DATASET, VERSIONS, min_samples=50)

    def doc(confidence: float, correct: bool, blocked: int = 0) -> LabelledDocument:
        return LabelledDocument(
            "s", "test", "inv", "1.0", True, True, blocked, (obs("total", confidence, correct),)
        )

    result = evaluate([doc(0.99, True), doc(0.99, False), doc(0.5, True), doc(0.99, True, 1)], cal)
    assert (result.documents_eligible, result.documents_eligible_correct) == (2, 1)
    assert (result.values_verified, result.values_verified_correct, result.values_total) == (
        3,
        2,
        4,
    )


def test_error_free_field_below_the_bound_pools_by_type() -> None:
    # 51 correct of 51 is just short of the bound: more data is needed, not a failure.
    data = [obs("number", 0.97, True, "identifier") for _ in range(51)]
    data += [obs("ref", 0.97, True, "identifier") for _ in range(60)]
    cal = fit(data, DATASET, VERSIONS, min_samples=50)
    assert cal.entries["value:inv.number"].status is CalibrationStatus.INSUFFICIENT_DATA
    assert lookup(cal, "inv", "1.0", "number", "identifier", VALUE).key == ("value:type:identifier")
    absent = [obs("po", None, True, "text", kind=MISSING) for _ in range(51)]
    cal = fit(absent, DATASET, VERSIONS, min_samples=50)
    assert cal.entries["missing:inv.po"].status is CalibrationStatus.INSUFFICIENT_DATA
