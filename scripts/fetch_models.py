"""Download pinned OCR model files during staging, and verify them.

Usage:
    python scripts/fetch_models.py [--models-dir models] [--only PREFIX] [--record]
    python scripts/fetch_models.py --verify [--models-dir models]   # no network

``--verify`` (used by the container build) checks every locked file's SHA-256 and fails on
any file in the models directory that the lock does not list: only approved models ship.

Network use is confined to this staging script; the application never downloads models.
Every file is fetched from the exact revision pinned in models.lock.json and checked
against its recorded SHA-256. With --record, missing hashes are filled in (first fetch of a
new model): for Hugging Face LFS files the computed hash must also match the SHA-256 that
Hugging Face publishes, so a hash is never trusted from a single source.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
LOCK = ROOT / "models.lock.json"
_TIMEOUT = 60
_ATTEMPTS = 4


def _out(message: str) -> None:
    sys.stdout.write(message + "\n")
    sys.stdout.flush()


def _url(source: dict[str, str], filename: str) -> str:
    path = urllib.parse.quote(filename)
    if source["type"] == "huggingface":
        return f"https://huggingface.co/{source['repo']}/resolve/{source['revision']}/{path}"
    if source["type"] == "github":
        return f"https://raw.githubusercontent.com/{source['repo']}/{source['revision']}/{path}"
    raise ValueError(f"unknown source type {source['type']!r}")


def _get(url: str) -> bytes:
    last: Exception | None = None
    for attempt in range(1, _ATTEMPTS + 1):
        try:
            with urllib.request.urlopen(url, timeout=_TIMEOUT) as response:  # noqa: S310  # nosec B310
                return bytes(response.read())
        except OSError as exc:  # includes URLError and timeouts
            last = exc
            time.sleep(2 * attempt)
    raise RuntimeError(f"download failed after {_ATTEMPTS} attempts: {url}") from last


def _published_lfs_hashes(source: dict[str, str]) -> dict[str, str]:
    if source["type"] != "huggingface":
        return {}
    url = f"https://huggingface.co/api/models/{source['repo']}/revision/{source['revision']}?blobs=true"
    info: dict[str, Any] = json.loads(_get(url))
    return {s["rfilename"]: s["lfs"]["sha256"] for s in info.get("siblings", []) if s.get("lfs")}


def _published_git_blobs(source: dict[str, str]) -> dict[str, str]:
    if source["type"] == "huggingface":
        # Non-LFS files (configs) are published with their git blob SHA-1.
        url = f"https://huggingface.co/api/models/{source['repo']}/tree/{source['revision']}"
        files: list[dict[str, Any]] = json.loads(_get(url))
        return {f["path"]: f["oid"] for f in files if f["type"] == "file" and not f.get("lfs")}
    if source["type"] != "github":
        return {}
    url = (
        f"https://api.github.com/repos/{source['repo']}/git/trees/{source['revision']}?recursive=1"
    )
    tree: dict[str, Any] = json.loads(_get(url))
    return {
        entry["path"]: entry["sha"] for entry in tree.get("tree", []) if entry["type"] == "blob"
    }


def _git_blob_sha1(data: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(data) + data, usedforsecurity=False).hexdigest()


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def fetch(lock_path: Path, models_dir: Path, only: str | None, record: bool) -> int:
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    changed = False
    failures = 0
    for model in lock["models"]:
        if only and not model["id"].startswith(only):
            continue
        target_dir = models_dir / model["dir"]
        target_dir.mkdir(parents=True, exist_ok=True)
        published: dict[str, str] | None = None
        blobs: dict[str, str] = {}
        for filename, expected in model["files"].items():
            # Repository paths may be nested; files are stored flat under the model dir.
            target = target_dir / Path(filename).name
            if expected and target.is_file() and _sha256(target.read_bytes()) == expected:
                _out(f"ok        {model['id']}/{filename}")
                continue
            data = _get(_url(model["source"], filename))
            digest = _sha256(data)
            if expected is None:
                if not record:
                    _out(f"UNPINNED  {model['id']}/{filename} (run with --record)")
                    failures += 1
                    continue
                if published is None:
                    published = _published_lfs_hashes(model["source"])
                    blobs = _published_git_blobs(model["source"])
                if filename in published and published[filename] != digest:
                    _out(f"MISMATCH  {model['id']}/{filename} vs published LFS hash")
                    failures += 1
                    continue
                if filename in blobs and blobs[filename] != _git_blob_sha1(data):
                    _out(f"MISMATCH  {model['id']}/{filename} vs published git blob")
                    failures += 1
                    continue
                model["files"][filename] = digest
                changed = True
                verified = filename in published or filename in blobs
                note = "cross-checked" if verified else "recorded"
                _out(f"{note:9s} {model['id']}/{filename} {digest[:16]}")
            elif digest != expected:
                _out(f"MISMATCH  {model['id']}/{filename}: got {digest}, expected {expected}")
                failures += 1
                continue
            else:
                _out(f"fetched   {model['id']}/{filename}")
            tmp = target.with_suffix(target.suffix + ".part")
            tmp.write_bytes(data)
            os.replace(tmp, target)
    if changed:
        lock_path.write_text(
            json.dumps(lock, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        _out(f"updated {lock_path.name}; review and commit it")
    return 1 if failures else 0


def verify(lock_path: Path, models_dir: Path) -> int:
    """Offline: every locked file present with its hash, and nothing else in the tree."""
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    expected: dict[Path, str] = {}
    for model in lock["models"]:
        for filename, digest in model["files"].items():
            expected[models_dir / model["dir"] / Path(filename).name] = digest
    failures = 0
    for path, digest in sorted(expected.items()):
        if not path.is_file():
            _out(f"MISSING   {path.relative_to(models_dir)}")
            failures += 1
        elif digest is None or _sha256(path.read_bytes()) != digest:
            _out(f"MISMATCH  {path.relative_to(models_dir)}")
            failures += 1
    for path in sorted(p for p in models_dir.rglob("*") if p.is_file()):
        if path not in expected:
            _out(f"UNLISTED  {path.relative_to(models_dir)} (not in {lock_path.name})")
            failures += 1
    _out(
        f"{len(expected) - failures} of {len(expected)} model files verified"
        if not failures
        else f"{failures} problem(s)"
    )
    return 1 if failures else 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lock", type=Path, default=LOCK)
    parser.add_argument("--models-dir", type=Path, default=ROOT / "models")
    parser.add_argument("--only", help="only model ids starting with this prefix")
    parser.add_argument("--record", action="store_true", help="record hashes for new models")
    parser.add_argument("--verify", action="store_true", help="verify only, no network")
    args = parser.parse_args()
    if args.verify:
        return verify(args.lock, args.models_dir)
    return fetch(args.lock, args.models_dir, args.only, args.record)


if __name__ == "__main__":
    sys.exit(main())
