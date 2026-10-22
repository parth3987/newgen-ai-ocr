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

## Phase 2: Document ingestion and PDF processing (**complete**, 2026-10-22)

Redefined by the project owner on 2026-10-22. The former Phase 2 (persistence, jobs and
containers) becomes Phase 3.

- [x] Upload endpoint `POST /api/v1/documents` with a streaming body-size limit.
- [x] Approved formats PDF/JPEG/PNG/TIFF detected by magic bytes; extension cross-check (ADR-0010).
- [x] Validation of size, page count, per-page pixel limits and integrity (full decode/parse).
- [x] Safe handling of malformed, truncated, encrypted and unsupported files with stable
      error codes. Quarantine and staging are cleaned up on every failure.
- [x] Native-text detection per PDF page (ADR-0011); embedded text extracted with
      segment bounding boxes.
- [x] Scanned/image-content PDF pages rendered at `NGOCR_RENDER_DPI` (300 by default).
- [x] Page numbering preserved (1-based, document order; TIFF frames in file order).
- [x] Originals stored separately from derived artefacts; atomic commit (ADR-0013).
- [x] Audit events for accepted and rejected documents (ADR-0014); no filenames or content in
      logs or the audit trail.
- [x] Dependency admission enforced mechanically (`--exclude-newer`); Pillow security override
      (ADR-0007 amendment). FastAPI telemetry disabled explicitly (ADR-0008).

Acceptance evidence (2026-10-22):

| Criterion | Evidence |
|---|---|
| Native-text PDFs do not undergo unnecessary OCR | `test_native_text_page_is_not_rendered`, `test_native_pdf_end_to_end`: `needs_ocr=false`, no image rendered or stored |
| Scanned PDFs are rendered correctly | `test_scanned_page_is_rendered_at_configured_dpi`: exact pixel size at DPI and pixel-level content check; manual check of a rendered page via the running server |
| Multipage documents preserve page order | `test_multipage_mixed_document_preserves_order` (6 alternating native/scanned pages), `test_multipage_tiff_preserves_frame_order` |
| Invalid files are rejected safely | Parametrised unit and API tests for empty, unknown, GIF/BMP/EXE/ZIP, extension mismatch, corrupt/truncated PDF and images, password-protected PDF, page/pixel/size limits, streamed bodies without Content-Length; each asserts that no files remain |
| Unit and integration tests cover supported and rejected inputs | 129 tests passing (unit plus `tests/integration/test_documents_api.py`), 98% coverage; `make check` exit 0; `make secrets` no leaks |

Known limitations, tracked as open decisions: parsing happens in-process (OD-12); ingestion is
synchronous (ADR-0012); rotated-page coordinate reconciliation (OD-14). No real-world or
Indic-language documents have been tested; fixtures are synthetic.

## Phase 3: Persistence, jobs, containers and authentication (next)

PostgreSQL schema + Alembic migrations (documents, pages, audit events); Valkey + Celery
worker. Ingestion processing moves to idempotent tasks keyed on `(document_id, stage,
input_hash)`, and parsing runs in the worker under resource limits (OD-12). Docker Compose
for api/worker/postgres/valkey on an internal network; readiness checks for DB and broker;
API authentication (OD-6).

Acceptance: compose stack starts from scratch; migrations apply and roll back on an empty DB;
upload returns 202 and the job completes with the same manifest as Phase 2; retries do not
duplicate artefacts; integration tests against real Postgres and Valkey; container image
scan clean (OD-5).

## Phase 4: Image quality assessment and preprocessing

Quality metrics (blur, contrast, skew, resolution, noise); deskew, denoise, binarisation and
orientation detection with OpenCV. Acceptance: unit tests on synthetic fixtures for each
transform; metrics validated against labelled samples.

## Phase 5: OCR engines and evaluation harness

`OcrEngine` protocol; Tesseract adapter (baseline); PaddleOCR adapter (CPU) with explicit
local model paths and runtime downloads disabled; MODEL_INVENTORY entries with hashes and
license review; evaluation harness (CER/WER) and first per-language measurements on a
curated, licensed test set. **Stop point:** any model with an unclear license is reported
before use.

## Phase 6: Classification and layout

Document type classification, language/script detection, reading order, multi-column
handling, table structure reconstruction, key-value detection.

## Phase 7: Extraction, validation and export

Field extraction (templates/rules per document type), validators (formats, checksums,
cross-field rules), confidence aggregation and review triggers; JSON/CSV/Excel exports with
evidence.

## Phase 8: Human review UI

React + TypeScript review application served locally (no CDN assets): queue, page viewer with
bounding-box overlays, field correction, audit trail.

## Phase 9: Handwriting recognition

Select, license-review and integrate a locally hosted handwriting model; measure per language.
Languages without a viable model are documented as unsupported, not approximated.

## Phase 10: Offline deployment and hardening

Offline bundle (wheels, images, models, SBOM, hashes); host-level egress blocking verified by
test; backups/restore drill; operations runbook; throughput benchmarks against the
100–10,000 pages/day target on the reference CPU server.
