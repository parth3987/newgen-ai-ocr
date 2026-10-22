# Third-Party Notices

Generated from `scripts/check_licenses.py` against `requirements.lock` on 2026-10-22.
Update this file whenever a lock file changes.

## Runtime (shipped)

| Package | Version | License |
|---|---|---|
| annotated-doc | 0.0.5 | MIT |
| annotated-types | 0.8.0 | MIT |
| anyio | 4.15.1 | MIT |
| click | 8.5.0 | BSD-3-Clause |
| fastapi | 0.159.1 | MIT |
| h11 | 0.16.0 | MIT |
| idna | 3.20 | BSD-3-Clause |
| opentelemetry-api | 1.45.1 | Apache-2.0 (see ADR-0008) |
| pydantic | 2.14.1 | MIT |
| pydantic-core | 2.50.1 | MIT |
| pydantic-settings | 2.16.1 | MIT |
| python-dotenv | 1.2.4 | BSD-3-Clause |
| starlette | 1.9.0 | BSD-3-Clause |
| typing-extensions | 4.16.0 | PSF-2.0 |
| typing-inspection | 0.4.4 | MIT |
| uvicorn | 0.54.0 | BSD-3-Clause |

Full license texts are included in each package's distribution metadata and will be collected
into the offline bundle (Phase 9).

## Development and CI only (not shipped)

pytest, pytest-cov, coverage, httpx, ruff, mypy, bandit, pip-audit, pip-licenses and their
dependencies, as pinned in `requirements-dev.lock`. Container image used in CI:
`ghcr.io/gitleaks/gitleaks:v8.21.2` (MIT).

## Models

None. See MODEL_INVENTORY.md.
