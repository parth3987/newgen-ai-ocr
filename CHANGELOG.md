# Changelog

All material changes by phase. Dates are ISO 8601.

## [Unreleased]

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
