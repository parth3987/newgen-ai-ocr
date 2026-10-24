"""Offline network test of the production stack (ADR-0031): ``make offline-test``.

Starts the stack from deploy/compose.yaml as its own compose project with
``NGOCR_ENVIRONMENT=production``. Then:
1. From inside the API and worker containers, tries to reach the outside world (DNS
   lookups, TCP to public addresses, UDP DNS, HTTPS to model hubs and PyPI). Every attempt
   must fail. As a control, the database must be reachable.
2. Through the TLS ingress only, as a client would: checks TLS versions and headers, signs
   in, uploads a document, waits for the background job, then checks the extracted values
   and the Excel export.
3. Checks that production started with the minimal OpenCV build and in service mode.

Writes reports/offline-test.json and exits 1 if anything fails. ``--keep`` leaves the stack
running.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import shutil
import socket
import ssl
import subprocess  # nosec B404 - fixed commands, no shell
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
PROJECT = "ngocr-offline"
PORT = 18443
USER, PASSWORD = "offline-admin", "offline test password 1"  # throwaway test account

PROBE = r"""
import json, socket, ssl, urllib.request
out = {}
def attempt(name, fn):
    try:
        fn(); out[name] = "REACHED"
    except Exception as exc:
        out[name] = f"blocked ({type(exc).__name__})"
attempt("dns:pypi.org", lambda: socket.getaddrinfo("pypi.org", 443))
attempt("dns:huggingface.co", lambda: socket.getaddrinfo("huggingface.co", 443))
attempt("tcp:1.1.1.1:443", lambda: socket.create_connection(("1.1.1.1", 443), 3).close())
attempt("tcp:8.8.8.8:53", lambda: socket.create_connection(("8.8.8.8", 53), 3).close())
def udp():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.settimeout(3)
    query = b"\x12\x34\x01\x00\x00\x01" + b"\x00" * 6 + b"\x03www\x00\x00\x01\x00\x01"
    s.sendto(query, ("8.8.8.8", 53))
    s.recvfrom(512)
