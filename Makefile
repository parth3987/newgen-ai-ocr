# Developer entry points. Requires uv (https://docs.astral.sh/uv/) and, for
# `secrets`, a Docker-compatible engine.

PYTHON_VERSION := 3.11
VENV           := .venv
BIN            := $(VENV)/bin
# ADR-0007: only resolve releases at least 14 days old.
EXCLUDE_NEWER  := $(shell date -u -d '14 days ago' +%Y-%m-%d)
# Security overrides (package=cutoff): newer releases admitted because the age-floor
# version has known vulnerabilities. Remove each once the floor passes it. See ADR-0007.
SECURITY_OVERRIDES := pillow=2026-10-22 certifi=2026-10-22 filelock=2026-10-18 setuptools=2026-10-20
UV_COMPILE     := uv pip compile --python-version $(PYTHON_VERSION) \
                  --python-platform x86_64-manylinux_2_28 --generate-hashes -q --upgrade \
                  --exclude-newer $(EXCLUDE_NEWER) --override overrides.txt \
                  $(foreach o,$(SECURITY_OVERRIDES),--exclude-newer-package $(o))
GITLEAKS_IMAGE := ghcr.io/gitleaks/gitleaks:v8.21.2@sha256:e5fa09c73ff086ea57f3731a4e1fb372969fb73314cc932020472eb23a5dfc7e
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

.PHONY: help evaluate release-gates signing-key staging bundle rehearse-install certs images stack-up stack-down image-scan backup restore offline-test frontend frontend-lock frontend-test frontend-licenses services-secrets services-up services-down venv lock models fonts eval-languages eval-handwriting eval-layout eval-extraction calibrate schemas lint format typecheck test security secrets licenses sbom bench eval check run clean

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
	$(BIN)/ruff format src tests scripts deploy/bundle
	$(BIN)/ruff check --fix src tests scripts deploy/bundle

lint: ## Ruff format check + lint
	$(BIN)/ruff format --check src tests scripts deploy/bundle
	$(BIN)/ruff check src tests scripts deploy/bundle

typecheck: ## mypy (strict)
	$(BIN)/mypy

test: ## pytest with coverage gate (service-mode tests need `make services-up`) -> reports/pytest.xml
	@mkdir -p $(REPORTS)
	@$(TEST_SERVICES) $(BIN)/pytest --cov --junitxml=$(REPORTS)/pytest.xml

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

services-secrets: ## Create random service passwords, connection URLs and dev TLS certs in deploy/secrets/ (once)
	@mkdir -p -m 700 $(SECRETS)
	@for name in postgres_password valkey_password; do \
		[ -f $(SECRETS)/$$name ] || (umask 077; $(BIN)/python -c "import secrets; print(secrets.token_hex(24), end='')" > $(SECRETS)/$$name); \
	done
	@# Connection URLs for the API and workers (inside the backend network).
	@umask 077; printf 'postgresql+psycopg://ngocr:%s@postgres:5432/ngocr' "$$(cat $(SECRETS)/postgres_password)" > $(SECRETS)/.database_url \
		&& mv -f $(SECRETS)/.database_url $(SECRETS)/database_url
	@umask 077; printf 'redis://:%s@valkey:6379/0' "$$(cat $(SECRETS)/valkey_password)" > $(SECRETS)/.broker_url \
		&& mv -f $(SECRETS)/.broker_url $(SECRETS)/broker_url
	@# Readable inside the containers (non-root users); the directory itself is owner-only.
	@chmod 0444 $(SECRETS)/postgres_password $(SECRETS)/valkey_password $(SECRETS)/database_url $(SECRETS)/broker_url
	@[ -f $(SECRETS)/tls/server.crt ] || $(MAKE) --no-print-directory certs

certs: ## Development/test TLS: a local CA and a server certificate for NGOCR_TLS_HOSTNAME (default localhost)
	@mkdir -p -m 700 $(SECRETS)/tls
	@cd $(SECRETS)/tls && umask 077 && \
	openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:P-256 -nodes -days 365 \
		-subj "/CN=New Gen AI OCR development CA" -keyout ca.key -out ca.crt 2>/dev/null && \
	openssl req -newkey ec -pkeyopt ec_paramgen_curve:P-256 -nodes \
		-subj "/CN=$${NGOCR_TLS_HOSTNAME:-localhost}" -keyout server.key -out server.csr 2>/dev/null && \
	printf 'subjectAltName=DNS:%s,DNS:localhost,IP:127.0.0.1\nextendedKeyUsage=serverAuth\nkeyUsage=digitalSignature\nbasicConstraints=CA:FALSE\n' \
		"$${NGOCR_TLS_HOSTNAME:-localhost}" > server.ext && \
	openssl x509 -req -in server.csr -CA ca.crt -CAkey ca.key -CAcreateserial -days 90 \
		-extfile server.ext -out server.crt 2>/dev/null && rm -f server.csr server.ext ca.srl && \
	chmod 0444 ca.crt server.crt && chmod 0400 server.key ca.key
	@echo "dev TLS: $(SECRETS)/tls/ca.crt (trust it in your browser for testing only)"

services-up: services-secrets ## Start PostgreSQL and Valkey (loopback only) for development and tests
	$(COMPOSE) up -d --wait

services-down: ## Stop PostgreSQL and Valkey (data volumes are kept)
	$(COMPOSE) down

