# Offline Deployment

Status: **implemented (Phase 12, ADR-0032)**. A signed bundle carries everything production
needs. `ngocrctl` installs it and runs it with no network. The rehearsal (§6) runs the whole
production workflow on a simulated host with no network at all.

## 1. Model

| Stage | Network | What happens |
|---|---|---|
| Staging (connected) | Allowed, controlled | Download the pinned artefacts and verify their hashes and licences. Build and scan the image, run every test, then produce and sign the bundle (`make staging`) |
| Transfer | n/a | Move the bundle tar and its signature to the production host. The signing key travels separately |
| Production (disconnected) | **Outbound blocked** | Verify the bundle, install from it alone, start the services, check health, and run OCR (`ngocrctl`) |

Application flags alone are not enough to keep production offline:
- **Containers:** the API, workers, database and queue sit on internal networks with no
  route out (tested in §5 and §6).
- **The host:** the nftables template adds a default-deny outbound rule for the host itself
  (OD-50).
- **Images:** nothing is ever pulled. Every service uses a local tag with
  `pull_policy: never`.

## 2. Connected staging: `make staging`

This needs uv, Node.js and npm, Podman (or Docker), OpenSSH and network access. It runs, in
order:

1. **Fetch and build.**
   - `venv`: the hash-pinned Python environment.
   - `models`: the pinned model revisions, every file's SHA-256 checked.
   - `frontend`: `npm ci` from the integrity-pinned lock, with install scripts off.
2. **Static checks.**
   - `lint` and `typecheck`.
   - `licenses` and `frontend-licenses`.
   - `security`: Bandit, `pip-audit` on both locks, and `npm audit`.
   - `secrets`: Gitleaks.
3. **Tests.** `services-up`, then `test` (writes `reports/pytest.xml`) and `frontend-test`.
4. **Image.** `images` builds the image: base images by digest, Python by hash, OpenCV from
   its hashed source, and the models verified inside.
5. **Scans and the offline test.**
   - `image-scan`: Trivy, plus the image SBOM.
   - `sbom`: the Python SBOM.
   - `offline-test`: the production stack with no egress.
6. **Bundle.** `bundle` runs `scripts/build_bundle.py`.

The bundle builder refuses to package anything the gates did not pass for:
- The Trivy report, the image SBOM and the offline-test report must name the **content ID
  of the image being shipped**.
- Trivy must show no fixable HIGH or CRITICAL finding.
- The offline test must have passed.
- The JUnit report must show no failures or errors.
- The models and licences are checked again.
- Every downloaded wheel must match a hash in `requirements.lock`, and every locked package
  must have a wheel.
- The minimal OpenCV wheel must be the one inside the image.
- The image's version must equal the source's.
- The git tree must be clean (`--allow-dirty` exists only for rehearsals; the bundle
  records it and `install` warns).

The signing key is created once with `make signing-key`: Ed25519, with a passphrase, kept
outside the repository (OD-51). Output in `dist/`:

| File | What |
|---|---|
| `newgen-ocr-<release>.tar` | The bundle. A deterministic tar: the same files give the same bytes |
| `newgen-ocr-<release>.tar.sig` | `ssh-keygen -Y sign` signature, namespace `newgen-ocr-bundle` |
| `newgen-ocr.allowed_signers` | The public key. Send it, and confirm its fingerprint, by a separate channel |

### Bundle contents

| Path | Contents |
|---|---|
| `images/*.tar.gz`, `images.txt` | The application, PostgreSQL, Valkey and nginx images (docker-archive, gzip). Upstream images are pulled by digest. Each is tagged `localhost/ngocr/<name>:<release>`, and `images.txt` records its content ID |
| `wheels/` | All 97 locked runtime wheels, with the minimal OpenCV build in place of PyPI's, plus the application wheel and `requirements.lock` |
| `models/`, `models.lock.json` | Every model file, pinned by SHA-256. The image's copy is checked against these at install |
| `migrations/` | The database migrations (the image runs them; shipped for review) |
| `config/` | `compose.yaml`, `compose.images.yaml` (local tags, never pull), `nginx/`, `ops/` (backup and restore), `egress/` (nftables template), `site.env.example` |
| `ngocrctl`, `INSTALL.md`, `scripts/` | The production tool, its guide, and the smoke and probe scripts that run inside containers |
| `licenses/`, `sbom/` | Licence notices, the model inventory, and the CycloneDX SBOMs (image and Python) |
| `os-packages/` | The OS packages inside each image, with versions |
| `evidence/` | The Trivy, offline-test and JUnit reports the gates produced |
| `docs/` | This guide, the runbook, the security review, the threat model and the changelog |
| `MANIFEST.json`, `release.env` | Release, version, database schema head, source commit, image IDs, gate summary and signer fingerprint |
| `SHA256SUMS`, `SHA256SUMS.sig` | The hash of every file above, signed |

