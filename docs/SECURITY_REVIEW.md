# Security Review: Phase 11 (2026-10-24)

The scans and manual reviews behind the Phase 11 acceptance criteria. Image:
`localhost/newgen-ocr:dev`, version 0.11.0, built from branch `phase-11-security`.
Reports are in `reports/` (not committed). Re-run everything for each release (checklist at
the end of THREAT_MODEL.md).

## 1. Automated scans

| Check | Tool / command | Result |
|---|---|---|
| Python static analysis | Bandit (`make security`) over `src/` and `scripts/` (18,274 lines) | **0 issues.** 17 reviewed suppressions (§2) |
| Python lint (security rules) | Ruff with `S` (flake8-bandit) rules | Clean |
| Type checking | mypy strict, 120 modules | Clean |
| Python dependencies | pip-audit `--strict --require-hashes` on both locks | **No known vulnerabilities**, after the filelock and setuptools overrides (§4) |
| npm dependencies | `npm audit` (`make security`); Trivy on `package-lock.json` | **0 vulnerabilities**, after the undici override (§4) |
| Container image: OS and Python packages | Trivy 0.75.0 (`make image-scan`) | **0 fixable HIGH or CRITICAL** (the gate). 49 MEDIUM and 9 LOW with no fix released yet, all in Ubuntu base packages; re-checked every release |
| Container image: secrets | Trivy secret scanner on the image | **0** |
| Repository misconfiguration | Trivy `fs --scanners misconfig` (Dockerfile) | 1 LOW: DS-0026, no `HEALTHCHECK` in the image. Accepted: one image serves the API, workers and one-shot jobs, so health checks are set per service in compose |
| Leaked secrets | Gitleaks (`make secrets`), git history and working tree, with `.gitleaks.toml` | **No leaks**: 13 commits of history, and the working tree. The allowlist skips only git-ignored generated or third-party trees (`deploy/secrets/`, `.venv/`, `node_modules/`, `models/`, `data/`, `reports/`, the web build) |
| SBOM | pip-audit CycloneDX (`make sbom`); Trivy CycloneDX of the image | `sbom-runtime.cdx.json`: 97 Python components (CycloneDX 1.4). `sbom-image.cdx.json`: 268 components, OS and Python (CycloneDX 1.7) |
| Licences: Python | `scripts/check_licenses.py` (`make licenses`) | 97 runtime packages, all allowlisted or individually reviewed |
| Licences: npm | `scripts/check_npm_licenses.py` | 141 packages; the 6 shipped are MIT; build-only exceptions reviewed |
| Licences: image | Trivy licence scanner | Copyleft only where reviewed (§3) |
| Test suite | `make test`, `make frontend-test` | **630 Python tests passed, none skipped** (PostgreSQL and Valkey live, real models), coverage 90.70%; **14 frontend tests passed**. The offline-policy test caught the new health check's `urllib` import; it is now a reviewed, loopback-only exception that is itself tested |

## 2. Reviewed suppressions

Every suppression names the rule and the reason in the code:

- **B404/B603 (`subprocess`), 12 lines in build and test scripts and the Tesseract
  calls:** fixed argument lists, never a shell, never user input.
- **B105, `ErrorCode.WEAK_PASSWORD`:** an error-code string, not a password.
- **B108, `/tmp/opencv-build`:** scratch space inside the build container only.
- **B310 (`urlopen`), 2 lines:** model fetching at staging, from pinned URLs, and the
  container health check of `127.0.0.1`.
- **B311:** a seeded random generator for synthetic evaluation data, not security.

## 3. Licence and model review (manual)

- **Software inventory:** THIRD_PARTY_NOTICES.md lists every Python, npm, system and image
  component with its licence. Weak copyleft is used only unmodified, as separate
  replaceable components:
  - python-bidi, psycopg (LGPL-3.0);
  - certifi, tqdm (MPL-2.0);
  - autocommand (LGPL-3.0, vendored inside setuptools; found by the image licence scan
    and added to the review);
  - libgomp (GPL with the GCC runtime exception);
  - Ubuntu's base packages, used unmodified as the OS.
  No GPL code is linked into the application. The minimal OpenCV has no FFmpeg.
