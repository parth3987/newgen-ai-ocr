"""Page orientation and script identification (ADR-0023).

Two measured signals are combined:

1. **Tesseract OSD** (pinned ``osd.traineddata``) for *orientation*, which was reliable on the
   corpus, and for *naming* a confidently detected unsupported script. OSD's script answer is
   not trusted for supported scripts: on Devanagari it varied with text size (Marathi at
   150 DPI read as "Cyrillic"; confidence 0.4 to 47 across DPI).
2. A **recognition census**: one Tesseract pass with every supported language loaded,
   on the upright page downscaled to about 150 DPI text size, followed by a Unicode script
   count of the output. This was correct on all 36 corpus pages at every DPI tested
   (dominant share 0.93 to 1.00). Unsupported scripts produce scattered, low-confidence output
   (top share at most 0.47, mean confidence about 0.45) and are never guessed.
"""

from __future__ import annotations

import io
import shutil
import subprocess  # nosec B404
import time

import cv2
import numpy as np
import numpy.typing as npt
from PIL import Image as PILImage

from newgen_ocr.errors import AppError
from newgen_ocr.ocr.language_id import script_shares
from newgen_ocr.ocr.model_store import ModelStore
from newgen_ocr.ocr.models import DetectionStatus, ScriptDetection
from newgen_ocr.ocr.tesseract_engine import TesseractEngine

Image = npt.NDArray[np.uint8]

# OSD script confidence above which an *unsupported* script is reported by name.
MIN_OSD_SCRIPT_CONFIDENCE = 1.0
# Census thresholds (calibrated on the corpus; see module docstring).
MIN_CENSUS_SHARE = 0.6
MIN_CENSUS_CONFIDENCE = 0.70
SECONDARY_SCRIPT_SHARE = 0.10
MIN_MIXED_COVERAGE = 0.85
SUPPORTED_SCRIPTS = frozenset({"Latin", "Devanagari", "Telugu", "Tamil", "Kannada"})
# Census reads every supported script at once (Marathi shares the Devanagari script).
CENSUS_LANGUAGES = ("en", "hi", "te", "ta", "kn")
# Target 75th-percentile glyph-component height for the census pass (~150 DPI, 11 pt).
CENSUS_TEXT_HEIGHT_PX = 20.0
_ROTATIONS = {90: cv2.ROTATE_90_CLOCKWISE, 180: cv2.ROTATE_180, 270: cv2.ROTATE_90_COUNTERCLOCKWISE}


def parse_osd(output: str) -> dict[str, str]:
    return {
        key.strip(): value.strip()
        for key, value in (line.split(":", 1) for line in output.splitlines() if ":" in line)
    }


def rotate_clockwise(image: Image, degrees: int) -> Image:
    if degrees not in _ROTATIONS:
        return image
    return np.ascontiguousarray(cv2.rotate(image, _ROTATIONS[degrees]))


def text_height(gray: Image) -> float:
    """75th-percentile height of glyph-sized connected components, in pixels."""
    _, binary_inv = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)
    _, _, stats, _ = cv2.connectedComponentsWithStats(binary_inv, connectivity=8)
    heights = stats[1:, cv2.CC_STAT_HEIGHT]
    areas = stats[1:, cv2.CC_STAT_AREA]
    heights = heights[(areas > 20) & (heights > 3) & (heights < gray.shape[0] / 4)]
    return float(np.percentile(heights, 75)) if heights.size else 0.0


def _gray(image: Image) -> Image:
    if image.ndim == 2:
        return image
    return np.asarray(cv2.cvtColor(image, cv2.COLOR_RGB2GRAY), dtype=np.uint8)


