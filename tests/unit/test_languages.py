"""Invariants of the supported-language matrix (docs/LANGUAGE_SUPPORT.md)."""

from __future__ import annotations

import json

from newgen_ocr.config import Settings
from newgen_ocr.ocr.languages import LANGUAGES, SCRIPT_LANGUAGES, HandwritingStatus
from newgen_ocr.ocr.models import SUPPORTED_LANGUAGES
from newgen_ocr.ocr.service import HANDWRITING_ENGINE, enabled_for
from tests.conftest import MODELS_LOCK

ENGINE_OF_LOCK = {"paddle": "paddleocr", "tesseract": "tesseract"}


def _lock_models() -> list[dict[str, object]]:
    return list(json.loads(MODELS_LOCK.read_text())["models"])


def test_matrix_covers_exactly_the_api_languages() -> None:
    assert set(LANGUAGES) == set(SUPPORTED_LANGUAGES)


def test_every_enabled_printed_engine_has_a_pinned_model() -> None:
    models = _lock_models()
    for code, info in LANGUAGES.items():
        for engine in info.printed_engines:
            pinned = [
                m
                for m in models
                if m["engine"] == ENGINE_OF_LOCK[engine]
                and m["kind"] == "recognition"
                and code in m["languages"]  # type: ignore[operator]
            ]
            assert pinned, f"{engine} enabled for {code} without a pinned model"


def test_handwriting_is_never_claimed_supported() -> None:
    """No language is verified on real handwriting yet (ADR-0024)."""
    assert all(info.handwriting is not HandwritingStatus.SUPPORTED for info in LANGUAGES.values())
    assert Settings(_env_file=None).ocr_handwriting_verified_languages == ()  # type: ignore[call-arg]


def test_handwriting_engine_only_where_a_model_exists() -> None:
    hw_languages = {
        lang
        for m in _lock_models()
        if m["kind"] == "handwriting_recognition"
        for lang in m["languages"]  # type: ignore[attr-defined]
    }
    for code, info in LANGUAGES.items():
        if info.handwriting is not HandwritingStatus.UNSUPPORTED:
            assert code in hw_languages
        assert enabled_for(HANDWRITING_ENGINE, code) == (
            info.handwriting is not HandwritingStatus.UNSUPPORTED
        )
    assert {
        c for c, i in LANGUAGES.items() if i.handwriting is not HandwritingStatus.UNSUPPORTED
    } == {"en"}


def test_kannada_is_tesseract_only() -> None:
    assert LANGUAGES["kn"].printed_engines == ["tesseract"]
    assert not enabled_for("paddle", "kn")


def test_script_routing_targets_enabled_languages() -> None:
    for script, languages in SCRIPT_LANGUAGES.items():
        assert LANGUAGES[languages[0]].script == script
        assert set(languages) <= set(LANGUAGES)