- **Models, reviewed individually:** MODEL_INVENTORY.md, "Individual terms review". All 13
  PaddlePaddle models and the 7 Tesseract models are Apache-2.0 at their pinned revisions,
  not gated, and carry no research-only or non-commercial terms. Training data is not
  disclosed by either publisher; this is recorded as a limitation.
- **Only approved models ship:** the image build runs `fetch_models.py --verify`
  (46 of 46 files verified; any unlisted file fails the build; tested).
- **No unapproved dependency ships.** Python comes from the hash-pinned lock with
  `--require-hashes`, without build isolation. The base interpreter's pip, setuptools and
  ensurepip wheels are removed. Their vendored, outdated certifi, urllib3 and msgpack were
  the only CRITICAL and HIGH findings of the first image scan.

## 4. Dependency decisions in this phase

ADR-0007 security overrides, approved by the owner on 2026-10-24:

| Component | Version | Why early | Reachable here? |
|---|---|---|---|
| nginx image | 1.30.6 | Five CVEs, including worker buffer overflows (CVE-2026-15176, CVE-2026-93479) | Yes, the ingress handles client traffic |
| filelock | 4.2.2 | CVE-2026-112940 / 112913 / 112907 | No: two are Windows-only; the Linux one needs `ReadWriteLock`, which nothing uses |
| setuptools | 84.2.0 | GHSA-xmj3-9gf7-h52w | No: Windows-only, during legacy wheel installs |
| undici (npm, dev-only) | 8.12.2 | GHSA-xjj6-f33q-929c (HIGH), GHSA-4qv8-2p9j-2685, GHSA-f72v-6w45-q472; published during this review | No: only jsdom uses it, in unit tests with mocked `fetch`; never shipped |

Each override lapses when the 14-day floor passes it; remove it then (Makefile
`SECURITY_OVERRIDES`; `overrides` in `frontend/package.json` for undici; ADR-0031 for nginx).

## 5. Offline network test (production processing)

`make offline-test` on the final image (`reports/offline-test.json`): **passed, no
failures.**
- **Egress from the API and worker containers:** DNS lookups of pypi.org and
  huggingface.co, TCP to 1.1.1.1:443 and 8.8.8.8:53, UDP DNS to 8.8.8.8, and HTTPS to
  Hugging Face and Paddle's model store were **all blocked**. The control (PostgreSQL on
  the internal network) was reachable.
- **Runtime:** `environment=production`, service mode, OpenCV 4.14.0 with no FFmpeg (the
  production guard passed).
- **TLS:** TLS 1.2 and 1.3 accepted; TLS 1.1 and plain HTTP refused. HSTS and
  `default-src 'none'` CSP present, and the session cookie is `Secure`.
- **Processing over TLS, with no network:** sign-in, then an invoice upload. The background
  job ran ingest, preprocessing, OCR, layout and extraction in 18.1 s on the first attempt.
  It classified the invoice as `gst_invoice`, and the invoice number and grand total equal
  the ground truth. The Excel export was produced.
- **Correction (2026-10-24, Phase 12).** This section first said the GSTIN was checked too.
  It was not: the test looked up a field named `gstin_seller`, which does not exist (the
  field is `seller_gstin`), so it compared two empty values. The check now covers
  `seller_gstin`, `buyer_gstin`, `invoice_number`, `invoice_date` and `grand_total`. It
  also refuses to compare a field whose expected value is empty. Re-run on image
  `245727de6e58`: all five match (§9).

The host nftables template is syntax-checked but not applied here; it needs root on the
production host (OD-50).

## 6. Backup and restore drill

Run on the final image, as a full loss of all volumes:
1. Process a document, then run `ops verify`. Result: ok; 1 document, 1 job, 1 result,
   1 review item, 1 history event, 8 audit events.
