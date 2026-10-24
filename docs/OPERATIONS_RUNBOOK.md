# Operations Runbook

Status: the production deployment is the container stack in `deploy/compose.yaml`
(ADR-0031; OFFLINE_DEPLOYMENT.md has the bundle). Development can still run the API and
workers from the virtual environment against `make services-up`.

## Start / stop (development)

```bash
make venv          # once
cp .env.example .env   # optional; adjust values
make run           # http://127.0.0.1:8000, Ctrl+C to stop
```

Equivalent: `uvicorn newgen_ocr.api.app:create_app --factory --host 127.0.0.1 --port 8000 --no-server-header`.

## Service mode (PostgreSQL, Valkey, workers; ADR-0029)

Required in staging and production. On Ubuntu hosts, install the OS libpq once:
`sudo apt install libpq5` (OD-44).

```bash
make services-up                 # random passwords in deploy/secrets/ (once), then start
export NGOCR_DATABASE_URL="postgresql+psycopg://ngocr:$(cat deploy/secrets/postgres_password)@127.0.0.1:55432/ngocr"
export NGOCR_BROKER_URL="redis://:$(cat deploy/secrets/valkey_password)@127.0.0.1:56379/0"
python -m newgen_ocr.db upgrade  # migrations (also after every upgrade of the code)
python -m newgen_ocr.auth create --user ingest-bot --role submitter
make run                         # the API: uploads now return 202 with a job
python -m newgen_ocr.worker      # one or more workers (same environment and data volume)
make services-down               # stop; data volumes are kept
```

The 127.0.0.1 ports come from `deploy/compose.dev.yaml` and are for development only.
In staging and production, use `deploy/compose.yaml` alone: PostgreSQL and Valkey then
have no published ports, and only containers on the `backend` network reach them.
Export the URLs in the service's environment (a secrets file or systemd credentials),
never in a shell history or in source.

**Migrations.** Use `python -m newgen_ocr.db upgrade` before starting new code, `current`
to show the revision, and `downgrade <rev>` to roll back. Readiness reports
`database_schema_current: false` until the database matches the code.

**Workers.** Each worker runs `NGOCR_WORKER_CONCURRENCY` processes (default 1; OCR is
CPU-bound, so set `NGOCR_OCR_CPU_THREADS` and `NGOCR_OPENCV_THREADS` so that processes ×
threads ≤ cores). Workers need the same data volume as the API.
- **Stopping.** `SIGTERM` stops a worker after its current job. A worker that is killed or
  crashes is harmless: its job is taken over when its lease (`NGOCR_JOB_LEASE_S`) expires.
- **Reaper.** Every worker reaps every `NGOCR_JOB_REAP_INTERVAL_S`. To run a pass by hand:
  `python -m newgen_ocr.jobs reap`.

**Web application.** Build it at staging with `make frontend` (Node 20 or later; `npm ci`
from the locked, hash-pinned dependencies). The result is in
`src/newgen_ocr/webapp/dist/`, which is part of the Python package. Production serves it from
the API at `http://<host>/app/`. Until it is built, `/app` answers `503` with a page saying so.
For frontend development only, run `cd frontend && npm run dev`: it proxies `/api` to `make run`.

**Web users.** Users sign in with passwords (ADR-0030); tokens remain for integrations.

```bash
python -m newgen_ocr.auth user-create --user asha --role reviewer   # asks for the password
python -m newgen_ocr.auth user-list
python -m newgen_ocr.auth user-password --user asha                 # also unlocks; ends sessions
python -m newgen_ocr.auth user-disable --user asha                  # ends sessions; user-enable
```

Create the first admin with the CLI; admins can then manage users in Admin → users. A locked
account (after `NGOCR_LOGIN_MAX_FAILURES` wrong passwords) unlocks itself after
`NGOCR_LOGIN_LOCKOUT_MINUTES`, or at once when an admin sets a new password. Serve the
application over HTTPS outside development: session cookies are `Secure` there, and
browsers will not send them over plain HTTP.

**Failed jobs.** List them with `GET /api/v1/jobs?status=failed`. `GET
/api/v1/jobs/{id}/events` shows what happened. Fix the cause, then `POST
/api/v1/jobs/{id}/retry`: the job resumes after its completed stages.

## Production stack (ADR-0031)