attempt("udp:8.8.8.8:53", udp)
attempt("https:huggingface.co", lambda: urllib.request.urlopen("https://huggingface.co", timeout=5))
BOS = "https://paddle-model-ecology.bj.bcebos.com"
attempt("https:paddle-model-ecology", lambda: urllib.request.urlopen(BOS, timeout=5))
attempt("control:postgres:5432", lambda: socket.create_connection(("postgres", 5432), 3).close())
print(json.dumps(out))
"""


def compose(*args: str, image: str, stdin: str | None = None, check: bool = True) -> str:
    env = {
        **os.environ,
        "NGOCR_IMAGE": image,
        "NGOCR_ENVIRONMENT": "production",
        "NGOCR_HTTPS_PORT": str(PORT),
        "NGOCR_LOG_LEVEL": "WARNING",
    }
    engine = "podman" if shutil.which("podman") else "docker"
    command = [
        engine,
        "compose",
        "-p",
        PROJECT,
        "-f",
        str(ROOT / "deploy" / "compose.yaml"),
        "--profile",
        "app",
        *args,
    ]
    result = subprocess.run(  # noqa: S603 - fixed arguments  # nosec B603
        command, env=env, input=stdin, capture_output=True, text=True, timeout=900
    )
    if check and result.returncode != 0:
        raise RuntimeError(
            f"{' '.join(args)} failed:\n{result.stdout[-2000:]}\n{result.stderr[-2000:]}"
        )
    return result.stdout


def tls_checks(ca: Path) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for name, version in (
        ("tls1.1", ssl.TLSVersion.TLSv1_1),
        ("tls1.2", ssl.TLSVersion.TLSv1_2),
        ("tls1.3", ssl.TLSVersion.TLSv1_3),
    ):
        context = ssl.create_default_context(cafile=str(ca))
        context.minimum_version = context.maximum_version = version
        if version == ssl.TLSVersion.TLSv1_1:
            context.set_ciphers("DEFAULT:@SECLEVEL=0")
        try:
            with (
                socket.create_connection(("127.0.0.1", PORT), 5) as raw,
                context.wrap_socket(raw, server_hostname="localhost") as tls,
            ):
                out[name] = f"accepted ({tls.version()})"
        except (ssl.SSLError, OSError) as exc:
            out[name] = f"refused ({type(exc).__name__})"
    try:
        with socket.create_connection(("127.0.0.1", PORT), 5) as raw:
            raw.sendall(b"GET /health/live HTTP/1.1\r\nHost: localhost\r\n\r\n")
            reply = raw.recv(200).decode(errors="replace")
        out["plain_http"] = "refused" if " 200 " not in reply.split("\r\n")[0] else "SERVED"
    except OSError:
        out["plain_http"] = "refused"
    return out


def run_job(ca: Path) -> dict[str, Any]:
    import httpx
    from PIL import Image

    from newgen_ocr.evaluation.extraction_corpus import gst_invoice

    out: dict[str, Any] = {}
    with httpx.Client(base_url=f"https://localhost:{PORT}", verify=str(ca), timeout=60) as client:
        shell = client.get("/app/")
        out["headers"] = {
            "hsts": "strict-transport-security" in shell.headers,
            "csp_default_none": "default-src 'none'"
            in shell.headers.get("content-security-policy", ""),
        }
        login = client.post("/api/v1/auth/login", json={"user": USER, "password": PASSWORD})
        login.raise_for_status()
        out["cookie_secure"] = "secure" in login.headers.get("set-cookie", "").lower()
        csrf = {"X-CSRF-Token": login.json()["csrf_token"]}
        image, truth = gst_invoice("intra", 200)
        buf = io.BytesIO()
        Image.fromarray(image).save(buf, "PNG")
        submitted = client.post(
            "/api/v1/documents",
            files={"file": ("offline-invoice.png", buf.getvalue(), "image/png")},
            data={"languages": "en"},
            headers=csrf,
        )
        submitted.raise_for_status()
        job = submitted.json()["job"]
        start = time.monotonic()
        while job["status"] in ("queued", "running") and time.monotonic() - start < 600:
            time.sleep(2)
            job = client.get(f"/api/v1/jobs/{job['id']}").json()
        out["job"] = {k: job[k] for k in ("status", "completed_stages", "attempts", "error_code")}
        out["job"]["seconds"] = round(time.monotonic() - start, 1)
        if job["status"] != "succeeded":
            return out
        doc = job["document_id"]
        result = client.get(f"/api/v1/documents/{doc}/extraction/auto").json()
        values = {f["name"]: f["value"] for f in result["fields"]}
        checked = ("gstin_seller", "invoice_number", "grand_total")
        out["extraction"] = {
            "schema_id": result["schema_id"],
            "matches": {k: values.get(k) == truth.fields.get(k) for k in checked},
        }
        export = client.get(
            f"/api/v1/documents/{doc}/extraction/auto/export", params={"format": "xlsx"}
        )
        out["export_xlsx_bytes"] = len(export.content) if export.status_code == 200 else 0
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", required=True)
    parser.add_argument("--keep", action="store_true")
    args = parser.parse_args()
    ca = ROOT / "deploy" / "secrets" / "tls" / "ca.crt"
    make = shutil.which("make") or "/usr/bin/make"
    subprocess.run(  # noqa: S603  # nosec B603 - our own Makefile, fixed arguments
        [make, "--no-print-directory", "services-secrets"], cwd=ROOT, check=True
    )
    report: dict[str, Any] = {"image": args.image}
    try:
        compose("up", "-d", "--wait", image=args.image)
        compose(
            "exec",
            "-T",
            "api",
            "python",
            "-m",
            "newgen_ocr.auth",
            "user-create",
            "--user",
            USER,
            "--role",
            "admin",
            "--password-stdin",
            image=args.image,
            stdin=PASSWORD + "\n",
            check=False,
        )
        report["egress"] = {
            service: json.loads(
                compose("exec", "-T", service, "python", "-c", PROBE, image=args.image)
                .strip()
                .splitlines()[-1]
            )
            for service in ("api", "worker")
        }
        report["runtime"] = json.loads(
            compose(
                "exec",
                "-T",
                "api",
                "python",
                "-c",
                "import json,cv2; from newgen_ocr.config import get_settings as g; s=g(); "
                "from newgen_ocr.imaging.runtime import opencv_has_ffmpeg as f; "
                "print(json.dumps({'environment': s.environment, 'service_mode': s.service_mode, "
                "'opencv': cv2.__version__, 'opencv_ffmpeg': f()}))",
                image=args.image,
            )
            .strip()
            .splitlines()[-1]
        )
        report["tls"] = tls_checks(ca)
        report["processing"] = run_job(ca)
    finally:
        if not args.keep:
            compose("down", "-v", image=args.image, check=False)
    failures = []
    for service, probes in report.get("egress", {}).items():
        for name, outcome in probes.items():
            reached = outcome == "REACHED"
            if name.startswith("control:") != reached:
                failures.append(f"{service} {name}: {outcome}")
    runtime = report.get("runtime", {})
    if (
        runtime.get("environment") != "production"
        or runtime.get("opencv_ffmpeg")
        or not runtime.get("service_mode")
    ):
        failures.append(f"runtime: {runtime}")
    tls = report.get("tls", {})
    if not str(tls.get("tls1.2", "")).startswith("accepted") or not str(
        tls.get("tls1.3", "")
    ).startswith("accepted"):
        failures.append(f"TLS 1.2/1.3: {tls}")
    if not str(tls.get("tls1.1", "")).startswith("refused") or tls.get("plain_http") != "refused":
        failures.append(f"old TLS or plain HTTP accepted: {tls}")
    processing = report.get("processing", {})
    if processing.get("job", {}).get("status") != "succeeded":
        failures.append(f"job: {processing.get('job')}")
    if not all(processing.get("extraction", {}).get("matches", {}).values() or [False]):
        failures.append(f"extraction: {processing.get('extraction')}")
    if not (processing.get("cookie_secure") and all(processing.get("headers", {}).values())):
        failures.append("cookie or security headers missing")
    report["failures"] = failures
    report["passed"] = not failures
    (ROOT / "reports").mkdir(exist_ok=True)
    (ROOT / "reports" / "offline-test.json").write_text(json.dumps(report, indent=2) + "\n")
    sys.stdout.write(json.dumps(report, indent=2) + "\n")
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
