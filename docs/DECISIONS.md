# Architecture Decisions

## Accepted

### ADR-0001: Python 3.11, uv and hash-pinned lock files (2026-10-22)
Python 3.11 matches the mandated runtime and has the widest support among the CPU OCR
stacks (PaddlePaddle compatibility is still to be verified in Phase 4). The dev host ships
3.14, so 3.11 is provisioned by uv. `requirements*.in` are the human-edited sources;
`uv pip compile --generate-hashes` produces `requirements*.lock`, installed with
`--require-hashes`. Locks are resolved for `x86_64-manylinux_2_28` / Python 3.11 regardless
of the host. `pyproject.toml` declares no dependencies, so there is a single source of truth.

### ADR-0002: `src/` layout, single package, app factory (2026-10-22)
One importable package `newgen_ocr` with subpackages per pipeline stage (ARCHITECTURE.md §3).
The FastAPI app is built by `create_app(settings)`, with no module-level app instance, so
tests inject settings and nothing reads the environment at import time.

### ADR-0003: Configuration via pydantic-settings (2026-10-22)
Environment variables prefixed `NGOCR_`; optional `.env` in development; the settings object
is frozen. Secrets will be `SecretStr` with no defaults.

### ADR-0004: Structured logging with the standard library (2026-10-22)
JSON lines to stdout (container-friendly), with no extra dependency. Policy: metadata only,
never document contents or field values. Query strings are not logged. HTTP-client
libraries (`httpx`, `httpcore`, `urllib3`) are capped at WARNING because they log full URLs.
The handler writes to the *current* `sys.stdout` at emit time so redirection works.

### ADR-0005: Disable Swagger UI and ReDoc (2026-10-22)
FastAPI's interactive docs load JavaScript and CSS from public CDNs, which violates the
no-external-dependency policy. The OpenAPI JSON is still served at `/api/v1/openapi.json`.
If interactive docs are wanted later, vendor the assets locally.

### ADR-0006: Error envelope and stable error codes (2026-10-22)
All errors return `{"error": {"code", "message", "request_id"}}`. Codes are a `StrEnum` and
are never renamed or reused. Validation errors report field locations and messages but never
echo input values. Unhandled exceptions return a generic 500.

### ADR-0007: Dependency admission policy (2026-10-22)
A new dependency (Python, npm, container image or model) needs: a license on the allowlist
(`scripts/check_licenses.py`) or explicit approval; a clean `pip-audit`; a release at least
14 days old; an entry in THIRD_PARTY_NOTICES.md (and MODEL_INVENTORY.md for models).

*Amended 2026-10-22 (Phase 2).* The Phase 1 locks violated the age rule (FastAPI 0.159.1,
Starlette 1.9.0, Pydantic 2.14.1, Ruff and others were under 14 days old) because ages were
checked by hand only for directly added packages. `make lock` now enforces the rule for every
transitive dependency with `uv pip compile --exclude-newer <today-14d> --upgrade`, which moved
FastAPI to 0.143.0 and Starlette to 1.7.0. **Security exception:** when the age-floor version
has known vulnerabilities fixed only in a newer release, that package gets a dated
`--exclude-newer-package` override in `SECURITY_OVERRIDES` (Makefile). Current overrides:
`pillow` (12.3.0 has about 25 CVEs, including TIFF decoder out-of-bounds reads, fixed in
13.0.0) and `certifi` (dev only). Remove each override once the floor passes it.

### ADR-0008: `opentelemetry-api` dependency; FastAPI telemetry disabled (2026-10-22)
FastAPI requires `opentelemetry-api` (Apache-2.0), which is a no-op without an SDK and
exporter. FastAPI also ships built-in instrumentation (`fastapi.telemetry`). With
`FASTAPI_OTEL_AUTO_CONFIGURE=true` and an `OTEL_EXPORTER_OTLP_*` endpoint in the environment,
it attaches OTLP exporters (verified 2026-10-22: a default `FastAPI()` enables all signals
under that variable). Controls: `create_app` passes
`telemetry={"tracing": False, "metrics": False, "logs": False, "auto_configure": False}`;
`tests/unit/test_offline_policy.py` asserts that this holds even with those variables set,
and fails if any OpenTelemetry SDK/exporter/instrumentation or hosted-AI SDK is installed.
Containers will also set `OTEL_SDK_DISABLED=true`.

### ADR-0009: Separate liveness and readiness probes (2026-10-22)
`/health/live` performs no I/O (it is for restart decisions). `/health/ready` checks the
dependencies needed to accept work (currently `data_dir`; DB and broker are added in Phase 3)
and returns 503 with per-check results when any check fails.

