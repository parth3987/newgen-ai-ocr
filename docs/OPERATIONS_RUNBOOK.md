# Operations Runbook

Status: development stage. Only the API process exists. Compose services, backups and
recovery are added in Phase 8 and Phase 10.

## Start / stop (development)

```bash
make venv          # once
cp .env.example .env   # optional; adjust values
make run           # http://127.0.0.1:8000, Ctrl+C to stop
```

Equivalent: `uvicorn newgen_ocr.api.app:create_app --factory --host 127.0.0.1 --port 8000 --no-server-header`.

## Configuration

All variables use the `NGOCR_` prefix. See `.env.example`.

| Variable | Default | Purpose |
|---|---|---|
| `NGOCR_DATA_DIR` | `./data` | Storage root (must exist and be writable) |
| `NGOCR_ALLOWED_FORMATS` | all | JSON list, a subset of `["pdf","jpeg","png","tiff"]` |
| `NGOCR_MAX_UPLOAD_BYTES` | 52428800 | Per-file upload limit |
| `NGOCR_MAX_PAGES` | 200 | Pages per PDF / frames per TIFF |
| `NGOCR_MAX_PAGE_PIXELS` | 80000000 | Decoded pixels per page (images; PDF pages at render DPI) |
| `NGOCR_RENDER_DPI` | 300 | DPI for rendering PDF pages that need OCR (72–600) |
| `NGOCR_NATIVE_TEXT_MIN_CHARS` | 20 | Minimum text-layer characters for a page to skip OCR |
| `NGOCR_DEFAULT_PREPROCESSING_PROFILE` | `standard` | Profile run after each upload (`none` disables) |
| `NGOCR_CUSTOM_PREPROCESSING_PROFILES` | `[]` | JSON list of extra profiles (see API_SPECIFICATION.md) |
| `NGOCR_OPENCV_THREADS` | OpenCV default | Threads per process; use `1` with multiple worker processes |
| `NGOCR_MODELS_DIR` / `NGOCR_MODELS_LOCK` | `./models` / `./models.lock.json` | Local OCR models and their pinned hashes |
| `NGOCR_OCR_PRIMARY_ENGINE` / `NGOCR_OCR_FALLBACK_ENGINE` | `paddle` / `tesseract` | Engine policy (ADR-0022); fallback may be null |
| `NGOCR_OCR_DEFAULT_LANGUAGES` | `["en"]` | Used when a request gives no languages |
| `NGOCR_OCR_LANGUAGE_PRIMARY` | `{"kn": "tesseract"}` | Per-language primary engine (engines swap roles) |
| `NGOCR_PADDLE_DETECTION_MODEL` | `PP-OCRv5_mobile_det` | Or `PP-OCRv5_server_det` (slower) |
| `NGOCR_PADDLE_ENABLE_MKLDNN` | `true` | oneDNN CPU acceleration (about 5x; needs PaddlePaddle 3.2.x) |
| `NGOCR_OCR_CPU_THREADS` | engine default | Threads per OCR call |
| `NGOCR_TESSERACT_CMD` | `tesseract` | Binary name or path |
| `NGOCR_OCR_PAGE_TIMEOUT_S` | 300 | Tesseract per-page timeout |
| `NGOCR_OCR_DISAGREEMENT_THRESHOLD` | 0.85 | Engine agreement below this flags a page for review |
| `NGOCR_OCR_DETECT_ORIENTATION` | `true` | OSD orientation correction even with explicit languages (about 1 s/page) |
| `NGOCR_OCR_MIN_PAGE_CONFIDENCE` / `NGOCR_OCR_MIN_LINE_CONFIDENCE` | 0.85 / 0.70 | Review thresholds (heuristic, LANGUAGE_SUPPORT.md) |
| `NGOCR_OCR_HANDWRITING_VERIFIED_LANGUAGES` | `[]` | Languages whose handwriting is verified on real samples; empty = always review |
| `NGOCR_LAYOUT_MODEL` | `PP-DocLayout_plus-L` | Or `PP-DocLayout-M` (about 5x faster, more pages routed to review) |
| `NGOCR_LAYOUT_SCORE_THRESHOLD` | 0.5 | Layout regions scored below this are discarded (their text becomes `unclassified`) |
| `NGOCR_LAYOUT_MIN_TABLE_SCORE` | 0.90 | SLANet structure score below this routes a table to review |
| `NGOCR_LAYOUT_RULING_CHECK` | `true` | Cross-check wired tables against traced ruling lines (ADR-0025); keep on |
| `NGOCR_EXTRACTION_SCHEMA_DIR` | unset | Directory of extra document-type schemas (JSON); validated at startup, ids must not clash with built-ins |
| `NGOCR_EXTRACTION_DAY_FIRST` | `true` | Read numeric dates as DD/MM/YYYY; `false` reports dates valid both ways as ambiguous |
| `NGOCR_EXTRACTION_MASK_AADHAAR` | `true` | Mask Aadhaar numbers to the last 4 digits in results and exports. Turn off only with a documented legal basis |
| `NGOCR_EXTRACTION_MIN_EVIDENCE_CONFIDENCE` | 0.70 | Extracted values read from OCR below this confidence are routed to review |

