"""CPU benchmark for quality assessment and preprocessing profiles.

Usage: python scripts/benchmark_preprocessing.py [--dpi 300] [--repeat 3] [--threads N]

Uses synthetic A4 pages (tests/fixtures/pages.py) so results are reproducible and contain
no real documents. Reports milliseconds per page; results go in docs/PERFORMANCE.md.
"""

from __future__ import annotations

import argparse
import statistics
import sys
import time
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import cv2
from tests.fixtures import pages

from newgen_ocr.imaging.quality import assess
from newgen_ocr.imaging.runtime import configure_opencv, runtime_info
from newgen_ocr.preprocessing.pipeline import preprocess_page
from newgen_ocr.preprocessing.profiles import BUILTIN_PROFILES

PROFILES = ["standard", "degraded_scan", "photo", "binarize"]


def samples(dpi: int) -> dict[str, pages.Gray]:
    clean = pages.text_page(dpi=dpi)
    return {
        "clean": clean,
        "skewed_3deg": pages.rotate(clean, 3.0),
        "rotated_90": pages.quarter_turn(clean, 1),
        "noisy_s20": pages.gaussian_noise(clean, 20),
        "faded": pages.text_page(dpi=dpi, ink=150, background=200),
        "shadowed": pages.shadow(clean),
    }


def _summary(values: list[float]) -> str:
    ordered = sorted(values)
    p95 = ordered[min(len(ordered) - 1, round(0.95 * (len(ordered) - 1)))]
    return f"{statistics.mean(values):8.0f} {p95:8.0f}"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dpi", type=int, default=300)
    parser.add_argument("--repeat", type=int, default=3)
    parser.add_argument("--threads", type=int, default=None)
    args = parser.parse_args()
    configure_opencv(args.threads)
    info = runtime_info()
    out = sys.stdout.write

    out(f"CPU: {info.cpu_model} | logical CPUs: {info.logical_cpus} | ")
    out(f"OpenCV {info.opencv_version} threads: {cv2.getNumThreads()} | ")
    out(f"NumPy {info.numpy_version}\n")
    out(f"Pages: synthetic A4 at {args.dpi} DPI, repeat={args.repeat}\n\n")

    data = samples(args.dpi)
    quality = {name: assess(img, args.dpi)[0] for name, img in data.items()}

    out(f"{'sample':14s} {'stage':16s} {'mean ms':>8s} {'p95 ms':>8s}  applied steps\n")
    step_times: dict[str, list[float]] = defaultdict(list)
    totals: dict[str, list[float]] = defaultdict(list)
    for name, img in data.items():
        timings = []
        for _ in range(args.repeat):
            start = time.perf_counter()
            assess(img, args.dpi)
            timings.append((time.perf_counter() - start) * 1000)
        totals["assessment"].extend(timings)
        flags = ",".join(f.value for f in quality[name].flags) or "-"
        out(f"{name:14s} {'assessment':16s} {_summary(timings)}  flags: {flags}\n")
        for profile_name in PROFILES:
            profile = BUILTIN_PROFILES[profile_name]
            timings = []
            applied: list[str] = []
            for _ in range(args.repeat):
                start = time.perf_counter()
                _, record = preprocess_page(1, img, args.dpi, profile, quality[name])
                timings.append((time.perf_counter() - start) * 1000)
                applied = [s.name for s in record.steps if s.applied]
                for step in record.steps:
                    if step.applied:
                        step_times[step.name].append(step.duration_ms)
            totals[profile_name].extend(timings)
            out(f"{'':14s} {profile_name:16s} {_summary(timings)}  {','.join(applied) or '-'}\n")

    out(f"\n{'overall':31s} {'mean ms':>8s} {'p95 ms':>8s}\n")
    for stage, values in totals.items():
        out(f"{stage:31s} {_summary(values)}\n")
    out(f"\n{'step (when applied)':31s} {'mean ms':>8s} {'p95 ms':>8s}\n")
    for step, values in sorted(step_times.items()):
        out(f"{step:31s} {_summary(values)}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
