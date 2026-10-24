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

## Phase 9: Database, background jobs and APIs (**complete**, 2026-10-23)

Defined by the owner on 2026-10-23. The owner's choices:
- metadata and results in PostgreSQL, files on the volume;
- plain psycopg with the OS libpq;
- database tokens with roles on every endpoint, plus a `submitter` role.

Application container images, the container scan (OD-5) and the minimal OpenCV build move
to Phase 11. Session sign-in moves to Phase 10 (OD-35).

Delivered (ADR-0029):
- **Database:** `newgen_ocr/db/`, with models, a hand-written initial migration, SQL stores
  for review, audit and tokens, and `python -m newgen_ocr.db`.
- **Jobs:** `newgen_ocr/jobs/`, with the repository (claims, leases, reaper), runner,
  service and Celery app; `python -m newgen_ocr.worker`; `python -m newgen_ocr.jobs reap`.
- **Shared builder:** `newgen_ocr/services.py`, used by the API and the workers.
- **API:** authentication on every router, document listing and status, job endpoints, and
  `202` uploads and stage runs in service mode.
- **Services:** `deploy/compose.yaml` and `deploy/compose.dev.yaml`; `make services-up`.

| Acceptance criterion | Evidence | Status |
|---|---|---|
| Asynchronous jobs work end to end | Upload → `202` → pipeline → results, the review item in PostgreSQL, and audit events naming the submitter (`test_upload_runs_the_whole_pipeline`). Stage jobs (`test_stage_jobs_rerun_part_of_the_pipeline`). The real worker over Valkey with real models (`test_jobs_survive_a_killed_worker`) | Met |
| Worker restarts do not corrupt job state | A real worker is SIGKILLed during OCR, and a new worker finishes the job on attempt 2, with each stage completed once (`test_jobs_survive_a_killed_worker`). A crash mid-ingest with a half-staged document and a moved original is recovered from clean state, and the dead worker can no longer write (`test_worker_crash_mid_ingest_is_recovered`). Duplicate messages run a job once (`test_one_active_job_per_document`) | Met |
| Failed jobs are observable and recoverable | `status`, `error_code`, a content-free `error_message`, `GET /jobs?status=failed` and per-job events. Transient errors retry with backoff, then fail; `retry` resumes after completed stages. Deterministic errors fail at once, and rejected uploads cannot be retried. `WORKER_LOST` after the last attempt, recoverable by retry. Lost messages are re-sent (tests) | Met |
| API schemas and error responses are documented | API_SPECIFICATION.md: the Jobs section, modes, pagination, idempotency and 8 new error codes; OpenAPI at `/api/v1/openapi.json` | Met |
| No credentials or document contents are exposed in ordinary logs | `SecretStr` URLs logged as host/port/database only; `hide_parameters`; content-free job errors. A log test checks the database password, document text and token. The real worker's log contains neither password | Met |

Tasks:
- the schema and migrations (upgrade, downgrade and re-upgrade run; a test compares the
  migration with the models);
- entities for documents, pages, jobs, extraction, review and audit;
- upload, status, results and review endpoints;
- Celery workers on Valkey;
- retry limits, idempotency keys and failure handling;
- cancellation and bounded concurrency (admission limits, worker concurrency);
- duplicate submissions (key replay, content dedupe, one active job per document);
- pagination and access control (tests);
- private database and queue ports (an internal network with no published ports).

Open:
- the PostgreSQL 17.11 update (OD-40);
- TLS if the services leave the host (OD-41);
- backups and retention (OD-42);
- libpq on hosts (OD-44).

## Phase 10: React frontend (**complete**, 2026-10-23)

Defined by the owner on 2026-10-23. The owner's choices:
- password sign-in with server-side sessions;
- a minimal React stack with no component library;
- the build made at staging and shipped in the wheel.

Delivered (ADR-0030):
- `frontend/`: React 19, TypeScript and Vite, with Vitest tests;
- `newgen_ocr/auth/users.py`, holding users, sessions and CSRF;
- the sign-in, admin and search routes;
- migration 0002;
- `newgen_ocr/api/routes/webapp.py`, which serves `/app` under a strict CSP;
- `make frontend`, `make frontend-test` and `scripts/check_npm_licenses.py`.

| Feature | Where |
|---|---|
| Login and authorised access | Sign-in page; password sessions; role-aware navigation (API-enforced) |
| Upload and batch submission | Upload: several files at once, three in parallel, progress per file, idempotency key per file, duplicate notice |
| Processing status and progress | Documents list and document page: stage progress refreshed while active, job history, cancel and retry |
| Document and extraction-result lists | Documents, Results (paginated) |
| Page preview and extracted text | Document → Pages and text: page image, OCR lines with confidence (low ones highlighted), quality flags |
| Table and structured-field display | Document → Extracted data: fields with status, confidence and source text; tables; checks; validation |
| Source-linked verification and correction | Review item: crop beside each value, page with the evidence highlighted, gate results, corrections, approve and reject |
| Search and filtering | Documents (filename or id, status, submitter, dates), Results (any extracted value, type, result, review status), Review (view, reason), Audit (event, actor, document, date) |
| JSON, CSV and Excel export | Document → Extracted data → JSON / CSV / Excel |
| Administrative monitoring and audit views | Admin: overview (queue, workers, running jobs, failures, sessions), audit log, users |