**Production startup fails** if the OpenCV build includes FFmpeg (the PyPI wheel) and
`NGOCR_ENVIRONMENT=production`. Install the minimal source-built OpenCV wheel (ADR-0015).

Set `TMPDIR` for the service to a directory on the protected data volume. Uploads over 1 MiB
are spooled there while the request is parsed (OD-15).

## Data layout

```
data/quarantine/   incoming bytes during validation (normally empty)
data/staging/      derived output under construction (normally empty)
data/originals/    accepted originals, read-only (0440)
data/derived/      manifest.json, pages/NNNN.png, text/NNNN.json per document
                   preprocessed/<profile>/run.json + pages/NNNN.png per profile run
                   ocr/<engine>.json, ocr/comparison.json per OCR run
                   layout/<ocr_run>.json per layout run
                   extraction/<layout_run>.json per extraction result
data/audit/        audit.jsonl
```

Files left in `quarantine/` or `staging/` while the service is idle are leftovers from a crash
and can be deleted. A directory in `originals/` with no matching `derived/<id>/manifest.json`
is an orphan from a crash between the two commit steps. Report it before deleting.

## Probes

| Probe | Use for | Healthy |
|---|---|---|
| `GET /health/live` | Process restart decisions | 200 |
| `GET /health/ready` | Load-balancer routing / startup gating | 200; 503 lists failing checks |

## Troubleshooting

| Symptom | Likely cause | Action |
|---|---|---|
| `/health/ready` 503 with `data_dir_writable: false` | `NGOCR_DATA_DIR` missing or not writable by the service user | Create the directory and fix ownership/permissions |
| Startup `ValidationError` mentioning `NGOCR_…` | Invalid environment value | Compare against `.env.example` |
| Need details of a 500 | Errors return only a generic message | Search logs for the response's `X-Request-ID` (`request_id` field) |
| Upload rejected | See the `error.code` in the response | Codes are listed in API_SPECIFICATION.md. The audit log has a matching `document.rejected` event with sha256 |
| `GET /api/v1/ocr/engines` shows an engine unavailable | Models not fetched, or `tesseract` not installed | Run `make models` at staging; `apt install tesseract-ocr` |
| `MODEL_INTEGRITY_ERROR` | A model file was changed or corrupted | Re-copy `models/` from the verified bundle; investigate as a security event |
| First OCR call slow (about 10 s) | Model loading, once per language per process | Expected |
| `GET /api/v1/layout/status` shows `available: false` | Layout/table models not fetched | Run `make models` at staging |
| `LAYOUT_FAILED` on a page | Image no longer matches the OCR run (e.g. the preprocessing run was replaced after OCR), or a model error (`layout model failed` log with `error_type`) | Re-run OCR, then layout; report repeated model errors |
| A layout looks wrong | Inspect `GET .../layout/{ocr_run}/pages/{n}/overlay` (regions, reading order, cells, review flags) | Route to review; collect the page for the real-document test set (OD-27) |
| Startup fails with a schema error | A custom schema in `NGOCR_EXTRACTION_SCHEMA_DIR` is invalid (unknown field/column/section, bad regex, duplicate id) | Fix the JSON; the message names the schema and the problem |
| Extraction `unclassified` | No document type reached its score, or two were too close | See `classification.scores`/`evidence`; pass `document_type` explicitly, or add keywords/a template |
| A value is `ambiguous`/`conflict` | Several candidates or readings; see `candidates` | Review; if systematic, add labels or sections to the schema |
| Intermittent `RuntimeError: std::exception` from Paddle | Paddle called from more than one thread (fixed: ADR-0020 addendum) | Must not recur; report with the request ID |
| Slow uploads under load | Ingestion is synchronous and PDFs are processed one at a time (ADR-0012) | Expected until Phase 8 moves processing to workers |

## Logs

JSON lines on stdout (`NGOCR_LOG_FORMAT=json`). Fields: `ts`, `level`, `logger`, `msg`,
`request_id`, plus event-specific metadata. Logs must not contain document content. Report it
as a security defect if they do.

## Capacity

See docs/PERFORMANCE.md. Measured on a laptop CPU: about 1.2 s/page at 300 DPI for ingest
plus `standard` preprocessing, and about 4–6 MB of storage per scanned page. Plan disk for
tens of GB per day at high volume (OD-16). Leftover `.staging-*` or `.old-*` directories
under `preprocessed/` after a crash can be deleted while the service is idle.

## Backups and recovery

To be defined in Phase 5 (PostgreSQL dumps + `data/` volume snapshots) and drilled in Phase 10.
