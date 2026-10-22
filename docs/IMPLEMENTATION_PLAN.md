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

## Phase 3: Image preprocessing and quality assessment (**complete**, 2026-10-22)

Redefined by the project owner on 2026-10-22. Persistence, jobs and containers move to Phase 4.

- [x] Image loading and normalisation: Phase 2 loader plus greyscale conversion; DPI
      sanity check (ADR-0017).
- [x] Rotation (text-line axis) and skew detection by projection profiles; 180° ambiguity
      deferred to OCR (ADR-0016).
- [x] Configurable denoising (median/bilateral/NL-means) and contrast enhancement
      (stretch + CLAHE); illumination flattening.
- [x] Optional adaptive thresholding (`binarize` profile only).
- [x] Detection of low resolution, unknown resolution, blur, blank pages, poor contrast,
      noise (Gaussian and salt-and-pepper), uneven background, skew and rotation.
- [x] Source page images preserved; per-profile outputs stored separately (ADR-0018).
- [x] Profiles (`none`, `standard`, `degraded_scan`, `photo`, `binarize`, custom), with
      flag-driven enhancement in the default profile.
- [x] Every decision, parameter and timing recorded per page and step in `run.json`.
- [x] OpenCV supply decision (ADR-0015) with a production startup guard.

Acceptance evidence (2026-10-22):

| Criterion | Evidence |
|---|---|
| Tests cover rotated, skewed, noisy and low-contrast samples | `test_geometry.py` (8 skew angles, 4 quarter turns, degraded upscaled JPEG, non-text), `test_quality.py` (each flag raised by its degradation and not by mild variants), `test_filters.py`, `test_preprocessing_pipeline.py` (rotated 90/270 + skew, skewed, noisy, faded, shadowed, blank), API tests on a skewed scanned PDF and a noisy PNG |
| Preprocessing does not silently replace the source | `test_source_image_is_never_modified` (array equality), `test_source_page_is_preserved_across_reruns` (source PNG sha256 unchanged across three runs; profiles stored side by side) |
| Quality flags are recorded | `quality` on every image page in the manifest; `quality_flags_before` / `quality_after` per page in each run; tests assert flags clear after correction |
| Performance is measured on CPU | docs/PERFORMANCE.md: per-stage, per-step and end-to-end timings at 300 DPI with 4 threads and 1 thread; `make bench` reproduces them. Measured optimisations cut end-to-end time from 1.9 to 1.2 s/page |

221 tests passing, 98% coverage; `make check` exit 0.

Limitations: thresholds are calibrated on synthetic pages only, and all timings come from a
laptop CPU, not the target server. Real-scan calibration happens in Phase 5 with the
evaluation set.

## Phase 4: Primary OCR and engine adapters (**complete**, 2026-10-22)

Redefined by the project owner on 2026-10-22. Persistence, jobs and containers move to Phase 5.

- [x] Common `OcrEngine` interface and engine-independent result schema (ADR-0022).
- [x] PaddleOCR 3.7 (PaddlePaddle 3.2.2) with locally stored, pinned, hash-verified models,
      locked down against runtime downloads (ADR-0020).
- [x] Tesseract 5 adapter (CLI, explicit `--tessdata-dir`, `tessdata_best`) as a separate
      baseline and fallback; Kannada-primary by measurement (OD-17).
- [x] Text, line boxes, polygons, words (Tesseract), 0..1 confidences and text-line/page
      orientation returned.
- [x] Engine version and exact model revisions + file hashes recorded in every run.
- [x] Explicit model paths (`models.lock.json`, `NGOCR_MODELS_DIR`); offline loading;
      `make models` at staging only (ADR-0021).
- [x] Engine comparison (agreement → review flag); defined policy that never merges results.
- [x] Tests for model loading/integrity, empty pages, errors, fallback and offline operation.
- [x] Accuracy (CER/WER) and CPU throughput measured on a synthetic multilingual corpus
      for both engines.

Acceptance evidence (2026-10-22):

