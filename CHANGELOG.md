# Changelog

All material changes by phase. Dates are ISO 8601.

## [Unreleased]

### Phase 13: Accuracy, performance and release readiness (2026-10-24)
- **Cohorts** (`evaluation/cohorts.json`, ADR-0033). Every document set has a role (tuning or evaluation) and a
  kind (synthetic or real), and lists its languages, document types and conditions. The four development sets
  are registered as tuning. There are 15 synthetic evaluation cohorts with fresh seeds, unseen fonts and random
  compositions:
  - clean printed text and clean printed documents;
  - poor scans, of text and of documents;
  - tables;
  - each printed language;
  - mixed language;
  - English and Devanagari handwriting.

  Real cohorts are folders under `evaluation/real/` (git-ignored, with a documented layout).
- **Separation of tuning and evaluation:** documents are checked by file hash and by truth-text hash, seeds may
  not be reused, and `calibrate_review.py` refuses evaluation folders.
- **Release evaluation** (`make evaluate`, `scripts/evaluate_release.py`). Metrics:
  - CER and WER (micro);
  - field and document exact match, and invented values;
  - table structure;
  - auto-accept precision with its coverage, and the manual-review rate;
  - per-page latency (p50 and p95), pages per minute, CPU seconds per page and peak memory;
  - failures.

  Every proportion has its count and a 95% Wilson interval. Each report states its size, languages, document
  types, conditions, data kind, independence and environment, with slices by language, condition and type.
- **Service benchmark** (`ngocrctl benchmark`): unique jobs through the TLS ingress from the bundle's fixed
  document mix. It reports pages per minute, latency, queue delay, processing time and failure rate, samples
  container CPU and memory, and records the host's identity.
- **Targets** (`evaluation/targets.json`), each one metric on one cohort:
  - provisional 99% targets for auto-accept precision, field exact match (clean printed) and character accuracy
    (clean printed, per language), binding once real data is measured;
  - throughput and failure rate, from the reference server only;
  - handwriting routed to review;
  - no evaluation failures.
- **Release gates** (`make release-gates`). Production approval needs:
  - the staging evidence for this image and the install rehearsal;
  - separation, freshness and complete reporting;
  - every target.
- Synthetic pages fail loudly if a font lacks a glyph, which caught English lines rendered as boxes in
  mixed-script pages. `render_page` takes a font per line. Version 0.13.0.

### Phase 12: Offline installation bundle (2026-10-24)
- **Signed offline bundle** (`make bundle`, `scripts/build_bundle.py`; ADR-0032). It contains:
  - the application, PostgreSQL, Valkey and nginx images, each with its content ID;
  - all 97 locked wheels, with the minimal OpenCV in place of PyPI's, plus the application wheel;
  - the models and their lock;
  - the migrations, configuration templates, licence notices, both SBOMs and the gate evidence;
  - `ngocrctl` and its guide.
  `SHA256SUMS` covers every file and is signed with OpenSSH Ed25519, and so is the deterministic outer tar.
  The builder refuses to package an image the gates did not pass for: the Trivy, SBOM and offline-test
  reports must name its content ID. It also checks every wheel against the lock.
- **Connected staging workflow:** `make staging` runs fetch, verify, lint, types, licences, audits, Gitleaks,
  tests (JUnit), the image build, Trivy, the SBOMs, the offline test, then the bundle. `make signing-key`
  creates the release key outside the repository.
- **Disconnected production workflow:** `ngocrctl`, POSIX sh needing only a container engine with Compose
  v2, OpenSSH and coreutils. Its commands are `verify`, `preflight`, `install` (images checked by content
  ID, models re-hashed inside the image, site configuration and secrets generated on the host), `smoke`,
  `status`, `backup`, `restore`, `rollback`, `verify-installed`, `create-admin` and `prune`. Nothing is
  ever pulled (`pull_policy: never` and local tags).
- **Smoke tests:**
  - every enabled model and an invoice end to end, in a container with no network;
  - the egress probe;
  - a job over the TLS ingress with a short-lived token;
  - `--restart`: the image store unchanged, the models unchanged, no download attempts in the logs.
- **Upgrade and rollback.** An upgrade takes a backup first and refuses an older schema. Rollback restores
  the pre-upgrade backup when the schemas differ, then checks the data and runs the smoke tests.