| Command | What it does |
|---|---|
| `make stack-up` | Migrations, then the API, workers and TLS ingress (`https://<NGOCR_HTTPS_BIND>:<NGOCR_HTTPS_PORT>`, default `127.0.0.1:8443`) |
| `make stack-down` | Stop everything; volumes are kept |
| `podman compose -f deploy/compose.yaml --profile app logs -f api worker` | Logs (JSON, rotated at 20 MB × 5 per container) |
| `... --profile app up -d --scale worker=3` | More workers. Each needs about 2 GiB; set `NGOCR_WORKER_MEMORY` and `NGOCR_WORKER_CPUS` |

**Bind address.** Set `NGOCR_HTTPS_BIND` to the internal interface clients use. Only the
ingress publishes a port; the database and queue never do.

**Certificates.** Put the organisation's certificate (full chain, PEM) in
`deploy/secrets/tls/server.crt` and its key in `server.key` (mode 0400). Then restart the
ingress: `podman compose -f deploy/compose.yaml --profile app restart ingress`. `make
certs` makes a development CA and certificate only (`ca.crt`, valid 90 days). Renew the
certificate before it expires; nginx reads it at start.

**Secrets.**
- **What they are:** `deploy/secrets/` (directory 0700) holds the database and Valkey
  passwords, the connection URLs built from them, and the TLS key. They are mounted only
  into the services that need them, at `/run/secrets`.
- **Rotation:** stop the stack, change the password in PostgreSQL (`ALTER ROLE ngocr
  PASSWORD …`) and in the file, run `make services-secrets` to rebuild the URLs, then start
  the stack. Valkey takes its password at start.
- **Never** put credentials in `.env`, compose files or the environment of a shell that
  keeps history.

**Upgrades.** On production hosts, use the signed bundle and `ngocrctl` ("Releases"
below). In a checkout: load the new image, set `NGOCR_IMAGE`, then `make stack-up`. The
`migrate` service applies migrations before the API and workers start. Take a backup first.

## Releases: install, upgrade, rollback (ADR-0032)

