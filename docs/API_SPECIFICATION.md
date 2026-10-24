# API Specification

Machine-readable schema: `GET /api/v1/openapi.json`. Interactive docs are intentionally
disabled (DECISIONS.md ADR-0005).

## Conventions

- Business endpoints live under `/api/v1/`. Health probes are unversioned under `/health/`.
- **Request correlation:** clients may send `X-Request-ID` (1–64 characters from
  `[A-Za-z0-9._-]`). Invalid or missing values are replaced by a server-generated 32-character
  hex id. Every response, including errors, carries `X-Request-ID`.
- **Caching:** every response carries `Cache-Control: no-store` and
  `X-Content-Type-Options: nosniff`.
- **Authentication:** every endpoint except `/health/…`, `POST /api/v1/auth/login` and the
  web application (`/app`) requires either `Authorization: Bearer <token>` (integrations;
  ADR-0027, ADR-0029) or the web application's session cookie (ADR-0030). With the cookie,
  every `POST`/`PATCH` must also send the session's CSRF token in `X-CSRF-Token`
  (`403 CSRF_FAILED` otherwise). Roles, lowest first:
  `submitter` (upload; read and re-run **its own** documents and jobs), `viewer` (read
  everything), `reviewer`, `approver`, `admin` (REVIEW.md). A document or job a submitter
  may not see is reported as not found (`404`), never as forbidden.
- **Modes:** in **local mode** (no database or broker configured; development only) uploads
  and stage runs are processed in the request and return `201` with the result. In
  **service mode** (PostgreSQL + Valkey; required in staging and production) they are queued
  and return `202` with the job (see "Jobs"). Read endpoints are the same in both modes.
- **Pagination:** list endpoints take `limit` (1–200, default 50) and `offset` (default 0)
  and return `{"items", "total", "limit", "offset"}`.
- **Idempotency:** `POST` endpoints that create jobs accept an `Idempotency-Key` header
  (1–128 characters from `[A-Za-z0-9._:-]`). Repeating a request with the same key (per
  user) returns the original job with `"duplicate": true`; using the key for a different
  request returns `409 IDEMPOTENCY_CONFLICT`.
- **Request size:** bodies larger than `NGOCR_MAX_UPLOAD_BYTES` + 64 KiB are refused with
  `413 FILE_TOO_LARGE`, either immediately (from Content-Length) or as soon as the streamed
  body exceeds the limit.

## Error envelope

All non-2xx responses (except readiness 503, below) use:

```json
{"error": {"code": "NOT_FOUND", "message": "Not Found", "request_id": "3b47…"}}
```

Messages never contain document content or submitted values.

| Code | HTTP | Meaning |
|---|---|---|
| `NOT_FOUND` | 404 | No such route |
| `METHOD_NOT_ALLOWED` | 405 | Route exists but the method is not supported |
| `VALIDATION_ERROR` | 422 | Request failed validation (e.g. missing `file` field, malformed document id). The message lists field locations only |
| `HTTP_ERROR` | various | Other framework-level HTTP errors |
| `INTERNAL_ERROR` | 500 | Unexpected failure; details are only in server logs (search by `request_id`) |
| `EMPTY_FILE` | 422 | Uploaded file has zero bytes |
| `FILE_TOO_LARGE` | 413 | Upload exceeds `NGOCR_MAX_UPLOAD_BYTES` |
| `UNSUPPORTED_FORMAT` | 415 | Content is not an approved format (PDF, JPEG, PNG, TIFF), judged by file signature |
| `FORMAT_MISMATCH` | 415 | Filename extension contradicts the detected format |
| `CORRUPT_DOCUMENT` | 422 | File is malformed, truncated, has no pages, or cannot be decoded |
| `ENCRYPTED_DOCUMENT` | 422 | PDF requires a password to open |
| `TOO_MANY_PAGES` | 422 | Page/frame count exceeds `NGOCR_MAX_PAGES` |
| `PAGE_TOO_LARGE` | 422 | A page exceeds `NGOCR_MAX_PAGE_PIXELS` (image size, or PDF page at render DPI) |
| `DOCUMENT_NOT_FOUND` | 404 | Unknown document id |
| `PAGE_NOT_FOUND` | 404 | Page number outside `1..page_count` |
| `PAGE_IMAGE_NOT_AVAILABLE` | 404 | Page has no image (native-text PDF pages are not rendered) |
| `UNKNOWN_PROFILE` | 422 | Preprocessing profile name is not defined |
| `PREPROCESSING_NOT_FOUND` | 404 | That profile has not been run for this document |
| `PREPROCESSED_IMAGE_NOT_AVAILABLE` | 404 | The profile did not change this page (or skipped it); use the source page image |
| `UNSUPPORTED_LANGUAGE` | 422 | Language not in `en, hi, mr, te, ta, kn`, or the chosen engine has no model for it |
| `UNKNOWN_ENGINE` | 422 | Engine name is not `auto`, `paddle` or `tesseract` |
| `OCR_ENGINE_UNAVAILABLE` | 503 | The engine (or every engine, for `auto`) cannot run: binary or models missing |
| `MODEL_NOT_AVAILABLE` | 503 | A required model file is not present locally (models are never downloaded at runtime) |
| `MODEL_INTEGRITY_ERROR` | 503 | A model file's SHA-256 does not match `models.lock.json` |
| `OCR_FAILED` | 500 | The engine failed on a page (per-page in runs; see `error_code`) |
| `OCR_TIMEOUT` | 504 | The engine exceeded `NGOCR_OCR_PAGE_TIMEOUT_S` (Tesseract) |
| `OCR_RUN_NOT_FOUND` | 404 | No OCR run (or comparison) with that name for this document |
| `LAYOUT_UNAVAILABLE` | 503 | Layout/table models are not present (fetch them at staging) |
| `LAYOUT_FAILED` | 500 | A layout model failed on a page (recorded per page in a layout run) |
| `LAYOUT_RUN_NOT_FOUND` | 404 | No layout run for that OCR run name |
| `UNKNOWN_DOCUMENT_TYPE` | 422 | No extraction schema with that id |
| `EXTRACTION_NOT_FOUND` | 404 | No extraction result for that layout run |
| `UNSUPPORTED_EXPORT` | 422 | The requested export part does not exist (e.g. unknown table) |
| `UNAUTHENTICATED` | 401 | Missing, malformed, unknown, expired or revoked token (`WWW-Authenticate: Bearer`) |
| `FORBIDDEN` | 403 | The token's role is not sufficient, or the four-eyes rule forbids approving |
| `REVIEW_ITEM_NOT_FOUND` | 404 | No review item for that document and layout run |
| `REVIEW_CONFLICT` | 409 | Stale `expected_version`, item assigned to someone else, not claimed, already decided, or extracted again since |
| `REVIEW_INCOMPLETE` | 422 | Approval refused: undecided values, an invalid corrected record, or failing checks not acknowledged (with a comment) |
| `INVALID_CORRECTION` | 422 | A corrected value fails its field's format or checksum rules, or the location does not exist |
| `EVIDENCE_NOT_FOUND` | 404 | No source evidence for that field, candidate or cell (e.g. a missing value) |
| `JOB_NOT_FOUND` | 404 | Unknown job id, or a job of another user (for submitters) |
| `JOB_CONFLICT` | 409 | The job's state does not allow it: cancelling a finished job, retrying one that is not failed or cancelled, or retrying a rejected upload |
| `JOB_ALREADY_ACTIVE` | 409 | The document already has a queued or running job; wait for it or cancel it |
| `IDEMPOTENCY_CONFLICT` | 409 | The `Idempotency-Key` was already used by this user for a different request |
| `QUEUE_FULL` | 503 | `NGOCR_MAX_QUEUED_JOBS` jobs are already waiting; retry later |
| `TOO_MANY_JOBS` | 429 | The user has `NGOCR_MAX_ACTIVE_JOBS_PER_USER` queued or running jobs; retry later |
| `WORKER_LOST` | 500 | (job `error_code` only) The worker stopped during the last allowed attempt |
| `DOCUMENT_NOT_READY` | 409 | The upload has not been ingested yet (or was rejected); see `…/status` |
| `LOGIN_FAILED` | 401 | Wrong user name or password, a locked or disabled account (one message for all), or a wrong current password |
| `CSRF_FAILED` | 403 | A cookie-authenticated change without the session's `X-CSRF-Token` |
| `SIGN_IN_UNAVAILABLE` | 503 | Web sign-in needs the database (service mode) |
| `WEAK_PASSWORD` | 422 | Fewer than 12 or more than 256 characters, or contains the user name |
| `USER_EXISTS` | 409 | A web user with that name exists |
| `USER_NOT_FOUND` | 404 | No web user with that name |

