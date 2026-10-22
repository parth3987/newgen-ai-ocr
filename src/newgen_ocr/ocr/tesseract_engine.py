"""Tesseract adapter (baseline / fallback engine).

Runs the ``tesseract`` CLI as a subprocess with an explicit ``--tessdata-dir`` pointing at
hash-verified ``tessdata_best`` models, never the OS language packs. The image is passed on
stdin and TSV is read from stdout, so no temporary files are written.
"""

from __future__ import annotations

import csv
import io
import os
import re
import shutil
import subprocess  # nosec B404
from collections.abc import Sequence
from functools import lru_cache

from PIL import Image as PILImage

from newgen_ocr.errors import AppError, ErrorCode
from newgen_ocr.ingestion.models import BoundingBox
from newgen_ocr.ocr.engine import Image, reading_order
from newgen_ocr.ocr.model_store import ModelEntry, ModelStore
from newgen_ocr.ocr.models import EngineInfo, OcrLine, OcrWord

# ISO 639-1 -> Tesseract language codes.
TESSERACT_CODES = {"en": "eng", "hi": "hin", "mr": "mar", "te": "tel", "ta": "tam", "kn": "kan"}
_VERSION = re.compile(r"tesseract\s+v?(\d+\.\d+\.\d+)")
TSV_HEADER = "level\tpage_num\tblock_num"
# Tesseract's Tamil model emits a ZERO WIDTH (NON-)JOINER after word-final virama. It is
# invisible and meaningless at a word boundary but breaks exact matching, so joiners directly
# before whitespace, punctuation or the end are removed. Mid-word joiners are kept: they
# control conjunct formation (ADR-0022).
_TRAILING_JOINER = re.compile(r"[\u200c\u200d]+(?=$|\s|[.,;:!?()\[\]\"'\u0964\u0965-])")


def clean_text(text: str) -> str:
    return _TRAILING_JOINER.sub("", text)


@lru_cache(maxsize=4)
def _binary_version(binary: str) -> str | None:
    try:
        result = subprocess.run(  # noqa: S603  # nosec B603
            [binary, "--version"], capture_output=True, text=True, timeout=10, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    match = _VERSION.search(result.stdout + result.stderr)
    return match.group(1) if match else None


def parse_tsv(tsv: str) -> list[OcrLine]:
    """Group Tesseract's word-level TSV rows (level 5) into lines."""
    lines: dict[tuple[int, int, int, int], list[OcrWord]] = {}
    for row in csv.DictReader(io.StringIO(tsv), delimiter="\t", quoting=csv.QUOTE_NONE):
        if row.get("level") != "5":
            continue
        text = clean_text((row.get("text") or "").strip())
        if not text:
            continue
        left, top = int(row["left"]), int(row["top"])
        width, height = int(row["width"]), int(row["height"])
        conf = float(row["conf"])
        key = (
            int(row["page_num"]),
            int(row["block_num"]),
            int(row["par_num"]),
            int(row["line_num"]),
        )
        lines.setdefault(key, []).append(
            OcrWord(
                text=text,
                confidence=round(conf / 100, 4) if conf >= 0 else None,
                bbox=BoundingBox(x0=left, y0=top, x1=left + width, y1=top + height),
            )
        )
    result = []
    for words in lines.values():
        confidences = [w.confidence for w in words if w.confidence is not None]
        result.append(
            OcrLine(
                text=" ".join(w.text for w in words),
                confidence=round(sum(confidences) / len(confidences), 4) if confidences else None,
                bbox=BoundingBox(
                    x0=min(w.bbox.x0 for w in words),
                    y0=min(w.bbox.y0 for w in words),
                    x1=max(w.bbox.x1 for w in words),
                    y1=max(w.bbox.y1 for w in words),
                ),
                words=words,
            )
        )
    return reading_order(result)


class TesseractEngine:
    name = "tesseract"

    def __init__(
        self, store: ModelStore, command: str, timeout_s: int, threads: int | None
    ) -> None:
        self.store = store
        self.command = command
        self.timeout_s = timeout_s
        self.threads = threads

    def _binary(self) -> str | None:
        return shutil.which(self.command)

    def _models(self, languages: Sequence[str]) -> list[ModelEntry]:
        if not languages:
            raise AppError(ErrorCode.UNSUPPORTED_LANGUAGE, "At least one language is required")
        models = []
        for language in languages:
            entry = self.store.for_language("tesseract", language)
            if entry is None or language not in TESSERACT_CODES:
                raise AppError(
                    ErrorCode.UNSUPPORTED_LANGUAGE, f"No Tesseract model for language {language}"
                )
            models.append(entry)
        return models

    def unavailable_reason(self) -> str | None:
        binary = self._binary()
        if binary is None:
            return f"tesseract binary not found ({self.command!r})"
        if _binary_version(binary) is None:
            return "tesseract binary did not report a version"
        if not self.languages():
            return "no tessdata models are present"
        return None

    def languages(self) -> frozenset[str]:
        present: set[str] = set()
        for entry in self.store.find("tesseract", "recognition"):
            if self.store.is_present(entry):
                present.update(entry.languages)
        return frozenset(present)

    def info(self, languages: Sequence[str]) -> EngineInfo:
        binary = self._binary()
        version = _binary_version(binary) if binary else None
        return EngineInfo(
            name=self.name,
            version=f"tesseract {version or 'unknown'}",
            models=[m.ref() for m in self._models(languages)],
            parameters={
                "languages": "+".join(TESSERACT_CODES[lang] for lang in languages),
                "oem": 1,
                "psm": 3,
                "threads": self.threads or 0,
            },
        )

    def recognize(self, image: Image, languages: Sequence[str]) -> list[OcrLine]:
        binary = self._binary()
        if binary is None:
            raise AppError(ErrorCode.OCR_ENGINE_UNAVAILABLE, "Tesseract is not installed")
        models = self._models(languages)
        tessdata = {self.store.verify(m) for m in models}
        if len(tessdata) != 1:
            raise AppError(ErrorCode.MODEL_NOT_AVAILABLE, "Tesseract models must share one dir")
        buffer = io.BytesIO()
        PILImage.fromarray(image).save(buffer, format="PNG", compress_level=1)
        env = dict(os.environ)
        if self.threads is not None:
            env["OMP_THREAD_LIMIT"] = str(self.threads)
        argv = [
            binary,
            "stdin",
            "stdout",
            "--tessdata-dir",
            str(tessdata.pop()),
            "-l",
            "+".join(TESSERACT_CODES[lang] for lang in languages),
            "--oem",
            "1",
            "--psm",
            "3",
            # Set the parameter directly: the `tsv` config *file* lives in the OS tessdata
            # dir, which we deliberately do not use (pinned models only).
            "-c",
            "tessedit_create_tsv=1",
        ]
        try:
            # Fixed argv, no shell.
            result = subprocess.run(  # noqa: S603  # nosec B603
                argv,
                input=buffer.getvalue(),
                capture_output=True,
                timeout=self.timeout_s,
                env=env,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise AppError(ErrorCode.OCR_TIMEOUT, "Tesseract timed out on this page") from exc
        output = result.stdout.decode("utf-8", errors="replace")
        # A missing TSV header means misconfiguration, not an empty page: never return [].
        if result.returncode != 0 or not output.startswith(TSV_HEADER):
            raise AppError(ErrorCode.OCR_FAILED, "Tesseract failed on this page")
        return parse_tsv(output)
