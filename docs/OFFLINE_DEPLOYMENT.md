# Offline Deployment

Status: **planned (Phase 10)**. Only the Python dependency pinning described in §2 exists
today.

## 1. Model

| Stage | Network | Activities |
|---|---|---|
| Staging (connected) | Allowed, controlled | Resolve and download wheels, container images and model weights; verify hashes and licenses; build the bundle |
| Transfer | n/a | Move the signed bundle to the production host |
| Production (disconnected) | **Outbound blocked at host/network layer** | Install from the bundle only; process documents |

Application flags alone are not sufficient. Egress is enforced by host firewall rules
(nftables default-deny outbound, allowing only loopback and the internal container network)
and by an `internal: true` Docker network for the api/worker/db/broker services.

## 2. Python dependencies [implemented]

- `requirements.lock` is hash-pinned for Python 3.11 / manylinux x86_64.
- Bundle creation (planned): `pip download --require-hashes -r requirements.lock -d wheels/`,
  then on the target `pip install --no-index --find-links wheels/ --require-hashes -r requirements.lock`.

## 3. Models [implemented P4]

- Staging: `make models` downloads exactly the revisions pinned in `models.lock.json` into
  `models/` and verifies every file's SHA-256 (about 198 MB). Copy `models/` and
  `models.lock.json` into the offline bundle.
- Production: the application only reads `models/` (`NGOCR_MODELS_DIR`) and verifies each
  model's hashes before first use. A missing model returns `MODEL_NOT_AVAILABLE`; a
  mismatch returns `MODEL_INTEGRITY_ERROR`. Nothing is downloaded.
- PaddleX's own downloader is disabled in code (ADR-0020). Tesseract always runs with an
  explicit `--tessdata-dir`.
- System package for the bundle: `tesseract-ocr` 5.5.0 (Ubuntu apt; download the `.deb`
  files at staging with `apt-get download`).

## 4. Verification [planned P10]

An automated test on the production host attempts outbound connections (DNS, HTTPS) and must
observe them being blocked, then runs an end-to-end OCR job successfully.