Pages that cannot be recognised reliably are **not** errors. They come back in the run with
`status: "unsupported"` and/or `review.needs_review: true` (see below and LANGUAGE_SUPPORT.md).

Codes are stable: they are never renamed or reused.

## Health

### `GET /health/live`
`200` → `{"status": "ok", "version": "0.5.0"}`. Performs no dependency checks.

### `GET /health/ready`
`200` → `{"status": "ready", "checks": {"data_dir_writable": true}}`
`503` → `{"status": "not_ready", "checks": {"data_dir_writable": false}}`

In service mode `checks` also has `database` (reachable), `database_schema_current` (at
the latest migration) and `broker` (Valkey reachable). No authentication.

## Documents

### `POST /api/v1/documents`
Role: submitter. `multipart/form-data` with one file part named `file` and an optional text
field `profile` (preprocessing profile; default `NGOCR_DEFAULT_PREPROCESSING_PROFILE`,
normally `standard`). An unknown profile is rejected with `422 UNKNOWN_PROFILE` before
anything is stored. Size, signature and extension are checked in the request in both modes.

**Local mode:** the document is processed in the request (ADR-0012), then the profile is
run best-effort: a preprocessing failure is audited but does not fail the upload.

**Service mode:** the whole pipeline is queued: ingest, preprocessing, OCR, layout,
extraction, then the review queue. Extra form fields: `engine` (`auto`, `paddle`,
`tesseract`), `languages` (comma-separated codes, or `auto`), `handwriting`, `compare`
(default `true`), `document_type` (default `auto`) and `force`. The same file from the same
user returns the earlier document's latest job (`"duplicate": true`) unless `force=true`.
`202` → `JobSubmitted` (see "Jobs"), with `Location: /api/v1/jobs/{id}`.

`201` (local mode) → `DocumentManifest`:

```json
{
  "document_id": "050752513003481ba03691bd2aede68b",
  "created_at": "2026-10-22T07:21:47.890000Z",
  "sha256": "eeca38f8…",
  "size_bytes": 56995,
  "format": "pdf",
  "media_type": "application/pdf",
  "original_filename": "mixed.pdf",
  "declared_content_type": "application/pdf",
  "pdf_encrypted": false,
  "page_count": 3,
  "pages": [
    {"page_number": 1, "source": "pdf_text", "needs_ocr": false, "ocr_reason": "native_text",
     "width_pt": 612.0, "height_pt": 792.0, "rotation": 0,
     "embedded_text_chars": 168, "has_embedded_text": true, "image": null},
    {"page_number": 2, "source": "pdf_render", "needs_ocr": true, "ocr_reason": "no_text_layer",
     "width_pt": 612.0, "height_pt": 792.0, "rotation": 0,
     "embedded_text_chars": 0, "has_embedded_text": false,
     "image": {"width_px": 2550, "height_px": 3300, "dpi": 300}}
  ],
  "processing": {"pipeline_version": "0.3.0", "render_dpi": 300, "native_text_min_chars": 20,
                 "pdfium_version": "156.0.8076.0", "pillow_version": "13.0.0",
                 "opencv_version": "4.14.0"}
}
```

| Field | Values |
|---|---|
| `source` | `pdf_text` (embedded text used), `pdf_render` (page rendered for OCR), `image` (image upload page/frame) |
| `ocr_reason` | `native_text`, `no_text_layer`, `insufficient_text`, `unreliable_text`, `invisible_text_layer`, `image_content`, `image_input` (see ADR-0011) |
| `pdf_encrypted` | `true` for permissions-only encrypted PDFs (accepted) |
| `image.dpi` | Render DPI for PDF pages; the file's declared DPI (or `null`) for image uploads |
| `quality` | `null` for pages without an image; otherwise `{"flags": [...], "metrics": {...}, "duration_ms"}` (below) |

#### Page `quality`

`flags` ⊆ `blank_page`, `low_resolution`, `resolution_unknown`, `blurry`, `poor_contrast`,
`noisy`, `uneven_background`, `skewed`, `rotated` (ADR-0017). `metrics`:

