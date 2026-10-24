"""Release gates (ADR-0033): ``make release-gates``. Production approval needs every
blocking gate to pass.

Usage::

    python scripts/release_gates.py --image localhost/newgen-ocr:0.13.0 \\
        [--benchmark state/benchmark-<stamp>.json ...] [--allow-dirty]

Gates:
1. **Staging evidence** (Phase 12): the tests passed, and the Trivy, image-SBOM and offline
   test reports are about this image (by content ID). The install rehearsal passed.
2. **Separation**: no evaluation document is a tuning document (from the evaluation run).
3. **Freshness**: the evaluation ran on this commit, complete (no ``--limit``), from a clean
   tree.
4. **Reporting**: every measured cohort reports its dataset size, languages, document types
   and conditions, and every cohort with automatic processing reports both auto-accept
   precision and coverage.
5. **Targets** (``evaluation/targets.json``), each tied to one metric and one cohort:
   - ``binding: always``: must be measured and met;
   - ``binding: when_measured`` (the owner's provisional 99% targets): reported as *not
     measured* until their real cohort has data; then they must be met, on at least
     ``min_sample`` observations;
   - performance targets count only from a service benchmark on the declared reference
     server (``reference_server`` in targets.json).

Synthetic counterparts of each target are reported beside it, never as evidence for it.
Writes reports/release-gates.json and reports/release-gates.md; exits 1 unless approved.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import operator
import shutil
import subprocess  # nosec B404 - git and the engine, fixed arguments
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / "reports"
EVALUATION = REPORTS / "evaluation"
COMPARE: dict[str, Callable[[float, float], bool]] = {">=": operator.ge, "<=": operator.le}


def _builder() -> Any:
    spec = importlib.util.spec_from_file_location(
        "build_bundle", ROOT / "scripts" / "build_bundle.py"
    )
    if spec is None or spec.loader is None:
        raise SystemExit("scripts/build_bundle.py not found")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def gate(
    gate_id: str, title: str, status: str, blocking: bool, detail: str = "", **extra: Any
) -> dict[str, Any]:
    return {
        "id": gate_id,
        "title": title,
        "status": status,
        "blocking": blocking,
        "detail": detail,
        **extra,
    }


def lookup(metrics: dict[str, Any], path: str) -> tuple[float | None, int | None]:
    """A metric's value and sample size from a report section (``a.b`` paths)."""
    node: Any = metrics
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return None, None
        node = node[part]
    if isinstance(node, dict):
        return node.get("value"), node.get("n")
    if isinstance(node, int | float):
        return float(node), None
    return None, None


def sample_size(metrics: dict[str, Any], path: str, n: int | None) -> int | None:
    if path.startswith("text."):
        return int(metrics.get("text", {}).get("reference_characters", 0))
    return n


def staging_gates(image_id: str) -> list[dict[str, Any]]:
    builder = _builder()
    problems, summary = builder.check_gates(REPORTS, image_id)
    out = [
        gate(
            "staging-evidence",
            "Tests, Trivy, SBOMs and the offline test passed for this image",
            "pass" if not problems else "fail",
            True,
            "; ".join(problems) or f"image {image_id[:12]}: {json.dumps(summary)}",
        )
    ]
    rehearsal = REPORTS / "install-rehearsal.json"
    if rehearsal.is_file():
        data = json.loads(rehearsal.read_text())
        ok = data.get("passed") is True
        out.append(
            gate(
                "install-rehearsal",
                "Disconnected install, restart, upgrade and rollback rehearsal",
                "pass" if ok else "fail",
                True,
                "; ".join(data.get("failures", [])) or data.get("bundle", ""),
            )
        )
    else:
        out.append(
            gate(
                "install-rehearsal",
                "Disconnected install rehearsal",
                "not_measured",
                True,
                "reports/install-rehearsal.json missing (make rehearse-install)",
            )
        )
    return out


