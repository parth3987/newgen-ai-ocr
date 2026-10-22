"""Script/orientation detection: OSD parsing (fast) and live Tesseract OSD (markers)."""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import pytest

from newgen_ocr.evaluation.corpus import DEFAULT_FONT_DIR, LANGUAGE_FONTS, load_lines, render_page
from newgen_ocr.ocr.model_store import ModelStore
from newgen_ocr.ocr.models import DetectionStatus, ScriptDetection
from newgen_ocr.ocr.script_detect import ScriptDetector, parse_osd
from tests.conftest import MODELS_DIR, MODELS_LOCK, ROOT

TEXT = ROOT / "evaluation" / "corpus" / "text"
OSD_OUTPUT = """Page number: 0
Orientation in degrees: 180
Rotate: 180
Orientation confidence: 30.58
Script: Telugu
Script confidence: 32.82
"""
SCRIPTS = {
    "en": "Latin",
    "hi": "Devanagari",
    "mr": "Devanagari",
    "te": "Telugu",
    "ta": "Tamil",
    "kn": "Kannada",
}
SUPPORTED = {"Latin", "Devanagari", "Telugu", "Tamil", "Kannada"}
ROTATED = {90: cv2.ROTATE_90_COUNTERCLOCKWISE, 180: cv2.ROTATE_180, 270: cv2.ROTATE_90_CLOCKWISE}


def test_parse_osd() -> None:
    fields = parse_osd(OSD_OUTPUT)
    assert fields["Script"] == "Telugu"
    assert fields["Rotate"] == "180"
    assert fields["Orientation confidence"] == "30.58"


def test_orientation_reliability_threshold() -> None:
    assert ScriptDetection(
        status=DetectionStatus.DETECTED, orientation_deg=180, orientation_confidence=5.0
    ).orientation_reliable
    assert not ScriptDetection(
        status=DetectionStatus.DETECTED, orientation_deg=180, orientation_confidence=0.79
    ).orientation_reliable


def test_unavailable_without_binary(tmp_path: Path) -> None:
    detector = ScriptDetector(ModelStore(MODELS_DIR, MODELS_LOCK), "no-such-tesseract", 30)
    assert detector.available() is False
    assert detector.detect(np.full((50, 50), 255, np.uint8)).status is DetectionStatus.UNAVAILABLE


def _page(language: str) -> np.ndarray:
    lines = load_lines(TEXT, language)[:10]
    return render_page(lines, DEFAULT_FONT_DIR / LANGUAGE_FONTS[language][0], 150)


@pytest.fixture(scope="module")
def detector() -> ScriptDetector:
    return ScriptDetector(ModelStore(MODELS_DIR, MODELS_LOCK), "tesseract", 60)


@pytest.mark.models
@pytest.mark.tesseract
@pytest.mark.parametrize("language", list(SCRIPTS))
def test_detects_script_of_each_supported_language(detector: ScriptDetector, language: str) -> None:
    result = detector.detect(_page(language))
    assert result.status is DetectionStatus.DETECTED
    assert result.script == SCRIPTS[language]
    assert result.orientation_deg == 0


@pytest.mark.models
@pytest.mark.tesseract
@pytest.mark.parametrize("rotation", [90, 180, 270])
@pytest.mark.parametrize("language", ["te", "hi", "en"])
def test_reports_clockwise_correction(
    detector: ScriptDetector, language: str, rotation: int
) -> None:
    """A page turned counter-clockwise by N degrees needs an N-degree clockwise correction."""
    turned = np.ascontiguousarray(cv2.rotate(_page(language), ROTATED[rotation]))
    result = detector.detect(turned)
    assert result.orientation_deg == rotation
    assert result.orientation_reliable


UNSUPPORTED_SAMPLES = [
    ("NotoSansBengali-Regular.ttf", "এই চালানটি নিচে তালিকাভুক্ত পণ্য সরবরাহের নিশ্চয়তা দেয়।"),
    ("NotoSansArabic-Regular.ttf", "تؤكد هذه الفاتورة تسليم البضائع المدرجة أدناه."),
    ("NotoSansGujarati-Regular.ttf", "આ ઇન્વોઇસ નીચે સૂચિબદ્ધ માલની ડિલિવરીની પુષ્ટિ કરે છે."),
]


@pytest.mark.models
@pytest.mark.tesseract
@pytest.mark.parametrize(("font", "text"), UNSUPPORTED_SAMPLES)
def test_unsupported_scripts_are_never_routed_as_supported(
    detector: ScriptDetector, font: str, text: str
) -> None:
    """OSD may misname an unsupported script (Bengali was reported as "Arabic"); what matters
    is that the page is never routed to a supported recogniser (ADR-0023)."""
    if not (DEFAULT_FONT_DIR / font).is_file():
        pytest.skip(f"{font} not installed")
    result = detector.detect(render_page([text] * 10, DEFAULT_FONT_DIR / font, 150))
    routed_supported = result.status is DetectionStatus.DETECTED and result.script in SUPPORTED
    assert not routed_supported, result


@pytest.mark.models
@pytest.mark.tesseract
def test_blank_page_is_undetermined(detector: ScriptDetector) -> None:
    result = detector.detect(np.full((1200, 900), 255, np.uint8))
    assert result.status is DetectionStatus.UNDETERMINED


@pytest.mark.models
@pytest.mark.tesseract
@pytest.mark.parametrize("dpi", [150, 200, 300])
@pytest.mark.parametrize("language", ["hi", "mr"])
def test_devanagari_identified_at_every_dpi(
    detector: ScriptDetector, language: str, dpi: int
) -> None:
    """Regression for OSD's DPI sensitivity (Marathi at 150 DPI read as "Cyrillic")."""
    lines = load_lines(TEXT, language)[:6]
    page = render_page(lines, DEFAULT_FONT_DIR / LANGUAGE_FONTS[language][0], dpi)
    result = detector.detect(page)
    assert (result.status, result.script) == (DetectionStatus.DETECTED, "Devanagari")


@pytest.mark.models
@pytest.mark.tesseract
def test_secondary_script_on_mixed_page(detector: ScriptDetector) -> None:
    en = render_page(load_lines(TEXT, "en")[:6], DEFAULT_FONT_DIR / "NotoSans-Regular.ttf", 200)
    hi_font = DEFAULT_FONT_DIR / LANGUAGE_FONTS["hi"][0]
    hi = render_page(load_lines(TEXT, "hi")[:2], hi_font, 200)
    result = detector.detect(np.vstack([en[150:1300], hi[150:500]]))
    assert result.script == "Latin"
    assert result.secondary_scripts == ["Devanagari"]


def test_orientation_only_mode_skips_census(detector: ScriptDetector) -> None:
    if not detector.available():
        pytest.skip("tesseract/osd not available")
    result = detector.detect(_page("te"), identify_script=False)
    assert result.method == "tesseract-osd"
    assert result.census == {}
    assert result.script is None
