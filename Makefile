# Developer entry points. Requires uv (https://docs.astral.sh/uv/) and, for
# `secrets`, a Docker-compatible engine.

PYTHON_VERSION := 3.11
VENV           := .venv
BIN            := $(VENV)/bin
UV_COMPILE     := uv pip compile --python-version $(PYTHON_VERSION) \
                  --python-platform x86_64-manylinux_2_28 --generate-hashes -q
GITLEAKS_IMAGE := ghcr.io/gitleaks/gitleaks:v8.21.2
REPORTS        := reports

.PHONY: help venv lock lint format typecheck test security secrets licenses sbom check run clean

help:
	@grep -E '^[a-z]+:.*?## ' $(MAKEFILE_LIST) | awk -F':.*?## ' '{printf "  %-10s %s\n", $$1, $$2}'

venv: ## Create .venv from the hash-pinned lock files
	uv venv --python $(PYTHON_VERSION) $(VENV)
	uv pip sync --python $(BIN)/python --require-hashes requirements.lock requirements-dev.lock
	uv pip install --python $(BIN)/python --no-deps -e .

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

test: ## pytest with coverage gate
	$(BIN)/pytest --cov

security: ## Bandit + dependency vulnerability audit
	$(BIN)/bandit -q -c pyproject.toml -r src scripts
	$(BIN)/pip-audit --strict --require-hashes -r requirements.lock
	$(BIN)/pip-audit --strict --require-hashes -r requirements-dev.lock

secrets: ## Gitleaks scan of git history and working tree
	docker run --rm -v "$(CURDIR):/repo:ro" $(GITLEAKS_IMAGE) git /repo --no-banner
	docker run --rm -v "$(CURDIR):/repo:ro" $(GITLEAKS_IMAGE) dir /repo --no-banner

licenses: ## Fail on non-allowlisted runtime licenses
	$(BIN)/python scripts/check_licenses.py requirements.lock

sbom: ## CycloneDX SBOM of runtime dependencies -> reports/
	mkdir -p $(REPORTS)
	$(BIN)/pip-audit --require-hashes -r requirements.lock \
		--format cyclonedx-json -o $(REPORTS)/sbom-runtime.cdx.json

check: lint typecheck test security licenses ## Full local acceptance gate (plus `make secrets`)

run: ## Run the API on 127.0.0.1:8000 with console logs
	mkdir -p data
	NGOCR_LOG_FORMAT=console $(BIN)/uvicorn newgen_ocr.api.app:create_app --factory \
		--host 127.0.0.1 --port 8000 --no-server-header

clean: ## Remove caches and build artefacts (not .venv or data/)
	rm -rf .pytest_cache .mypy_cache .ruff_cache .coverage htmlcov build src/*.egg-info $(REPORTS)