```json
{"width_px": 2550, "height_px": 3300, "dpi": 300, "effective_dpi": 300,
 "ink_fraction": 0.071, "intensity_spread": 255.0, "text_contrast": 196.1,
 "sharpness": 1.12, "noise_sigma": 0.0, "impulse_noise_ratio": 0.0,
 "background_variation": 0.0, "text_axis": "horizontal", "axis_ratio": 7.0,
 "skew_deg": 3.0, "skew_confidence": 9.1}
```

`skew_deg` is counter-clockwise positive, relative to the detected text axis, and `null` when
unreliable. `sharpness` and `noise_sigma` are `null` when not measurable (e.g. blank pages).

Errors: `413 FILE_TOO_LARGE`, `415 UNSUPPORTED_FORMAT | FORMAT_MISMATCH`,
`422 EMPTY_FILE | CORRUPT_DOCUMENT | ENCRYPTED_DOCUMENT | TOO_MANY_PAGES | PAGE_TOO_LARGE |
VALIDATION_ERROR`.

### `GET /api/v1/documents?q=&status=&submitted_by=&created_from=&created_to=&limit=&offset=`
Role: submitter (own documents) or viewer (all, filterable by `submitted_by`). Newest first.
`q` matches the filename (substring, any case; `%` and `_` are literal) or the start of the
document id. `status`: `received` (queued, not yet ingested), `ready` or `rejected`.
`created_from` (inclusive) and `created_to` (exclusive) are ISO 8601 times.
→ `{"items": [DocumentSummary], "total", "limit", "offset"}`. `DocumentSummary` includes
`filename` (sanitised).

### `GET /api/v1/documents/{document_id}/status`
→ `DocumentSummary`:

```json
{"id": "8231aa86…", "status": "ready", "sha256": "…", "size_bytes": 1456, "format": "png",
 "page_count": 1, "submitted_by": "alice", "error_code": null,
 "created_at": "…", "updated_at": "…", "latest_job": {"id": "1b21…", "status": "succeeded", …}}
```

A rejected upload has `status: "rejected"` and the ingestion error in `error_code` (for
example `CORRUPT_DOCUMENT`). In local mode every stored document is `ready` and
`latest_job` is null.

### `GET /api/v1/documents/{document_id}`
`200` → `DocumentManifest`. `404 DOCUMENT_NOT_FOUND`; `422 VALIDATION_ERROR` if the id is not
32 lowercase hex characters.

### `GET /api/v1/documents/{document_id}/pages/{page_number}/text`
`200` → embedded PDF text with source evidence. Coordinates are in PDF points, origin at the
top-left of the (unrotated) page:

```json
{"page_number": 3, "units": "pt", "page_width": 612.0, "page_height": 792.0,
 "text": "Page three is native text again, long enough.",
 "segments": [{"text": "Page three is native text again, long enough.",
               "bbox": {"x0": 72.6, "y0": 63.2, "x1": 330.1, "y1": 75.0}}]}
```

Pages without an embedded text layer return empty `text` and `segments`.
`404 DOCUMENT_NOT_FOUND | PAGE_NOT_FOUND`.

### `GET /api/v1/documents/{document_id}/pages/{page_number}/image`
`200` → `image/png`, the rendered PDF page or the normalised (EXIF-oriented, 8-bit L/RGB) image
page. `404 DOCUMENT_NOT_FOUND | PAGE_NOT_FOUND | PAGE_IMAGE_NOT_AVAILABLE`.

## Preprocessing

### `GET /api/v1/preprocessing/profiles`
`200` → list of profile configurations (`name`, `description`, `orientation`, `deskew`,
`deskew_min_angle`, `illumination`, `denoise`, `denoise_method`, `denoise_strength`,
`contrast`, `clahe_clip_limit`, `clahe_tile_grid`, `adaptive_threshold`,
`threshold_block_inches`, `threshold_c`). Step modes are `never`, `auto` (only when the
matching quality flag is set) or `always`.

### `GET /api/v1/documents/{document_id}/preprocessing`
`200` → names of profiles that have been run, e.g. `["binarize", "standard"]`.

### `POST /api/v1/documents/{document_id}/preprocessing/{profile}`
(Re)runs a profile. It replaces any earlier run of the same profile atomically; other profiles
and the source page images are untouched. `201` → `PreprocessingRun`.
`422 UNKNOWN_PROFILE`; `404 DOCUMENT_NOT_FOUND`.

### `GET /api/v1/documents/{document_id}/preprocessing/{profile}`
`200` → `PreprocessingRun`:

```json
{"document_id": "…", "profile": {"name": "standard", "…": "…"},
 "created_at": "…", "pipeline_version": "0.3.0",
 "runtime": {"opencv_version": "4.14.0", "numpy_version": "2.4.6",
             "cpu_model": "Intel(R) Core(TM) i5-7200U CPU @ 2.50GHz",
             "logical_cpus": 4, "opencv_threads": 4},
 "total_ms": 281.4,
 "pages": [
   {"page_number": 1, "status": "skipped", "skip_reason": "no_page_image", "steps": [], "…": "…"},
   {"page_number": 2, "status": "processed", "skip_reason": null,
    "steps": [
      {"name": "orientation", "applied": false, "reason": "text_axis:horizontal", "params": {}, "duration_ms": 0.0},
      {"name": "deskew", "applied": true, "reason": "skew_detected", "params": {"angle_deg": 4.0}, "duration_ms": 14.2},
      {"name": "illumination", "applied": false, "reason": "disabled", "params": {}, "duration_ms": 0.0},
      {"name": "denoise", "applied": false, "reason": "not_flagged", "params": {}, "duration_ms": 0.0},
      {"name": "contrast", "applied": false, "reason": "not_flagged", "params": {}, "duration_ms": 0.0},
      {"name": "threshold", "applied": false, "reason": "disabled", "params": {}, "duration_ms": 0.0}],
    "rotation_applied_deg": 0, "skew_detected_deg": 4.0, "skew_corrected_deg": 4.0,
    "quality_flags_before": ["skewed"],
    "output": {"width_px": 1712, "height_px": 2397, "dpi": 200},
    "quality_after": {"flags": [], "metrics": {"…": "…"}, "duration_ms": 190.0},
    "duration_ms": 214.9}]}
```

