# Changelog

All material changes by phase. Dates are ISO 8601.

## [Unreleased]

### Phase 1: Development foundation (2026-10-22)
- Python 3.11 project (`pyproject.toml`) with Ruff, strict mypy, pytest and a 90% coverage gate.
- Hash-pinned `requirements.lock` / `requirements-dev.lock` generated from `requirements*.in`.
- FastAPI app factory, `NGOCR_*` settings, JSON structured logging with request ids, stable
  error envelope.
- `/health/live` and `/health/ready` endpoints.
- Policy tests: no hosted-AI or telemetry SDKs, no network-client imports, CDN docs UI disabled.
- Tooling: Makefile, license allowlist script, Bandit, pip-audit, Gitleaks, CycloneDX SBOM,
  GitHub Actions CI.
- `.env.example`; `.gitignore` extended for `data/`, caches and reports.

### Phase 0: Planning (2026-10-22)
- Repository and host inspection; architecture, implementation plan, decisions, security,
  testing, evaluation, model inventory, offline deployment and runbook documents.
