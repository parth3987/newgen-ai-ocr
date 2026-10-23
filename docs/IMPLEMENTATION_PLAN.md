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

## Phase 5: Language and handwriting support (**complete**, 2026-10-23)

Redefined by the project owner. Persistence and jobs move to Phase 6. Restarted once on
2026-10-23 (the work was kept and finished from a reviewed plan).

- [x] Supported-language matrix (`ocr/languages.py`, `/api/v1/languages`, LANGUAGE_SUPPORT.md).
- [x] Open-source recognition models per script identified, pinned, licence-checked
      (MODEL_INVENTORY.md); handwriting candidates reviewed and rejected with reasons.
- [x] Printed English and each Indian language evaluated independently (Phase 4 corpus, both engines).
- [x] Script/orientation identification (OSD + census) and per-page routing (`languages: ["auto"]`).
- [x] Optional handwriting as a separate engine (`paddle_handwriting`, English, experimental).
- [x] Checkpoint licensing/redistribution verified: Apache-2.0 weights, hashes pinned and
      cross-checked; fonts OFL, evaluation only.
- [x] Handwriting evaluated per supported language: English (synthetic proxy only); Indian
      scripts unsupported (no clean model); real-sample harness ready.
- [x] Unsupported-language and uncertain-recognition path: `unsupported` status + review reasons.
- [x] Mixed-script and mixed-language test cases (unit, live and evaluation).

Acceptance evidence (2026-10-23):

| Criterion | Evidence | Status |
|---|---|---|
| Every enabled language has documented model provenance and test results | LANGUAGE_SUPPORT.md (per-language models + CER) → MODEL_INVENTORY.md (revision, hashes, licences); `test_every_enabled_printed_engine_has_a_pinned_model`; Kannada PaddleOCR disabled by measurement | Met |
| Handwriting support is not claimed for untested scripts | Indian scripts `unsupported`; English `experimental` (synthetic only) with every result `handwriting_unverified`; `test_handwriting_is_never_claimed_supported`, `test_indic_handwriting_is_unsupported` | Met |
| Model weights are stored locally and hash-verified | `models.lock.json` (16 models) + runtime verification + offline tests; new models cross-checked against publisher hashes | Met |
| Unsupported or low-quality inputs can be routed to human review | `unsupported` status + 13 review reasons; service tests (fake detector) and live API tests (Bengali page, handwriting, mixed, upside-down) | Met |

Key findings: OSD's Devanagari script detection is text-size dependent, so census-based
identification was added. The English "handwriting" recogniser underperformed the printed one
on the synthetic proxy and drops word spaces on some inputs. Confidence does not reveal a
systematically wrong recogniser.

## Phase 6: Layout, tables and document structure (**complete**, 2026-10-23)

Redefined by the owner (previously Phase 7; persistence moves to Phase 7). Components approved
2026-10-23: `PP-DocLayout_plus-L` (default) / `PP-DocLayout-M`, `PP-LCNet_x1_0_table_cls`,
`SLANet_plus`, plus a deterministic ruling-line check (ADR-0025). Explicit-separator key-value
pairs only.

Delivered: `newgen_ocr/layout/` (models wrapper, region assignment, XY-cut reading order,
table structure parsing, ruling tracing, OCR-evidence checks, cell re-reads, key-value rules,
inspection overlay); `POST/GET …/layout`, overlay PNG and `GET /api/v1/layout/status`; layout
runs stored per OCR run; synthetic layout test set and `scripts/evaluate_layout.py`
(`make eval-layout`); 4 models pinned and cross-checked; configs of every Paddle model now
cross-checked against Hugging Face git-blob hashes. **Defect found and fixed:** Paddle
predictors with oneDNN failed (and once degraded OCR silently) when called from more than one
thread, which affected the Phase 4–5 API too; all Paddle calls now run on one thread
(ADR-0020 addendum).

