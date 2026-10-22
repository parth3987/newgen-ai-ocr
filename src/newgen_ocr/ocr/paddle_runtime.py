"""Offline lockdown for the PaddleOCR / PaddleX stack (ADR-0020).

PaddleX resolves "official" models through a model manager that can download from Hugging
Face, AIStudio, ModelScope or Baidu BOS. This module is the only place that imports PaddleX,
and it:

1. sets offline environment flags before the import (no connectivity checks, hub offline);
2. disables the model manager outright, so any attempt to resolve a model by name raises
   instead of downloading (we always pass explicit, hash-verified model directories);
3. declares that our ``opencv-python-headless`` satisfies PaddleX's dependency checks for
   ``opencv-contrib-python`` (removed via uv override; PaddleX's OCR code uses no contrib-only
   OpenCV modules, verified by source search for the pinned version).

Steps 2-3 patch PaddleX internals, so they are tied to the exact pinned version and fail
loudly on any other version.

All Paddle model creation and inference must run through ``run_on_paddle_thread``: with
oneDNN enabled, predictors used from more than one OS thread fail intermittently with
``RuntimeError: std::exception`` and can return degraded results (measured: OCR from a new
thread per call failed on 2 of 6 pages; never on one thread). FastAPI runs sync endpoints on
a thread pool, so every Paddle call is confined to one dedicated thread per process
(ADR-0020 addendum).
"""

from __future__ import annotations

import importlib.metadata
import os
import threading
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from types import ModuleType
from typing import Any, NoReturn, TypeVar

PINNED_PADDLEX = "3.7.2"
PINNED_PADDLEOCR = "3.7.0"

_OFFLINE_ENV = {
    "PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK": "True",
    "HF_HUB_OFFLINE": "1",
    "HF_HUB_DISABLE_TELEMETRY": "1",
    "HF_DATASETS_OFFLINE": "1",
    "TRANSFORMERS_OFFLINE": "1",
    "MODELSCOPE_OFFLINE": "1",
    "DISABLE_TELEMETRY": "1",
    "OTEL_SDK_DISABLED": "true",
}

_lock = threading.Lock()
_module: ModuleType | None = None
_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="paddle")

T = TypeVar("T")


def run_on_paddle_thread(fn: Callable[..., T], *args: Any) -> T:
    """Run ``fn(*args)`` on the process's single Paddle thread and return its result."""
    if threading.current_thread().name.startswith("paddle"):
        return fn(*args)  # already on the Paddle thread (nested call)
    return _executor.submit(fn, *args).result()


class OfflineModelResolutionError(RuntimeError):
    """Raised if anything asks PaddleX to resolve or download a model by name."""


def _refuse(*args: Any, **kwargs: Any) -> NoReturn:
    raise OfflineModelResolutionError(
        "PaddleX attempted to resolve an official model by name. Model downloads are "
        "disabled; pass explicit local model directories (ADR-0020)."
    )


@contextmanager
def _opencv_distribution_alias() -> Iterator[None]:
    """While PaddleX imports, report opencv-python-headless for opencv-contrib-python.

    41 PaddleX modules decide at import time whether to ``import cv2`` by asking for the
    opencv-contrib-python distribution, so the alias must exist during the import itself.
    The original lookup is restored afterwards.
    """
    original = importlib.metadata.version

    def version(distribution_name: str) -> str:
        alias = distribution_name == "opencv-contrib-python"
        return original("opencv-python-headless" if alias else distribution_name)

    importlib.metadata.version = version
    try:
        yield
    finally:
        importlib.metadata.version = original


def load_paddleocr(cache_dir: Path) -> ModuleType:
    """Import and lock down PaddleOCR once per process. Returns the ``paddleocr`` module."""
    global _module
    with _lock:
        if _module is not None:
            return _module
        os.environ.update(_OFFLINE_ENV)
        # PaddleX writes locks/temp files under its cache home; keep them inside our data dir.
        os.environ["PADDLE_PDX_CACHE_HOME"] = str(cache_dir)
        cache_dir.mkdir(parents=True, exist_ok=True)

        with _opencv_distribution_alias():
            import paddleocr
            import paddlex
            from paddlex.inference.utils import official_models
            from paddlex.utils import deps

        if paddlex.__version__ != PINNED_PADDLEX or paddleocr.__version__ != PINNED_PADDLEOCR:
            raise RuntimeError(
                f"PaddleX {paddlex.__version__} / PaddleOCR {paddleocr.__version__} installed; "
                f"the offline lockdown is verified only for {PINNED_PADDLEX} / "
                f"{PINNED_PADDLEOCR}. Re-verify paddle_runtime.py before upgrading."
            )

        manager = official_models._ModelManager
        manager.get_model_path = _refuse
        manager._download_from_hoster = _refuse
        manager._build_hosters = lambda self: []

        # PaddleX checks dependencies by distribution name (deps.require_deps looks up
        # deps.is_dep_available at call time, so wrapping it covers class/function checks
        # and extras). Our cv2 is distributed as opencv-python-headless.
        original_is_dep_available = deps.is_dep_available

        def is_dep_available(dep: str, /, check_version: bool = False) -> bool:
            if dep == "opencv-contrib-python":
                dep = "opencv-python-headless"
                check_version = False
            return bool(original_is_dep_available(dep, check_version=check_version))

        deps.is_dep_available = is_dep_available
        deps.is_extra_available.cache_clear()

        module: ModuleType = paddleocr
        _module = module
        return module
