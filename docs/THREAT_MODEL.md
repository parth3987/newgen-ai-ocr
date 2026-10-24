# Threat Model

Scope: the production deployment in `deploy/compose.yaml` (ADR-0031) on one host, plus the
staging build that produces it. Method: data-flow diagram, trust boundaries, then STRIDE
per element. Reviewed 2026-10-24 (Phase 11). Re-review it whenever a component, data flow
or trust boundary changes. Control status and residual risks are tracked in SECURITY.md and
DECISIONS.md.

## Assets

| Asset | Why it matters |
|---|---|
| Uploaded documents and page images | Personal, financial and identity data (PAN, Aadhaar, GSTIN, bank statements) |
| Extracted values, OCR text, review corrections | The same data in structured form, and the record of decisions |
| Review history and audit log | Evidence of who did what; must be tamper-evident |
| Credentials | User passwords (hashed), session cookies, API tokens, database and broker passwords, TLS key |
| Model weights and the software supply | Integrity of every result depends on them |
| Availability of the processing pipeline | Backlogs delay business processes |

## Actors

- **Web users** with roles: submitter, viewer, reviewer, approver, admin.
- **Integrations** using API tokens.
- **Operators** with shell access to the host.
- **Attackers:**
  - an unauthenticated network client;
  - an authenticated user exceeding their role;
  - malicious document content;
  - a compromised dependency or model;
  - someone holding a stolen backup.

## Data flows and trust boundaries

```
 Browser / integration ──TLS──▶ [B1] ingress (nginx) ──http, edge net──▶ API ──┐
                                                                               │ backend net (internal)
                                         workers ◀── Valkey (job ids) ◀────────┤
                                            │                                  ▼
                                            └──────────▶ PostgreSQL ◀──────────┘
 API + workers ◀──▶ document volume (/data)        models (read-only, in image)
 [B2] container ↔ host    [B3] host ↔ outside (egress blocked)    [B4] staging build ↔ internet
```

- **B1, the client to the service.** Everything crossing it is untrusted: files,
  parameters, headers and cookies.
- **B2, a container to the host and the others.** Each container is confined to its own
  user, capabilities, filesystem and networks.
- **B3, the host to the outside.** Production has no outbound path (layer 1: internal
  container networks; layer 2: the host firewall).
- **B4, staging to the internet.** This is the only place anything is downloaded: base
  images, packages and models, each checked against pinned hashes.

## STRIDE by element

| Element | Threat (STRIDE) | Control | Residual / open |
|---|---|---|---|
| Ingress | **S** spoofed server; **I** eavesdropping | TLS 1.2/1.3 only, forward-secret AEAD ciphers, HSTS, session tickets off; organisation-issued certificates | Certificate lifecycle is the operator's (runbook) |
| Ingress | **D** request floods, slow clients, oversized bodies | Per-address request and connection limits, a stricter limit on sign-in, body cap 51 MiB, header and body timeouts | One host: no upstream DDoS protection |
| Ingress | **T** forged `X-Forwarded-For` | The header is replaced, not appended | — |
| API | **S** stolen or guessed credentials | scrypt passwords with lockout; 256-bit tokens; HttpOnly, SameSite=Strict, Secure session cookies with idle and absolute expiry | No MFA or SSO (OD-45) |
| API | **T** CSRF, clickjacking, XSS | CSRF token on every cookie change; CSP `default-src 'none'`; `frame-ancestors 'none'`; text-only rendering | — |
| API | **E** a role exceeding its rights | Role checks on every route; submitters limited to their own documents and jobs (others are reported as not found); four-eyes approval; tests call the API directly as each role | — |
| API | **I** data in logs or errors | Metadata-only logs; generic 500s; SQL parameters hidden; job errors carry codes only; tested with marker content | — |
| API | **D** queue flooding | Queue and per-user admission limits; worker concurrency bounded | — |
| API, workers | **T, E** malicious documents (polyglots, decompression bombs, parser exploits) | Signature and extension checks, size, page and pixel limits before decoding; restricted decoders; quarantine; parsing in workers; non-root user, no capabilities, read-only root, memory and process limits | Parsers still run in the worker process, not a separate sandbox (OD-12) |
| Workers | **R** disputed processing | Job events per attempt and stage; audit events name the submitter | — |
| Workers | **I** exfiltration by a compromised dependency or model code | No route out (internal networks; host firewall); runtime downloads disabled in code; socket-audit tests | Within the host, a compromised worker can read what the worker can read |
| PostgreSQL, Valkey | **S, I** reached from outside, or with default credentials | No published ports; internal network only; random passwords from secret files; SCRAM; Valkey password in a tmpfs config file | No TLS between containers on one host (OD-41) |
| PostgreSQL | **T** rewriting history or audit | Append-only triggers; hash-chained review history with `verify` | A database superuser can still rewrite: copy history and audit off the host (OD-34) |
| Document volume | **I** read access to stored documents | Directories 0750, files 0640, originals 0440, owned by the service user; temporary uploads on the volume (owner-only) | Encrypting the volume at rest is a host control (runbook) |
| Backups | **I** a stolen backup; **T** a corrupted restore | Backup directory 0700; SHA-256 sums checked before restore; `ops verify` after restore | Backups are not encrypted by this tool: store them on encrypted media (OD-42) |
| Supply chain | **T** a compromised package, image or model | Hash-pinned Python and npm locks, 14-day age rule, licence allowlists, pip-audit, npm audit and Trivy; images pinned by digest; models hash-locked and verified at build and at first use, with unlisted files refused; OpenCV built from a hash-checked source | Upstream compromise inside the age window before detection |
| Staging build | **T** a tampered build environment | Builds run only from the pinned inputs above; SBOM and scans kept with each release | No reproducible-build attestation yet |
| Telemetry | **I** silent egress (analytics, model hubs, AI services) | None in the code; FastAPI telemetry off; PaddleX hub access disabled; CSP in the browser; no egress route (offline test) | — |

## Review checklist (per release)

1. Is there a new dependency, model or image? It needs lock hashes, an age check, a licence
   review and an inventory entry.
2. Has a data flow or port changed? Update this diagram, and check the compose networks and
   nginx.
3. Run `make check`, `make secrets`, `make image-scan` and `make offline-test`, then a
   backup and restore drill (SECURITY_REVIEW.md lists the evidence).
