# New Gen AI OCR: Precision Data Extraction

A locally executed, open-source AI OCR platform for multilingual document processing,
scanned PDFs and handwriting recognition.

## Objectives
- CPU-based OCR processing
- Multilingual document extraction (English, Telugu, Hindi, Tamil, Kannada, Marathi)
- Scanned and native PDF processing, tables, forms, batch jobs
- Local-only inference that works with outbound network access blocked
- No external LLM or hosted AI APIs

## Status

Phase 2 (document ingestion and PDF processing) is complete. The service accepts PDF, JPEG,
PNG and TIFF uploads, validates them safely, uses embedded text from native PDF pages and
renders scanned pages to images, ready for OCR. **No OCR engine is integrated yet.** See [docs/IMPLEMENTATION_PLAN.md](docs/IMPLEMENTATION_PLAN.md).

## Quick start

Requirements: [uv](https://docs.astral.sh/uv/), GNU make; Docker for `make secrets`.

```bash
make venv              # Python 3.11 venv from hash-pinned locks
make check             # lint, types, tests, security, licenses
make run               # API on http://127.0.0.1:8000
curl localhost:8000/health/ready
curl -F file=@document.pdf localhost:8000/api/v1/documents
```

Configuration: see [.env.example](.env.example).

## Documentation

| Doc | Purpose |
|---|---|
| [ARCHITECTURE](docs/ARCHITECTURE.md) | Components, pipeline, module boundaries |
| [IMPLEMENTATION_PLAN](docs/IMPLEMENTATION_PLAN.md) | Phases, gates, status |
| [DECISIONS](docs/DECISIONS.md) | ADRs and open decisions |
| [API_SPECIFICATION](docs/API_SPECIFICATION.md) | Endpoints and error codes |
| [SECURITY](docs/SECURITY.md) | Threat model and controls |
| [OFFLINE_DEPLOYMENT](docs/OFFLINE_DEPLOYMENT.md) | Staging, bundle and disconnected install |
| [TESTING](docs/TESTING.md) | Test strategy and commands |
| [ACCURACY_EVALUATION](docs/ACCURACY_EVALUATION.md) | Metrics and dataset rules |
| [MODEL_INVENTORY](docs/MODEL_INVENTORY.md) | Models, hashes and licenses |
| [THIRD_PARTY_NOTICES](docs/THIRD_PARTY_NOTICES.md) | Dependency licenses |
| [OPERATIONS_RUNBOOK](docs/OPERATIONS_RUNBOOK.md) | Running and troubleshooting |
| [CHANGELOG](CHANGELOG.md) | Changes by phase |