| Acceptance criterion | Evidence | Status |
|---|---|---|
| Table results preserve row and column associations on the defined test set | Synthetic set of 13 table designs × 2 conditions: association F1 1.000 on 23 of 24 detected tables; 0 wrong structures returned without review; the one wrong structure (wireless, degraded scan) was routed to review (ACCURACY_EVALUATION.md) | Met (synthetic set) |
| Layout errors can be inspected | Every region, cell and pair carries coordinates, scores, model labels and OCR line links; `issues` and `review` per table/page; overlay PNG endpoint | Met |
| The implementation records unsupported structures instead of fabricating relationships | `unsupported` list (formula, chart, seal, figure text, unrecoverable table structure), invalid grids return no cells, ruling/OCR-evidence mismatches and unread cells route to review, pairs only from explicit evidence; service tests | Met |
| Merged cells and complex tables tested where supported | colspan, rowspan and two-level header tables: exact structure and `verified` on clean and scan (live tests + evaluation) | Met |
| Coordinates link output to source pages | OCR-image coordinates + `image_source`/`image_rotation_deg`; `line_indices` into the OCR run | Met |

Not supported (recorded): native-text PDF pages (OD-25), unruled tables (OD-26), tables across
pages, nested and rotated tables; no real-document measurement yet (OD-27).

## Phase 7: Structured data extraction without an LLM (**complete**, 2026-10-23)

Redefined by the owner (absorbs the former "Extraction, validation and export" phase;
persistence moves to Phase 8). Document types chosen 2026-10-23: GST tax invoice, receipt /
cash memo, bank statement, PAN card, Aadhaar card. Excel via openpyxl (approved).

Delivered: `newgen_ocr/extraction/`. It covers:
- rule and template classification;
- JSON field schemas, validated on load, with custom schemas from
  `NGOCR_EXTRACTION_SCHEMA_DIR`;
- label, section, table-row, format and template candidates;
- normalisers (dates, amounts, GSTIN/PAN/Aadhaar/IFSC checksums, addresses, Indian-script
  digits);
- table-to-record conversion;
- Pydantic validation and 11 kinds of consistency check;
- evidence on every value;
- review routing and Aadhaar masking;
- JSON/CSV/XLSX export with formula-injection protection.

Also: `POST/GET …/extraction`, `…/export` and `GET /api/v1/extraction/schemas`; generated
JSON Schemas (`docs/schemas/`) and type reference (EXTRACTION_SCHEMAS.md), checked by the
tests; a synthetic test set and `scripts/evaluate_extraction.py` (`make eval-extraction`).

