"""Release evaluation (ADR-0033): cohorts, separation, metric definitions and gates."""

from __future__ import annotations

import importlib.util
import json
import random
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from newgen_ocr.evaluation import cohort_data
from newgen_ocr.evaluation.cohorts import (
    DataKind,
    DocumentRecord,
    Role,
    check_separation,
    load_registry,
    text_hash,
)
from newgen_ocr.evaluation.release_metrics import (
    field_scores,
    field_summary,
    percentile,
    review_summary,
    text_errors,
    wilson,
)

ROOT = Path(__file__).resolve().parents[2]
TEXT_DIR = ROOT / "evaluation" / "corpus" / "text"


def _script(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


# ----------------------------------------------------------------------------- cohorts
def test_registry_has_every_required_cohort() -> None:
    registry = load_registry(ROOT / "evaluation" / "cohorts.json")
    evaluation = {c.id: c for c in registry.by_role(Role.EVALUATION)}
    conditions = {k for c in evaluation.values() for k in c.conditions}
    assert {"clean", "scan", "heavy", "handwriting-font"} <= conditions
    assert any("tables" in c.metrics for c in evaluation.values())
    assert any("language_id" in c.metrics for c in evaluation.values())  # mixed language
    for language in ("en", "hi", "mr", "te", "ta", "kn"):  # each printed language
        assert f"eval-printed-{language}" in evaluation
    handwriting = {
        lang for c in evaluation.values() if "handwriting" in c.metrics for lang in c.languages
    }
    assert handwriting == {"en", "hi", "mr", "te", "ta", "kn"}  # each script, real if no font
    # Every synthetic evaluation cohort says how independent of tuning it is.
    assert all(c.independence for c in evaluation.values() if c.kind == DataKind.SYNTHETIC)
    assert registry.by_role(Role.TUNING)


def test_registry_refuses_a_reused_seed(tmp_path: Path) -> None:
    data = json.loads((ROOT / "evaluation" / "cohorts.json").read_text())
    data["cohorts"].append({**data["cohorts"][4], "id": "eval-copy"})
    path = tmp_path / "cohorts.json"
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="reuses the generator seed"):
        load_registry(path)


def _record(cohort: str, doc: str, file: str, text: str) -> DocumentRecord:
    return DocumentRecord(
        cohort=cohort,
        id=doc,
        file_sha256=file,
        text_sha256=text_hash(text),
        language="en",
        document_type="text",
        condition="clean",
    )


def test_separation_refuses_tuning_documents_by_file_or_text() -> None:
    tuning = [_record("tuning", "a", "f1", "Hello  World"), _record("tuning", "b", "f2", "")]
    same_file = _record("eval", "x", "f1", "other")
    same_text = _record("eval", "y", "f9", "hello world")  # normalised: same truth text
    fresh = _record("eval", "z", "f8", "something new")
    problems = check_separation(tuning, [same_file, same_text, fresh])
    assert len(problems) == 2
    assert "eval/x is the same file as tuning/a" in problems[0]
    assert "eval/y has the same text" in problems[1]
    assert check_separation(tuning, [fresh]) == []


# ----------------------------------------------------------------------------- metrics
def test_wilson_interval() -> None:
    result = wilson(99, 100)
    assert result["value"] == 0.99 and result["n"] == 100
    low, high = result["ci95"]
    assert 0.945 < low < 0.95 and 0.998 < high <= 1.0  # textbook Wilson bounds
    assert wilson(0, 0)["value"] is None
    # 99% on a small sample is visibly weak.
    assert wilson(40, 40)["ci95"][0] < 0.92


def test_percentile_nearest_rank() -> None:
    values = [float(v) for v in range(1, 101)]
    assert percentile(values, 50) == 50 and percentile(values, 95) == 95
    assert percentile([3.0], 95) == 3.0 and percentile([], 50) is None


def test_text_errors_are_micro_averaged() -> None:
    pages = [
        {"char_edits": 1, "chars": 100, "word_edits": 1, "words": 20},
        {"char_edits": 9, "chars": 900, "word_edits": 2, "words": 180},
    ]
    result = text_errors(pages)
    assert result["cer"] == 0.01 and result["character_accuracy"] == 0.99
    assert result["wer"] == 0.015 and result["reference_characters"] == 1000
    assert result["pages_cer_at_most_1pct"]["successes"] == 2


def test_field_scores_count_invented_values() -> None:
    truth = {"total": "100.00", "gstin": "27AAACN1234A1Z7", "due_date": None}
    extracted = {
        "total": {"status": "extracted", "value": "100.00"},
        "gstin": {"status": "extracted", "value": "27AAACN1234A1Z8"},
        "due_date": {"status": "extracted", "value": "2024-01-01"},
    }
    counts = field_scores(truth, extracted)
    assert counts == {
        "values": 2,
        "values_correct": 1,
        "absent": 1,
        "absent_correct": 0,
        "invented": 1,
    }
    summary = field_summary([{"fields": counts, "type_correct": True}])
    assert summary["field_exact_match"]["value"] == 0.5
    assert summary["document_exact_match"]["value"] == 0.0 and summary["invented_values"] == 1


