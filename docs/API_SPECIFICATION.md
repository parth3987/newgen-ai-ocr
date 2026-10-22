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
  network until Phase 3 adds it.
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

Codes are stable: they are never renamed or reused.

## Health

### `GET /health/live`
`200` → `{"status": "ok", "version": "0.2.0"}`. Performs no dependency checks.

### `GET /health/ready`
`200` → `{"status": "ready", "checks": {"data_dir_writable": true}}`
`503` → `{"status": "not_ready", "checks": {"data_dir_writable": false}}`

## Documents

### `POST /api/v1/documents`
`multipart/form-data` with one file part named `file`. Processing is synchronous for now
(ADR-0012).

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
  "processing": {"pipeline_version": "0.2.0", "render_dpi": 300, "native_text_min_chars": 20,
                 "pdfium_version": "156.0.8076.0", "pillow_version": "13.0.0"}
}
```

| Field | Values |
|---|---|
| `source` | `pdf_text` (embedded text used), `pdf_render` (page rendered for OCR), `image` (image upload page/frame) |
| `ocr_reason` | `native_text`, `no_text_layer`, `insufficient_text`, `unreliable_text`, `invisible_text_layer`, `image_content`, `image_input` (see ADR-0011) |
| `pdf_encrypted` | `true` for permissions-only encrypted PDFs (accepted) |
| `image.dpi` | Render DPI for PDF pages; the file's declared DPI (or `null`) for image uploads |

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
