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

### ADR-0008: Accept `opentelemetry-api` as a transitive dependency (2026-10-22)
FastAPI 0.159 requires `opentelemetry-api` (Apache-2.0). The API package is a no-op without
an SDK and exporter and contains no network code. `tests/unit/test_offline_policy.py` fails
if `opentelemetry-sdk`, any exporter or instrumentation package, or a hosted-AI SDK is
installed. The environment variable `OTEL_SDK_DISABLED=true` will also be set in containers.

### ADR-0009: Separate liveness and readiness probes (2026-10-22)
`/health/live` performs no I/O (it is for restart decisions). `/health/ready` checks the
dependencies needed to accept work (currently `data_dir`; DB and broker are added in Phase 2)
and returns 503 with per-check results when any check fails.

## Open decisions (defaults in force until decided)

| ID | Question | Default assumption |
|---|---|---|
| OD-1 | Production container base image | `ubuntu:24.04` + uv-provisioned Python 3.11, for parity with the target OS |
| OD-2 | Queue broker | Valkey (BSD-3-Clause) |
| OD-3 | Handwriting model | None selected. Candidates are license-reviewed in Phase 8; **stop and report** if none is clearly licensed |
| OD-4 | PaddleOCR coverage for te/hi/ta/kn/mr on CPU | Unverified. Measured in Phase 4; no capability is claimed until measured |
| OD-5 | Container/image vulnerability scanner | Trivy (Apache-2.0); Grype as an alternative. Needs approval before adoption |
| OD-6 | API authentication | API keys (hashed in DB) for system integrations, plus session login for the review UI; OIDC optional later |
| OD-7 | Test-client transport | Starlette warns that `httpx` is deprecated in favour of `httpx2`. Migration is held for approval (new dependency) |
| OD-8 | GitHub Actions pinning | Pinned to major tags today; pin to commit SHAs before the first protected-branch merge |
| OD-9 | Untracked empty `requirements.txt` | Left untouched; superseded by `requirements.in`/`.lock`. Propose deleting it |
| OD-10 | Git remote is a placeholder (`YOUR_USERNAME`) | No pushes or commits until the owner authorizes them and sets the remote |
| OD-11 | Dev host is Ubuntu 26.04, target is 24.04 | CI runs on `ubuntu-24.04`; containers will use a 24.04 base (OD-1) |