| Acceptance criterion | Evidence | Status |
|---|---|---|
| The frontend uses the internal API only | The client calls relative `/api/v1/…` paths on its own origin. A unit test checks every call goes to `/api/v1/`. The CSP sets `connect-src 'self'` | Met |
| No external CDN, analytics or hosted AI services | No such dependency (6 shipped packages: React and React Router). The built `index.html` loads only `/app/assets/…`; a test checks for no external URL. CSP `default-src 'none'`; system fonts | Met |
| Error and review states are clear | Every error shows the API's message and request reference. Statuses are badges with words, not colour alone. Failed jobs show the code and a retry; rejected uploads say why; review items show reasons, gate results, unresolved locations and conflicts (reloaded on a stale version). Pages say when nothing is there yet | Met |
| Access-control rules are enforced by the backend | `test_access_rules_are_enforced_by_the_api`: as each role, other users' documents and jobs return `404`, and review, admin, user creation and job control return `403`. CSRF, session expiry, and disabling or changing a user are tested on the API. The UI only hides what the API refuses | Met |

End to end on the live stack (API, worker, PostgreSQL, Valkey, real models), driven the way
the application calls the API:
- cookie sign-in, then upload (`202`); the pipeline finished in 19 s;
- the invoice classified, with the correct grand total;
- page image, and JSON, CSV and Excel exports;
- both searches; a review crop and claim; a change without CSRF refused;
- the admin overview and audit; logout.

Open:
- multi-factor sign-in or SSO (OD-45);
- the web application needs service mode (OD-46);
- no automated real-browser test (OD-47).

## Phase 11: Security and open-source compliance (**complete**, 2026-10-24)

Defined by the owner on 2026-10-24. The owner's choices:
- commit Phase 10 first;
- nginx for TLS;
- Trivy for image scanning;
- the minimal OpenCV built from source in the image;
- security overrides for nginx 1.30.6, filelock, setuptools and (dev-only) undici.

Delivered (ADR-0031):
- `deploy/Dockerfile`: a hardened image with the minimal OpenCV and verified models;
- `deploy/compose.yaml`: the full stack on internal networks, behind an nginx TLS ingress;
- `deploy/nginx/nginx.conf`, `deploy/egress/nftables-egress.nft` and `deploy/ops/` (backup,
  restore);
- `newgen_ocr/ops.py` (`verify`, `retention`) and `newgen_ocr/healthcheck.py`;
- `scripts/offline_test.py` and `scripts/build_opencv_wheel.py`;
- `fetch_models.py --verify`;
- Makefile targets: `images`, `image-scan`, `stack-up`, `certs`, `backup`, `restore`,
  `offline-test`;
- THREAT_MODEL.md and SECURITY_REVIEW.md.

| Task | Where |
|---|---|
| Threat model | THREAT_MODEL.md (data flows, trust boundaries, STRIDE per element) |
| Authentication and authorisation | Phases 8 to 10 (tokens, sessions, roles, ownership, CSRF), reviewed in SECURITY_REVIEW.md §7 |
| Upload limits and safe temporary files | nginx body and time limits, streaming; the API's limits; `TMPDIR` on the volume (0700, tested) |
| TLS at the internal ingress | nginx: TLS 1.2/1.3, HSTS, per-address limits (the offline test checks the protocols) |
| Secrets and file permissions | Secret files (`NGOCR_SECRETS_DIR`, tested), a 0700 directory, read-only `/opt`, non-root users |
| Static analysis and dependency scans | Bandit, Ruff, mypy, pip-audit, npm audit, Trivy (image and repository) |
| Leaked-secret scan | Gitleaks on history and working tree (`.gitleaks.toml`) |
| SBOM | Python (CycloneDX 1.4, 97 components) and image (CycloneDX 1.7, 268 components) |
| Software and model licence inventory | THIRD_PARTY_NOTICES.md and MODEL_INVENTORY.md; licence checks for Python and npm; Trivy licence scan |
| Model weights and dataset terms, individually | MODEL_INVENTORY.md "Individual terms review" (20 models at their pinned revisions) |
| Container hardening and resource limits | compose: non-root (API and workers hold no capabilities), read-only roots, `no-new-privileges`, memory, CPU and process limits (inspected on the live stack) |
| Audit logs, retention, backup and restore tests | Audit events including `document.deleted`; `ops retention` (tested); the backup and restore drill (SECURITY_REVIEW.md §6) |
| Production egress restrictions | Internal container networks (tested); host nftables template |
| Telemetry and external calls disabled | Tests from earlier phases plus the offline test; CSP in the browser |

| Acceptance criterion | Evidence | Status |
|---|---|---|
| Security scans and manual reviews are documented | SECURITY_REVIEW.md: every scan with its result, the 17 reviewed suppressions, and the licence, model and manual reviews | Met |
| No unapproved dependency or model is shipped | Hash-pinned locks installed with `--require-hashes`, without build isolation; the unused base pip and setuptools removed; models verified, with unlisted files refused (tested); licence checks pass; Trivy finds no fixable HIGH or CRITICAL | Met |
| No external AI endpoint is required | No AI or telemetry SDKs (test); rules and templates only (ADR-0026); the offline test processes a document with every outbound route blocked | Met |
| Production processing passes an offline network test | `make offline-test` passed: every probe blocked, production mode, the TLS checks, and a job that extracted correct values in 18.1 s (SECURITY_REVIEW.md §5) | Met |
| Backup and restore procedures are verified | A full-loss drill: the restored state is identical (counts, fingerprint, history chain); triggers, sign-in and results work afterwards (SECURITY_REVIEW.md §6) | Met |

Open:
- OD-12, OD-41, OD-45, and OD-47 to OD-50;
- removing the dated overrides when the age floor passes them;
- a real-browser check on the deployed stack.

## Next (not yet defined by the owner)

Carried forward from the earlier deployment plan, and from the open decisions:
- readiness that also checks the OCR and layout models;
- throughput benchmarks on the reference CPU server (OD-19);
- real-document and real-handwriting evaluation sets (OD-21, OD-23, OD-28, OD-33);
- MFA or SSO (OD-45);
- a per-document parser sandbox (OD-12).
