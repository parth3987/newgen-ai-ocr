# Developer entry points. Requires uv (https://docs.astral.sh/uv/) and, for
# `secrets`, a Docker-compatible engine.

PYTHON_VERSION := 3.11
VENV           := .venv
BIN            := $(VENV)/bin
# ADR-0007: only resolve releases at least 14 days old.
EXCLUDE_NEWER  := $(shell date -u -d '14 days ago' +%Y-%m-%d)
# Security overrides (package=cutoff): newer releases admitted because the age-floor
# version has known vulnerabilities. Remove each once the floor passes it. See ADR-0007.
SECURITY_OVERRIDES := pillow=2026-10-22 certifi=2026-10-22
UV_COMPILE     := uv pip compile --python-version $(PYTHON_VERSION) \
                  --python-platform x86_64-manylinux_2_28 --generate-hashes -q --upgrade \
                  --exclude-newer $(EXCLUDE_NEWER) --override overrides.txt \
                  $(foreach o,$(SECURITY_OVERRIDES),--exclude-newer-package $(o))
GITLEAKS_IMAGE := ghcr.io/gitleaks/gitleaks:v8.21.2
# PostgreSQL and Valkey for service mode (ADR-0029); dev override publishes on 127.0.0.1.
ENGINE         := $(shell command -v podman >/dev/null 2>&1 && echo podman || echo docker)
COMPOSE        := $(ENGINE) compose -f deploy/compose.yaml -f deploy/compose.dev.yaml
SECRETS        := deploy/secrets
# Service-mode tests run against these (a throwaway database per run; Valkey db 15).
# Without the secrets files they are skipped, and pytest reports them as skipped.
TEST_SERVICES  := if [ -f $(SECRETS)/postgres_password ]; then \
                  export NGOCR_TEST_DATABASE_URL="postgresql+psycopg://ngocr:$$(cat $(SECRETS)/postgres_password)@127.0.0.1:55432/ngocr"; \
                  export NGOCR_TEST_BROKER_URL="redis://:$$(cat $(SECRETS)/valkey_password)@127.0.0.1:56379/15"; fi;
REPORTS        := reports

.PHONY: help frontend frontend-lock frontend-test frontend-licenses services-secrets services-up services-down venv lock models fonts eval-languages eval-handwriting eval-layout eval-extraction calibrate schemas lint format typecheck test security secrets licenses sbom bench eval check run clean

help:
	@grep -E '^[a-z-]+:.*?## ' $(MAKEFILE_LIST) | awk -F':.*?## ' '{printf "  %-17s %s\n", $$1, $$2}'

venv: ## Create .venv from the hash-pinned lock files
	uv venv --clear --python $(PYTHON_VERSION) $(VENV)
	uv pip sync --python $(BIN)/python --require-hashes requirements.lock requirements-dev.lock
	uv pip install --python $(BIN)/python --no-deps -e .

models: ## STAGING ONLY (network): fetch pinned OCR models into models/ and verify hashes
	$(BIN)/python scripts/fetch_models.py

fonts: ## STAGING ONLY (network): fetch pinned OFL handwriting fonts (evaluation only)
	$(BIN)/python scripts/fetch_models.py --lock evaluation/fonts.lock.json --models-dir evaluation/fonts

lock: ## Re-resolve and hash-pin requirements*.lock (review the diff!)
	$(UV_COMPILE) requirements.in -o requirements.lock
	$(UV_COMPILE) requirements-dev.in -o requirements-dev.lock

format: ## Auto-format code
	$(BIN)/ruff format src tests scripts
	$(BIN)/ruff check --fix src tests scripts

lint: ## Ruff format check + lint
	$(BIN)/ruff format --check src tests scripts
	$(BIN)/ruff check src tests scripts

typecheck: ## mypy (strict)
	$(BIN)/mypy

test: ## pytest with coverage gate (service-mode tests need `make services-up`)
	@$(TEST_SERVICES) $(BIN)/pytest --cov

frontend: ## STAGING (network for npm ci): build the web application into the Python package
	cd frontend && npm ci --ignore-scripts --no-audit && npm run build