def test_review_summary_reports_precision_with_coverage() -> None:
    docs = [
        {
            "review": {
                "values_total": 10,
                "values_verified": 8,
                "values_verified_correct": 8,
                "document_eligible": False,
                "document_correct": True,
            }
        },
        {
            "review": {
                "values_total": 10,
                "values_verified": 10,
                "values_verified_correct": 9,
                "document_eligible": True,
                "document_correct": False,
            }
        },
    ]
    result = review_summary(docs)
    assert result["auto_accept_precision"]["value"] == round(17 / 18, 6)
    assert result["auto_accept_coverage"]["value"] == 0.9
    assert result["manual_review_rate"]["value"] == 0.5
    assert result["document_auto_accept_precision"]["value"] == 0.0


# ----------------------------------------------------------------------------- generators
def test_missing_glyphs_are_detected() -> None:
    tamil = cohort_data.FONT_ROOT / cohort_data.EVALUATION_FONTS["ta"][0]
    if not tamil.is_file():
        pytest.skip("Noto Tamil not installed")
    assert cohort_data.missing_glyphs(tamil, "பயன் any") == ["a", "n", "y"]
    with pytest.raises(cohort_data.MissingGlyphsError):
        rng = random.Random(1)  # noqa: S311 - synthetic data
        cohort_data.render_fitting(rng, [["any", "word"]], tamil, 100)


@pytest.mark.parametrize(
    "generator", ["printed_text", "mixed_text", "business_documents", "tables"]
)
def test_generators_write_truth_and_records(tmp_path: Path, generator: str) -> None:
    sources: dict[str, dict[str, Any]] = {
        "printed_text": {"seed": 7, "count": 1, "languages": ["hi"], "conditions": ["scan"]},
        "mixed_text": {"seed": 7, "count": 1, "languages": ["te"]},
        "business_documents": {
            "seed": 7,
            "count": 1,
            "document_types": ["pan_card"],
            "conditions": ["clean"],
        },
        "tables": {"seed": 7, "count": 1, "conditions": ["clean"]},
    }
    records = cohort_data.GENERATORS[generator](tmp_path, "c", sources[generator], TEXT_DIR)
    assert records
    for record in records:
        truth = json.loads((tmp_path / f"{record.id}.truth.json").read_text())
        assert (tmp_path / f"{record.id}.png").is_file()
        assert truth["language"] == record.language and truth["condition"] == record.condition
        assert truth.get("text") or truth.get("fields") or truth.get("tables")
    second = tmp_path / "again"
    second.mkdir()
    again = cohort_data.GENERATORS[generator](second, "c", sources[generator], TEXT_DIR)
    assert [r.file_sha256 for r in again] == [r.file_sha256 for r in records]  # seeded


# ----------------------------------------------------------------------------- gates
gates = _script("release_gates")


def _summary(**cohorts: dict[str, Any]) -> dict[str, Any]:
    return {"cohorts": cohorts}


def _measured(metrics: dict[str, Any], **extra: Any) -> dict[str, Any]:
    return {"status": "measured", "languages": ["en"], "metrics": metrics, **extra}


TARGETS = {
    "reference_server": None,
    "targets": [
        {
            "id": "aa",
            "title": "AA",
            "metric": "review.auto_accept_precision",
            "cohort": "real",
            "comparison": ">=",
            "threshold": 0.99,
            "min_sample": 300,
            "binding": "when_measured",
            "synthetic_counterparts": ["synthetic"],
        },
        {
            "id": "hw",
            "title": "HW",
            "metric": "handwriting_routed_to_review",
            "cohort": "hw",
            "comparison": ">=",
            "threshold": 1.0,
            "binding": "always",
        },
    ],
}


def test_provisional_targets_do_not_block_until_measured() -> None:
    summary = _summary(
        real={"status": "not measured (no data)"},
        synthetic=_measured({"review": {"auto_accept_precision": wilson(50, 50)}}),
        hw=_measured({"handwriting_routed_to_review": wilson(4, 4)}),
    )
    result = {g["id"]: g for g in gates.target_gates(TARGETS, summary, [])}
    assert result["aa"]["status"] == "not_measured" and not result["aa"]["blocking"]
    assert result["aa"]["synthetic_counterparts"] == {"synthetic": 1.0}  # shown, not counted
    assert result["hw"]["status"] == "pass" and result["hw"]["blocking"]