- **Install rehearsal** (`make rehearse-install`): the production workflow on a simulated host with no
  network at all, an empty image store and its own cgroups. Unprivileged: no root needed.
- Compose reads secrets from `NGOCR_SECRETS_PATH` (default `deploy/secrets/`). The offline test shares the
  smoke test's egress probe and records the image's content ID. `pyproject.toml` now reads the version
  from the package. Version 0.12.0.

### Phase 11: Security and open-source compliance (2026-10-24)
- **Production image** (`deploy/Dockerfile`, `make images`). Ubuntu 24.04 and uv's Python 3.11, pinned by
  digest. Packages from the hash-pinned lock, with no unpinned build tools. The models are verified, and
  unlisted files are refused. It runs as uid 10001 with a read-only `/opt`, in production mode, with
  temporary uploads on the data volume.
- **Minimal OpenCV built from source** (ADR-0015, ADR-0031). The PyPI source archive is checked against
  its published SHA-256 and built with core, imgproc, imgcodecs and photo only. It has no FFmpeg, video
  I/O or OpenSSL, links statically, and is packaged as `opencv-python-headless 4.14.0.94+ngocr.minimal`.
  It has its own build stage.
- **Compose stack** (`make stack-up`): migrations, API, workers and an nginx TLS ingress.
  - Internal-only networks for everything except the ingress.
  - On every service: `no-new-privileges`, all capabilities dropped, read-only roots, tmpfs mounts,
    memory, CPU and process limits, rotated logs.
  - Secrets come from files (`NGOCR_SECRETS_DIR`).
- **TLS ingress:** TLS 1.2 and 1.3, Mozilla-intermediate ciphers, HSTS, no session tickets, body and time
  limits, and per-address request, connection and sign-in limits. Logs carry no query strings. `make
  certs` creates a development CA.
- **Egress:** `make offline-test` runs the production stack, checks that every outbound probe fails from
  the API and workers, and completes a job over TLS. A host nftables template is provided.
- **Backups:** `make backup` and `make restore` (pg_dump plus the document volume, SHA-256 sums).
  `python -m newgen_ocr.ops verify` checks the counts, a fingerprint, the review history chain and the
  document files.
- **Retention:** `python -m newgen_ocr.ops retention` (dry run by default; `NGOCR_RETENTION_*`).
  Deletions are audited as `document.deleted`.
- **Scanning:** `make image-scan` runs Trivy on the image (vulnerabilities, secrets, licences) and
  produces a CycloneDX image SBOM. Gitleaks now has a config and is pinned by digest.
- **Docs:** THREAT_MODEL.md (STRIDE by element), SECURITY_REVIEW.md (scan and manual review results),
  and an individual terms review of every model at its pinned revision.
- **Security overrides (ADR-0007, approved by the owner):**
  - nginx 1.30.6 (5 CVEs);
  - filelock 4.2.2 and setuptools 84.2.0, for new advisories. None is reachable here; both upgraded
    anyway.
- Version 0.11.0.

### Phase 10: React frontend (2026-10-23)
- **Web application** at `/app` (React 19, TypeScript, Vite). It covers:
  - sign-in;
  - document list with search (filename or id, status, submitter, dates) and live
    processing progress;
  - batch upload, with per-file progress and an idempotency key per file;
  - a document page with job stages, history, cancel and retry, page preview with the OCR
    text and low-confidence lines, extracted fields with source text, tables, checks,
    validation, JSON/CSV/Excel export and re-processing;
  - result search, including by extracted value;
  - the review queue, and review items with source crops next to values, the page with
    evidence highlighted, corrections, approval and rejection;
  - administration: overview, audit log and users;
  - an account page for changing your password.
  It replaces the Phase 8 `/review` page, which now redirects.
- **Password sign-in** (ADR-0030):
  - scrypt hashes with lockout;
  - server-side sessions in PostgreSQL, behind HttpOnly, SameSite=Strict, Secure cookies,
    with idle and absolute timeouts;
  - CSRF tokens;
  - role and status re-read on every request.
  New commands: `python -m newgen_ocr.auth user-create|user-list|user-password|user-disable|user-enable`.
  Migration 0002 adds users, sessions and document filenames.
