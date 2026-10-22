# API Specification

Machine-readable schema: `GET /api/v1/openapi.json`. Interactive docs are intentionally
disabled (DECISIONS.md ADR-0005).

## Conventions

- Business endpoints will live under `/api/v1/` (none are implemented yet). Health probes are
  unversioned under `/health/`.
- **Request correlation:** clients may send `X-Request-ID` (1–64 characters from
  `[A-Za-z0-9._-]`). Invalid or missing values are replaced by a server-generated 32-character
  hex id. Every response, including errors, carries `X-Request-ID`.
- **Authentication:** not yet implemented (OD-6). Do not expose the service beyond a trusted
  network until Phase 2 adds it.

## Error envelope

All non-2xx responses (except readiness 503, below) use:

```json
{"error": {"code": "NOT_FOUND", "message": "Not Found", "request_id": "3b47…"}}
```

| Code | HTTP | Meaning |
|---|---|---|
| `NOT_FOUND` | 404 | No such route or resource |
| `METHOD_NOT_ALLOWED` | 405 | Route exists but the method is not supported |
| `VALIDATION_ERROR` | 422 (or as raised) | Request failed validation. The message lists field locations, never submitted values |
| `HTTP_ERROR` | various | Other framework-level HTTP errors |
| `INTERNAL_ERROR` | 500 | Unexpected failure; details are only in server logs (search by `request_id`) |

Codes are stable: they are never renamed or reused.

## Endpoints

### `GET /health/live`
Liveness. No dependency checks.

`200` → `{"status": "ok", "version": "0.1.0"}`

### `GET /health/ready`
Readiness. Checks the dependencies needed to accept work.

`200` → `{"status": "ready", "checks": {"data_dir_writable": true}}`
`503` → `{"status": "not_ready", "checks": {"data_dir_writable": false}}`

Checks: `data_dir_writable` means `NGOCR_DATA_DIR` exists and is writable. Database and
broker checks are added in Phase 2.