def evaluation_gates(
    summary: dict[str, Any], commit: str, allow_dirty: bool
) -> list[dict[str, Any]]:
    out = []
    separation = summary.get("separation", {})
    out.append(
        gate(
            "separation",
            "No evaluation document is a tuning document",
            "pass" if separation.get("passed") else "fail",
            True,
            f"{separation.get('evaluation_documents')} evaluation vs "
            f"{separation.get('tuning_documents')} tuning documents; "
            f"{len(separation.get('problems', []))} problems",
        )
    )
    env = summary.get("environment", {})
    fresh = env.get("commit") == commit and (allow_dirty or not env.get("dirty"))
    trials = [
        c for c, r in summary["cohorts"].items() if str(r.get("status", "")).startswith("trial")
    ]
    out.append(
        gate(
            "freshness",
            "The evaluation ran, complete, on this commit",
            "pass" if fresh and not trials else "fail",
            True,
            f"evaluated {str(env.get('commit'))[:12]} (dirty: {env.get('dirty')}), "
            f"current {commit[:12]}; limited cohorts: {trials or 'none'}",
        )
    )
    missing = []
    for cohort_id, report in summary["cohorts"].items():
        if report.get("status") != "measured":
            continue
        for key in ("dataset_size", "languages", "document_types", "conditions"):
            if not report.get(key):
                missing.append(f"{cohort_id}: {key}")
        review = report.get("metrics", {}).get("review")
        if review is not None and not (
            "auto_accept_precision" in review and "auto_accept_coverage" in review
        ):
            missing.append(f"{cohort_id}: auto-accept precision and coverage")
    out.append(
        gate(
            "reporting",
            "Reports state size, language, document type and conditions; accuracy with coverage",
            "pass" if not missing else "fail",
            True,
            "; ".join(missing) or "complete",
        )
    )
    return out


