from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from newgen_ocr.config import Settings
from newgen_ocr.imaging.runtime import check_opencv_build, opencv_has_ffmpeg, runtime_info
from newgen_ocr.preprocessing.profiles import BUILTIN_PROFILES, ProfileConfig, merge_profiles


def test_builtin_profiles() -> None:
    assert set(BUILTIN_PROFILES) == {"none", "standard", "degraded_scan", "photo", "binarize"}
    # Only explicit opt-in profiles apply filters unconditionally.
    standard = BUILTIN_PROFILES["standard"]
    assert standard.denoise.value == "auto"
    assert standard.contrast.value == "auto"
    assert standard.adaptive_threshold is False


def test_custom_profile_from_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    custom = [{"name": "fax", "denoise": "always", "adaptive_threshold": True}]
    monkeypatch.setenv("NGOCR_CUSTOM_PREPROCESSING_PROFILES", json.dumps(custom))
    monkeypatch.setenv("NGOCR_DEFAULT_PREPROCESSING_PROFILE", "fax")
    settings = Settings(data_dir=tmp_path, _env_file=None)  # type: ignore[call-arg]
    assert settings.preprocessing_profiles["fax"].adaptive_threshold is True
    assert "standard" in settings.preprocessing_profiles


def test_custom_profile_cannot_shadow_builtin() -> None:
    with pytest.raises(ValueError, match="already defined"):
        merge_profiles((ProfileConfig(name="standard"),))


def test_unknown_default_profile_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValidationError):
        Settings(data_dir=tmp_path, default_preprocessing_profile="nope", _env_file=None)  # type: ignore[call-arg]


@pytest.mark.parametrize("name", ["Standard", "../x", "a" * 40, "", "1abc"])
def test_profile_names_are_safe_identifiers(name: str) -> None:
    with pytest.raises(ValidationError):
        ProfileConfig(name=name)


def test_unknown_profile_fields_rejected() -> None:
    with pytest.raises(ValidationError):
        ProfileConfig.model_validate({"name": "x", "sharpen": True})


FFMPEG_BUILD = "  Video I/O:\n    FFMPEG:                      YES\n"
MINIMAL_BUILD = "  Video I/O:\n    FFMPEG:                      NO\n"


def test_ffmpeg_detection() -> None:
    assert opencv_has_ffmpeg(FFMPEG_BUILD)
    assert not opencv_has_ffmpeg(MINIMAL_BUILD)


def test_production_refuses_ffmpeg_build() -> None:
    with pytest.raises(RuntimeError, match="ADR-0015"):
        check_opencv_build("production", FFMPEG_BUILD)
    check_opencv_build("production", MINIMAL_BUILD)
    check_opencv_build("development", FFMPEG_BUILD)


def test_runtime_info() -> None:
    info = runtime_info()
    assert info.opencv_version.startswith("4.")
    assert info.logical_cpus >= 1
    assert info.opencv_threads >= 1
    assert info.cpu_model
