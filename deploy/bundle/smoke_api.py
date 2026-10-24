"""Service smoke test over the TLS ingress, run inside the application image (ADR-0032).

``ngocrctl smoke`` pipes this file into ``python -`` in a one-off container on the host
network, so it connects exactly as a client on the host would: to the published HTTPS
address, checking the certificate against the site CA for the configured host name. It
uploads a generated GST invoice with a short-lived submitter token, waits for the
background job and checks the extracted values. Standard library only, so the same script
checks older releases (rollback).

Arguments: ADDRESS PORT HOSTNAME CA_FILE TOKEN_FILE SAMPLE_DIR (``smoke-invoice.png`` and
the expected values in ``smoke-invoice.json``, rendered at staging). Prints one JSON
object; exits 1 if any check fails.
"""

from __future__ import annotations

import http.client
import json
import socket
import ssl
import sys
import time
import uuid
import zlib
from pathlib import Path
from typing import Any


class _Connection(http.client.HTTPSConnection):
    """HTTPS to a fixed address, verifying the certificate for ``host`` (SNI)."""

    def __init__(self, address: str, port: int, hostname: str, context: ssl.SSLContext) -> None:
        super().__init__(hostname, port, context=context, timeout=60)
        self._address = address

    def connect(self) -> None:
        raw = socket.create_connection((self._address, self.port), self.timeout)
        self.sock = self._context.wrap_socket(raw, server_hostname=self.host)  # type: ignore[attr-defined]


def _unique_png(png: bytes) -> bytes:
    """The same image with a random ``tEXt`` chunk before ``IEND``."""
    body = b"tEXt" + b"ngocr-smoke\x00" + uuid.uuid4().hex.encode()
    chunk = len(body[4:]).to_bytes(4, "big") + body + zlib.crc32(body).to_bytes(4, "big")
    end = png.rindex(b"IEND") - 4
    return png[:end] + chunk + png[end:]


def main(argv: list[str]) -> int:
    address, port, hostname, ca_file, token_file, sample_dir = argv
    sample = Path(sample_dir)
    # A fresh text chunk makes every run a new document (uploads are de-duplicated by
    # content), so each smoke test really goes through the queue and the workers.
    invoice = _unique_png((sample / "smoke-invoice.png").read_bytes())
    expected = json.loads((sample / "smoke-invoice.json").read_text())
    context = ssl.create_default_context(cafile=ca_file)
    token = open(token_file, encoding="ascii").read().strip()  # noqa: SIM115

    def call(
        method: str, path: str, body: bytes | None = None, content_type: str | None = None
    ) -> tuple[int, Any]:
        connection = _Connection(address, int(port), hostname, context)
        headers = {"Authorization": f"Bearer {token}"}
        if content_type:
            headers["Content-Type"] = content_type
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        data = response.read()
        connection.close()
        try:
            return response.status, json.loads(data)
        except ValueError:
            return response.status, None

    out: dict[str, Any] = {}
    failures: list[str] = []
    status, ready = call("GET", "/health/ready")
    out["ready"] = status
    if status != 200:
        failures.append(f"readiness: {status} {ready}")

    boundary = uuid.uuid4().hex
    body = (
        (
            f'--{boundary}\r\nContent-Disposition: form-data; name="languages"\r\n\r\nen\r\n'
            f'--{boundary}\r\nContent-Disposition: form-data; name="file"; '
            f'filename="ngocr-smoke.png"\r\nContent-Type: image/png\r\n\r\n'
        ).encode()
        + invoice
        + f"\r\n--{boundary}--\r\n".encode()
    )
    status, submitted = call(
        "POST", "/api/v1/documents", body, f"multipart/form-data; boundary={boundary}"
    )
    if status not in (200, 201, 202) or not submitted or "job" not in submitted:
        failures.append(f"upload: {status} {submitted}")
    else:
        job = submitted["job"]
        if job["status"] not in ("queued", "running"):
            failures.append("the upload returned an earlier result instead of a new job")
        start = time.monotonic()
        while job["status"] in ("queued", "running") and time.monotonic() - start < 600:
            time.sleep(2)
            job = call("GET", f"/api/v1/jobs/{job['id']}")[1]
        out["job"] = {k: job.get(k) for k in ("status", "completed_stages", "attempts")}
        out["job"]["seconds"] = round(time.monotonic() - start, 1)
        out["document_id"] = job.get("document_id")

        if job["status"] != "succeeded":
            failures.append(f"job: {out['job']}")
        else:
            doc = job["document_id"]
            status, result = call("GET", f"/api/v1/documents/{doc}/extraction/auto")
            values = {f["name"]: f["value"] for f in (result or {}).get("fields", [])}
            matches = {k: values.get(k) == v for k, v in expected.items()}
            out["extraction"] = {"schema_id": (result or {}).get("schema_id"), "matches": matches}
            if not matches or not all(matches.values()):
                failures.append(f"extraction: {out['extraction']}")
    out["failures"] = failures
    out["passed"] = not failures
    sys.stdout.write(json.dumps(out) + "\n")
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
