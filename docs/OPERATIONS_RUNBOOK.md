# Operations Runbook

Status: development stage. Only the API process exists. Compose services, backups and
recovery are added in Phase 2 and Phase 9.

## Start / stop (development)

```bash
make venv          # once
cp .env.example .env   # optional; adjust values
make run           # http://127.0.0.1:8000, Ctrl+C to stop
```

Equivalent: `uvicorn newgen_ocr.api.app:create_app --factory --host 127.0.0.1 --port 8000 --no-server-header`.

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

## Logs

JSON lines on stdout (`NGOCR_LOG_FORMAT=json`). Fields: `ts`, `level`, `logger`, `msg`,
`request_id`, plus event-specific metadata. Logs must not contain document content. Report it
as a security defect if they do.

## Backups and recovery

To be defined in Phase 2 (PostgreSQL dumps + `data/` volume snapshots) and drilled in Phase 9.