def test_measured_targets_block_on_threshold_and_sample_size() -> None:
    small = _summary(
        real=_measured({"review": {"auto_accept_precision": wilson(100, 100)}}),
        hw=_measured({"handwriting_routed_to_review": wilson(3, 4)}),
    )
    result = {g["id"]: g for g in gates.target_gates(TARGETS, small, [])}
    assert result["aa"]["status"] == "fail" and "sample below 300" in result["aa"]["detail"]
    assert result["hw"]["status"] == "fail"
    big = _summary(
        real=_measured({"review": {"auto_accept_precision": wilson(397, 400)}}),
        hw=_measured({"handwriting_routed_to_review": wilson(4, 4)}),
    )
    result = {g["id"]: g for g in gates.target_gates(TARGETS, big, [])}
    assert result["aa"]["status"] == "pass" and result["aa"]["blocking"]


def test_always_binding_targets_block_when_unmeasured() -> None:
    summary = _summary(real={"status": "not measured (no data)"}, hw={"status": "not measured"})
    result = {g["id"]: g for g in gates.target_gates(TARGETS, summary, [])}
    assert result["hw"]["status"] == "not_measured" and result["hw"]["blocking"]


def test_per_language_targets_check_every_language() -> None:
    target = {
        "targets": [
            {
                "id": "cer",
                "title": "CER",
                "metric": "text.character_accuracy",
                "cohort": "text",
                "per": "language",
                "comparison": ">=",
                "threshold": 0.99,
                "min_sample": 100,
                "binding": "when_measured",
            }
        ]
    }
    by_language = {
        "en": {"text": {"character_accuracy": 0.999, "reference_characters": 500}},
        "kn": {"text": {"character_accuracy": 0.95, "reference_characters": 500}},
    }
    summary = _summary(text=_measured({}, by_language=by_language))
    (result,) = gates.target_gates(target, summary, [])
    assert result["status"] == "fail" and "kn: 0.9500" in result["detail"]


def test_performance_counts_only_on_the_reference_server() -> None:
    target = {
        "id": "tp",
        "title": "TP",
        "metric": "benchmark.pages_per_minute",
        "cohort": "benchmark",
        "comparison": ">=",
        "threshold": 6.95,
        "binding": "always",
    }
    laptop = {
        "host": {"cpu_model": "Laptop", "cpus": 4, "memory_mib": 30000},
        "started": "1",
        "benchmark": {"pages_per_minute": 9.0, "documents": 50, "clients": 2},
    }
    server = {"cpu_model": "Xeon", "cpus": 32, "memory_mib_min": 64000}
    undeclared = gates.benchmark_gate(target, "TP", [laptop], None)
    assert undeclared["status"] == "not_measured" and undeclared["blocking"]
    elsewhere = gates.benchmark_gate(target, "TP", [laptop], server)
    assert elsewhere["status"] == "not_measured"
    on_server = {
        **laptop,
        "host": {"cpu_model": "Xeon", "cpus": 32, "memory_mib": 128000},
        "benchmark": {**laptop["benchmark"], "pages_per_minute": 5.0},
    }
    assert gates.benchmark_gate(target, "TP", [laptop, on_server], server)["status"] == "fail"


# ----------------------------------------------------------------------------- benchmark
def test_benchmark_stats_parsing() -> None:
    spec = importlib.util.spec_from_file_location(
        "benchmark_report", ROOT / "deploy" / "bundle" / "benchmark_report.py"
    )
    assert spec and spec.loader
    report = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(report)
    stats = (
        "newgen-ocr-worker-1 150.5% 2.5GB / 6.442GB\nnewgen-ocr-api-1 3.0% 512MiB / 3GiB\n--\n"
        "newgen-ocr-worker-1 190.0% 3.1GB / 6.442GB\nnewgen-ocr-api-1 1.0% 520MiB / 3GiB\n--\n"
    )
    parsed = report.parse_stats(stats)
    assert parsed["newgen-ocr-worker-1"]["cpu_percent_peak"] == 190.0
    assert parsed["newgen-ocr-worker-1"]["cpu_percent_mean"] == 170.2
    assert parsed["newgen-ocr-api-1"]["memory_mib_peak"] == 520.0
    assert round(report.size_mib("3.1GB")) == round(3.1e9 / 2**20)


def test_tuning_tools_refuse_evaluation_data(tmp_path: Path) -> None:
    from newgen_ocr.evaluation.cohorts import refuse_evaluation_data

    registry = load_registry(ROOT / "evaluation" / "cohorts.json")
    for path in (
        tmp_path / "evaluation" / "real" / "documents",
        tmp_path / "evaluation" / "real" / "documents" / "batch-1",
        tmp_path / "evaluation" / "cohorts" / "generated" / "eval-tables",
        tmp_path / "evaluation",  # a parent of evaluation data
    ):
        with pytest.raises(ValueError, match="evaluation cohort"):
            refuse_evaluation_data(path, registry, tmp_path)
    refuse_evaluation_data(tmp_path / "evaluation" / "real" / "my-tuning-set", registry, tmp_path)