frontend-lock: ## Re-resolve frontend/package-lock.json from releases at least 14 days old
	cd frontend && rm -rf node_modules package-lock.json && \
		npm install --ignore-scripts --before=$(EXCLUDE_NEWER)

frontend-test: ## Frontend type check and unit tests (Vitest), plus the npm licence check
	cd frontend && npx tsc --noEmit && npx vitest run
	$(BIN)/python scripts/check_npm_licenses.py

frontend-licenses: ## npm lock: integrity hashes and licence allowlist
	$(BIN)/python scripts/check_npm_licenses.py

services-secrets: ## Create random PostgreSQL and Valkey passwords in deploy/secrets/ (once)
	@mkdir -p -m 700 $(SECRETS)
	@for name in postgres_password valkey_password; do \
		[ -f $(SECRETS)/$$name ] || (umask 077; $(BIN)/python -c "import secrets; print(secrets.token_hex(24), end='')" > $(SECRETS)/$$name); \
	done

services-up: services-secrets ## Start PostgreSQL and Valkey (loopback only) for development and tests
	$(COMPOSE) up -d --wait

services-down: ## Stop PostgreSQL and Valkey (data volumes are kept)
	$(COMPOSE) down

security: ## Bandit + dependency vulnerability audit
	$(BIN)/bandit -q -c pyproject.toml -r src scripts
	$(BIN)/pip-audit --strict --require-hashes --disable-pip -r requirements.lock
	$(BIN)/pip-audit --strict --require-hashes --disable-pip -r requirements-dev.lock
	cd frontend && npm audit --audit-level=low

secrets: ## Gitleaks scan of git history and working tree
	docker run --rm -v "$(CURDIR):/repo:ro" $(GITLEAKS_IMAGE) git /repo --no-banner
	docker run --rm -v "$(CURDIR):/repo:ro" $(GITLEAKS_IMAGE) dir /repo --no-banner

licenses: ## Fail on non-allowlisted runtime licenses
	$(BIN)/python scripts/check_licenses.py requirements.lock

sbom: ## CycloneDX SBOM of runtime dependencies -> reports/
	mkdir -p $(REPORTS)
	$(BIN)/pip-audit --require-hashes --disable-pip -r requirements.lock \
		--format cyclonedx-json -o $(REPORTS)/sbom-runtime.cdx.json

bench: ## CPU benchmark of quality assessment and preprocessing (docs/PERFORMANCE.md)
	$(BIN)/python scripts/benchmark_preprocessing.py --repeat 5

eval: ## OCR accuracy + throughput on the synthetic baseline corpus (docs/ACCURACY_EVALUATION.md)
	$(BIN)/python scripts/evaluate_ocr.py --build

eval-languages: ## Script/orientation ID, hi/mr ID and mixed-script routing evaluation
	$(BIN)/python scripts/evaluate_language_id.py

eval-handwriting: ## Handwriting evaluation (synthetic fonts; --dataset DIR for real samples)
	$(BIN)/python scripts/evaluate_handwriting.py

eval-layout: ## Layout, tables, reading order and key-value pairs on the synthetic layout set
	$(BIN)/python scripts/evaluate_layout.py --rebuild

eval-extraction: ## Classification, fields, tables and checks on the synthetic extraction set
	$(BIN)/python scripts/evaluate_extraction.py --rebuild

calibrate: ## Fit review thresholds on the synthetic calibration set (docs/REVIEW.md)
	$(BIN)/python scripts/calibrate_review.py --rebuild

schemas: ## Regenerate documented JSON Schemas and the document-type reference
	$(BIN)/python scripts/generate_schemas.py

check: lint typecheck services-up test frontend-test security licenses ## Full local acceptance gate (plus `make secrets`)

run: ## Run the API on 127.0.0.1:8000 with console logs
	mkdir -p data
	NGOCR_LOG_FORMAT=console $(BIN)/uvicorn newgen_ocr.api.app:create_app --factory \
		--host 127.0.0.1 --port 8000 --no-server-header

clean: ## Remove caches and build artefacts (not .venv or data/)
	rm -rf .pytest_cache .mypy_cache .ruff_cache .coverage htmlcov build src/*.egg-info $(REPORTS)
