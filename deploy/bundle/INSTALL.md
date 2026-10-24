# Installing New Gen AI OCR from the offline bundle

This bundle installs and runs New Gen AI OCR on a host with **no internet access**. Nothing
is downloaded, during installation or afterwards. It contains:

- the container images;
- the OCR models;
- the Python wheels;
- the configuration;
- the database migrations;
- licence notices and SBOMs;
- the evidence from the staging checks;
- `ngocrctl`, the tool that installs and runs it.

The full guide is `docs/OFFLINE_DEPLOYMENT.md`, and daily operations are in
`docs/OPERATIONS_RUNBOOK.md`. Both are included in this bundle.

## What you need

**Received separately from the bundle**, over a channel you trust:
- `newgen-ocr.allowed_signers`, the release signing key.
- The key's fingerprint. Check it with `ssh-keygen -l -f newgen-ocr.allowed_signers`.

Never take the signing key from inside the bundle.

**The production host:**
- Linux on x86-64, with 4 CPUs and 12 GiB of memory recommended (8 GiB minimum).
- 15 GiB free for the images, and another 15 GiB under the install prefix.
- A container engine, installed from your organisation's approved package mirror:
  - Podman 4.9 or later, with its API socket on (`systemctl enable --now podman.socket`,
    or `systemctl --user ...` for rootless use) and Docker Compose v2.20 or later as its
    compose provider; or
  - Docker Engine 24 or later with the Compose v2 plugin.
- OpenSSH, which provides `ssh-keygen`, and coreutils.
- A TLS certificate and key for the host name users will connect to, and the CA that
  signed them. For a trial only, `--self-signed-tls` makes a local CA (it needs `openssl`).

Run as root for a system install under `/opt/newgen-ocr`. For a rootless Podman install,
run as the service user with `NGOCR_PREFIX=$HOME/newgen-ocr`.

## Install

```sh
# 1. Check the transferred archive before you extract it.
ssh-keygen -Y verify -f newgen-ocr.allowed_signers -I newgen-ocr-release \
    -n newgen-ocr-bundle -s newgen-ocr-<release>.tar.sig < newgen-ocr-<release>.tar
tar -xf newgen-ocr-<release>.tar && cd newgen-ocr-<release>

# 2. Check every file against the signed SHA256SUMS, then check this host.
./ngocrctl verify --signers ../newgen-ocr.allowed_signers
./ngocrctl preflight

# 3. Install and start. This also runs the smoke tests.
./ngocrctl install --signers ../newgen-ocr.allowed_signers \
    --hostname ocr.example.internal --bind 10.0.0.5 \
    --tls-cert server.crt --tls-key server.key --tls-ca ca.crt

# 4. Create the first administrator. You will be asked for the password.
/opt/newgen-ocr/current/ngocrctl create-admin --user admin
```

`install` refuses to continue if any of these fail:
- the signature or any file hash;
- an image whose content ID differs from the manifest;
- a model file that differs from the lock;
- a failed health check.

It then runs the smoke tests:
- **OCR with no network.** Every enabled model is loaded in a container with no network
  interface, and a test invoice is read end to end.
- **Egress probe.** From the API and worker containers, DNS, TCP, UDP and HTTPS requests
  to the outside must all fail.
- **A job over HTTPS.** A short-lived token uploads a test invoice. The token is revoked
  afterwards; the test document stays and is shown as `ngocr-smoke.png`.

Then apply the host firewall rules from `config/egress/nftables-egress.nft`, edited for
your site.

Site settings are in `<prefix>/config/site.env`:
- the listening address and port;
- the certificate host name;
- worker counts and limits.

They are written on the first install and never overwritten. After editing them, run
`ngocrctl restart`.

## Check that restarting downloads nothing

```sh
/opt/newgen-ocr/current/ngocrctl smoke --restart
```

This stops and recreates every service, then checks four things:
- the image store is unchanged, so nothing was pulled;
- the models still match the lock;
- the logs show no download attempts;
- the smoke tests still pass.

## Benchmark this server

Performance targets count only when measured on the production CPU server itself (ADR-0033):

```sh
/opt/newgen-ocr/current/ngocrctl benchmark --documents 200 --clients 2
```

This sends the bundle's 18 benchmark documents, each made unique so every one is a new
job, through HTTPS with that many clients at once. It samples every container's CPU and
memory every 5 seconds. It writes `<prefix>/state/benchmark-<stamp>.json` with:
- pages per minute;
- p50 and p95 latency;
- queue delay and processing time;
- the failure rate;
- CPU and memory per container;
- this host's CPU model, cores and memory.

Run it before go-live, when the server has no other load. Try a few `--clients` values
(1, 2, then the number of worker processes) and keep the best sustained result. Bring the
report back to staging for `make release-gates BENCHMARK=<file>`.

Like the smoke test, it leaves its documents behind (named `bench-<n>.png`, owned by
`ngocr-benchmark`).

## Upgrade

Transfer and verify the new bundle as in step 1, then run its `install`. Before switching,
`install` does the following:
1. It refuses a release with an older database schema; use `rollback` to go back.
2. It stops the API and workers, and backs up the database and documents.
3. It records the old release, so `rollback` can return to it.
4. It switches `<prefix>/current` to the new release and starts it. Migrations run first.
5. It runs the smoke tests. If they fail, it prints the rollback command.

## Rollback and recovery

| Situation | What to do |
|---|---|
| The new release fails its health check or smoke test | `ngocrctl rollback` returns to the previous release. If the database schema changed, it also restores the backup taken before the upgrade. |
| Return to the previous release while keeping data written since the upgrade | `ngocrctl rollback --no-restore`. This is only possible when both releases have the same schema; otherwise it refuses. |
| Return to a specific older release | `ngocrctl rollback --to <release> --restore`. This needs the backup taken when upgrading from that release. |
| Data damaged or lost | `ngocrctl restore <stamp>` restores a backup from `<prefix>/backups/`. Then `ngocrctl status`. |
| You suspect the installed files or images were changed | `ngocrctl verify-installed` re-checks the signature, every hash, the image IDs and the models. |
| The host is lost | On a new host: install the same release, then `ngocrctl restore <stamp>` with the backup copied into `<prefix>/backups/`. Copy `<prefix>/config/` from your secure store first, or use new secrets. |

**A restore loses everything written after that backup.** Rollback says which backup it
will restore before it starts.

Each rollback:
1. Verifies the target release again.
2. Stops the services.
3. Switches `current`.
4. Restores the backup, if needed.
5. Starts the services.
6. Checks the stored data: counts, fingerprint and the review history chain.
7. Runs the smoke tests.

Every action is logged in `<prefix>/state/history`. `ngocrctl prune` removes releases
other than the current and previous ones.

Back up regularly with `ngocrctl backup`, and copy `<prefix>/backups/` and
`<prefix>/config/` off the host. Backups do not contain the secrets in `<prefix>/config/`,
and are not encrypted (OD-49), so keep both in your secure store.
