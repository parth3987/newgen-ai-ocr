"""Offline OCR smoke test, run inside the application image (ADR-0032).

``ngocrctl smoke`` pipes this file into ``python -`` in a one-off container started with
``--network none``: no network interface at all. It loads every OCR model the language
matrix enables and reads a generated GST invoice end to end (OCR, layout, extraction) in
local mode, with nothing written outside a temporary directory. Uses only the package's
public API, so the same script checks older releases (rollback).

Argument: the directory with ``smoke-invoice.png`` and ``smoke-invoice.json`` (the expected
field values), rendered at staging because production images carry no fonts. Prints one
JSON object; exits 1 if any check fails.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path


def main(argv: list[str]) -> int:
    from fastapi.testclient import TestClient

    from newgen_ocr.api.app import create_app
    from newgen_ocr.config import Settings
    from newgen_ocr.evaluation.client import authorized

    sample = Path(argv[0] if argv else "/smoke")
    invoice = (sample / "smoke-invoice.png").read_bytes()
    expected = json.loads((sample / "smoke-invoice.json").read_text())

    out: dict[str, object] = {"failures": []}
    failures: list[str] = out["failures"]  # type: ignore[assignment]
    with tempfile.TemporaryDirectory(prefix="ngocr-smoke-") as tmp:
        # Local mode (no database): the image's models, a throwaway data directory.
        settings = Settings(  # type: ignore[call-arg]
            environment="development",
            data_dir=Path(tmp),
            models_dir=Path(os.environ.get("NGOCR_MODELS_DIR", "models")),
            models_lock=Path(os.environ.get("NGOCR_MODELS_LOCK", "models.lock.json")),
            log_level="WARNING",
            _env_file=None,
        )
        with TestClient(create_app(settings)) as raw:
            client = authorized(raw)
            doc = client.post(
                "/api/v1/documents", files={"file": ("smoke.png", invoice, "image/png")}
            ).json()["document_id"]

            # Every model the matrix enables loads from the image and reads a page.
            loaded: dict[str, str] = {}
            for info in client.get("/api/v1/languages").json():
                for engine in info["printed_engines"]:
                    start = time.monotonic()
                    response = client.post(
                        f"/api/v1/documents/{doc}/ocr",
                        json={"engine": engine, "languages": [info["code"]]},
                    )
                    key = f"{engine}:{info['code']}"
                    if response.status_code == 201:
                        loaded[key] = f"ok ({time.monotonic() - start:.1f}s)"
                    else:
                        loaded[key] = f"failed ({response.status_code})"
                        failures.append(f"model {key}: {response.text[:200]}")
            out["models"] = loaded

            # The invoice end to end, in English with automatic engine choice.
            ocr = client.post(f"/api/v1/documents/{doc}/ocr", json={"languages": ["en"]})
            layout = client.post(f"/api/v1/documents/{doc}/layout", json={})
            result = client.post(f"/api/v1/documents/{doc}/extraction", json={})
            if not (ocr.status_code == layout.status_code == result.status_code == 201):
                failures.append(
                    f"pipeline: ocr {ocr.status_code}, layout {layout.status_code}, "
                    f"extraction {result.status_code}"
                )
            else:
                body = result.json()
                values = {f["name"]: f["value"] for f in body["fields"]}
                matches = {k: values.get(k) == v for k, v in expected.items()}
                out["extraction"] = {"schema_id": body["schema_id"], "matches": matches}
                if body["schema_id"] != "gst_invoice" or not matches or not all(matches.values()):
                    failures.append(f"extraction: {out['extraction']}")
    out["passed"] = not failures
    sys.stdout.write(json.dumps(out) + "\n")
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
