"""Rehearse the disconnected production install (ADR-0032): ``make rehearse-install``.

Builds a simulated production host on this machine and runs the production workflow on it
with the real bundles, as an operator would:

- **No network at all**: a new network namespace with only a loopback interface (so not
  even DNS can leave), an empty container image store, and its own cgroup tree so the
  containers' memory, CPU and process limits apply. Podman runs as root inside an
  unprivileged user namespace (``unshare --map-auto``); nothing needs real root.
  The one difference from a real host: no systemd, so a loop runs Podman's health checks
  every 3 s (systemd timers do that on a real host).
- Steps: check the outer signature, extract, ``ngocrctl verify``, ``install`` (preflight,
  image and model checks, configuration, start, smoke tests), ``smoke --restart``, an
  upgrade to a second bundle (backup first), new data after the upgrade, then
  ``rollback --restore`` and a check that the stored data is exactly what it was before
  the upgrade.

Writes reports/install-rehearsal.json and the full log to reports/install-rehearsal.log;
exits 1 if any step fails. ``--keep`` leaves the work directory (dist/rehearsal).
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess  # nosec B404 - fixed commands, no shell
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
ANSI = re.compile(r"\x1b\[[0-9;]*m")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--upgrade-bundle", type=Path, required=True)
    parser.add_argument(
        "--signers", type=Path, default=ROOT / "dist" / "newgen-ocr.allowed_signers"
    )
    parser.add_argument("--work", type=Path, default=ROOT / "dist" / "rehearsal")
    parser.add_argument("--keep", action="store_true")
    args = parser.parse_args()
    for path in (args.bundle, args.upgrade_bundle, args.signers):
        if not path.resolve().is_file():
            parser.error(f"{path} not found")
    work = args.work.resolve()
    clean(work)
    work.mkdir(parents=True)
    command = [
        *(_tool("systemd-run"), "--user", "--scope", "-q", "-p", "Delegate=yes"),
        *(_tool("unshare"), "--map-auto", "--map-root-user", "--net", "--mount", "--cgroup"),
        *(
            "--fork",
            "--propagation",
            "private",
            "sh",
            str(ROOT / "scripts" / "rehearse_install.sh"),
        ),
        *(str(work), str(args.bundle.resolve()), str(args.upgrade_bundle.resolve())),
        str(args.signers.resolve()),
    ]
    reports = ROOT / "reports"
    reports.mkdir(exist_ok=True)
    report: dict[str, Any] = {
        "bundle": args.bundle.name,
        "upgrade_bundle": args.upgrade_bundle.name,
    }
    steps: dict[str, dict[str, Any]] = {}
    current = None
    started = time.monotonic()
    with (reports / "install-rehearsal.log").open("w") as log:
        process = subprocess.Popen(  # noqa: S603  # nosec B603 - fixed arguments
            command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
        )
        for raw in process.stdout or ():
            log.write(raw)
            log.flush()
            sys.stdout.write(raw)
            # Compose's banner can leave colour codes in front of the next line's marker.
            marker = re.search(r"@@(?:BEGIN|STEP|DATA) .*", ANSI.sub("", raw))
            line = marker.group(0) + "\n" if marker else ""
            if line.startswith("@@BEGIN "):
                current = line.split()[1]
                steps[current] = {"started_s": round(time.monotonic() - started)}
            elif line.startswith("@@STEP "):
                _, name, rc = line.split()
                steps[name]["passed"] = rc == "0"
                steps[name]["seconds"] = (
                    round(time.monotonic() - started) - steps[name]["started_s"]
                )
            elif line.startswith("@@DATA "):
                _, key, value = line.rstrip("\n").split(" ", 2)
                try:
                    report[key] = json.loads(value)
                except ValueError:
                    report[key] = value
        process.wait()
    report["steps"] = steps
    failures = [name for name, step in steps.items() if not step.get("passed")]
    before, after = report.get("verify_before", {}), report.get("verify_after", {})
    if isinstance(before, dict) and isinstance(after, dict) and before and after:
        report["rollback_restored_exactly"] = (
            before.get("fingerprint") == after.get("fingerprint")
            and before.get("counts") == after.get("counts")
            and after.get("ok") is True
        )
        # The upgraded release wrote new data (a web user); the rollback's restore removes it.
        report["upgrade_added_data"] = report.get("upgrade_user_created") is True
        removed = report.get("upgrade_user_removed") is True
        if not (report["rollback_restored_exactly"] and report["upgrade_added_data"] and removed):
            failures.append("rollback did not restore the pre-upgrade data exactly")
    else:
        failures.append("no before/after verification")
    if "teardown" not in steps:
        failures.append("did not finish")
    report["failures"] = failures
    report["passed"] = not failures
    (reports / "install-rehearsal.json").write_text(json.dumps(report, indent=2) + "\n")
    if not args.keep:
        clean(work)
    sys.stdout.write(json.dumps({k: report[k] for k in ("passed", "failures")}, indent=2) + "\n")
    return 0 if not failures else 1


def _tool(name: str) -> str:
    path = shutil.which(name)
    if path is None:
        raise SystemExit(f"{name} not found (util-linux and systemd are needed)")
    return path


def clean(work: Path) -> None:
    """Remove the work directory (container files belong to subordinate user IDs)."""
    if work.exists():
        subprocess.run(  # noqa: S603  # nosec B603 - fixed arguments
            [_tool("unshare"), "--map-auto", "--map-root-user", "rm", "-rf", str(work)],
            check=False,
        )
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