| Field | Values |
|---|---|
| `status` | `processed` (output image written), `unchanged` (no step applied; use the source), `skipped` (`skip_reason`: `no_page_image` for native-text PDF pages, `blank_page`) |
| step `reason` | `flag:<flag>`, `always`, `enabled`, `not_flagged`, `disabled`, `skew_detected`, `below_min_angle`, `skew_undetermined`, `text_axis:<axis>` |
| `rotation_applied_deg` | `90` when vertical text was turned clockwise. A 180° ambiguity always remains for the OCR stage (ADR-0016) |

`404 PREPROCESSING_NOT_FOUND`; `422 VALIDATION_ERROR` for a malformed profile name.

### `GET /api/v1/documents/{document_id}/preprocessing/{profile}/pages/{page_number}/image`
`200` → `image/png` (8-bit greyscale). Deskewed images are enlarged to avoid cropping.
`404 PREPROCESSED_IMAGE_NOT_AVAILABLE | PAGE_NOT_FOUND | PREPROCESSING_NOT_FOUND`.

## OCR

OCR runs synchronously in the request for now (ADR-0012). Expect several seconds per page on
CPU (docs/PERFORMANCE.md).

### `GET /api/v1/ocr/engines`
`200` → `[{"name": "paddle", "role": "primary", "available": true, "reason": null,
"languages": ["en","hi","kn","mr","ta","te"]}, {"name": "tesseract", "role": "fallback", ...}]`

### `POST /api/v1/documents/{document_id}/ocr`
JSON body (all optional):

| Field | Default | Meaning |
|---|---|---|
| `engine` | `"auto"` | `auto` (primary with per-page fallback; the primary is per language, e.g. Kannada → Tesseract), `paddle` or `tesseract` (no fallback) |
| `languages` | `NGOCR_OCR_DEFAULT_LANGUAGES` (`["en"]`) | ISO 639-1 codes (PaddleOCR uses the first; Tesseract uses all), or `["auto"]` to identify the script per page (ADR-0023) |
| `handwriting` | `false` | Use the handwriting engine (English only, experimental: always reviewed). Other languages return `unsupported` (ADR-0024). Requires `engine: "auto"` and no `compare` |
| `preprocessing_profile` | default profile | Read that profile's preprocessed images where they exist; otherwise source pages |
| `compare` | `false` | Also run the secondary engine and store `ocr/comparison` |

`201` → `OcrRun`, which replaces any earlier run stored under the same name (`auto`,
`paddle` or `tesseract`):

```json
{"document_id": "…", "requested_engine": "auto",
 "engines": [{"name": "paddle", "version": "paddleocr 3.7.0; paddlepaddle 3.2.2",
              "models": [{"id": "paddle/PP-OCRv5_mobile_det", "revision": "0d63e78e…",
                          "files": {"inference.pdiparams": "afa1820c…", "…": "…"}},
                         {"id": "paddle/te_PP-OCRv5_mobile_rec", "…": "…"}],
              "parameters": {"detection_model": "PP-OCRv5_mobile_det",
                             "recognition_language": "te", "textline_orientation": false,
                             "cpu_threads": 0, "enable_mkldnn": true}}],
 "languages": ["te"], "preprocessing_profile": "standard",
 "created_at": "…", "pipeline_version": "0.4.0",
 "runtime": {"opencv_version": "4.14.0", "…": "…"}, "total_ms": 7012.4,
 "pages": [
   {"page_number": 1, "status": "skipped", "skip_reason": "native_text", "…": "…"},
   {"page_number": 2, "status": "ok", "skip_reason": null, "error_code": null,
    "engine": "paddle", "fallback_from": null, "fallback_reason": null,
    "image_source": "preprocessed:standard", "image_width": 1240, "image_height": 1754,
    "orientation_deg": null,
    "lines": [{"text": "ఈ ఇన్వాయిస్ సరుకుల సరఫరాను నిర్ధారిస్తుంది.", "confidence": 0.9871,
               "bbox": {"x0": 150.0, "y0": 148.0, "x1": 870.0, "y1": 190.0},
               "polygon": [[150.0, 148.0], [870.0, 148.0], [870.0, 190.0], [150.0, 190.0]],
               "words": null, "orientation_deg": null}],
    "text": "ఈ ఇన్వాయిస్ …", "mean_confidence": 0.9812, "duration_ms": 6850.3}]}
```

| Field | Values |
|---|---|
| page `status` | `ok`, `empty` (engine found no text), `skipped` (`skip_reason`: `native_text`, `blank_page`, `no_page_image`), `failed` (`error_code`) |
| `engine` / `fallback_from` / `fallback_reason` | Which engine produced the page result, and whether it was a fallback and why (error code or `primary_unavailable`) |
| `image_source` | `source` or `preprocessed:<profile>`. Coordinates refer to this image |
| `bbox`, `polygon` | Pixels, top-left origin, in `image_source` |
| `confidence` | 0..1 as reported by the engine (PaddleOCR line score; Tesseract mean word confidence / 100). Not calibrated |
| `orientation_deg` | Line: 0/180 from the text-line classifier (English only; `null` otherwise). Page: majority of lines; 180 means lines were ordered for the upright page |
| `words` | Tesseract only: word boxes and confidences |

Errors: `422 UNSUPPORTED_LANGUAGE | UNKNOWN_ENGINE | VALIDATION_ERROR`,
`503 OCR_ENGINE_UNAVAILABLE | MODEL_NOT_AVAILABLE | MODEL_INTEGRITY_ERROR`, `404 DOCUMENT_NOT_FOUND`.

### `GET /api/v1/documents/{document_id}/ocr`
`200` → stored run names, e.g. `["auto", "tesseract"]`.

### `GET /api/v1/documents/{document_id}/ocr/{engine}`
`200` → `OcrRun`. `404 OCR_RUN_NOT_FOUND`.

### `GET /api/v1/documents/{document_id}/ocr/comparison`
`200` → `{"document_id", "primary", "secondary", "threshold": 0.85, "created_at",
"pages": [{"page_number", "agreement", "needs_review", "reason"}]}`. `reason` is `agree`,
`disagreement`, `engine_failed` or `skipped:<reason>`. Informational only: results are never
merged (ADR-0022). `404 OCR_RUN_NOT_FOUND`.

## Languages and review (Phase 5)

