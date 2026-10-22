"""OpenCV runtime configuration, environment facts for performance records, and the
production build guard (ADR-0015)."""

from __future__ import annotations

import os
import platform
import re
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np

from newgen_ocr.preprocessing.models import RuntimeInfo

_FFMPEG_LINE = re.compile(r"^\s*FFMPEG:\s*YES", re.MULTILINE)


def opencv_has_ffmpeg(build_information: str | None = None) -> bool:
    info = build_information if build_information is not None else cv2.getBuildInformation()
    return bool(_FFMPEG_LINE.search(info))


def check_opencv_build(environment: str, build_information: str | None = None) -> None:
    """Refuse to run in production with the PyPI OpenCV build (bundles EOL OpenSSL/FFmpeg)."""
    if environment == "production" and opencv_has_ffmpeg(build_information):
        raise RuntimeError(
            "OpenCV build includes FFmpeg (PyPI wheel). Production requires the minimal "
            "source-built OpenCV; see docs/DECISIONS.md ADR-0015."
        )


def configure_opencv(threads: int | None) -> None:
    if threads is not None:
        cv2.setNumThreads(threads)


@lru_cache(maxsize=1)
def _cpu_model() -> str:
    try:
        for line in Path("/proc/cpuinfo").read_text().splitlines():
            if line.startswith("model name"):
                return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return platform.processor() or "unknown"


def runtime_info() -> RuntimeInfo:
    return RuntimeInfo(
        opencv_version=cv2.__version__,
        numpy_version=np.__version__,
        cpu_model=_cpu_model(),
        logical_cpus=os.cpu_count() or 0,
        opencv_threads=cv2.getNumThreads(),
    )