2. Back up. The database dump, the volume archive, the manifest (schema 0002) and the
   SHA-256 sums were written, and `sha256sum -c` passed.
3. Delete every volume (`down -v`) and start an empty stack: 0 documents.
4. Restore (sums checked first), then restart. The health checks pass.
5. Run `ops verify` again. Result: **ok, no problems; counts identical; fingerprint
   identical; review history chain valid.**
6. After the restore, the append-only trigger still refuses `UPDATE audit_events`, the
   restored user can sign in over TLS, and the document, its extraction (total 11800.00)
   and its page image are served.

The drill also found and fixed two defects:
- the backup container could not read the service user's files (it now has the read-only
  `DAC_READ_SEARCH` capability);
- a failed backup left a directory that looked complete (backups are now written under
  `.partial` and renamed when done).

## 7. Manual reviews

| Area | What was checked | Finding |
|---|---|---|
| Authentication and authorisation | Every router's dependencies; roles; ownership checks; CSRF on cookie changes; session expiry and revocation; lockout (Phases 8 to 10; tests call the API as each role) | No gaps found. MFA or SSO is open (OD-45) |
| Upload limits and temporary files | nginx body cap (51 MiB) and timeouts; streaming to the API; the API's exact limit, signature, page and pixel checks before decoding; quarantine; `TMPDIR` on the volume, mode 0700 (tested) | As designed |
| Secrets and permissions | Secrets in files under a 0700 directory, mounted only where used, read through `NGOCR_SECRETS_DIR` (tested); no credentials in the environment or compose; URLs masked in logs; the TLS key and CA key readable by their owner only; data 0750/0640, originals 0440; `/opt` read-only in the image | As designed. Secret files are mode 0444 inside the 0700 directory so non-root container users can read them |
| Container hardening | Inspected on the running stack: API and worker as uid 10001 with **no capabilities at all**; read-only roots everywhere; `no-new-privileges`; memory, CPU and process limits; only the ingress publishes a port, on 127.0.0.1 | As designed. PostgreSQL's entrypoint keeps five capabilities, and nginx keeps CHOWN, SETUID and SETGID |
| TLS configuration | nginx config test; protocol checks in the offline test | TLS 1.2+ only, AEAD ciphers, no session tickets (CVE-2026-97629 does not apply), HSTS |
| Telemetry and external calls | No telemetry or AI SDKs installed (test); network clients absent from the application source (test); FastAPI telemetry off (test); PaddleX hub resolution and downloads disabled (test, including OCR in a network namespace with no interfaces); browser CSP `default-src 'none'`; Trivy `--skip-version-check`; and, decisively, no egress route (offline test) | No external service is required or reachable |
| Audit and retention | Audit events for sign-ins, administration, review, documents and deletions; retention dry run and apply (tested: open work kept, files removed, deletion audited, `verify` still ok) | Audit and history are append-only and never deleted (OD-48) |
| Logs | JSON, with document content, query strings, passwords, tokens and cookies excluded (tests in Phases 9 and 10); nginx logs the path only; container logs rotate | As designed |

## 8. Open items carried forward

- **OD-12:** a per-document parser sandbox.
- **OD-41:** TLS between containers, if they ever span hosts.
- **OD-45:** MFA or SSO.
- **OD-47:** browser tests.
- **OD-48:** personal data in the append-only history.
- **OD-49:** backup encryption.
- **OD-50:** applying the host egress rules.
- **Image build reproducibility:** the OpenCV wheel's hash differs between builds
  (timestamps); its hash is recorded in each image.
- **The age floor:** remove the dated overrides when it passes them.

## 9. Phase 12: offline bundle and disconnected install (2026-10-24)

The image under test is `localhost/newgen-ocr:0.12.0`, content ID `245727de6e58`. It was
rebuilt for this phase, and the image-bound gates were run again on it:
- **Trivy:** 0 fixable HIGH or CRITICAL findings. 49 MEDIUM and 9 LOW have no fix yet, all
  in Ubuntu packages.