**OS packages.** None are installed on the host. The application's packages (Tesseract,
libpq and the OpenMP runtime, on Ubuntu 24.04) are inside its image. The host supplies:
- a container engine with Compose v2;
- OpenSSH;
- coreutils.

These come from the organisation's approved package mirror, and `ngocrctl preflight`
checks for them.

## 3. Disconnected production: `ngocrctl`

`ngocrctl` is POSIX sh. Step by step, with commands, in the bundle's `INSTALL.md`:

1. **Verify the outer tar before extracting it.**
   ```sh
   ssh-keygen -Y verify -f newgen-ocr.allowed_signers -I newgen-ocr-release \
       -n newgen-ocr-bundle -s <tar>.sig < <tar>
   ```
2. **`./ngocrctl verify --signers FILE`.** Checks the signature on `SHA256SUMS`, then every
   hash. It refuses files that are not listed, and links.
3. **`./ngocrctl preflight`.** Checks the engine and Compose versions, the Podman socket,
   free disk for images and releases, memory, CPUs, and the tools it needs.
4. **`./ngocrctl install --signers FILE --tls-cert … --tls-key … --tls-ca …`**
   (`--self-signed-tls` for trials).
   1. Verifies the bundle and runs preflight.
   2. Loads each image and checks its content ID against `images.txt`.
   3. Checks the image's model lock against the bundle's, then re-hashes every model file
      inside the image. This runs with `--network none`.
   4. Copies the verified bundle to `<prefix>/releases/<release>/`, read-only.
   5. On the first install, writes `config/site.env`, random service passwords and TLS
      (never overwritten afterwards).
   6. Switches `current` and starts the stack. Migrations run first, and it waits for
      every health check.
   7. Runs the smoke tests (§4).

## 4. Smoke tests: `ngocrctl smoke [--no-job] [--restart]`

- **OCR with no network at all.** A one-off container with `--network none` loads every
  model the language matrix enables (11 engine and language pairs). It then reads a
  generated GST invoice end to end. The GSTIN, invoice number and total must equal the
  truth.
- **Egress.** From the API and worker containers, DNS, TCP, UDP and HTTPS requests to
  public addresses and model hubs must fail, while the database answers.
- **A job through the TLS ingress.** A client on the host connects to the published HTTPS
  address and checks the certificate against the site CA for `NGOCR_TLS_HOSTNAME`. It
  uploads an invoice with a one-day submitter token, which is revoked afterwards. Then it
  waits for the background job and checks the extracted values.
- **`--restart`.** Recreates every service first. Then:
  - the image store must be unchanged (nothing pulled);
  - the models in the running container must still match the lock;
  - the API and worker logs since the restart must not mention a download.

## 5. Offline network test (staging): `make offline-test`

This runs the production stack as its own compose project and checks:
1. The egress probe from the API and workers. It is the same script the smoke test uses.
2. TLS: 1.2 and 1.3 are accepted; 1.1 and plain HTTP are refused.
3. A signed-in job over HTTPS. The extracted values must be correct, and the Excel export
   must work.
4. Production mode with the minimal OpenCV.

The report records the image's content ID, and the bundle builder requires it to match.
Results are in SECURITY_REVIEW.md.

## 6. Install rehearsal: `make rehearse-install`

`scripts/rehearse_install.py` builds a simulated production host on the staging machine.
**No root is needed.**

- **No network:** a user namespace (`unshare --map-auto`) with a network namespace that has
  only a loopback interface, so not even DNS can leave.
- **An empty image store**, so every image must come from the bundle.
- **Its own cgroup tree**, so the containers' memory, CPU and process limits apply.
- **Podman as root inside it**, with its API socket.
- **No systemd**, which is the one difference from a real host. A small loop runs
  Podman's health checks every 3 s, which systemd timers do on a real host.

It then runs the production workflow with the real bundles:
1. Check the outer signature, extract, and `verify`.
2. `install`, including the smoke tests.
3. `smoke --restart`.
4. Record the stored data with `ops verify`.
5. Upgrade to a second bundle, which takes a backup first, then add new data.
6. `rollback --restore`.
7. Check that the counts and the fingerprint equal the pre-upgrade record exactly.

The report is written to `reports/install-rehearsal.json`, with the full log beside it.
Results are in SECURITY_REVIEW.md §9.

## 7. Upgrade, rollback and recovery

See `INSTALL.md` in the bundle (also `deploy/bundle/INSTALL.md`) and OPERATIONS_RUNBOOK.md
§"Releases".

- **Upgrade:** a backup with the old release's tools, then the switch, start and smoke
  tests. A release with an older schema is refused.
- **Rollback:** `ngocrctl rollback`. When the schemas differ, it restores the backup
  recorded for that upgrade. Data written since the upgrade is lost, and the command says
  so before it starts.
- **Recovery:**
  - `ngocrctl restore <stamp>` restores any backup;
  - `ngocrctl verify-installed` re-checks the signature, hashes, image IDs and models;
  - a lost host means installing the same release on a new host, then restoring.