| Acceptance criterion | Evidence | Status |
|---|---|---|
| Outputs conform to documented schemas | Result, record and table-row JSON Schemas generated from the Pydantic models and checked for drift by a test; results round-trip through the schema; Pydantic validation of every record | Met |
| Missing values remain missing rather than being invented | `missing` status with `value: null`; checks never assume 0 (except declared optional terms, stated in the message); synthetic set: **0 invented** in 248 field judgements (incl. the unregistered buyer's GSTIN, IGST on intra-state invoices) | Met (synthetic set) |
| Validation errors are explicit | `validation.issues` with code and location per field/cell; check results with expected/actual/difference and failing rows; review reasons | Met |
| Extracted values can be traced back to source evidence | `evidence` on every value and table cell: page, source, OCR line indices, box and units, region/table/row/col, confidence; candidates listed | Met |
| No external LLM API is called | No LLM or network library in the extraction package (import-scan test); extraction + export under a socket audit hook: 0 events (test) | Met |

Findings: on a held-out invoice design, 6 of 12 values were found blind, and 9 of 12 after
generic fixes. Parties in an unheaded letterhead are not extracted (OD-29). Also open: real
documents (OD-28), Aadhaar in OCR artefacts (OD-30), unruled statements (OD-31) and amounts
in words (OD-32).

## Phase 8: Confidence, verification and human review (**complete**, 2026-10-23)

Defined by the owner on 2026-10-23. It takes the review queue and a minimal review page from
the former review-UI phase; persistence moves to Phase 9. The owner's choices:
- local hashed tokens with roles (ADR-0027);
- a minimal built-in review page;
- the local file store;
- thresholds calibrated on held-out synthetic data, with automatic acceptance gated and
  **off by default** (ADR-0028).

Delivered: `newgen_ocr/review/` and `newgen_ocr/auth/` (REVIEW.md). They cover:
- field-specific verification gates (calibrated threshold, engine agreement, business
  rules, source flags);
- calibration with a Wilson lower bound and type pooling, a held-out test split, and the
  `scripts/calibrate_review.py` script (`make calibrate`);
- a field-level cross-check against the second OCR engine;
- the review queue with priorities, claim/release, corrections normalised like extracted
  values, approve (four eyes, acknowledged failing checks) and reject;
- reviewed results re-validated and re-checked;
- a hash-chained history and content-free audit events;
- source crops and page previews (Aadhaar blacked out);
- the `/review` page;
- correction patterns.

| Acceptance criterion | Evidence | Status |
|---|---|---|
| Low-confidence and conflicting results are routed appropriately | Per-field reasons (`below_threshold`, `not_calibrated`, `engine_disagreement`, `check_failed`, `conflict`, `ambiguous`, `invalid`, `source_flagged`, `required_missing`); priority ordering; failed checks and engine disagreements never auto-accepted (tests) | Met |
| Corrections are auditable | Each correction records actor, role, time, request id, location, extracted/previous/new value, reason code and comment in a hash-chained history (tamper test); audit events without content; four-eyes approval; optimistic concurrency | Met |
| Confidence thresholds are supported by evaluation data | Per-field thresholds fitted on 400 synthetic documents and measured on 200 held-out documents, with reliability bins (ACCURACY_EVALUATION.md "Review thresholds"): 831 of 1,375 test values verified, all correct; 26 of 200 documents eligible, all correct; every wrong value in the set was routed to a person | Met on **synthetic** data; real documents pending (OD-33) |
| Automatic acceptance is not based on uncalibrated OCR confidence alone | No calibration means no verified value (test). Verification needs five gates besides the threshold. Thresholds come only from a hash-identified calibration file bound to schema versions. Auto-accept is off by default, and production refuses a synthetic calibration | Met |

Open: real-document calibration (OD-33), an off-host history copy (OD-34), session sign-in
(OD-35), token rate limiting (OD-36), table text cells against the second engine (OD-37),
and corrections across re-extraction (OD-38).

## Phase 9: Persistence, jobs, containers and authentication (next)

Planned work:
- **Database:** a PostgreSQL schema with Alembic migrations, covering documents, pages,
  preprocessing, OCR, layout and extraction runs, review items and history, and audit
  events.
- **Workers:** Valkey + Celery. Ingestion, preprocessing, OCR, layout, extraction and review
  queueing move to idempotent tasks keyed on `(document_id, stage, input_hash)`. Parsing and
  OCR run in workers under resource limits (OD-12), with one Paddle thread per worker
  process.
- **Containers:** Docker Compose (api/worker/postgres/valkey, internal network) with the
  **minimal source-built OpenCV wheel** (ADR-0015), `tesseract-ocr` and the verified
  `models/`.
- **Readiness checks** for the database, broker and OCR/layout models.
- **Authentication on every endpoint:** database-backed API keys and session sign-in
  (OD-6, OD-35).
- **Review history** in append-only storage (OD-34).

Acceptance:
- the compose stack starts from scratch;
- migrations apply and roll back;
- upload → 202 → jobs produce the same results as Phase 8;
- retries are idempotent;
- integration tests run on real Postgres/Valkey;
- the container scan is clean (OD-5);
- production starts with the minimal OpenCV build and no network.

## Phase 10: Full review application

A React + TypeScript review application served locally (no CDN assets). It replaces the
minimal `/review` page:
- page viewer with layout overlays and side-by-side engine readings;
- keyboard-driven correction and batch confirmation;
- reviewer workload and correction-pattern dashboards.

## Phase 11: Offline deployment and hardening

Planned work:
- offline bundle (wheels, images, models, SBOM, hashes);
- host-level egress blocking, verified by test;
- backups and a restore drill;
- storage retention (OD-16);
- runbook;
- throughput benchmarks on the reference CPU server (OD-19);
- real-document and real-handwriting evaluation sets (OD-21, OD-23, OD-28, OD-33).
