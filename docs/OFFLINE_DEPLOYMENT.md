# Offline Deployment

Status: **implemented (Phase 11, ADR-0031)**. The bundle is the container images plus
`deploy/`. The offline test (§4) runs the production stack with no egress.

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

## 3a. Web application [implemented P10]

- Staging: `make frontend` installs the frontend dependencies with `npm ci` from
  `frontend/package-lock.json` (sha512 integrity, install scripts disabled) and builds into
  `src/newgen_ocr/webapp/dist/`. Build the wheel after it.
- Production: no Node and no npm. The API serves the built files under `/app`, and the
  Content-Security-Policy allows only this origin.

## 3b. Images and the bundle [implemented P11]

At staging (connected):

```bash
make venv models frontend        # pinned Python packages, verified models, web application
make images                      # localhost/newgen-ocr:<version>: Python, OpenCV and models inside
make image-scan                  # Trivy report and image SBOM -> reports/
podman save -o newgen-ocr-<version>.tar localhost/newgen-ocr:<version>
podman pull <the nginx, postgres and valkey images by the digests in deploy/compose.yaml>
podman save -o base-images.tar <those three references>
```

Transfer the two archives, `deploy/` (without `secrets/` or `backups/`), `reports/` and
the release notes. On the production host (no network):

```bash
podman load -i newgen-ocr-<version>.tar && podman load -i base-images.tar
make services-secrets            # or place the organisation's certificate in deploy/secrets/tls/
NGOCR_IMAGE=localhost/newgen-ocr:<version> make stack-up
podman compose -f deploy/compose.yaml --profile app exec api \
    python -m newgen_ocr.auth user-create --user admin --role admin
```

Then apply the host egress rules (`deploy/egress/nftables-egress.nft`, edited for the
site) and run the verification below.

## 4. Verification [implemented P11]

`make offline-test` runs the production stack as its own compose project. It then:
1. From inside the API and worker containers, tries DNS lookups (pypi.org,
   huggingface.co), TCP to 1.1.1.1:443 and 8.8.8.8:53, UDP DNS to 8.8.8.8, and HTTPS to
   Hugging Face and Paddle's model store. Every attempt must fail, while the database
   (the control) stays reachable.
2. Checks TLS: 1.2 and 1.3 are accepted, 1.1 and plain HTTP refused.
3. Signs in over HTTPS with the local CA, uploads an invoice, waits for the background
   job, and compares the extracted GSTIN, invoice number and total with the truth. It also
   checks the Excel export, the `Secure` cookie, HSTS and the CSP.
4. Confirms the API runs in production mode with the minimal OpenCV (no FFmpeg).

The report goes to `reports/offline-test.json` (results in SECURITY_REVIEW.md). The host
firewall template adds a second layer for the host itself and the ingress (OD-50).