### `GET /api/v1/languages`
`200` → the language matrix: `[{"code": "kn", "name": "Kannada", "script": "Kannada",
"printed_engines": ["tesseract"], "handwriting": "unsupported", "notes": "…"}, …]`.
`handwriting` is `unsupported`, `experimental` or `supported` (none is `supported` yet).

### Additional `OcrPage` fields

```json
{"status": "ok",
 "languages": ["hi", "mr", "en"],
 "handwriting": false,
 "image_rotation_deg": 180,
 "script": {"status": "detected", "script": "Devanagari", "script_confidence": 0.95,
            "secondary_scripts": ["Latin"], "osd_script": "Devanagari",
            "osd_script_confidence": 38.3, "census": {"Devanagari": 0.95, "Latin": 0.05},
            "census_mean_confidence": 0.94, "orientation_deg": 180,
            "orientation_confidence": 30.6, "method": "tesseract-osd+census",
            "duration_ms": 3980.0},
 "detected_language": {"languages": ["hi"], "scripts": {"Devanagari": 0.96, "Latin": 0.04},
                       "mixed_script": false, "uncertain": false,
                       "reason": "devanagari_words(hi=13,mr=0)"},
 "review": {"needs_review": true, "reasons": ["low_confidence_lines"],
            "details": ["2 of 16 lines"]}}
```

| Field | Meaning |
|---|---|
| `status` | Adds `unsupported`: the page was not OCRed because it cannot be read reliably (`review.reasons` says why) |
| `languages` | Languages the engine was asked to read for this page (per page in auto mode) |
| `image_rotation_deg` | Clockwise rotation applied before OCR (from OSD). Coordinates refer to the rotated image |
| `script` | Detection evidence: null when not run. `status`: `detected`, `uncertain`, `undetermined`, `unavailable` |
| `detected_language` | Script census and language guess of the recognised text; Hindi vs Marathi from function words, `uncertain` when the evidence is weak |
| `review.reasons` | `unsupported_script`, `script_uncertain`, `script_mismatch`, `language_not_enabled`, `handwriting_unsupported`, `handwriting_unverified`, `mixed_script`, `low_confidence`, `low_confidence_lines`, `empty_result`, `ocr_failed`, `low_quality`, `engine_disagreement` |

`OcrRun` adds `handwriting` (bool) and `pages_needing_review` (count). With `languages:
["auto"]`, `503 OCR_ENGINE_UNAVAILABLE` is returned if script identification (Tesseract + OSD
model) is unavailable.

## Layout, tables and key-value pairs (Phase 6)

Layout is derived from a **stored OCR run** and never changes it (ADR-0025). Coordinates are
pixels in the image that OCR read (`image_source` after `image_rotation_deg`), the same space
as `OcrPage.lines`. Regions, cells and pairs link to OCR lines by index (`line_indices` into
`OcrPage.lines`). Runs synchronously in the request (ADR-0012).

### `GET /api/v1/layout/status`
`200` → `{"available": true, "reason": null, "models": [EngineInfo, …]}` for the layout,
table-classification and table-structure models (pinned revision and file hashes).

### `POST /api/v1/documents/{document_id}/layout`
Body (optional): `{"ocr_run": "auto"}`, the name of a stored OCR run (as listed by
`GET …/ocr`). `201` → `LayoutRun`, stored and replacing any earlier layout of that OCR run.
Errors: `404 OCR_RUN_NOT_FOUND`, `503 LAYOUT_UNAVAILABLE` (models not fetched),
`422 VALIDATION_ERROR`. A per-page model failure is recorded on that page
(`status: failed`, `error_code: LAYOUT_FAILED`).

### `GET /api/v1/documents/{document_id}/layout`
`200` → names of stored layout runs (one per OCR run), e.g. `["auto"]`.

### `GET /api/v1/documents/{document_id}/layout/{ocr_run}`
`200` → `LayoutRun`. `404 LAYOUT_RUN_NOT_FOUND`.

### `GET /api/v1/documents/{document_id}/layout/{ocr_run}/pages/{page_number}/overlay`
`200 image/png`: the page with regions coloured by type, reading-order numbers, table cells
and review flags drawn, for inspecting layout errors. Not stored. `404 PAGE_NOT_FOUND`,
`404 PAGE_IMAGE_NOT_AVAILABLE` (page skipped or failed).

### `LayoutRun`

```json
{"document_id": "…", "ocr_run": "auto", "models": [EngineInfo, …],
 "parameters": {"min_table_score": 0.9, "ruling_check": true},
 "created_at": "…", "pipeline_version": "0.6.0", "runtime": {…}, "total_ms": 2412.3,
 "pages_needing_review": 0,
 "pages": [{
   "page_number": 1, "status": "ok", "image_source": "preprocessed:standard",
   "image_rotation_deg": 0, "image_width": 1705, "image_height": 2374, "column_count": 2,
   "regions": [{"id": 0, "type": "header", "label": "header", "score": 0.92,
                "bbox": {…}, "order": 0, "line_indices": [0], "text": "…", "table_id": null}],
   "tables": [{
     "id": 0, "region_id": 5, "bbox": {…}, "status": "ok",
     "kind": "wired", "kind_score": 0.94, "structure_score": 0.9997,
     "rows": 5, "cols": 4,
     "cells": [{"row": 0, "col": 1, "row_span": 1, "col_span": 2, "header": true,
                "bbox": {…}, "text": "Sales (units)", "text_source": "ocr_lines",
                "line_indices": [12], "confidence": 0.99}],
     "grid": [[0, 1, 1, 2], …],
     "ruling_check": "verified", "cell_boxes": "rulings",
     "ruling_rows": 5, "ruling_cols": 4, "issues": [], "html": "<table>…</table>"}],
   "key_values": [{"key": "Invoice No", "value": "INV-2024-0042", "source": "separator",
                   "region_id": 7, "line_index": 30, "bbox": {…},
                   "key_bbox": null, "value_bbox": null, "table_id": null, "row": null}],
   "unsupported": [{"kind": "formula", "region_id": 9, "detail": "…"}],
   "text": "…", "review": {"needs_review": false, "reasons": [], "details": []},
   "duration_ms": 2401.0}]}
```

