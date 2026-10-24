"""Service benchmark through the TLS ingress (ADR-0033): ``ngocrctl benchmark``.

Run inside the application image on the host network, like the smoke test. It submits the
bundle's benchmark documents (``benchmark/``: a fixed mix of invoices, receipts, statements,
ID cards, tables and text pages in several languages, rendered at staging from a seed no
tuning set uses) as new documents, ``CLIENTS`` at a time, until ``DOCUMENTS`` have been
submitted. Each upload is made unique, so every one is a real job.

Measured per job:
- **end-to-end latency:** from the start of the upload to the client seeing it finish;
- **queue delay:** ``started_at - created_at``;
- **processing time:** ``finished_at - started_at``;
- **outcome:** succeeded, failed, or timed out.

Throughput is pages over the time from the first upload to the last job finishing. CPU and
memory are sampled outside, by ``ngocrctl``.

Arguments: ADDRESS PORT HOSTNAME CA_FILE TOKEN_FILE SCRIPTS_DIR DOCUMENTS CLIENTS. Prints
one JSON object.
"""

from __future__ import annotations

import json
import math
import sys
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any


def percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return round(ordered[max(1, math.ceil(q / 100 * len(ordered))) - 1], 3)


def main(argv: list[str]) -> int:
    address, port, hostname, ca_file, token_file, scripts, documents, clients = argv
    sys.path.insert(0, scripts)
    import ssl

    from smoke_api import _Connection, _unique_png  # the smoke test's TLS client

    context = ssl.create_default_context(cafile=ca_file)
    token = Path(token_file).read_text(encoding="ascii").strip()
    samples = sorted((Path(scripts) / "benchmark").glob("*.png"))
    if not samples:
        raise SystemExit("no benchmark documents")

    def call(
        method: str, path: str, body: bytes | None = None, ctype: str | None = None
    ) -> tuple[int, Any]:
        connection = _Connection(address, int(port), hostname, context)
        headers = {"Authorization": f"Bearer {token}"}
        if ctype:
            headers["Content-Type"] = ctype
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        data = response.read()
        connection.close()
        try:
            return response.status, json.loads(data)
        except ValueError:
            return response.status, None

    jobs: list[dict[str, Any]] = []
    lock = threading.Lock()
    counter = iter(range(int(documents)))

    def worker() -> None:
        while True:
            with lock:
                n = next(counter, None)
            if n is None:
                return
            sample = samples[n % len(samples)]
            boundary = uuid.uuid4().hex
            language = sample.name.split("-")[0] if "-" in sample.name else "en"
            part = f"--{boundary}\r\nContent-Disposition: form-data; "
            body = (
                (
                    f'{part}name="languages"\r\n\r\n{language}\r\n'
                    f'{part}name="file"; filename="bench-{n}.png"\r\n'
                    "Content-Type: image/png\r\n\r\n"
                ).encode()
                + _unique_png(sample.read_bytes())
                + f"\r\n--{boundary}--\r\n".encode()
            )
            start = time.monotonic()
            record: dict[str, Any] = {"document": sample.name, "submitted": time.time()}
            try:
                status, submitted = call(
                    "POST", "/api/v1/documents", body, f"multipart/form-data; boundary={boundary}"
                )
                if status not in (200, 201, 202) or not submitted:
                    record["outcome"] = f"upload failed ({status})"
                else:
                    job = submitted["job"]
                    while (
                        job["status"] in ("queued", "running") and time.monotonic() - start < 1800
                    ):
                        time.sleep(1)
                        job = call("GET", f"/api/v1/jobs/{job['id']}")[1] or job
                    record["outcome"] = (
                        job["status"] if job["status"] in ("succeeded", "failed") else "timeout"
                    )
                    record["error_code"] = job.get("error_code")
                    created, started, finished = (
                        _time(job.get(k)) for k in ("created_at", "started_at", "finished_at")
                    )
                    if created and started:
                        record["queue_delay_s"] = round(started - created, 3)
                    if started and finished:
                        record["processing_s"] = round(finished - started, 3)
            except OSError as exc:
                record["outcome"] = f"error ({type(exc).__name__})"
            record["latency_s"] = round(time.monotonic() - start, 3)
            record["finished"] = time.time()
            with lock:
                jobs.append(record)

    began = time.time()
    threads = [threading.Thread(target=worker) for _ in range(int(clients))]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    ended = max((j["finished"] for j in jobs), default=began)
    done = [j for j in jobs if j["outcome"] == "succeeded"]
    failed = [j for j in jobs if j["outcome"] != "succeeded"]
    latencies = [j["latency_s"] for j in done]
    queue = [j["queue_delay_s"] for j in done if "queue_delay_s" in j]
    processing = [j["processing_s"] for j in done if "processing_s" in j]
    out = {
        "documents": len(jobs),
        "pages": len(jobs),  # every benchmark document is one page
        "clients": int(clients),
        "sample_documents": [s.name for s in samples],
        "wall_seconds": round(ended - began, 1),
        "pages_per_minute": round(len(done) / (ended - began) * 60, 3) if ended > began else None,
        "latency_p50_s": percentile(latencies, 50),
        "latency_p95_s": percentile(latencies, 95),
        "queue_delay_p50_s": percentile(queue, 50),
        "queue_delay_p95_s": percentile(queue, 95),
        "processing_p50_s": percentile(processing, 50),
        "processing_p95_s": percentile(processing, 95),
        "succeeded": len(done),
        "failed": len(failed),
        "failure_rate": round(len(failed) / len(jobs), 6) if jobs else None,
        "failures": [{k: j.get(k) for k in ("document", "outcome", "error_code")} for j in failed][
            :20
        ],
    }
    sys.stdout.write(json.dumps(out) + "\n")
    return 0


def _time(value: str | None) -> float | None:
    return datetime.fromisoformat(value).timestamp() if value else None


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