IMAGE          ?= localhost/newgen-ocr:$(shell $(BIN)/python -c "import newgen_ocr; print(newgen_ocr.__version__)")
STACK          := $(ENGINE) compose -f deploy/compose.yaml --profile app
TRIVY_IMAGE    := docker.io/aquasec/trivy:0.75.0@sha256:af6acf9a6b85dfe389a1941505c0ce9efef52a4719635e1a962f022a3d855daa
TRIVY          := $(ENGINE) run --rm -v $(CURDIR):/repo:ro -v $(CURDIR)/$(REPORTS):/reports \
                  -v ngocr-trivy-cache:/root/.cache/trivy \
                  -e TRIVY_SKIP_VERSION_CHECK=true $(TRIVY_IMAGE)

images: ## STAGING (network): build the API/worker image (needs `make frontend` and `make models`)
	$(BIN)/python scripts/fetch_models.py --verify
	test -f src/newgen_ocr/webapp/dist/index.html
	$(ENGINE) build -f deploy/Dockerfile -t $(IMAGE) .

stack-up: services-secrets ## Start the whole stack (TLS on https://127.0.0.1:8443 by default)
	NGOCR_IMAGE=$(IMAGE) $(STACK) up -d --wait

stack-down: ## Stop the whole stack (volumes kept)
	$(STACK) down

image-scan: ## STAGING (network for the vulnerability DB): Trivy on the image, OS packages and locks -> reports/
	mkdir -p $(REPORTS)
	rm -f $(REPORTS)/image.tar
	$(ENGINE) save -o $(REPORTS)/image.tar $(IMAGE)
	status=0; \
	$(TRIVY) image --input /reports/image.tar --scanners vuln,secret,license --format json -o /reports/trivy-image.json && \
	$(TRIVY) image --input /reports/image.tar --format cyclonedx -o /reports/sbom-image.cdx.json && \
	$(TRIVY) image --input /reports/image.tar --scanners vuln --severity HIGH,CRITICAL --exit-code 1 --ignore-unfixed || status=$$?; \
	rm -f $(REPORTS)/image.tar; exit $$status
	$(TRIVY) fs /repo --scanners vuln,secret,misconfig --skip-dirs /repo/models,/repo/data,/repo/.venv,/repo/frontend/node_modules,/repo/deploy/secrets --format json -o /reports/trivy-fs.json

backup: ## Back up the database and document volume -> deploy/backups/<timestamp>
	mkdir -p deploy/backups && chmod 700 deploy/backups
	$(ENGINE) compose -f deploy/compose.yaml --profile ops run --rm backup

restore: ## Restore BACKUP=<timestamp> (stops the API and workers first), then verify
	$(STACK) stop api worker ingress
	BACKUP=$(BACKUP) $(ENGINE) compose -f deploy/compose.yaml --profile ops run --rm restore
	$(STACK) up -d --wait
	$(STACK) exec -T api python -m newgen_ocr.ops verify

offline-test: ## Production stack with no egress: network probes and a full job over TLS
	$(BIN)/python scripts/offline_test.py --image $(IMAGE)

security: ## Bandit + dependency vulnerability audit
	$(BIN)/bandit -q -c pyproject.toml -r src scripts
	$(BIN)/pip-audit --strict --require-hashes --disable-pip -r requirements.lock
	$(BIN)/pip-audit --strict --require-hashes --disable-pip -r requirements-dev.lock
	cd frontend && npm audit --audit-level=low

secrets: ## Gitleaks scan of git history and working tree
	$(ENGINE) run --rm -v "$(CURDIR):/repo:ro" $(GITLEAKS_IMAGE) git /repo --no-banner
	$(ENGINE) run --rm -v "$(CURDIR):/repo:ro" $(GITLEAKS_IMAGE) dir /repo --no-banner

licenses: ## Fail on non-allowlisted runtime licenses
	$(BIN)/python scripts/check_licenses.py requirements.lock

sbom: ## CycloneDX SBOM of runtime dependencies (Python; the image SBOM is in `make image-scan`) -> reports/
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

# ------------------------------------------------------------------ offline bundle (ADR-0032)
SIGNING_KEY    ?= $(HOME)/.config/newgen-ocr/bundle-signing/id_ed25519

signing-key: ## Create the bundle signing key (Ed25519; asks for a passphrase), outside the repository
	@if [ -f "$(SIGNING_KEY)" ]; then echo "exists: $(SIGNING_KEY)"; else \
		mkdir -p -m 700 "$$(dirname "$(SIGNING_KEY)")" && \
		ssh-keygen -t ed25519 -C newgen-ocr-release -f "$(SIGNING_KEY)"; fi
	@ssh-keygen -l -f "$(SIGNING_KEY)"

staging: ## STAGING (network): fetch, verify, build, scan, test, then the signed bundle -> dist/
	$(MAKE) venv models frontend
	$(MAKE) lint typecheck licenses frontend-licenses security secrets
	$(MAKE) services-up
	$(MAKE) test frontend-test
	$(MAKE) images
	$(MAKE) image-scan sbom offline-test
	$(MAKE) evaluate
	$(MAKE) bundle

bundle: ## Signed offline bundle of IMAGE from the staging gates' reports -> dist/
	$(BIN)/python scripts/build_bundle.py --image $(IMAGE) --signing-key "$(SIGNING_KEY)" $(BUNDLE_ARGS)

rehearse-install: ## Install, restart, upgrade and roll back BUNDLE on a simulated host with no network
	$(BIN)/python scripts/rehearse_install.py $(REHEARSAL_ARGS)

# ------------------------------------------------------------------ release readiness (ADR-0033)
evaluate: ## Release evaluation: every evaluation cohort, every metric -> reports/evaluation/
	$(BIN)/python scripts/evaluate_release.py $(EVALUATE_ARGS)

release-gates: ## Production approval: staging evidence, separation, freshness, targets (BENCHMARK=report.json)
	$(BIN)/python scripts/release_gates.py --image $(IMAGE) $(foreach b,$(BENCHMARK),--benchmark $(b)) $(GATES_ARGS)