- **New endpoints:**
  - `POST /api/v1/auth/login|logout|password`, `GET /api/v1/auth/session`;
  - `GET /api/v1/extractions`;
  - `GET /api/v1/admin/overview|audit|users`, `POST /api/v1/admin/users`,
    `PATCH /api/v1/admin/users/{name}`.
  Document search filters are new. So are six error codes: `LOGIN_FAILED`, `CSRF_FAILED`,
  `SIGN_IN_UNAVAILABLE`, `WEAK_PASSWORD`, `USER_EXISTS`, `USER_NOT_FOUND`.
- **Only the internal API, nothing external.** The app is served from the same origin under
  a strict CSP (`default-src 'none'`, `'self'` only). It loads no CDN, fonts, analytics or
  AI service. Access rules stay in the API and are tested there.
- **Frontend supply chain:**
  - 141 npm packages pinned with integrity hashes, all at least 14 days old, with install
    scripts disabled;
  - an npm licence check and `npm audit` in the gate;
  - 6 shipped packages, all MIT;
  - built at staging (`make frontend`); production never runs Node.
- **Tests:** 12 API tests (sign-in, CSRF, sessions, access rules, search, admin and the
  served application) and 14 frontend unit tests. Version 0.10.0.

### Phase 9: Database, background jobs and APIs (2026-10-23)
- **Service mode** (ADR-0029), used when `NGOCR_DATABASE_URL` and `NGOCR_BROKER_URL` are
  set; staging and production require it. Local mode is unchanged, for development.
- **PostgreSQL 17** schema, managed only by Alembic migrations (`python -m newgen_ocr.db
  upgrade|downgrade|current`). It holds:
  - API tokens;
  - documents and pages;
  - jobs and job events;
  - extraction results (JSONB);
  - review items, revisions and the hash-chained history;
  - audit events.
  The history and audit tables are append-only (trigger). Files stay on the data volume.
- **Background jobs** with Celery on Valkey (`python -m newgen_ocr.worker`):
  - Uploads return `202` with a job that runs ingest, preprocessing, OCR, layout,
    extraction and review queueing. Stage endpoints queue `ocr`, `layout`, `extraction`
    or `preprocessing` jobs, which also run the stages after them.
  - Atomic claims and heartbeat-renewed leases; late acknowledgement and requeue when a
    worker dies.
  - A reaper in every worker requeues jobs from dead workers and re-sends lost messages.
  - Completed stages are skipped on retry.
  - Deterministic errors fail at once; unexpected errors retry with backoff up to
    `NGOCR_JOB_MAX_ATTEMPTS`, then fail with the code recorded.
  - Cancellation between stages; retry of failed or cancelled jobs; a per-job event log.
- **Duplicates.** `Idempotency-Key` replay (with request fingerprints), per-user content
  dedupe (`force` to override), and one active job per document (unique index).
- **Bounded work.** Queue and per-user admission limits (`503 QUEUE_FULL`,
  `429 TOO_MANY_JOBS`); worker concurrency setting; prefetch of one.
- **Access.**
  - Bearer tokens on every endpoint except health and the static review page.
  - Tokens are stored in PostgreSQL in service mode.
  - New `submitter` role that reaches only its own documents and jobs.
  - Every audit event names the authenticated user, including actions workers take for
    them.
- **New endpoints:**
  - `GET /api/v1/documents` (paginated), `GET /api/v1/documents/{id}/status`;
  - `POST /api/v1/documents/{id}/jobs`;
  - `GET /api/v1/jobs`, `GET /api/v1/jobs/{id}`, `GET /api/v1/jobs/{id}/events`;
  - `POST /api/v1/jobs/{id}/cancel`, `POST /api/v1/jobs/{id}/retry`.
  New error codes: `JOB_NOT_FOUND`, `JOB_CONFLICT`, `JOB_ALREADY_ACTIVE`,
  `IDEMPOTENCY_CONFLICT`, `QUEUE_FULL`, `TOO_MANY_JOBS`, `WORKER_LOST`,
  `DOCUMENT_NOT_READY`. Readiness checks the database, schema version and broker.
- **`deploy/compose.yaml`.** PostgreSQL 17.10 and Valkey 8.1.10, pinned by digest, on an
  internal network with no published ports and passwords from secret files.
  `deploy/compose.dev.yaml` publishes them on 127.0.0.1 for development.
  New Makefile targets: `make services-up` and `make services-down`.