### ADR-0010: Approved formats detected by signature (2026-10-22)
Accepted inputs are PDF, JPEG, PNG and classic TIFF (not BigTIFF), configurable down through
`NGOCR_ALLOWED_FORMATS`. The format is decided by magic bytes at offset 0 only. A filename
extension that contradicts the detected format is rejected (`FORMAT_MISMATCH`), and the
declared Content-Type is recorded but not trusted. Pillow is invoked with `formats=[<detected>]`,
so decoders for other formats (EPS, GIF, XBM, ...) are unreachable. PDFs that need a user
password are rejected (`ENCRYPTED_DOCUMENT`). Permissions-only (owner-password) PDFs are
accepted and flagged `pdf_encrypted: true`.

### ADR-0011: Per-page OCR decision for PDFs (2026-10-22)
Each PDF page is classified from measurements of its text layer (`newgen_ocr/pdf/analysis.py`).
In order: no text → OCR (`no_text_layer`); ≥90% of text objects invisible (render mode 3, a
third-party OCR layer) → OCR (`invisible_text_layer`, embedded text kept for comparison);
>10% replacement/control/private-use characters → OCR (`unreliable_text`); fewer than
`NGOCR_NATIVE_TEXT_MIN_CHARS` (20) characters → OCR (`insufficient_text`); raster images
covering ≥50% of the page → OCR (`image_content`, which catches scans with a digital header
line); otherwise the native text is used and the page is **not rendered**. This trades some
unnecessary OCR on image-heavy digital pages for never silently losing scanned content.
Thresholds are named constants and will be tuned against the evaluation set (Phase 5).

### ADR-0012: Synchronous ingestion with a global PDFium lock (temporary) (2026-10-22)
Until the job queue exists (Phase 3), `POST /api/v1/documents` ingests synchronously in
FastAPI's thread pool. PDFium is not thread-safe, so all PDFium use is serialised by
`PDFIUM_LOCK`. Throughput is therefore limited to one PDF at a time per process, which is
acceptable for development and must not be used for production volumes.

### ADR-0013: Filesystem document store and manifests as the metadata record (2026-10-22)
Until PostgreSQL arrives (Phase 3), `derived/<id>/manifest.json` is the system of record for
document metadata. Originals (`originals/`, mode 0440) are stored separately from derived
artefacts (`derived/`). Derived output is built in `staging/` and renamed into place
atomically, with the manifest included. Client filenames never touch the filesystem. Document
ids are random UUID4 hex strings. Duplicate uploads become separate documents (sha256 is
recorded so they can be deduplicated later).

### ADR-0014: Append-only JSONL audit trail (2026-10-22)
`audit/audit.jsonl` records `document.accepted` and `document.rejected` with request id,
actor (`anonymous` until auth exists), sha256, size, format, page counts and error code,
never filenames or content. Each line is appended with `O_APPEND` and fsync. It moves to a
database table in Phase 3, with the file kept as a tamper-evident export.

## Open decisions (defaults in force until decided)

| ID | Question | Default assumption |
|---|---|---|
| OD-1 | Production container base image | `ubuntu:24.04` + uv-provisioned Python 3.11, for parity with the target OS |
| OD-2 | Queue broker | Valkey (BSD-3-Clause) |
| OD-3 | Handwriting model | None selected. Candidates are license-reviewed in Phase 9; **stop and report** if none is clearly licensed |
| OD-4 | PaddleOCR coverage for te/hi/ta/kn/mr on CPU | Unverified. Measured in Phase 5; no capability is claimed until measured |
| OD-5 | Container/image vulnerability scanner | Trivy (Apache-2.0); Grype as an alternative. Needs approval before adoption (Phase 3) |
| OD-6 | API authentication | API keys (hashed in DB) for system integrations, plus session login for the review UI; OIDC optional later |
| OD-7 | Test-client transport | Starlette warns that `httpx` is deprecated in favour of `httpx2`. Migration is held for approval (new dependency) |
| OD-8 | GitHub Actions pinning | Pinned to major tags today; pin to commit SHAs before the first protected-branch merge |
| OD-9 | Untracked empty `requirements.txt` | Left untouched; superseded by `requirements.in`/`.lock`. Propose deleting it |
| OD-10 | Git remote is a placeholder (`YOUR_USERNAME`) | No pushes or commits until the owner authorizes them and sets the remote |
| OD-11 | Dev host is Ubuntu 26.04, target is 24.04 | CI runs on `ubuntu-24.04`; containers will use a 24.04 base (OD-1) |
| OD-12 | Parser isolation | PDFium and Pillow parse untrusted files in the API process. Default: move parsing into the worker in Phase 3 with resource limits (memory, CPU time, no network); evaluate a separate sandboxed subprocess per document |
| OD-13 | Retention of rejected uploads | Deleted immediately; only the audit event (sha256, size, code) remains. Alternative: a quarantine hold for security review |
| OD-14 | Rotated PDF pages | `rotation` is recorded. Text bounding boxes are in unrotated page space with a top-left origin, while rendered images follow PDFium's display orientation. Reconcile in Phase 6 (layout) |
| OD-15 | Upload spooling | Starlette spools multipart files over 1 MiB to `TMPDIR` before the handler runs (size-capped by the body limit). Default: set `TMPDIR` to a directory on the protected data volume in deployment |