| Criterion | Evidence | Status |
|---|---|---|
| OCR works without internet access | `test_paddle_ocr_runs_with_no_network_namespace`: OCR under `unshare -rn` (no interfaces), plus an audit hook recording **0** socket connect/DNS events across PaddleX import and recognition. Tesseract is a local subprocess with an explicit `--tessdata-dir` and has no network code paths | Met |
| No model is downloaded automatically at runtime | `test_paddlex_cannot_resolve_or_download_models` (model manager raises); `test_missing_models_report_unavailable` and `test_missing_model_is_reported_not_downloaded` (`MODEL_NOT_AVAILABLE`, nothing fetched); PaddleX cache dir contains no model files after OCR; tamper test (`MODEL_INTEGRITY_ERROR`) | Met |
| Output follows a documented schema | `newgen_ocr/ocr/models.py` = API_SPECIFICATION.md §OCR; schema assertions in `test_english_accuracy_and_schema`, `test_ocr_run_lifecycle` | Met |
| The system records the engine used | Per run: engine version and models (revision + file hashes), `test_info_records_exact_models`. Per page: `engine`, `fallback_from` / `fallback_reason` (`test_fallback_*`); audit event `ocr.completed` | Met |
| Accuracy and CPU throughput measured on a baseline corpus | ACCURACY_EVALUATION.md and PERFORMANCE.md: 36-page synthetic corpus, 6 languages × 3 conditions, CER/WER and s/page for both engines | Met (synthetic corpus only; real documents are not yet measured) |

Additional findings: PaddlePaddle 3.3.1 oneDNN failure leading to the 3.2.2 pin (5x speed);
the text-line orientation model garbling Indic scripts (now English-only); **Kannada is not
usable on PaddleOCR** (CER 0.39, WER 1.0), so it is routed to Tesseract (CER 0.013);
Tesseract's TSV config-file pitfall and Tamil zero-width joiners were found and fixed by
measurement.

## Phase 5: Persistence, jobs, containers and authentication (next)

PostgreSQL schema + Alembic migrations (documents, pages, preprocessing and OCR runs, audit
events); Valkey + Celery workers. Ingestion, preprocessing and OCR move to idempotent tasks
keyed on `(document_id, stage, input_hash)`, with parsing and OCR in workers under resource
limits (OD-12) and one OCR model set per worker process. Docker Compose for
api/worker/postgres/valkey on an internal network, with the **minimal source-built OpenCV
wheel** (ADR-0015), `tesseract-ocr` and the verified `models/` in the images. Readiness checks
for DB, broker and OCR engines; API authentication (OD-6).

Acceptance: compose stack starts from scratch; migrations apply and roll back on an empty DB;
upload returns 202 and the jobs produce the same manifest, preprocessing run and OCR run as
Phase 4; retries do not duplicate artefacts; integration tests against real Postgres and
Valkey; container image scan clean (OD-5); production-mode startup succeeds with the minimal
OpenCV build and no network.

## Phase 6: Classification and layout

Document type classification, language/script detection (feeds OCR language selection,
OD-20), page orientation for Devanagari (OD-18), reading order, multi-column handling, table
structure reconstruction, key-value detection; rotated-page coordinate reconciliation (OD-14).

## Phase 7: Extraction, validation and export

Field extraction (templates/rules per document type), validators (formats, checksums,
cross-field rules), confidence calibration and aggregation, review triggers; JSON/CSV/Excel
exports with evidence.

## Phase 8: Human review UI

React + TypeScript review application served locally (no CDN assets): queue, page viewer with
bounding-box overlays, source vs preprocessed comparison, engine disagreements, field
correction, audit trail.

## Phase 9: Handwriting recognition

Select, license-review and integrate a locally hosted handwriting model; measure per language.
Languages without a viable model are documented as unsupported, not approximated.

## Phase 10: Offline deployment and hardening

Offline bundle (wheels, images, models, SBOM, hashes); host-level egress blocking verified by
test; backups/restore drill; storage retention (OD-16); operations runbook; throughput
benchmarks against the 100–10,000 pages/day target on the reference CPU server (OD-19); a
real-document evaluation set.