- **Logs.** Database and broker URLs are secrets; bound SQL values are hidden; job errors
  never hold exception text.
- **New dependencies:** SQLAlchemy, Alembic, psycopg (with the OS libpq) and Celery with
  redis-py; 18 packages in all, licence-reviewed (THIRD_PARTY_NOTICES.md).
- **Tests:** service-mode tests run on real PostgreSQL and Valkey, including a test that
  kills a real worker mid-job. Version 0.9.0.

### Phase 8: Confidence, verification and human review (2026-10-23)
- `newgen_ocr/review/`: field-specific verification gates. A value is verified only when
  it was extracted, the type is reliable, its page is clean, no check involving it failed,
  the second OCR engine does not read a different value there, and its confidence reaches
  a threshold calibrated for that field. OCR confidence alone never verifies a value.
- Calibration: `scripts/calibrate_review.py` (`make calibrate`) fits per-field thresholds
  (one-sided Wilson lower bound, type pooling with a per-field guard) on a calibration
  split and measures them on a held-out test split. Sources: a seeded synthetic set
  (`evaluation/calibration_corpus.py`), real labelled documents, or approved reviews.
  Calibration files are hash-identified and bound to schema versions.
- Review queue with priorities; claim/release; corrections (`set`/`clear`/`confirm`) with
  reason codes, normalised and validated like extracted values; approval with four eyes
  and acknowledged failing checks; rejection with reason codes; optimistic concurrency;
  reviewed results re-validated and re-checked.
- Hash-chained review history (actor, role, time, values, reason, comment) with a verify
  endpoint; review steps audited without content; correction patterns (per field, reason,
  type, character confusions, verified-but-corrected).
- Source crops and page previews from the exact OCR image, with Aadhaar numbers blacked
  out; a minimal review page at `/review` (no external assets, strict CSP).
- `newgen_ocr/auth/`: local hashed bearer tokens with roles (viewer, reviewer, approver,
  admin), expiry and revocation; `python -m newgen_ocr.auth`. Review endpoints require them.
- Automatic acceptance off by default; production refuses a synthetic calibration.
- No new dependencies. Implementation plan renumbered: persistence moves to Phase 9, the
  full review application to Phase 10, deployment to Phase 11.

### Phase 7: Structured data extraction without an LLM (2026-10-23)
- `newgen_ocr/extraction/`: rule and template classification; configurable JSON field
  schemas (built-in: GST invoice, receipt, bank statement, PAN card, Aadhaar card; custom via
  `NGOCR_EXTRACTION_SCHEMA_DIR`); candidates from labels, section blocks, label table rows,
  identifier formats and template regions; explicit statuses (`extracted`, `missing`,
  `ambiguous`, `conflict`, `invalid`). Nothing is invented.
- Normalisation of dates (day-first policy), amounts (Indian grouping, exact decimals),
  GSTIN (mod-36), PAN, Aadhaar (Verhoeff; masked by default), IFSC, PIN, phone, addresses,
  and digits in Indian scripts.
- Table-to-record conversion with header mapping, summary rows and per-cell evidence;
  Pydantic validation with explicit issues; 11 kinds of consistency check (arithmetic,
  GST tax type, balances, running balance, dates).
- Evidence on every value (page, source, OCR line indices, box, confidence); native PDF
  text pages supported.
- `POST/GET …/extraction`, `…/export?format=json|csv|xlsx`, `GET /api/v1/extraction/schemas`;
  CSV/XLSX formula-injection protection; exports audited.
- New dependency: openpyxl 3.1.5 (+ et-xmlfile), MIT.
- Generated JSON Schemas (`docs/schemas/`) and type reference, checked by the tests;
  synthetic extraction set and `scripts/evaluate_extraction.py`.
- Implementation plan renumbered: persistence and jobs move to Phase 8.

### Phase 6: Layout, tables and document structure (2026-10-23)
- Layout stage over a stored OCR run (`POST/GET /api/v1/documents/{id}/layout`,
  `GET /api/v1/layout/status`): typed regions (title, text, header, footer, page number,
  table, figure, formula, …), XY-cut reading order for multi-column pages, column count,
  page text in reading order. Every OCR line is kept; text outside regions is `unclassified`.
