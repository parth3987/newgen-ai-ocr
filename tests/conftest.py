from __future__ import annotations

import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from newgen_ocr.api.app import create_app
from newgen_ocr.config import Settings


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    return Settings(environment="test", log_format="json", data_dir=data_dir, _env_file=None)  # type: ignore[call-arg]


@pytest.fixture
def app(settings: Settings) -> FastAPI:
    return create_app(settings)


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


ROOT = Path(__file__).resolve().parents[1]
MODELS_DIR = ROOT / "models"
MODELS_LOCK = ROOT / "models.lock.json"


def models_present() -> bool:
    return (MODELS_DIR / "paddle" / "PP-OCRv5_mobile_det" / "inference.pdiparams").is_file()


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    skip_models = pytest.mark.skip(reason="OCR models not fetched (scripts/fetch_models.py)")
    skip_tesseract = pytest.mark.skip(reason="tesseract binary not installed")
    has_models, has_tesseract = models_present(), shutil.which("tesseract") is not None
    for item in items:
        if "models" in item.keywords and not has_models:
            item.add_marker(skip_models)
        if "tesseract" in item.keywords and not has_tesseract:
            item.add_marker(skip_tesseract)
