"""Combine a service benchmark with its CPU and memory samples (ADR-0033).

Run by ``ngocrctl benchmark`` in a container with no network. Reads ``bench.json`` (from
benchmark.py) and ``stats`` (``<engine> stats`` samples: ``NAME CPU% USED / LIMIT`` lines,
one block per sample, separated by ``--``). Writes the report the release gates read.

Arguments: DIR CPU_MODEL CPUS MEMORY_MIB RELEASE STARTED.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

UNITS = {"b": 1, "kb": 1e3, "mb": 1e6, "gb": 1e9, "tb": 1e12, "kib": 2**10, "mib": 2**20,
         "gib": 2**30, "tib": 2**40}  # fmt: skip


def size_mib(text: str) -> float | None:
    match = re.fullmatch(r"([0-9.]+)\s*([a-zA-Z]+)", text.strip())
    if not match or match.group(2).lower() not in UNITS:
        return None
    return float(match.group(1)) * UNITS[match.group(2).lower()] / 2**20


def parse_stats(text: str) -> dict[str, dict[str, Any]]:
    """Per container: mean and peak CPU (% of one core), peak memory (MiB), samples."""
    cpu: dict[str, list[float]] = {}
    memory: dict[str, list[float]] = {}
    for line in text.splitlines():
        parts = line.split(None, 2)
        if len(parts) != 3 or not parts[1].endswith("%"):
            continue
        name, percent, usage = parts
        try:
            cpu.setdefault(name, []).append(float(percent.rstrip("%")))
        except ValueError:
            continue
        used = size_mib(usage.split("/")[0])
        if used is not None:
            memory.setdefault(name, []).append(used)
    return {
        name: {
            "samples": len(values),
            "cpu_percent_mean": round(sum(values) / len(values), 1),
            "cpu_percent_peak": round(max(values), 1),
            "memory_mib_peak": round(max(memory.get(name, [0.0])), 1),
        }
        for name, values in sorted(cpu.items())
    }


def main(argv: list[str]) -> int:
    folder, cpu_model, cpus, memory_mib, release, started = argv
    bench = json.loads((Path(folder) / "bench.json").read_text())
    stats_file = Path(folder) / "stats"
    containers = parse_stats(stats_file.read_text() if stats_file.is_file() else "")
    app = {n: v for n, v in containers.items() if any(s in n for s in ("api", "worker"))}
    report = {
        "kind": "service-benchmark",
        "release": release,
        "started": started,
        "host": {"cpu_model": cpu_model, "cpus": int(cpus), "memory_mib": int(memory_mib)},
        "benchmark": bench,
        "cpu": {
            "containers": containers,
            "api_and_workers_cpu_percent_mean": round(
                sum(v["cpu_percent_mean"] for v in app.values()), 1
            ),
        },
        "memory": {
            "api_and_workers_peak_mib": round(sum(v["memory_mib_peak"] for v in app.values()), 1),
        },
    }
    sys.stdout.write(json.dumps(report, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