- Tables: PP-LCNet wired/wireless classifier + SLANet_plus structure (rows, columns,
  rowspan/colspan, header rows), grid + HTML output, cell text from OCR lines with line links.
  Wired tables are verified against traced ruling lines (exact cell boxes when verified);
  wireless tables are checked against the OCR lines; cells are re-read from crops when a line
  spans cells or page OCR missed them; invalid structures are `unsupported`, never repaired.
- Evidence-only key-value pairs ("Key: value" lines, two-column table rows).
- Inspection overlay PNG; 9 layout review reasons; `unsupported` structures list.
- New pinned models: `PP-DocLayout_plus-L` (default), `PP-DocLayout-M`,
  `PP-LCNet_x1_0_table_cls`, `SLANet_plus`. The fetch script now cross-checks Hugging Face
  config files against their git-blob hashes (all Paddle models re-verified).
- Synthetic layout test set and `scripts/evaluate_layout.py` (`make eval-layout`).
- **Fixed:** Paddle predictors (oneDNN) failed intermittently, and once silently degraded OCR,
  when the API called them from different worker threads (Phases 4–5 affected). All Paddle
  calls now run on one dedicated thread per process.
- Implementation plan renumbered: persistence and jobs move to Phase 7.

### Phase 5: Language and handwriting support (2026-10-23)
- Supported-language matrix (`ocr/languages.py`, `GET /api/v1/languages`,
  docs/LANGUAGE_SUPPORT.md); PaddleOCR disabled for Kannada by measurement.
- Script and orientation identification: Tesseract OSD (orientation) + a recognition census
  (script), per-page routing with `languages: ["auto"]`, Indian-script + English mixed pages
  routed to the Indian-script recogniser; Hindi/Marathi identification from text.
- Review routing: `unsupported` page status and 13 machine-readable review reasons
  (unsupported/uncertain script, mismatch, handwriting, low confidence, low quality, …).
- Experimental English handwriting engine (`paddle_handwriting`, `PP-OCRv5_server_rec`),
  always reviewed; Indian-script handwriting unsupported (no clean-provenance model).
- New pinned models: `PP-OCRv5_server_rec`, `tessdata_best/osd`; evaluation-only OFL fonts
  (`evaluation/fonts.lock.json`, `make fonts`); fetch script supports any lock file.
- Evaluations: `scripts/evaluate_language_id.py`, `scripts/evaluate_handwriting.py`
  (+ real-sample harness, `evaluation/handwriting/README.md`); `make eval-languages`,
  `make eval-handwriting`.
- Fixed an import cycle (schemas consolidated in `ocr/models.py`).
- Implementation plan renumbered: persistence and jobs move to Phase 6.

### Phase 4: Primary OCR and engine adapters (2026-10-22)
- `OcrEngine` protocol and engine-independent OCR schema (text, confidence, bbox, polygon,
  words, orientation); runs record the engine version and exact model revisions and hashes.
- PaddleOCR 3.7 adapter (PaddlePaddle 3.2.2, oneDNN) with an offline lockdown: model manager
  disabled, explicit model dirs, offline flags, version-pinned patches (ADR-0020).
- Tesseract 5 CLI adapter with `tessdata_best` and an explicit `--tessdata-dir`.
- `models.lock.json` (14 models pinned by revision and SHA-256), `scripts/fetch_models.py`
  with independent publisher-hash cross-checks, and runtime integrity verification (ADR-0021).
- Text-line orientation model enabled for English only (measured to garble upright Indic
  lines); upside-down English pages are detected and their lines ordered correctly.
- OCR service: native/blank pages skipped, preprocessed image used when available,
  primary→fallback per page, comparison with review flag, never merging (ADR-0022).
- API: `GET /api/v1/ocr/engines`, `POST/GET /api/v1/documents/{id}/ocr[/{engine}|/comparison]`.
- Evaluation: CER/WER metrics, a synthetic multilingual corpus (en/hi/mr/te/ta/kn; Noto fonts,
  raqm shaping; clean/scan/low-res), `scripts/evaluate_ocr.py`, `make eval`, `make models`.
- License checker parses SPDX expressions; weak-copyleft packages reviewed; uv overrides drop
  `opencv-contrib-python` and `crc32c`.
- Tests: offline OCR in a network namespace, audit-hook proof of zero network attempts,
  model tamper and missing-model tests, fake-engine policy tests, real-engine accuracy smoke
  tests (`models` / `tesseract` markers).