| Field | Meaning |
|---|---|
| `status` | `ok`, `skipped` (`skip_reason`: the OCR page's skip reason, e.g. `native_text`, or `ocr_<status>`), `failed` (`error_code`) |
| `regions[].type` | `title`, `section_title`, `text`, `caption`, `header`, `footer`, `page_number`, `footnote`, `aside`, `table`, `figure`, `chart`, `formula`, `seal`, `unclassified` (OCR text outside every detected region, or an unknown label). `label` keeps the model's own label |
| `regions[].order` | Reading order (headers first, then the flow by XY-cut, then footers/page numbers); `regions` is sorted by it |
| `column_count` | Most side-by-side text regions in the main flow |
| `tables[].status` | `ok`; `needs_review` (structure returned, `issues` say why); `unsupported` (no reliable structure: no cells, the text stays in the region) |
| `tables[].grid` | `rows × cols` indices into `cells`; a spanned cell repeats its index. Row/column association: cell `grid[r][c]` |
| `tables[].ruling_check` | `verified` (traced borders give exactly these cells), `mismatch` (review), `inconclusive`, `not_applicable` (wireless), `disabled` |
| `tables[].cell_boxes` | `rulings` (exact traced interiors, after `verified`) or `model` (SLANet's approximate boxes) |
| `cells[].text_source` | `ocr_lines` (the page's OCR lines inside the cell), `cell_ocr` (re-read from the cell crop: a line spanned cells, or page OCR left it empty), `empty` |
| `key_values[].source` | `separator` ("Key: value" on one line; `bbox` is the line's, no per-word boxes are estimated) or `table_row` (a two-column table row; exact `key_bbox`/`value_bbox`) |
| `unsupported[].kind` | `formula`, `chart`, `seal`, `figure_text`, `table_structure` |
| `review.reasons` | `table_structure_mismatch`, `table_structure_invalid`, `table_low_confidence`, `table_text_unassigned`, `table_cell_text_ambiguous`, `table_cell_unread`, `unsupported_structure`, `overlapping_regions`, `unclassified_text` |
| `text` | Page text in reading order: regions separated by a blank line, table rows on lines with cells tab-separated |

## Structured extraction (Phase 7)

Rules and templates only, with no LLM (ADR-0026). Concepts, statuses and normalisation:
EXTRACTION.md. JSON Schemas: `docs/schemas/extraction_result.schema.json` (result),
`docs/schemas/document_schema.schema.json` (document-type schema format) and
`docs/schemas/records/<type>.schema.json` (each type's validated record). These are generated
from the code and checked by the tests.

### `GET /api/v1/extraction/schemas`
`200` → `[{"id": "gst_invoice", "version": "1.0", "title": "GST tax invoice", "fields": [...],
"tables": ["line_items"]}, …]`.

### `GET /api/v1/extraction/schemas/{schema_id}`
`200` → the full document-type schema (labels, sections, tables, checks, templates).
`422 UNKNOWN_DOCUMENT_TYPE`.

### `POST /api/v1/documents/{document_id}/extraction`
Body (optional): `{"layout_run": "auto", "document_type": "auto"}`. Use `document_type` with
a schema id to skip classification. `201` → `ExtractionResult`, stored and replacing any
earlier extraction of that layout run. Errors: `404 LAYOUT_RUN_NOT_FOUND`,
`404 OCR_RUN_NOT_FOUND`, `422 UNKNOWN_DOCUMENT_TYPE`.

### `GET /api/v1/documents/{document_id}/extraction`
`200` → names of stored extraction results (one per layout run).

### `GET /api/v1/documents/{document_id}/extraction/{layout_run}`
`200` → `ExtractionResult`. `404 EXTRACTION_NOT_FOUND`.

### `GET /api/v1/documents/{document_id}/extraction/{layout_run}/export`
Query: `format=json|csv|xlsx` (default `json`); with `format=csv`, `table=<name>` gives that
table's records instead of the fields. `200` with `Content-Disposition: attachment`.
`422 UNSUPPORTED_EXPORT` (no such table). Audited as `extraction.exported`.

### `ExtractionResult` (abridged)

```json
{"document_id": "…", "layout_run": "auto", "ocr_run": "auto",
 "schema_id": "gst_invoice", "schema_version": "1.0", "status": "complete",
 "classification": {"status": "classified", "document_type": "gst_invoice", "template": null,
                    "score": 14.0, "scores": {"gst_invoice": 14.0, "receipt": 0.0, …},
                    "evidence": {"gst_invoice": ["keyword:tax invoice", …]}},
 "fields": [{"name": "grand_total", "type": "amount", "required": true,
             "status": "extracted", "value": "11800.00", "raw": "11,800.00",
             "method": "label", "confidence": 0.99,
             "evidence": [{"page_number": 1, "source": "ocr_line",
                           "text": "Grand Total: 11,800.00", "bbox": {…}, "units": "px",
                           "line_indices": [27], "region_id": 9, "confidence": 0.99}],
             "candidates": [{"value": "11800.00", "raw": "11,800.00", "method": "label",
                             "evidence": [...], "alternatives": [], "errors": []}],
             "notes": [], "errors": [], "sensitive": false},
            {"name": "due_date", "status": "missing", "value": null, …}],
 "tables": [{"name": "line_items", "status": "extracted", "columns": [...],
             "column_types": {…}, "records": [{"index": 0, "page_number": 1, "table_id": 0,
             "row": 1, "values": {"amount": {"status": "extracted", "value": "5000.00",
             "raw": "5000.00", "evidence": {…}}, …}}], "summary_rows": [], "sources": [...],
             "issues": []}],
 "record": {"invoice_number": "INV-2024-0042", "due_date": null, …},
 "validation": {"valid": true, "issues": []},
 "checks": [{"id": "grand_total", "kind": "field_sum", "status": "passed",
             "message": "grand_total: expected 11800.00, found 11800.00", "fields": [...],
             "expected": "11800.00", "actual": "11800.00", "difference": "0.00", "rows": []}],
 "review": {"needs_review": false, "reasons": [], "details": []},
 "source_notes": [], "created_at": "…", "pipeline_version": "0.7.0", "total_ms": 21.3}
```

| Field | Values |
|---|---|
| `status` | `complete` (valid, no review), `needs_review`, `unclassified` (no document type: no fields) |
| `classification.status` | `classified`, `template`, `requested`, `ambiguous`, `unknown` |
| `fields[].status` | `extracted`, `missing`, `ambiguous`, `conflict`, `invalid`; `value` is null unless `extracted` |
| `fields[].method` | `template`, `label`, `section`, `table_row`, `pattern` (`unattributed` candidates are never chosen) |
| `checks[].status` | `passed`, `failed`, `not_applicable` (inputs missing: nothing assumed) |
| `validation.issues[].code` | `missing_required`, `invalid_value`, `field_ambiguous`, `field_conflict`, `field_invalid`, `cell_ambiguous`, `cell_invalid` |
| `review.reasons` | `document_type_unknown`, `document_type_ambiguous`, `required_field_missing`, `field_ambiguous`, `field_conflict`, `field_invalid`, `table_not_found`, `table_value_invalid`, `check_failed`, `low_confidence_evidence`, `source_needs_review` |

## Jobs (Phase 9, service mode)

Mounted only in service mode (ADR-0029). The stage endpoints `POST …/preprocessing/{profile}`,
`…/ocr`, `…/layout` and `…/extraction` keep their request bodies but return `202` with a
job instead of running in the request. A stage job also runs the stages after it:

| Job `kind` | Stages |
|---|---|
| `pipeline` (an upload) | `ingest`, `preprocessing`, `ocr`, `layout`, `extraction` |
| `preprocessing` | `preprocessing` |
| `ocr` | `ocr`, `layout`, `extraction` |
| `layout` | `layout`, `extraction` |
| `extraction` | `extraction` |

The OCR engine name (`auto`, `paddle` or `tesseract`) is also the name of the OCR, layout
and extraction runs the job writes. Extraction results are queued for review as in Phase 8.

| Endpoint | Role | Result |
|---|---|---|
| `POST /api/v1/documents/{document_id}/jobs` `{"kind", "params"}` | submitter (own document) | `202` `JobSubmitted`. The document must be `ready` (`409 DOCUMENT_NOT_READY`) |
| `GET /api/v1/jobs?status=&document_id=&limit=&offset=` | submitter (own) / viewer (all) | `{"items": [Job], …}`, newest first; `status` may repeat |
| `GET /api/v1/jobs/{job_id}` | submitter (own) / viewer | `Job` |
| `GET /api/v1/jobs/{job_id}/events` | submitter (own) / viewer | `[JobEvent]`, oldest first |
| `POST /api/v1/jobs/{job_id}/cancel` | the submitter, or admin | `Job`. Queued: cancelled at once. Running: `cancel_requested`, stops before its next stage |
| `POST /api/v1/jobs/{job_id}/retry` | the submitter, or admin | `Job`, queued again with fresh attempts; resumes at the first stage not completed |

`params` (all optional): `profile`, `engine`, `languages`, `compare` (default `true`),
`handwriting`, `document_type` (default `auto`).

**`Job`:**

```json
{"id": "1b211e6f…", "document_id": "8231aa86…", "kind": "pipeline", "status": "running",
 "params": {"profile": null, "engine": "auto", "languages": ["en"], "compare": true,
            "handwriting": false, "document_type": "auto", "filename": "inv.png",
            "content_type": "image/png"},
 "stages": ["ingest", "preprocessing", "ocr", "layout", "extraction"],
 "completed_stages": ["ingest", "preprocessing"], "current_stage": "ocr",
 "attempts": 1, "max_attempts": 3, "cancel_requested": false,
 "error_code": null, "error_message": null, "submitted_by": "alice",
 "idempotency_key": "order-42", "created_at": "…", "started_at": "…",
 "finished_at": null, "updated_at": "…"}
```

`JobSubmitted` is `{"job": Job, "duplicate": false}`.

| `status` | Meaning |
|---|---|
| `queued` | Waiting for a worker, or for its retry backoff |
| `running` | A worker holds it (its lease is renewed while it runs) |
| `succeeded` | Every stage completed |
| `failed` | `error_code` says why; `error_message` names the stage and code, never document content. Recoverable with `retry` (except a rejected upload) |
| `cancelled` | Stopped on request; completed stages keep their results |

**Failures.** Errors caused by the input or request (for example `CORRUPT_DOCUMENT`,
`UNSUPPORTED_LANGUAGE`, `LAYOUT_UNAVAILABLE`) fail the job at once, because a retry cannot
help. Unexpected errors (`INTERNAL_ERROR`, `OCR_TIMEOUT`) are retried after 30 s, 60 s,
120 s and so on (at most 10 minutes), up to `max_attempts`. If a worker dies, its job is
taken over when its lease expires (`lease_expired` event); after the last attempt it fails
with `WORKER_LOST`.

**`JobEvent`:** `{"ts", "event", "stage", "attempt", "detail"}`. Events: `submitted`, `started`,
`stage_started`, `stage_completed`, `retry_scheduled`, `lease_expired`, `redispatched`,
`cancel_requested`, `cancelled`, `retried`, `succeeded`, `failed`. `detail` holds only ids,
counts and codes.

## Web sign-in (Phase 10)

The web application signs in with a password and keeps an HttpOnly session cookie (ADR-0030).
Integrations keep using bearer tokens.

| Endpoint | Auth | Result |
|---|---|---|
| `POST /api/v1/auth/login` `{"user", "password"}` | none | `200` `SessionInfo` and `Set-Cookie: ngocr_session=…; HttpOnly; SameSite=Strict; Path=/` (`Secure` outside development). `401 LOGIN_FAILED` for every refusal |
| `GET /api/v1/auth/session` | cookie or token | `SessionInfo`; `csrf_token` is null for bearer callers |
| `POST /api/v1/auth/logout` | cookie + CSRF | `204`; the session is revoked and the cookie cleared |
| `POST /api/v1/auth/password` `{"current_password", "new_password"}` | cookie + CSRF | `204`; every session of the user ends (sign in again) |

`SessionInfo`: `{"user", "role", "csrf_token", "expires_at"}`. Sessions end after 30 minutes
without a request and 12 hours after sign-in (configurable); disabling a user, changing
their role or password ends their sessions at once.

## Search and administration (Phase 10, service mode)

| Endpoint | Role | Result |
|---|---|---|
| `GET /api/v1/extractions?q=&schema_id=&status=&review_status=&submitted_by=&created_from=&created_to=&limit=&offset=` | submitter (own) / viewer (all) | `{"items": [ExtractionSummary], …}`, newest first. `q` matches the filename or any extracted value (substring, any case) |
| `GET /api/v1/admin/overview` | admin | `Overview`: jobs by status, oldest queued job age, running jobs with worker and lease, jobs per worker, the 10 latest failures, failures in the last 24 h by code, documents and review items by status, active sessions |
| `GET /api/v1/admin/audit?event=&actor=&document_id=&since=&until=&limit=&offset=` | admin | `{"items": [AuditEntry], …}`, newest first. `event` is a name (`review.approved`) or a prefix ending in `.` (`auth.`) |
| `GET /api/v1/admin/users` | admin | `[UserInfo]` |
| `POST /api/v1/admin/users` `{"name", "role", "password"}` | admin | `201` `UserInfo` |
| `PATCH /api/v1/admin/users/{name}` `{"role"?, "disabled"?, "password"?}` | admin | `UserInfo`. A new role, disabling or a new password ends the user's sessions (and clears a lockout). Admins cannot disable or demote themselves (`403`) |

`ExtractionSummary`: `{"document_id", "layout_run", "schema_id", "status", "review_status",
"filename", "submitted_by", "created_at", "preview"}` (`preview`: up to six extracted values).
`AuditEntry`: `{"id", "ts", "event", "actor", "request_id", "document_id", "fields"}`.
`UserInfo`: `{"name", "role", "disabled", "locked_until", "created_at",
"password_changed_at", "last_login_at"}` (never the password hash).

New audit events: `auth.login`, `auth.login_failed` (with `reason`: `unknown`,
`wrong_password`, `locked`, `disabled`), `auth.logout`, `auth.csrf_failed`, `user.created`,
`user.updated`. Passwords never appear in audit events or logs.

## Web application (Phase 10)

`GET /app/…` serves the built React application (`make frontend`); unknown paths under
`/app` return the application shell (client-side routes), and `/app/assets/*` are
content-hashed files cached for a year. `GET /` redirects to `/app/`, and the Phase 8
`/review` page to `/app/review`. Every response carries `Content-Security-Policy:
default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' blob: data:;
connect-src 'self'; font-src 'self'; form-action 'self'; frame-ancestors 'none';
base-uri 'none'`, `X-Frame-Options: DENY` and `Referrer-Policy: no-referrer`. When the
application is not built, `/app` returns `503` with a page saying so.

## Verification and review (Phase 8)

Every extraction result is queued for review when `POST …/extraction` stores it. Gates,
calibration, workflow and reason codes: REVIEW.md. Every endpoint below needs a bearer
token; the minimum role is given for each.

| Endpoint | Role | Result |
|---|---|---|
| `GET /api/v1/auth/whoami` | viewer | `{"user", "role", "token_id"}` |
| `GET /api/v1/review/policy` | viewer | `{"auto_accept", "require_second_person", "calibration"}` (the full calibration, or null) |
| `GET /api/v1/review/items?status=&schema_id=&reason=&assigned_to=&limit=&offset=` | viewer | `{"total", "items": [ReviewItemSummary]}`, highest priority first. Default statuses: `pending`, `in_review` |
| `GET /api/v1/review/items/{document_id}/{layout_run}` | viewer | `ReviewItem` |
| `GET …/result` | viewer | `ReviewedResult`: record with corrections applied, validation, re-run checks, `unresolved` |
| `GET …/history` | viewer | The item's `HistoryEvent`s, oldest first |
| `GET …/crop?field=[&candidate=]` or `?table=&record=&column=` | viewer | PNG of the evidence area |
| `GET …/pages/{page_number}[?field=]` | viewer | PNG of the page with the field's evidence (red) and other candidates (blue) |
| `POST …/claim` `{"expected_version", "force"}` | reviewer | `ReviewItem` (`force`: admin only) |
| `POST …/release` `{"expected_version"}` | reviewer | `ReviewItem` |
| `POST …/corrections` `{"expected_version", "changes": [Change]}` | reviewer (the assignee) | `ReviewItem` |
| `POST …/approve` `{"expected_version", "comment", "acknowledge_failed_checks"}` | approver | `ReviewItem` |
| `POST …/reject` `{"expected_version", "reason", "comment"}` | reviewer | `ReviewItem` |
| `GET /api/v1/review/patterns` | approver | `CorrectionPatterns` |
| `GET /api/v1/review/history/verify` | admin | `{"valid", "events", "first_bad_seq", "message"}` |

`…` is `/api/v1/review/items/{document_id}/{layout_run}`.

**`Change`.** Give either `field`, or `table` (+ `record` and `column` for a cell). Then:
- `action`: `set` (with `value` as printed), `clear`, or `confirm` (a whole table can only
  be confirmed);
- `reason`: a correction reason code, required except for `confirm`;
- `comment`: required with `other`.

### `ReviewItem` (abridged)

```json
{"document_id": "…", "layout_run": "auto", "schema_id": "gst_invoice",
 "schema_version": "1.0", "revision": 1, "version": 3, "status": "in_review",
 "priority": 46, "reasons": ["check_failed", "below_threshold", "table_check_failed"],
 "assigned_to": "asha",
 "assessment": {"calibration_id": "3f1c…", "auto_accept_enabled": false, "eligible": false,
   "reasons": ["check_failed"],
   "fields": [{"field": "grand_total", "type": "amount", "required": true,
               "status": "extracted", "verified": false,
               "reasons": ["check_failed"], "details": ["grand_total: failed grand_total"],
               "confidence": 0.98, "threshold": 0.9, "calibration_key": "value:type:amount",
               "engine": {"status": "agree", "engine": "tesseract",
                          "readings": ["11900.00"], "text": "Grand Total: 11,900.00"},
               "checks_passed": [], "checks_failed": ["grand_total"]}],
   "tables": [{"table": "line_items", "status": "extracted", "verified": false,
               "reasons": ["check_failed"], "checks_passed": ["line_amount"],
               "checks_failed": ["items_total"], "cells_flagged": [],
               "engine_disagreements": 0, "engine_disagreements_corroborated": 1}]},
 "corrections": {"fields.grand_total": {"location": "fields.grand_total", "action": "set",
   "value": "11800.00", "previous": "11900.00", "reason": "ocr_misread",
   "actor": "asha", "at": "…", "seq": 7}},
 "decision": null, "created_at": "…", "updated_at": "…"}
```

`status`: `pending`, `in_review`, `approved`, `rejected`, `auto_accepted`.
`engine.status`: `agree`, `disagree`, `no_reading`, `unavailable`.

