"""Model integrity and offline guarantees (ADR-0020)."""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from newgen_ocr.errors import AppError, ErrorCode
from newgen_ocr.ocr.model_store import ModelStore
from tests.conftest import MODELS_DIR, MODELS_LOCK, ROOT


def _lock_with(tmp_path: Path, files: dict[str, str | None]) -> Path:
    lock = {
        "schema": 1,
        "models": [
            {
                "id": "test/model",
                "engine": "paddleocr",
                "name": "test_model",
                "kind": "recognition",
                "languages": ["en"],
                "license": "Apache-2.0",
                "training_data": "n/a",
                "source": {"type": "huggingface", "repo": "x/y", "revision": "abc"},
                "dir": "test/model",
                "files": files,
            }
        ],
    }
    path = tmp_path / "lock.json"
    path.write_text(json.dumps(lock))
    return path


def test_lock_file_pins_every_model_file() -> None:
    lock = json.loads(MODELS_LOCK.read_text())
    assert len(lock["models"]) >= 14
    for model in lock["models"]:
        assert len(model["source"]["revision"]) == 40, model["id"]
        assert model["license"] == "Apache-2.0"
        for name, digest in model["files"].items():
            assert digest and len(digest) == 64, f"{model['id']}/{name} is not pinned"


def test_verify_accepts_matching_file(tmp_path: Path) -> None:
    (tmp_path / "test" / "model").mkdir(parents=True)
    (tmp_path / "test" / "model" / "w.bin").write_bytes(b"weights")
    digest = hashlib.sha256(b"weights").hexdigest()
    store = ModelStore(tmp_path, _lock_with(tmp_path, {"w.bin": digest}))
    assert store.verify(store.entries[0]) == tmp_path / "test" / "model"


def test_tampered_model_is_rejected(tmp_path: Path) -> None:
    (tmp_path / "test" / "model").mkdir(parents=True)
    (tmp_path / "test" / "model" / "w.bin").write_bytes(b"tampered")
    store = ModelStore(tmp_path, _lock_with(tmp_path, {"w.bin": "0" * 64}))
    with pytest.raises(AppError) as exc:
        store.verify(store.entries[0])
    assert exc.value.code is ErrorCode.MODEL_INTEGRITY_ERROR


def test_missing_model_is_reported_not_downloaded(tmp_path: Path) -> None:
    store = ModelStore(tmp_path, _lock_with(tmp_path, {"w.bin": "0" * 64}))
    assert not store.is_present(store.entries[0])
    with pytest.raises(AppError) as exc:
        store.verify(store.entries[0])
    assert exc.value.code is ErrorCode.MODEL_NOT_AVAILABLE


def test_unpinned_model_is_rejected(tmp_path: Path) -> None:
    (tmp_path / "test" / "model").mkdir(parents=True)
    (tmp_path / "test" / "model" / "w.bin").write_bytes(b"x")
    store = ModelStore(tmp_path, _lock_with(tmp_path, {"w.bin": None}))
    with pytest.raises(AppError) as exc:
        store.verify(store.entries[0])
    assert exc.value.code is ErrorCode.MODEL_INTEGRITY_ERROR


def test_missing_lock_file(tmp_path: Path) -> None:
    with pytest.raises(AppError) as exc:
        ModelStore(tmp_path, tmp_path / "nope.json")
    assert exc.value.code is ErrorCode.MODEL_NOT_AVAILABLE


# ---------------------------------------------------------------- PaddleX lockdown
# Run in subprocesses: the lockdown mutates PaddleX module state for the whole process.

_LOCKDOWN_PROBE = """
import importlib.metadata, sys
from pathlib import Path
from newgen_ocr.ocr.paddle_runtime import OfflineModelResolutionError, load_paddleocr
load_paddleocr(Path(sys.argv[1]))
from paddlex.inference.utils.official_models import official_models
try:
    official_models["PP-OCRv5_mobile_det"]
    print("RESOLVED")
except OfflineModelResolutionError:
    print("REFUSED")
try:
    importlib.metadata.version("opencv-contrib-python")
    print("ALIAS_LEAKED")
except importlib.metadata.PackageNotFoundError:
    print("ALIAS_RESTORED")
"""


@pytest.mark.models
def test_paddlex_cannot_resolve_or_download_models(tmp_path: Path) -> None:
    result = subprocess.run(  # noqa: S603
        [sys.executable, "-c", _LOCKDOWN_PROBE, str(tmp_path / "cache")],
        capture_output=True,
        text=True,
        cwd=ROOT,
        timeout=300,
        check=False,
    )
    assert "REFUSED" in result.stdout, result.stderr[-2000:]
    assert "ALIAS_RESTORED" in result.stdout
    assert not list((tmp_path / "cache").rglob("*.pdiparams"))


def test_alias_is_not_active_in_this_process() -> None:
    with pytest.raises(importlib.metadata.PackageNotFoundError):
        importlib.metadata.version("opencv-contrib-python")


_OFFLINE_OCR = """
import sys
from pathlib import Path
from tests.fixtures.pages import text_page
from newgen_ocr.ocr.model_store import ModelStore
from newgen_ocr.ocr.paddle_engine import PaddleEngine
import socket
attempts = []
def hook(event, args):
    if event in ("socket.connect", "socket.getaddrinfo", "socket.gethostbyname"):
        attempts.append(event)
sys.addaudithook(hook)
store = ModelStore(Path(sys.argv[1]), Path(sys.argv[2]))
engine = PaddleEngine(store, Path(sys.argv[3]), "PP-OCRv5_mobile_det", None)
lines = engine.recognize(text_page(dpi=150), ["en"])
print("LINES", len(lines))
print("FIRST", lines[0].text if lines else "")
print("NETWORK_ATTEMPTS", len(attempts))
"""


@pytest.mark.models
def test_paddle_ocr_runs_with_no_network_namespace(tmp_path: Path) -> None:
    """Runs OCR in a fresh network namespace (no interfaces but loopback): proves no egress."""
    unshare = shutil.which("unshare")
    if unshare is None:
        pytest.skip("unshare not available")
    probe = subprocess.run([unshare, "-rn", "true"], capture_output=True, check=False)  # noqa: S603
    if probe.returncode != 0:
        pytest.skip("unprivileged network namespaces are not permitted here")
    result = subprocess.run(  # noqa: S603
        [
            unshare,
            "-rn",
            sys.executable,
            "-c",
            _OFFLINE_OCR,
            str(MODELS_DIR),
            str(MODELS_LOCK),
            str(tmp_path / "cache"),
        ],
        capture_output=True,
        text=True,
        cwd=ROOT,
        timeout=600,
        check=False,
    )
    out = result.stdout
    assert "LINES" in out, result.stderr[-3000:]
    assert int(out.split("LINES")[1].split()[0]) > 20
    assert "NETWORK_ATTEMPTS 0" in out
    assert not list((tmp_path / "cache").rglob("*.pdiparams"))
