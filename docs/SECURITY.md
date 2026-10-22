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
| Data exfiltration via egress | Library telemetry, model auto-download, hosted AI call | Host/network egress block; no AI/telemetry SDKs (test-enforced); runtime downloads disabled; explicit model paths | Test guard **implemented**; egress block **planned P9** |
| Malicious upload | Polyglot file, PDF bomb, decompression bomb, path traversal | Magic-byte sniffing, size/page/pixel limits, quarantine, sandboxed parsing, generated storage names | **Planned P2/P3** |
| Sensitive data in logs | Field values or text in logs/errors | Metadata-only logging policy; query strings not logged; validation errors never echo input; generic 500s | **Implemented** |
| Log injection | Crafted `X-Request-ID` | Strict pattern; otherwise replaced | **Implemented** |
| Unauthorized access | Unauthenticated API use | API keys + review UI sessions (OD-6) | **Planned P2**. Until then, bind to localhost/trusted network only |
| Supply chain | Compromised or typosquatted package | Hash-pinned locks with `--require-hashes`, pip-audit, license allowlist, 14-day release age, SBOM | **Implemented** (Python); containers **planned P2** |
| Secrets in source | Committed credentials | `.env` ignored, `.env.example` placeholders only, Gitleaks on history and working tree | **Implemented** |
| Model tampering | Swapped weights | SHA-256 recorded in MODEL_INVENTORY and verified at load | **Planned P4** |
| Insecure code | Shell injection, unsafe deserialization | Bandit + Ruff `S` rules in the gate | **Implemented** |

## Security checks in the gate

`make security` (Bandit, pip-audit on both locks), `make secrets` (Gitleaks), `make licenses`,
`make sbom`. Container image scanning is to be added in Phase 2 (OD-5).

Note: `pip-audit` queries public vulnerability databases. It runs in development/CI only,
never on production hosts.

## Data handling rules

- Never commit real documents. Test fixtures are synthetic or explicitly licensed.
- Production documents and credentials are never sent to external AI services, including
  AI-assisted development tools.
- `data/` is git-ignored.

## Incident considerations

On suspected compromise: isolate the host (network), preserve logs and the `data/` volume
snapshot, rotate DB/broker/API credentials, review access logs by `request_id`, and verify
model and package hashes against MODEL_INVENTORY and the lock files. The full procedure goes
in OPERATIONS_RUNBOOK.md in Phase 9.
