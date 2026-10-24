"""Guardrails for the no-egress / no-external-AI policy (docs/SECURITY.md).

These are defence in depth only; the binding control is host-level egress blocking.
"""

from __future__ import annotations

import ast
from importlib import metadata
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from newgen_ocr.api.app import create_app
from newgen_ocr.config import Settings

SRC = Path(__file__).resolve().parents[2] / "src" / "newgen_ocr"

# Distributions that must never be installed: hosted AI SDKs, telemetry exporters/SDKs.
FORBIDDEN_DISTRIBUTION_PREFIXES = (
    "openai",
    "anthropic",
    "google-generativeai",
    "google-genai",
    "google-cloud-documentai",
    "azure-ai-",
    "boto3",
    "cohere",
    "mistralai",
    "sentry-sdk",
    "opentelemetry-sdk",
    "opentelemetry-exporter",
    "opentelemetry-instrumentation",
)

# Network clients the application source must not import directly. Outbound calls,
# if ever approved, go through a single reviewed module.
FORBIDDEN_IMPORTS = {
    "requests",
    "httpx",
    "httpx2",
    "urllib3",
    "aiohttp",
    "urllib.request",
    "http.client",
}

# Reviewed exceptions: modules that may import a client because they only ever call the
# local host. Each is checked below to name a loopback address only.
LOOPBACK_ONLY = {"healthcheck.py": "urllib.request"}


def test_no_forbidden_distributions_installed() -> None:
    installed = {(d.metadata["Name"] or "").lower() for d in metadata.distributions()}
    offenders = sorted(
        name for name in installed if name.startswith(FORBIDDEN_DISTRIBUTION_PREFIXES)
    )
    assert offenders == []


def test_application_source_imports_no_network_clients() -> None:
    offenders = []
    for path in SRC.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            else:
                continue
            for name in names:
                if LOOPBACK_ONLY.get(str(path.relative_to(SRC))) == name:
                    continue
                if name in FORBIDDEN_IMPORTS or name.split(".")[0] in FORBIDDEN_IMPORTS:
                    offenders.append(f"{path.relative_to(SRC)}: {name}")
    assert offenders == []


def test_loopback_exceptions_call_only_the_local_host() -> None:
    import re

    for module in LOOPBACK_ONLY:
        source = (SRC / module).read_text(encoding="utf-8")
        urls = re.findall(r"https?://[^\s\"']+", source)
        assert urls and all(u.startswith("http://127.0.0.1:") for u in urls), (module, urls)


def test_cdn_backed_docs_ui_is_disabled(client: TestClient) -> None:
    assert client.get("/docs").status_code == 404
    assert client.get("/redoc").status_code == 404
    assert client.get("/api/v1/openapi.json").status_code == 200


def test_fastapi_telemetry_disabled_even_if_environment_requests_it(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    monkeypatch.setenv("FASTAPI_OTEL_AUTO_CONFIGURE", "true")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://collector.invalid:4318")
    app = create_app(settings)
    config = app._telemetry
    assert config["auto_configure"] is False
    assert (config["tracing"], config["metrics"], config["logs"]) == (False, False, False)
    assert app._native_telemetry.enabled() is False