class ScriptDetector:
    def __init__(self, store: ModelStore, command: str, timeout_s: int) -> None:
        self.store = store
        self.command = command
        self.timeout_s = timeout_s
        self._census = TesseractEngine(store, command, timeout_s, threads=None)

    def available(self) -> bool:
        entries = self.store.find("tesseract", "script_detection")
        census_ready = set(CENSUS_LANGUAGES) <= self._census.languages()
        return (
            bool(entries)
            and self.store.is_present(entries[0])
            and census_ready
            and shutil.which(self.command) is not None
        )

    # ------------------------------------------------------------ OSD

    def _osd(self, image: Image) -> dict[str, str] | None:
        """OSD fields; {} when OSD could not decide; None when OSD is unavailable."""
        binary = shutil.which(self.command)
        entries = self.store.find("tesseract", "script_detection")
        if binary is None or not entries:
            return None
        try:
            tessdata = self.store.verify(entries[0])
        except AppError:
            return None
        buffer = io.BytesIO()
        PILImage.fromarray(image).save(buffer, format="PNG", compress_level=1)
        argv = [binary, "stdin", "stdout", "--tessdata-dir", str(tessdata), "--psm", "0"]
        try:
            # Fixed argv, no shell.
            result = subprocess.run(  # noqa: S603  # nosec B603
                [*argv, "-l", "osd"],
                input=buffer.getvalue(),
                capture_output=True,
                timeout=self.timeout_s,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return {}
        fields = parse_osd(result.stdout.decode("utf-8", errors="replace"))
        return fields if result.returncode == 0 and "Script" in fields else {}

    # ------------------------------------------------------------ census

    def _census_pass(self, image: Image) -> tuple[dict[str, float], float | None]:
        gray = _gray(image)
        height = text_height(gray)
        if height > CENSUS_TEXT_HEIGHT_PX * 1.3:
            scale = CENSUS_TEXT_HEIGHT_PX / height
            gray = np.asarray(
                cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA),
                dtype=np.uint8,
            )
        try:
            lines = self._census.recognize(gray, CENSUS_LANGUAGES)
        except AppError:
            return {}, None
        confidences = [line.confidence for line in lines if line.confidence is not None]
        mean = round(sum(confidences) / len(confidences), 4) if confidences else None
        return script_shares("\n".join(line.text for line in lines)), mean

    # ------------------------------------------------------------ public

    def detect(self, image: Image, identify_script: bool = True) -> ScriptDetection:
        """Orientation via OSD; with ``identify_script``, the script via the census."""
        start = time.perf_counter()
        fields = self._osd(image)
        if fields is None:
            return ScriptDetection(status=DetectionStatus.UNAVAILABLE)
        detection = ScriptDetection(status=DetectionStatus.UNDETERMINED, method="tesseract-osd")
        if fields:
            detection.osd_script = fields["Script"]
            detection.osd_script_confidence = float(fields.get("Script confidence", "0") or 0)
            detection.orientation_deg = int(fields.get("Rotate", "0") or 0)
            detection.orientation_confidence = float(fields.get("Orientation confidence", "0") or 0)
        if not identify_script:
            if detection.orientation_reliable:
                detection.status = DetectionStatus.DETECTED
            detection.duration_ms = round((time.perf_counter() - start) * 1000, 2)
            return detection

        upright = image
        if detection.orientation_reliable:
            upright = rotate_clockwise(image, int(detection.orientation_deg or 0))
        census, mean = self._census_pass(upright)
        detection.method = "tesseract-osd+census"
        detection.census = census
        detection.census_mean_confidence = mean
        detection.status = _decide(detection, census, mean)
        detection.duration_ms = round((time.perf_counter() - start) * 1000, 2)
        return detection


def _decide(
    detection: ScriptDetection, census: dict[str, float], mean: float | None
) -> DetectionStatus:
    supported = {s: share for s, share in census.items() if s in SUPPORTED_SCRIPTS}
    ranked = sorted(supported, key=lambda s: supported[s], reverse=True)
    top = ranked[0] if ranked else None
    # A confident page dominated by one script, or a confident Indic + Latin mix (e.g. a
    # Hindi page with English lines measured Devanagari 0.595 / Latin 0.405 at conf 0.95).
    # Unsupported scripts fail on confidence (their census output scored about 0.45).
    dominant = top is not None and supported[top] >= MIN_CENSUS_SHARE
    mixed = (
        len(ranked) >= 2
        and "Latin" in ranked[:2]
        and supported[ranked[0]] + supported[ranked[1]] >= MIN_MIXED_COVERAGE
    )
    confident = mean is not None and mean >= MIN_CENSUS_CONFIDENCE
    if top is not None and (dominant or mixed) and confident:
        detection.script = top
        detection.script_confidence = supported[top]
        detection.secondary_scripts = [
            s for s, share in supported.items() if s != top and share >= SECONDARY_SCRIPT_SHARE
        ]
        return DetectionStatus.DETECTED
    osd = detection.osd_script
    if (
        osd is not None
        and osd not in SUPPORTED_SCRIPTS
        and (detection.osd_script_confidence or 0.0) >= MIN_OSD_SCRIPT_CONFIDENCE
    ):
        detection.script = osd  # a named, unsupported script
        detection.script_confidence = detection.osd_script_confidence
        return DetectionStatus.DETECTED
    if not census and osd is None:
        return DetectionStatus.UNDETERMINED
    return DetectionStatus.UNCERTAIN