- **SBOMs:** the image has 268 components, Python 97.
- **Offline test:** passed. Every probe was blocked, TLS 1.2 and 1.3 were accepted, TLS
  1.1 and plain HTTP refused. The job succeeded in 20.1 s, and all five checked fields
  were correct (see the §5 correction).

The other checks:
- **Tests:** 656 passed, 0 skipped, 90.70% coverage. This includes the bundle tests: gate
  refusals, the wheel and lock checks, the deterministic tar, `ngocrctl verify` under
  `dash` (tampered file, unlisted file, re-hashed sums, other signer, links), the egress
  verdict, the smoke sample's truth, and the offline OCR smoke over 11 models.
- **Static checks:** lint, mypy (126 files) and Bandit are clean. Bandit has 25 reviewed
  suppressions, up from 17; the new ones are fixed-argument subprocess calls in the bundle
  builder, the rehearsal and the offline test.
- **Audits and licences:** pip-audit and npm audit are clean. The licence checks pass
  (97 Python packages; 141 npm, 6 shipped).
- **Gitleaks:** no leaks.

**Bundle.** Built by `scripts/build_bundle.py` from those reports, 2.55 GB:
- 186 files under a signed `SHA256SUMS`;
- 4 images, by content ID;
- 98 wheels: 96 from PyPI, the minimal OpenCV and the application;
- 46 model files.

The builder refuses an image the reports do not name (tested). It was signed with a
**throwaway rehearsal key** and built from an uncommitted tree (`--allow-dirty`, recorded in
the bundle). It is a rehearsal bundle, not a release: the release key is the owner's
(OD-51).

**Disconnected install rehearsal** (`make rehearse-install`, run 7, passed). The simulated
host:
- **Network:** a network namespace with only loopback. There was no other interface, and
  `pypi.org` did not resolve.
- **Images:** an empty image store.
- **Limits:** its own cgroups, so the containers' limits applied.
- **Engine:** root Podman inside a user namespace.
- **No systemd:** a loop ran Podman's health checks, which systemd timers do on a real host.

| Step | Result | Seconds |
|---|---|---|
| Outer signature, extract, `verify` | Good signature (rehearsal key); all 186 files match | 28 |
| `install` | Preflight passed. 4 images loaded, each content ID equal to the manifest. Inside the image, the model lock equals the bundle's and 46 of 46 files verify. Configuration and secrets generated, self-signed TLS, migrations, all services healthy. Smoke tests: 11 of 11 models loaded with `--network none`, and the invoice's 5 fields were correct. Egress was blocked from the API and worker, with the database reachable. A new job over TLS succeeded in about 18 s | 360 |
| `smoke --restart` | Every service recreated. Image store unchanged (4 images), models unchanged, no download attempts in the logs. The smoke tests, including a new job, passed again | 111 |
| Upgrade to `0.12.0-drill.1` | Backup taken first with the old release's tools. Switch, start, smoke tests passed | 259 |
| New data after the upgrade | A web user `after-upgrade` was created | 3 |
| `rollback --restore` | Verified the target release, stopped the services, switched, restored the pre-upgrade backup, started. `ops verify` ok, smoke tests passed | 109 |
| After rollback | `ops verify` counts and fingerprint **identical** to the record taken before the upgrade. The `after-upgrade` user is gone | 3 |

Both releases used the same image (two bundles named differently). This exercises the
mechanics of upgrade and rollback, but not a schema change; both schema heads are 0002.

**Defects the rehearsal found, all fixed:**
- the OCR smoke test read the image's production settings;
- the smoke tests rendered their invoice at run time, but production images have no fonts.
  The invoice is now rendered at staging and shipped;
- installed release files were owner-only, so containers could not read them;
- `ngocrctl` parsed the backup's output to find it. It now finds the backup on disk;
- after a failed pre-upgrade backup, the old release was left stopped. It is now
  restarted, and nothing changes;
- `grep -qv` gave the wrong exit status in the egress verdict. It is replaced and tested;
- repeated smoke uploads were de-duplicated and checked an old result. Each upload is now
  unique, and an old result fails the test.