Production hosts run releases installed from the signed offline bundle
(OFFLINE_DEPLOYMENT.md; the bundle's `INSTALL.md`). `<prefix>` is `/opt/newgen-ocr`, or
`NGOCR_PREFIX` for a rootless install. Run every command as `<prefix>/current/ngocrctl …`
except `install`, which runs from the extracted new bundle.

| Command | What it does |
|---|---|
| `verify --signers FILE` | Signature, every hash, nothing unlisted, no links |
| `preflight` | Engine and Compose versions, Podman socket, disk, memory, CPUs, tools |
| `install --signers FILE [TLS options]` | Install or upgrade (backup first), start, smoke tests |
| `status` | Current and previous release, service states |
| `start`, `stop`, `restart` | The whole stack; `restart` recreates the containers |
| `smoke [--no-job] [--restart]` | Offline OCR, egress probe and a job over HTTPS. `--restart` first restarts everything and checks that nothing was downloaded |
| `backup` | Pauses the API and workers, then backs up to `<prefix>/backups/<stamp>` |
| `restore STAMP` | Restores a backup, then runs `ops verify` |
| `rollback [--to R] [--restore\|--no-restore]` | Returns to the previous release, restoring the pre-upgrade backup when the schemas differ |
| `verify-installed` | Re-checks the installed release: signature, hashes, image IDs, models |
| `create-admin --user NAME` | The first web administrator (asks for the password) |
| `prune` | Removes releases other than the current and previous ones, with their images |
| `benchmark [--documents N] [--clients C]` | Service benchmark through HTTPS: pages per minute, p50 and p95 latency, queue delay, failures, and each container's CPU and memory, with this host's identity. Writes `<prefix>/state/benchmark-<stamp>.json` for the release gates (ADR-0033) |

- **Settings and secrets:** `<prefix>/config/site.env` holds the bind address, port, TLS
  host name and worker limits. `<prefix>/config/secrets/` holds the passwords and TLS.
  Both are written on the first install and never overwritten. To replace a certificate,
  put the new files in `secrets/tls/`, then `restart`.
- **When an upgrade fails:** `install` prints the rollback command and stops. It does not
  roll back on its own, because a restore loses data written since the upgrade. Read the
  output of `status` and the failed smoke test first.
- **History:** every install, rollback, backup, restore and smoke test is a line in
  `<prefix>/state/history`.
- **Smoke documents:** each smoke test with a job leaves one `ngocr-smoke.png` document,
  owned by `ngocr-smoke`. Retention removes it like any other document.

## Backup and restore

```bash
make backup                       # -> deploy/backups/<UTC timestamp>/ (db.dump, data.tar.gz, MANIFEST, SHA256SUMS)
make restore BACKUP=<timestamp>   # stops API/workers/ingress, checks sums, replaces both, verifies
podman compose -f deploy/compose.yaml --profile app exec api python -m newgen_ocr.ops verify
```

- **Run nightly**, for example from a systemd timer or cron, and copy each backup to
  encrypted off-host storage (OD-49). The database and the document volume are backed up
  together because the database names files on the volume.
- **Restore drill** at least quarterly, and after every upgrade of PostgreSQL. Restore
  into a test host. `ops verify` must report `"ok": true`, a valid review history, and the
  same counts and fingerprint as the report taken when the backup was made. (Phase 11's
  drill is in SECURITY_REVIEW.md.)
- **Live backups:** `make backup` also works with the stack running. A document committed
  during the copy may be in one half only; `ops verify` reports it after a restore.

## Retention

```bash
podman compose -f deploy/compose.yaml --profile app exec api python -m newgen_ocr.ops retention          # dry run
podman compose -f deploy/compose.yaml --profile app exec api python -m newgen_ocr.ops retention --apply
```

- **Settings:** set `NGOCR_RETENTION_DOCUMENT_DAYS` to your records policy (unset keeps
  documents) and `NGOCR_RETENTION_JOB_EVENT_DAYS` (default 90). Schedule the job daily.
- **What it keeps:** documents with an active job or an open review item, until they are
  done. Audit events and the review history are never deleted (append-only; OD-48).
- **Audit:** each deleted document gets a `document.deleted` audit event.

## Egress and network checks

- **Containers:** the API, workers, database and queue have no route out (internal
  networks). `make offline-test` proves it on a test host.
- **Host:** adapt `deploy/egress/nftables-egress.nft` to the site (client and admin
  subnets, internal DNS and NTP, container subnets), check it with
  `nft -c -f deploy/egress/nftables-egress.nft`, then load it with `nft -f`. Afterwards
  `curl -m 5 https://pypi.org` on the host must fail, and the drop counters
  (`nft list table inet ngocr_egress`) show what was blocked.

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
| `NGOCR_AUTH_TOKENS_FILE` | `<data_dir>/auth/tokens.json` | Hashed tokens in local mode (`python -m newgen_ocr.auth`); service mode uses the database |
| `NGOCR_DATABASE_URL` | unset | `postgresql+psycopg://user:pass@host:5432/db`. Set with `NGOCR_BROKER_URL` for service mode (required in staging/production) |
| `NGOCR_BROKER_URL` | unset | `redis://:pass@host:6379/0` (Valkey) |
| `NGOCR_JOBS_EAGER` | `false` | Run jobs inside the API process (tests and evaluation only; refused in production) |
| `NGOCR_JOB_MAX_ATTEMPTS` | 3 | Attempts per job for unexpected errors and lost workers |
| `NGOCR_JOB_LEASE_S` | 600 | A running job's lease; renewed every third of it while the job runs |
| `NGOCR_JOB_REAP_INTERVAL_S` | 60 | How often each worker recovers expired leases and lost messages |
| `NGOCR_BROKER_VISIBILITY_TIMEOUT_S` | 7200 | Valkey redelivers unacknowledged messages after this; must exceed the longest job |
| `NGOCR_MAX_QUEUED_JOBS` | 1000 | Uploads are refused (`503 QUEUE_FULL`) beyond this many queued jobs |
| `NGOCR_MAX_ACTIVE_JOBS_PER_USER` | 50 | Per-user limit on queued and running jobs (`429 TOO_MANY_JOBS`) |
| `NGOCR_WORKER_CONCURRENCY` | 1 | Processes per worker |
| `NGOCR_SESSION_IDLE_MINUTES` | 30 | Web sessions end after this long without a request |
| `NGOCR_SESSION_MAX_HOURS` | 12 | Web sessions end this long after sign-in in any case |
| `NGOCR_SESSION_COOKIE_SECURE` | auto | `Secure` cookies; on in staging/production (production refuses `false`), off in development/test |
| `NGOCR_LOGIN_MAX_FAILURES` / `NGOCR_LOGIN_LOCKOUT_MINUTES` | 5 / 15 | Wrong passwords before a lockout, and its length |
| `NGOCR_SECRETS_DIR` | unset (`/run/secrets` in the image) | Directory of secret files named after settings (`NGOCR_DATABASE_URL`, `NGOCR_BROKER_URL`) |
| `NGOCR_RETENTION_DOCUMENT_DAYS` | unset (keep) | Documents older than this are deleted by `ops retention --apply` |
| `NGOCR_RETENTION_JOB_EVENT_DAYS` | 90 | Events of finished jobs older than this are deleted |
| `NGOCR_REVIEW_CALIBRATION_FILE` | unset | Calibration from `scripts/calibrate_review.py`; without one no value is verified |
| `NGOCR_REVIEW_AUTO_ACCEPT` | `false` | Accept fully verified documents without a person (needs a calibration file) |
| `NGOCR_REVIEW_ALLOW_SYNTHETIC_CALIBRATION` | `false` | Let production auto-accept with a synthetic calibration (not recommended) |
| `NGOCR_REVIEW_REQUIRE_SECOND_PERSON` | `true` | The approver must not have corrected or confirmed values on the item |

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
data/review/       items/<document>/<layout_run>.json (+ .r<N>.json earlier revisions),
                   history.jsonl (hash-chained corrections and decisions), .lock
data/auth/         tokens.json (hashes only, 0600)
data/audit/        audit.jsonl
```

In service mode, `data/review/`, `data/auth/` and `data/audit/` are not used: tokens, review
items, revisions, history and audit events are in PostgreSQL, with documents, pages, jobs,
job events and extraction results. A queued upload waits in `data/quarantine/<id>.part`
until a worker ingests it.

### Review operations

- **Tokens.** Create them with `python -m newgen_ocr.auth create --user <name> --role
  viewer|reviewer|approver|admin [--expires-days N]` and hand each one over privately.
  `list` shows ids, roles and expiry (never secrets). `revoke <id>` takes effect at once.
- **Review.** The queue is at `/app/review`; an item is at
  `/app/review/<document_id>/<layout_run>`.
- **History integrity.** Run `GET /api/v1/review/history/verify` (admin) after restores and
  periodically. `valid: false` names the first bad event: restore `data/review/` from a
  backup and investigate.
- **Thresholds.** Run `make calibrate` to fit them (synthetic, for evaluation only), or
  `python scripts/calibrate_review.py --dataset DIR` / `--from-reviews data` to fit them on
  real labelled or approved documents. Review `reports/calibration/report.json` before
  setting `NGOCR_REVIEW_CALIBRATION_FILE`. A calibration edited by hand is refused at
  startup.
- **Patterns.** Check `GET /api/v1/review/patterns` regularly. `verified_but_corrected`
  entries are calibration failures; investigate them first.

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
| Slow uploads under load | Local mode processes uploads in the request (ADR-0012) | Use service mode: uploads return 202 and workers do the processing |
| `/health/ready` 503 with `database: false` or `broker: false` | PostgreSQL or Valkey down, or wrong URL or password | `make services-up` (development); check the service's environment and `deploy/secrets/` |
| `database_schema_current: false` | Migrations not applied for this code version | `python -m newgen_ocr.db upgrade` |
| `psycopg` import error: libpq not found | OS libpq missing | `sudo apt install libpq5` (OD-44) |
| Jobs stay `queued` | No worker running, or workers cannot reach Valkey | Start `python -m newgen_ocr.worker`; check its logs. Queued jobs are safe: they run once a worker starts |
| A job is `running` but nothing happens | Its worker died | It is requeued when its lease expires (`NGOCR_JOB_LEASE_S`) and after the next reaper pass. After the last attempt it fails with `WORKER_LOST`; then retry it |
| Signed out straight after signing in (development over plain HTTP) | `NGOCR_SESSION_COOKIE_SECURE=true` (or staging) on `http://` | Use HTTPS, or leave it unset in development |
| `CSRF_FAILED` in the web application | The page was open across a sign-in in another tab, or a script calls the API with the cookie | Reload the page. Scripts should use bearer tokens |
| `/app` shows "not built" | `make frontend` has not been run for this build | Run it at staging and redeploy the package |
| `JOB_ALREADY_ACTIVE` | The document already has a queued or running job | Wait for it, or cancel it, then submit again |

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

See "Backup and restore" and "Releases" above. On an installed host, use
`ngocrctl backup` and `ngocrctl restore STAMP`, which wrap the same scripts.
