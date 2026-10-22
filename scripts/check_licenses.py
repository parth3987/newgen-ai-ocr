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
}

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
    rejected = [row for row in rows if row["License"] not in ALLOWED]

    for row in rows:
        status = "OK " if row["License"] in ALLOWED else "BAD"
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
