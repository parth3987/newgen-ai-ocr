"""Fail on npm packages without integrity hashes, or with licences outside the allowlist.

Reads ``frontend/package-lock.json`` (ADR-0030). Runtime packages (shipped in the built
application) must be permissive. Build and test tools may also use the reviewed
weak-copyleft or public-domain licences in BUILD_ONLY: they run at staging and are not
shipped. Any new licence needs review (THIRD_PARTY_NOTICES.md) before it is added here.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOCK = ROOT / "frontend" / "package-lock.json"

PERMISSIVE = {"MIT", "ISC", "Apache-2.0", "BSD-2-Clause", "BSD-3-Clause", "0BSD", "MIT-0"}
# Reviewed 2026-10-23 for build/test tools only (THIRD_PARTY_NOTICES.md).
BUILD_ONLY = {
    "MPL-2.0": "lightningcss (CSS minifier used by Vite at build time)",
    "BlueOak-1.0.0": "lru-cache (permissive)",
    "CC0-1.0": "mdn-data (public domain data)",
}


def main() -> int:
    lock = json.loads(LOCK.read_text())
    problems: list[str] = []
    runtime = 0
    for path, entry in lock["packages"].items():
        if not path:
            continue
        name = path.split("node_modules/")[-1]
        label = f"{name}@{entry.get('version')}"
        if not entry.get("integrity", "").startswith("sha512-"):
            problems.append(f"{label}: no sha512 integrity hash")
        licence = entry.get("license")
        if not entry.get("dev"):
            runtime += 1
            if licence not in PERMISSIVE:
                problems.append(f"{label}: runtime licence {licence!r} not allowed")
        elif licence not in PERMISSIVE and licence not in BUILD_ONLY:
            problems.append(f"{label}: licence {licence!r} needs review")
        if entry.get("hasInstallScript"):
            # Never run: frontend/.npmrc sets ignore-scripts (fsevents: macOS only).
            sys.stdout.write(f"note {label}: install script, not run\n")
    for problem in problems:
        sys.stdout.write(f"FAIL {problem}\n")
    if problems:
        return 1
    sys.stdout.write(
        f"npm licence check passed ({len(lock['packages']) - 1} packages, {runtime} shipped)\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
