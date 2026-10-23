# Security

## Assets

Uploaded documents (may contain PII, financial and identity data), extracted fields, review
decisions and audit trail, model weights (integrity), credentials (DB, broker, API keys).

## Trust boundaries

1. Client → API (untrusted input: files, parameters).
2. API/worker → PostgreSQL, Valkey, local storage (trusted network, authenticated).
3. Host → internet: **blocked in production**. Allowed only during controlled staging.

## Threat model (summary)

| Threat | Example | Control | Status |
|---|---|---|---|
| Data exfiltration via egress | Library telemetry, model auto-download, hosted AI call | Host/network egress block; FastAPI built-in telemetry disabled (ADR-0008); no AI/telemetry SDKs (test-enforced); runtime downloads disabled; explicit model paths | Code and test guards **implemented**; egress block **planned P10** |
| Malicious upload | Polyglot file, PDF bomb, decompression bomb, path traversal | Signature detection at offset 0 + extension cross-check; streaming body limit; size/page/pixel limits checked before decoding/rendering; Pillow restricted to the detected format's decoder; quarantine with cleanup on every failure; generated storage names and strict id validation | **Implemented P2**. Parsing is still in-process (OD-12) |
| Parser memory-safety bugs | Crafted PDF/TIFF exploits PDFium or libtiff | Patched versions (pip-audit gate, Pillow security override); format allowlist; process isolation with resource limits | Versions **implemented**; isolation **planned P6** (OD-12) |
| Sensitive data in logs | Field values or text in logs/errors | Metadata-only logging policy; query strings not logged; validation errors never echo input; generic 500s | **Implemented** |
| Log injection | Crafted `X-Request-ID` | Strict pattern; otherwise replaced | **Implemented** |
| Unauthorized access | Unauthenticated API use, including document retrieval | Hashed bearer tokens with roles, expiry and revocation on every endpoint except health and the static review page (ADR-0027, ADR-0029); tokens in PostgreSQL in service mode; `submitter` tokens reach only their own documents and jobs, and others' are reported as not found | **Implemented P9**; session sign-in for the full review app **planned P10** (OD-35) |
| Unauthorised or self-approved corrections | A reviewer edits values and approves their own work; two reviewers overwrite each other | Roles per endpoint; only the assignee corrects; four-eyes approval (configurable); `expected_version` optimistic concurrency; corrections normalised and validated like extracted values | **Implemented P8** (ADR-0028) |
| Tampering with review records | Editing or deleting a correction after the fact | Append-only history with a SHA-256 hash chain and `verify` endpoint; audit events per step; superseded revisions kept read-only; in service mode a database trigger refuses `UPDATE`/`DELETE` on the history and audit tables | Chain **implemented P8**; append-only tables **implemented P9**; off-host copy **planned P11** (OD-34) |
| Unearned automatic acceptance | Values accepted on raw OCR confidence | Six gates incl. a per-field threshold from a hash-identified calibration file bound to schema versions; off by default; production refuses synthetic calibrations | **Implemented P8** (ADR-0028) |
| Token theft from the review page | XSS reading the token from session storage | Strict CSP (`script-src 'self'`, no inline script, no external assets), text-only DOM insertion, `no-referrer`, `X-Frame-Options: DENY`; tokens never logged | **Implemented P8**; cookie sessions considered (OD-35) |
| Personal identifiers in review crops | Aadhaar numbers visible in source crops | Aadhaar numbers the OCR read are blacked out in crops and previews when masking is on (best effort); history values masked | **Implemented P8** (OD-30 remains for OCR artefacts) |
| Data at rest exposure | Read access to `data/` | Directories 0750, files 0640, originals 0440; quarantine files 0600; `no-store` on responses; uploads over 1 MiB spool to `TMPDIR` (OD-15) | Permissions **implemented**; volume encryption is a deployment control |
| Bundled native libraries | OpenCV PyPI wheel loads FFmpeg, codecs and a vendor OpenSSL 1.1.1k build into the process | Unused by the app; production requires a minimal source-built OpenCV, enforced by a startup guard (ADR-0015) | Guard **implemented**; minimal build **planned P6** |
| Supply chain | Compromised or typosquatted package | Hash-pinned locks with `--require-hashes`, pip-audit, license allowlist with per-version review, 14-day release age enforced by `--exclude-newer` (dated security overrides only), SBOM | **Implemented** (Python); containers **planned P6** |
| Secrets in source | Committed credentials | `.env` ignored, `.env.example` placeholders only, Gitleaks on history and working tree | **Implemented** |
| Model tampering | Swapped or corrupted weights | SHA-256 per file in `models.lock.json`, cross-checked against publisher hashes at fetch time, verified before first use at runtime | **Implemented P4** |
| Runtime model download / hub egress | PaddleX fetching models from Hugging Face/AIStudio/ModelScope/BOS | Model manager disabled (raises), explicit model dirs only, offline env flags, version-pinned lockdown; tests with an audit hook (0 socket attempts) and a no-network namespace | **Implemented P4** (ADR-0020) |
| Large third-party OCR stack | 56 extra packages incl. unused hub SDKs | Hash-pinned, age-gated, audited, licence-reviewed; never invoked; host egress block remains the binding control | Accepted by owner (ADR-0020) |
| Spreadsheet formula injection | An OCR'd cell such as `=HYPERLINK(...)` executes when an export is opened | CSV: text starting with `= + - @`, tab or CR prefixed with `'`; XLSX: every text cell stored as a string, never a formula (tested) | **Implemented P7** (ADR-0026) |
| Personal identifiers in outputs | Aadhaar numbers in extraction results and exports | Masked to the last 4 digits by default in values, raw text, candidates and evidence; identity fields marked `sensitive`; exports audited without content. OCR/layout artefacts still hold page text (OD-30) | **Implemented P7** |
| LLM / hosted AI in extraction | Field extraction calling an external model | Rules and templates only; import scan and socket audit-hook test on the extraction package | **Implemented P7** (ADR-0026) |
| Repudiation | Disputed upload, rejection, correction or approval | Audit events with request id, sha256 and outcome code (ADR-0014); every event carries the authenticated user as `actor` (workers act as the job's submitter; `system` only for maintenance) | **Implemented P2/P8/P9** |
| Database or broker exposed | PostgreSQL or Valkey reachable from outside, or with default passwords | No published ports on an internal-only network (`deploy/compose.yaml`); random passwords from secret files; SCRAM for PostgreSQL; Valkey `requirepass` from a config file, not the command line; development publishes on 127.0.0.1 only | **Implemented P9**; TLS if they ever leave the host (OD-41) |
| Credentials or content in logs from the new services | Database URLs, bound SQL values, broker URLs or exception text in logs or job records | URLs are `SecretStr`, logged as host/port/database only; SQLAlchemy `hide_parameters`; Celery banner masks the password; job errors store the code's public message only; log test with a document-content marker and the passwords | **Implemented P9** (tested) |
| Duplicate or concurrent processing | Retried requests, redelivered messages or two workers writing one document | Idempotency keys with request fingerprints; per-user content dedupe; one active job per document (unique index); atomic claims and leases checked on every write | **Implemented P9** (ADR-0029) |
| Queue flooding | A client submits faster than workers can process | `NGOCR_MAX_QUEUED_JOBS` (503) and `NGOCR_MAX_ACTIVE_JOBS_PER_USER` (429) checked in the submitting transaction; bounded worker concurrency | **Implemented P9**; per-address rate limiting at the proxy (OD-36) |
| Insecure code | Shell injection, unsafe deserialization | Bandit + Ruff `S` rules in the gate | **Implemented** |

## Security checks in the gate

`make security` (Bandit, pip-audit on both locks), `make secrets` (Gitleaks), `make licenses`,
`make sbom`. Container image scanning is added with the application images in Phase 11 (OD-5).

Note: `pip-audit` queries public vulnerability databases. It runs in development/CI only,
never on production hosts.

## Data handling rules

- Never commit real documents. Test fixtures are synthetic or explicitly licensed.
- Production documents and credentials are never sent to external AI services, including
  AI-assisted development tools.
- `data/` is git-ignored.
- Identity-document test fixtures use fake, checksum-valid numbers and are marked SPECIMEN.

## Incident considerations

On suspected compromise: isolate the host (network), preserve logs and the `data/` volume
snapshot, rotate DB/broker/API credentials, review access logs by `request_id`, and verify
model and package hashes against MODEL_INVENTORY and the lock files. The full procedure goes
in OPERATIONS_RUNBOOK.md in Phase 11.
