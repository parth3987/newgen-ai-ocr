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
- **Authentication:** not yet implemented (OD-6). Do not expose the service beyond a trusted
  network until Phase 6 adds it.
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

Pages that cannot be recognised reliably are **not** errors. They come back in the run with
`status: "unsupported"` and/or `review.needs_review: true` (see below and LANGUAGE_SUPPORT.md).

Codes are stable: they are never renamed or reused.

## Health

### `GET /health/live`
`200` → `{"status": "ok", "version": "0.5.0"}`. Performs no dependency checks.

### `GET /health/ready`
`200` → `{"status": "ready", "checks": {"data_dir_writable": true}}`
`503` → `{"status": "not_ready", "checks": {"data_dir_writable": false}}`

## Documents

### `POST /api/v1/documents`
`multipart/form-data` with one file part named `file` and an optional text field `profile`
(preprocessing profile; default `NGOCR_DEFAULT_PREPROCESSING_PROFILE`, normally `standard`).
An unknown profile is rejected with `422 UNKNOWN_PROFILE` before anything is stored.
Processing is synchronous for now (ADR-0012). After the document is stored, the profile is run
best-effort: a preprocessing failure is audited but does not fail the upload.

`201` → `DocumentManifest`:

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
