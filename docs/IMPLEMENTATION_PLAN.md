# Implementation Plan

Each phase ends with an acceptance gate. A phase is marked complete only when every
criterion was actually executed and verified. Later phases may be re-planned as findings
emerge; changes are recorded in DECISIONS.md.

## Standard gate (applies to every phase)

`make check` passes (ruff format + lint, mypy strict, pytest with ≥90% coverage, Bandit,
pip-audit on both locks, license allowlist), `make secrets` reports no leaks, documentation
and CHANGELOG are updated, and no stub is left unmarked.

---

## Phase 0: Repository inspection and planning (**complete**, 2026-10-22)

- [x] Inspected the repository: one commit (README, .gitignore), untracked empty
      `requirements.txt` (left untouched), placeholder `origin` remote, branch `main`.
- [x] Inspected the host: Ubuntu 26.04 dev box (target is 24.04), Python 3.14 system, uv 0.13.1,
      Docker-compatible engine, Node 24. Python 3.11.17 provisioned via uv.
- [x] Architecture and module boundaries documented (ARCHITECTURE.md).
- [x] Open decisions with default assumptions recorded (DECISIONS.md).
- [x] No destructive changes: existing files were only appended to or extended.

## Phase 1: Environment and development foundation (**complete**, 2026-10-22)

- [x] Python 3.11 pinned (`requires-python = ">=3.11,<3.12"`), uv-managed venv.
- [x] `pyproject.toml` with Ruff, mypy (strict), pytest and coverage configuration.
- [x] `requirements.in` / `requirements-dev.in` → hash-pinned `requirements.lock` /
      `requirements-dev.lock`.
- [x] Configuration management (`config.py`) and structured logging (`logging_config.py`).
- [x] Error envelope with stable codes (`errors.py`).
- [x] Health endpoints `/health/live` and `/health/ready`.
- [x] CI workflow (`.github/workflows/ci.yml`).
- [x] License allowlist check, pip-audit, Bandit, Gitleaks, CycloneDX SBOM.

Acceptance evidence (2026-10-22): `.venv` deleted and rebuilt with `make venv`; `make check`
exit 0 (23 tests passed, 98% coverage); `make sbom` produced 16 components; `make secrets`
reported no leaks; the server was started with uvicorn and `/health/live` → 200 and
`/health/ready` → 200 were verified with curl. The CI workflow has not yet run on a hosted
runner because the Git remote is a placeholder.

## Phase 2: Persistence, storage, jobs and containers (next)

Scope: PostgreSQL schema + Alembic migrations; Valkey + Celery worker with idempotent task
pattern; document upload API with magic-byte type validation, size/page limits and
quarantine; content-addressed local storage; Docker Compose for api/worker/postgres/valkey on
an internal network; readiness checks for DB and broker; API authentication (see OD-6).

Acceptance: compose stack starts from scratch; migrations apply and roll back on an empty DB;
upload → job record → no-op worker task → status visible via API; malicious/oversized/
mismatched files rejected with stable error codes; integration tests against real Postgres
and Valkey containers; container image scan (Trivy or Grype, pending approval) clean.

## Phase 3: PDF handling and image preprocessing

Native text-layer extraction vs page rendering decision per page (pypdfium2/pypdf, license
check first); image quality metrics (blur, contrast, skew, resolution); deskew, denoise,
binarisation, orientation. Acceptance: unit tests on synthetic fixtures for each transform;
quality metrics validated against labelled samples.

## Phase 4: OCR engines and evaluation harness

`OcrEngine` protocol; Tesseract adapter (baseline); PaddleOCR adapter (CPU) with explicit
local model paths and runtime downloads disabled; MODEL_INVENTORY entries with hashes and
license review; evaluation harness (CER/WER) and first per-language measurements on a
curated, licensed test set. **Stop point:** any model with unclear license is reported
before use.

## Phase 5: Classification and layout

Document type classification, language/script detection, reading order, multi-column
handling, table structure reconstruction, key-value detection.

## Phase 6: Extraction, validation and export

Field extraction (templates/rules per document type), validators (formats, checksums,
cross-field rules), confidence aggregation and review triggers; JSON/CSV/Excel exports with
evidence.

## Phase 7: Human review UI

React + TypeScript review application served locally (no CDN assets): queue, page viewer with
bounding-box overlays, field correction, audit trail.

## Phase 8: Handwriting recognition

Select, license-review and integrate a locally hosted handwriting model; measure per language.
Languages without a viable model are documented as unsupported, not approximated.

## Phase 9: Offline deployment and hardening

Offline bundle (wheels, images, models, SBOM, hashes); host-level egress blocking verified by
test; backups/restore drill; operations runbook; throughput benchmarks against the
100–10,000 pages/day target on the reference CPU server.