def target_gates(
    targets: dict[str, Any], summary: dict[str, Any], benchmarks: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    out = []
    reference = targets.get("reference_server")
    for target in targets["targets"]:
        title = f"{target['title']} [{target['metric']} on {target['cohort']}]"
        blocking_when_measured = target["binding"] == "when_measured"
        compare = COMPARE[target["comparison"]]
        threshold = float(target["threshold"])
        counterparts = {
            c: lookup(summary["cohorts"].get(c, {}).get("metrics", {}), target["metric"])[0]
            for c in target.get("synthetic_counterparts", [])
        }
        if target["cohort"] == "benchmark":
            out.append(benchmark_gate(target, title, benchmarks, reference))
            continue
        cohorts = (
            [c for c, r in summary["cohorts"].items() if r.get("status") == "measured"]
            if target["cohort"] == "*"
            else [target["cohort"]]
        )
        results = []
        for cohort_id in cohorts:
            report = summary["cohorts"].get(cohort_id, {})
            if report.get("status") != "measured":
                results.append((cohort_id, None, None))
                continue
            path = target["metric"]
            sections: dict[str, dict[str, Any]]
            if path.startswith("performance."):
                path = path.removeprefix("performance.")
                sections = {cohort_id: report.get("performance", {})}
            elif target.get("per") == "language":
                # Slices exist when a cohort has several languages; else the whole cohort.
                sections = report.get("by_language") or {report["languages"][0]: report["metrics"]}
                sections = {k: v.get("metrics", v) for k, v in sections.items()}
            else:
                sections = {cohort_id: report["metrics"]}
            for name, metrics in sections.items():
                value, n = lookup(metrics, path)
                results.append((name, value, sample_size(metrics, target["metric"], n)))
        measured = [r for r in results if r[1] is not None]
        if not measured:
            status = "not_measured"
            blocking = not blocking_when_measured
            detail = "no data for " + ", ".join(r[0] for r in results)
        else:
            too_small = [
                r
                for r in measured
                if target.get("min_sample") and (r[2] or 0) < target["min_sample"]
            ]
            failed = [r for r in measured if not compare(float(r[1]), threshold)]
            status = "fail" if failed or too_small or len(measured) < len(results) else "pass"
            blocking = True
            detail = "; ".join(f"{name}: {value:.4f} (n={n})" for name, value, n in measured) + (
                f"; sample below {target['min_sample']}: {[r[0] for r in too_small]}"
                if too_small
                else ""
            )
        out.append(
            gate(
                target["id"],
                title,
                status,
                blocking,
                detail,
                threshold=f"{target['comparison']} {threshold}",
                synthetic_counterparts=counterparts,
                basis=target.get("basis", ""),
            )
        )
    return out


def benchmark_gate(
    target: dict[str, Any],
    title: str,
    benchmarks: list[dict[str, Any]],
    reference: dict[str, Any] | None,
) -> dict[str, Any]:
    compare = COMPARE[target["comparison"]]
    threshold = float(target["threshold"])
    if reference is None:
        return gate(
            target["id"],
            title,
            "not_measured",
            True,
            "no reference server declared in evaluation/targets.json",
            threshold=f"{target['comparison']} {threshold}",
            other_hosts=[_bench_value(b, target["metric"]) for b in benchmarks],
        )
    on_server = [b for b in benchmarks if _matches(b["host"], reference)]
    if not on_server:
        return gate(
            target["id"],
            title,
            "not_measured",
            True,
            f"no benchmark from the reference server ({reference.get('cpu_model')})",
            threshold=f"{target['comparison']} {threshold}",
            other_hosts=[_bench_value(b, target["metric"]) for b in benchmarks],
        )
    latest = max(on_server, key=lambda b: b.get("started", ""))
    value = _bench_value(latest, target["metric"])["value"]
    ok = value is not None and compare(float(value), threshold)
    return gate(
        target["id"],
        title,
        "pass" if ok else "fail",
        True,
        f"{value} on {latest['host']['cpu_model']} ({latest['host']['cpus']} CPUs), "
        f"{latest['benchmark']['documents']} documents, {latest['benchmark']['clients']} clients",
        threshold=f"{target['comparison']} {threshold}",
    )


def _bench_value(report: dict[str, Any], metric: str) -> dict[str, Any]:
    key = metric.removeprefix("benchmark.")
    return {"host": report["host"], "value": report["benchmark"].get(key)}


def _matches(host: dict[str, Any], reference: dict[str, Any]) -> bool:
    return (
        host.get("cpu_model") == reference.get("cpu_model")
        and int(host.get("cpus", 0)) == int(reference.get("cpus", -1))
        and int(host.get("memory_mib", 0)) >= int(reference.get("memory_mib_min", 0))
    )


def markdown(result: dict[str, Any]) -> str:
    lines = [
        f"# Release gates: {'APPROVED' if result['approved'] else 'NOT APPROVED'}",
        "",
        f"Image `{result['image_id'][:12]}`, commit `{result['commit'][:12]}`, "
        f"{result['created']}.",
        "",
        "| Gate | Status | Blocking | Detail |",
        "|---|---|---|---|",
    ]
    for g in result["gates"]:
        detail = g["detail"].replace("|", "\\|")
        if g.get("synthetic_counterparts"):
            synthetic = ", ".join(
                f"{c}: {'-' if v is None else f'{v:.4f}'}"
                for c, v in g["synthetic_counterparts"].items()
            )
            detail += f" (synthetic, not evidence: {synthetic})"
        lines.append(
            f"| {g['title']} | **{g['status']}** | {'yes' if g['blocking'] else 'no'} | {detail} |"
        )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", required=True)
    parser.add_argument("--benchmark", type=Path, action="append", default=[])
    parser.add_argument("--allow-dirty", action="store_true")
    args = parser.parse_args()
    engine = shutil.which("podman") or shutil.which("docker") or "podman"
    image_id = (
        subprocess.run(  # noqa: S603  # nosec B603 - fixed arguments
            [engine, "image", "inspect", "--format", "{{.Id}}", args.image],
            capture_output=True,
            text=True,
            check=True,
        )
        .stdout.strip()
        .removeprefix("sha256:")
    )
    git = shutil.which("git") or "git"
    commit = subprocess.run(  # noqa: S603  # nosec B603 - fixed arguments
        [git, "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.strip()
    summary_file = EVALUATION / "summary.json"
    if not summary_file.is_file():
        raise SystemExit("reports/evaluation/summary.json missing (make evaluate)")
    summary = json.loads(summary_file.read_text())
    targets = json.loads((ROOT / "evaluation" / "targets.json").read_text())
    benchmarks = []
    for path in args.benchmark:
        report = json.loads(path.read_text())
        if not isinstance(report, dict) or report.get("kind") != "service-benchmark":
            raise SystemExit(f"{path} is not a service benchmark report (ngocrctl benchmark)")
        benchmarks.append(report)
    gates = [
        *staging_gates(image_id),
        *evaluation_gates(summary, commit, args.allow_dirty),
        *target_gates(targets, summary, benchmarks),
    ]
    blocking = [g for g in gates if g["blocking"] and g["status"] != "pass"]
    result = {
        "created": datetime.now(tz=UTC).isoformat(),
        "image_id": image_id,
        "commit": commit,
        "approved": not blocking,
        "blocking": [g["id"] for g in blocking],
        "gates": gates,
    }
    (REPORTS / "release-gates.json").write_text(json.dumps(result, indent=2) + "\n")
    (REPORTS / "release-gates.md").write_text(markdown(result))
    sys.stdout.write(markdown(result))
    return 0 if result["approved"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