- Per-language primary engine (`NGOCR_OCR_LANGUAGE_PRIMARY`); Kannada → Tesseract by
  measurement. Tesseract TSV set via parameter (not an OS config file); non-TSV output is a
  failure; word-final zero-width joiners removed.
- Implementation plan renumbered: persistence and jobs move to Phase 5.

### Phase 3: Image preprocessing and quality assessment (2026-10-22)
- Quality assessment for every page image, recorded in the manifest: blank, low/unknown
  resolution, blur, poor contrast, Gaussian and salt-and-pepper noise, uneven background,
  skew and rotation, each with its metric.
- Text-axis (90°) and skew detection by projection profiles (±0.15° on synthetic pages).
- Preprocessing profiles `none`, `standard` (flag-driven), `degraded_scan`, `photo`,
  `binarize`, plus custom profiles via environment; steps for orientation, deskew,
  illumination flattening, denoise, contrast (stretch + CLAHE) and adaptive threshold.
- Per-profile runs stored beside immutable source pages; every step's decision, parameters
  and timing recorded; re-run endpoint; `preprocessing.completed/failed` audit events.
- API: `profile` field on upload; `/api/v1/preprocessing/profiles`;
  `/api/v1/documents/{id}/preprocessing[/{profile}[/pages/{n}/image]]`.
- OpenCV 4.14 (PyPI, development only) and NumPy 2.4.6. Production startup guard rejects
  FFmpeg-enabled OpenCV builds (ADR-0015).
- CPU benchmark script, `make bench` and docs/PERFORMANCE.md. Measured optimisations:
  end-to-end 1.9 → 1.2 s/page (PNG level 3, low-resolution background estimation).
- `pip-audit` runs with `--disable-pip` (no package downloads).
- Test PDF builder declares greyscale JPEGs correctly.
- Implementation plan renumbered: persistence and jobs move to Phase 4.

### Phase 2: Document ingestion and PDF processing (2026-10-22)
- `POST /api/v1/documents`, `GET /api/v1/documents/{id}`, `.../pages/{n}/text` and
  `.../pages/{n}/image`.
- Signature-based detection of PDF/JPEG/PNG/TIFF; extension cross-check; size, page and pixel
  limits; streaming request-body limit; corrupt, truncated and encrypted file handling with
  new stable error codes.
- Per-page native-text detection for PDFs (native pages are not rendered); embedded text with
  segment bounding boxes; rendering of scanned pages at a configurable DPI.
- Image normalisation (EXIF orientation, alpha on white, 16-bit scaling); multi-frame TIFF.
- Local document store: quarantine → staging → atomic commit; originals separate from derived.
- JSONL audit trail for accepted and rejected documents.
- `Cache-Control: no-store` and `X-Content-Type-Options: nosniff` on all responses.
- FastAPI built-in telemetry explicitly disabled, with a policy test.
- Release-age rule enforced in `make lock` via `--exclude-newer`. Locks re-resolved
  (FastAPI 0.143.0, Starlette 1.7.0). Security overrides: Pillow 13.0.0, certifi.
- New runtime dependencies: pypdfium2 5.14.0, Pillow 13.0.0, python-multipart 0.0.32.
- Error handling split: framework-free `newgen_ocr.errors`, HTTP mapping in `newgen_ocr.api.errors`.
- Implementation plan renumbered: persistence and jobs move to Phase 3.

### Phase 1: Development foundation (2026-10-22)
- Python 3.11 project (`pyproject.toml`) with Ruff, strict mypy, pytest and a 90% coverage gate.
- Hash-pinned `requirements.lock` / `requirements-dev.lock` generated from `requirements*.in`.
- FastAPI app factory, `NGOCR_*` settings, JSON structured logging with request ids, stable
  error envelope.
- `/health/live` and `/health/ready` endpoints.
- Policy tests: no hosted-AI or telemetry SDKs, no network-client imports, CDN docs UI disabled.
- Tooling: Makefile, license allowlist script, Bandit, pip-audit, Gitleaks, CycloneDX SBOM,
  GitHub Actions CI.
- `.env.example`; `.gitignore` extended for `data/`, caches and reports.

### Phase 0: Planning (2026-10-22)
- Repository and host inspection; architecture, implementation plan, decisions, security,
  testing, evaluation, model inventory, offline deployment and runbook documents.
