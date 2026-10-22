# Operations Runbook

Status: development stage. Only the API process exists. Compose services, backups and
recovery are added in Phase 3 and Phase 10.

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

Set `TMPDIR` for the service to a directory on the protected data volume. Uploads over 1 MiB
are spooled there while the request is parsed (OD-15).

## Data layout

```
data/quarantine/   incoming bytes during validation (normally empty)
data/staging/      derived output under construction (normally empty)
data/originals/    accepted originals, read-only (0440)
data/derived/      manifest.json, pages/NNNN.png, text/NNNN.json per document
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
| Slow uploads under load | Ingestion is synchronous and PDFs are processed one at a time (ADR-0012) | Expected until Phase 3 moves processing to workers |

## Logs

JSON lines on stdout (`NGOCR_LOG_FORMAT=json`). Fields: `ts`, `level`, `logger`, `msg`,
`request_id`, plus event-specific metadata. Logs must not contain document content. Report it
as a security defect if they do.

## Backups and recovery

To be defined in Phase 3 (PostgreSQL dumps + `data/` volume snapshots) and drilled in Phase 10.
