"""Fail if any runtime dependency in requirements.lock has a non-allowlisted license.

Usage: python scripts/check_licenses.py [requirements.lock]

Uses pip-licenses against the active environment, so run it inside the project venv
after installing the lock. New license types require review and an update to
docs/THIRD_PARTY_NOTICES.md before being added to ALLOWED.
"""

from __future__ import annotations

import json
import re
import subprocess  # nosec B404
import sys
from pathlib import Path

ALLOWED = {
    "MIT",
    "MIT License",
    "BSD-2-Clause",
    "BSD-3-Clause",
    "BSD License",
    "Apache-2.0",
    "Apache Software License",
    "PSF-2.0",
    "Python Software Foundation License",
    "ISC",
    "ISC License (ISCL)",
    "MIT-CMU",
}

# Packages whose declared license is not a single allowlisted identifier, approved after
# manual review of the exact version. Upgrading such a package forces a re-review.
REVIEWED: dict[tuple[str, str], str] = {
    # BSD-3-Clause/Apache-2.0 bindings plus bundled PDFium binary; dependency licenses
    # (FreeType FTL, IJG, ICU/Unicode, zlib, MIT, BSD, Apache) reviewed 2026-10-22.
    # See docs/THIRD_PARTY_NOTICES.md.
    ("pypdfium2", "5.14.0"): "BSD-3-Clause, Apache-2.0, dependency licenses",
    # All components permissive (BSD-3-Clause, 0BSD, MIT, Zlib, CC0-1.0); reviewed 2026-10-22.
    ("numpy", "2.4.6"): "BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0",
}
# NOTE: opencv-python-headless declares Apache-2.0 and passes the allowlist, but the PyPI
# wheel bundles LGPL FFmpeg and EOL OpenSSL 1.1.1k. It is accepted for development only;
# production must use the minimal source build (docs/DECISIONS.md ADR-0015), which the
# application enforces at startup.


def approved(row: dict[str, str]) -> bool:
    if row["License"] in ALLOWED:
        return True
    key = (row["Name"].lower().replace("_", "-"), row["Version"])
    return REVIEWED.get(key) == row["License"]


_PIN = re.compile(r"^([A-Za-z0-9_.-]+)==", re.MULTILINE)


def locked_packages(lock: Path) -> list[str]:
    return _PIN.findall(lock.read_text(encoding="utf-8"))


def main() -> int:
    lock = Path(sys.argv[1] if len(sys.argv) > 1 else "requirements.lock")
    packages = locked_packages(lock)
    # Fixed argv, no shell; package names come from our own lock file.
    result = subprocess.run(  # noqa: S603  # nosec B603
        [
            sys.executable,
            "-m",
            "piplicenses",
            "--from=mixed",
            "--format=json",
            "--packages",
            *packages,
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    rows = json.loads(result.stdout)
    found = {row["Name"].lower().replace("_", "-") for row in rows}
    missing = sorted({p.lower().replace("_", "-") for p in packages} - found)
    rejected = [row for row in rows if not approved(row)]

    for row in rows:
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
