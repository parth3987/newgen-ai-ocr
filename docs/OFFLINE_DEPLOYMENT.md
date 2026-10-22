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

## 3. Models [planned P5]

All weights are downloaded during staging, recorded in MODEL_INVENTORY.md with SHA-256, and
loaded from explicit local paths. Engine options that auto-download are disabled, and a
startup check fails if a configured model file is missing or its hash differs.

## 4. Verification [planned P10]

An automated test on the production host attempts outbound connections (DNS, HTTPS) and must
observe them being blocked, then runs an end-to-end OCR job successfully.
