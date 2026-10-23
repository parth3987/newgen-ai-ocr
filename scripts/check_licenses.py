"""Fail if any runtime dependency in requirements.lock has a non-approved license.

Usage: python scripts/check_licenses.py [requirements.lock]

Uses pip-licenses against the active environment, so run it inside the project venv
after installing the lock. A package passes if its declared license expression consists only
of PERMISSIVE terms (all terms for AND / ";" lists, any term for OR), or if the exact
(name, version, license) was manually reviewed (REVIEWED). New license types require review
and an update to docs/THIRD_PARTY_NOTICES.md.
"""

from __future__ import annotations

import json
import re
import subprocess  # nosec B404
import sys
from pathlib import Path

# SPDX identifiers and the trove-classifier spellings packages actually declare.
PERMISSIVE = {
    "MIT",
    "MIT License",
    "MIT-0",
    "MIT-CMU",
    "BSD-2-Clause",
    "BSD-3-Clause",
    "BSD License",
    "3-Clause BSD License",
    "0BSD",
    "Apache-2.0",
    "Apache 2.0",
    "Apache License 2.0",
    "Apache Software License",
    "PSF-2.0",
    "Python Software Foundation License",
    "ISC",
    "ISC License (ISCL)",
    "Zlib",
    "CC0-1.0",
    "Public Domain",
    "TCL",
    "HPND",
}

# Exact (name, version) -> declared license, approved after manual review. Use for copyleft
# or bundled-binary cases. Upgrading such a package forces a re-review.
REVIEWED: dict[tuple[str, str], str] = {
    # BSD-3-Clause/Apache-2.0 bindings plus bundled PDFium binary; dependency licenses
    # (FreeType FTL, IJG, ICU/Unicode, zlib, MIT, BSD, Apache) reviewed 2026-10-22.
    ("pypdfium2", "5.14.0"): "BSD-3-Clause, Apache-2.0, dependency licenses",
    # Weak copyleft, used unmodified as separately replaceable packages; obligations are
    # notice + replaceability (docs/THIRD_PARTY_NOTICES.md). Reviewed 2026-10-22.
    ("python-bidi", "0.6.11"): "GNU Library or Lesser General Public License (LGPL)",
    ("certifi", "2026.10.19"): "Mozilla Public License 2.0 (MPL 2.0)",
    ("tqdm", "4.70.1"): "MPL-2.0 AND MIT",
    # Pure-Python build, unmodified and replaceable; loads the system libpq (PostgreSQL
    # licence). Owner-approved 2026-10-23 (ADR-0029).
    ("psycopg", "3.3.6"): "LGPL-3.0-only",
}
# NOTE: opencv-python-headless declares Apache-2.0 and passes, but the PyPI wheel bundles
# LGPL FFmpeg and a vendor OpenSSL 1.1.1k build. Development only; production must use the
# minimal source build (docs/DECISIONS.md ADR-0015), enforced at application startup.

_PIN = re.compile(r"^([A-Za-z0-9_.-]+)==", re.MULTILINE)


def normalise(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def permissive(expression: str) -> bool:
    terms = [t.strip(" ()") for t in re.split(r"\s+(?:AND|OR)\s+|;\s*", expression)]
    terms = [t for t in terms if t]
    if not terms:
        return False
    if " OR " in expression and " AND " not in expression:
        return any(t in PERMISSIVE for t in terms)
    return all(t in PERMISSIVE for t in terms)


def approved(row: dict[str, str]) -> bool:
    if permissive(row["License"]):
        return True
    return REVIEWED.get((normalise(row["Name"]), row["Version"])) == row["License"]


def locked_packages(lock: Path) -> list[str]:
    return _PIN.findall(lock.read_text(encoding="utf-8"))


def main() -> int:
    lock = Path(sys.argv[1] if len(sys.argv) > 1 else "requirements.lock")
    packages = locked_packages(lock)
    # Fixed argv, no shell; package names come from our own lock file. --with-system so
    # pip-licenses does not hide its own dependencies (e.g. prettytable) or setuptools.
    result = subprocess.run(  # noqa: S603  # nosec B603
        [
            sys.executable,
            "-m",
            "piplicenses",
            "--from=mixed",
            "--format=json",
            "--with-system",
            "--packages",
            *packages,
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    rows = json.loads(result.stdout)
    found = {normalise(row["Name"]) for row in rows}
    missing = sorted({normalise(p) for p in packages} - found)
    rejected = [row for row in rows if not approved(row)]

    for row in sorted(rows, key=lambda r: normalise(r["Name"])):
        status = "OK " if approved(row) else "BAD"
        sys.stdout.write(f"{status} {row['Name']}=={row['Version']}  {row['License']}\n")
    if missing:
        sys.stdout.write(f"NOT INSTALLED (cannot verify): {', '.join(missing)}\n")
    if rejected or missing:
        sys.stdout.write("License check FAILED\n")
        return 1
    sys.stdout.write(f"License check passed ({len(rows)} runtime packages)\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
