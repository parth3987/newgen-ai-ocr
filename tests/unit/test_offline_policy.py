"""Guardrails for the no-egress / no-external-AI policy (docs/SECURITY.md).

These are defence in depth only; the binding control is host-level egress blocking.
"""

from __future__ import annotations

import ast
from importlib import metadata
from pathlib import Path

from fastapi.testclient import TestClient

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
                if name in FORBIDDEN_IMPORTS or name.split(".")[0] in FORBIDDEN_IMPORTS:
                    offenders.append(f"{path.relative_to(SRC)}: {name}")
    assert offenders == []


def test_cdn_backed_docs_ui_is_disabled(client: TestClient) -> None:
    assert client.get("/docs").status_code == 404
    assert client.get("/redoc").status_code == 404
    assert client.get("/api/v1/openapi.json").status_code == 200
