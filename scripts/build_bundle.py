"""Build the signed offline installation bundle at staging (ADR-0032): ``make bundle``.

Needs the network only for what staging already fetched or can fetch by pinned hash: the
upstream images by digest and the locked Python wheels. It does not build the image or run
the gates itself; ``make staging`` does that first. Instead it refuses to package anything
the gates did not pass for:

- the Trivy report and image SBOM must be about this exact image (its ID), with no fixable
  HIGH or CRITICAL vulnerability;
- the offline network test must have passed on this exact image;
- the test report must show no failures or errors;
- every model file must match models.lock.json (nothing unlisted), and the runtime and npm
  licence checks must pass;
- every wheel must match a hash in requirements.lock, and the minimal OpenCV wheel must be
  the one inside the image.

Output in ``dist/``: ``newgen-ocr-<release>.tar`` (a deterministic tar of the bundle
directory), its detached signature ``.tar.sig``, and ``newgen-ocr.allowed_signers`` with the
public key (send its fingerprint to the production site by a separate channel). Inside the
bundle, ``SHA256SUMS`` covers every file and is signed too (``SHA256SUMS.sig``).
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import re
import shutil
import subprocess  # nosec B404 - fixed commands, no shell
import sys
import tarfile
import tempfile
import xml.etree.ElementTree as ET  # nosec B405 - our own pytest report
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
NAMESPACE = "newgen-ocr-bundle"  # ssh-keygen -Y signature namespace
PRINCIPAL = "newgen-ocr-release"
DEFAULT_KEY = Path.home() / ".config" / "newgen-ocr" / "bundle-signing" / "id_ed25519"
# Which compose services run which upstream image (by repository name).
UPSTREAM = {"postgres": "postgres", "valkey": "valkey", "nginx": "ingress"}
APP_SERVICES = ("migrate", "api", "worker")
WHEEL_PLATFORM = [
    *("--platform", "manylinux_2_28_x86_64", "--platform", "manylinux_2_17_x86_64"),
    *("--platform", "manylinux2014_x86_64", "--platform", "linux_x86_64"),
    *("--python-version", "3.11", "--implementation", "cp"),
    *("--abi", "cp311", "--abi", "abi3", "--abi", "none"),
]
EXECUTABLE = {"ngocrctl"}
# Fields the smoke tests compare with the truth (every one must have a value).
SMOKE_FIELDS = ("seller_gstin", "buyer_gstin", "invoice_number", "invoice_date", "grand_total")


class BundleError(RuntimeError):
    pass


# ----------------------------------------------------------------------------- helpers
def run(*command: str, cwd: Path = ROOT, stdin: str | None = None) -> str:
    result = subprocess.run(  # noqa: S603  # nosec B603 - fixed argument lists
        command, cwd=cwd, input=stdin, capture_output=True, text=True, check=False
    )
    if result.returncode != 0:
        raise BundleError(f"{' '.join(command[:4])} ... failed:\n{result.stderr[-2000:]}")
    return result.stdout


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def lock_hashes(text: str) -> dict[str, set[str]]:
    """Package name (normalised) -> its allowed sha256 hashes, from a hash-pinned lock."""
    out: dict[str, set[str]] = {}
    for match in re.finditer(
        r"^([A-Za-z0-9_.-]+)==\S+((?:\s+\\\n\s+--hash=sha256:[0-9a-f]{64})+)", text, re.M
    ):
        name = re.sub(r"[-_.]+", "-", match.group(1)).lower()
        out[name] = set(re.findall(r"sha256:([0-9a-f]{64})", match.group(2)))
    return out


def wheel_name(filename: str) -> str:
    return re.sub(r"[-_.]+", "-", filename.split("-")[0]).lower()


def check_wheels(wheels: list[Path], lock: dict[str, set[str]]) -> list[str]:
    """Every wheel is pinned by hash, and every locked package has a wheel."""
    problems = []
    seen = set()
    for wheel in wheels:
        name = wheel_name(wheel.name)
        seen.add(name)
        if name not in lock:
            problems.append(f"{wheel.name}: not in the lock")
        elif sha256_file(wheel) not in lock[name]:
            problems.append(f"{wheel.name}: hash not in the lock")
    problems += [f"{name}: no wheel" for name in sorted(set(lock) - seen)]
    return problems


def fixable_high(trivy: dict[str, Any]) -> list[str]:
    return [
        f"{v['PkgName']} {v['VulnerabilityID']} ({v['Severity']})"
        for result in trivy.get("Results", [])
        for v in result.get("Vulnerabilities") or []
        if v.get("Severity") in ("HIGH", "CRITICAL") and v.get("FixedVersion")
    ]


def check_gates(reports: Path, image_id: str) -> tuple[list[str], dict[str, Any]]:
    """The staging gates' reports, checked against the image being shipped."""
    problems: list[str] = []
    summary: dict[str, Any] = {}

    def load(name: str) -> Any:
        path = reports / name
        if not path.is_file():
            problems.append(f"{name}: missing (run `make staging`)")
            return None
        return json.loads(path.read_text())

    trivy = load("trivy-image.json")
    if trivy is not None:
        found = str(trivy.get("Metadata", {}).get("ImageID", "")).removeprefix("sha256:")
        if found != image_id:
            problems.append(f"trivy-image.json is about image {found[:12]}, not {image_id[:12]}")
        high = fixable_high(trivy)
        if high:
            problems.append(f"fixable HIGH/CRITICAL vulnerabilities: {', '.join(high[:5])}")
        summary["trivy_fixable_high_critical"] = len(high)
    sbom = load("sbom-image.cdx.json")
    if sbom is not None:
        props = {p["name"]: p["value"] for p in sbom["metadata"]["component"].get("properties", [])}
        if props.get("aquasecurity:trivy:ImageID", "").removeprefix("sha256:") != image_id:
            problems.append("sbom-image.cdx.json is not about this image")
        summary["sbom_image_components"] = len(sbom.get("components", []))
    runtime_sbom = load("sbom-runtime.cdx.json")
    if runtime_sbom is not None:
        summary["sbom_runtime_components"] = len(runtime_sbom.get("components", []))
    offline = load("offline-test.json")
    if offline is not None:
        if offline.get("image_id") != image_id:
            problems.append("offline-test.json is not about this image")
        if not offline.get("passed"):
            problems.append(f"offline test failed: {offline.get('failures')}")
        summary["offline_test"] = "passed" if offline.get("passed") else "failed"
    junit = reports / "pytest.xml"
    if not junit.is_file():
        problems.append("pytest.xml: missing (run `make staging`)")
    else:
        root = ET.parse(junit).getroot()  # noqa: S314  # nosec B314 - our own report
        suite = root if root.tag == "testsuite" else root.find("testsuite")
        if suite is None:
            raise BundleError("pytest.xml has no test suite")
        counts = {k: int(suite.get(k, "0")) for k in ("tests", "failures", "errors", "skipped")}
        if counts["tests"] == 0 or counts["failures"] or counts["errors"]:
            problems.append(f"tests: {counts}")
        summary["tests"] = counts
    return problems, summary


def write_sums(bundle: Path) -> list[str]:
    """SHA256SUMS over every file (sha256sum format), sorted; returns the listed paths."""
    files = sorted(
        p.relative_to(bundle).as_posix()
        for p in bundle.rglob("*")
        if p.is_file() and p.name not in ("SHA256SUMS", "SHA256SUMS.sig")
    )
    lines = [f"{sha256_file(bundle / f)}  {f}\n" for f in files]
    (bundle / "SHA256SUMS").write_text("".join(lines))
    return files


def deterministic_tar(source: Path, output: Path, mtime: int) -> None:
    """A tar whose bytes depend only on the files' contents and names."""

    def normalise(info: tarfile.TarInfo) -> tarfile.TarInfo:
        info.uid = info.gid = 0
        info.uname = info.gname = ""
        info.mtime = mtime
        executable = info.isdir() or Path(info.name).name in EXECUTABLE
        info.mode = 0o755 if executable else 0o644
        return info

    with tarfile.open(output, "w", format=tarfile.PAX_FORMAT) as tar:
        paths = [source, *sorted(source.rglob("*"))]
        for path in paths:
            arcname = Path(source.name) / path.relative_to(source)
            tar.add(path, arcname=arcname.as_posix(), recursive=False, filter=normalise)


def compose_images(images: list[dict[str, str]]) -> str:
    by_name = {image["name"]: image["tag"] for image in images}
    services = {name: by_name["app"] for name in APP_SERVICES}
    services |= {service: by_name[name] for name, service in UPSTREAM.items()}
    services |= {"backup": by_name["postgres"], "restore": by_name["postgres"]}
    lines = [
        "# Generated by scripts/build_bundle.py: the images this release loaded, by local tag.",
        "# Nothing is ever pulled (ADR-0032).",
        "services:",
    ]
    for service, tag in services.items():
        lines += [f"  {service}:", f"    image: {tag}", "    pull_policy: never"]
    return "\n".join(lines) + "\n"


def write_smoke_sample(out: Path) -> None:
    """The invoice the smoke tests read, rendered here: production images carry no fonts."""
    from PIL import Image

    from newgen_ocr.evaluation.extraction_corpus import gst_invoice

    image, truth = gst_invoice("intra", 200)
    expected = {name: truth.fields.get(name) for name in SMOKE_FIELDS}
    missing = [name for name, value in expected.items() if value in (None, "")]
    if missing:
        raise BundleError(f"smoke sample has no truth for {missing}")
    Image.fromarray(image).save(out / "smoke-invoice.png", optimize=True)
    (out / "smoke-invoice.json").write_text(json.dumps(expected, indent=2) + "\n")


def sign(key: Path, path: Path) -> Path:
    signature = path.with_name(path.name + ".sig")
    signature.unlink(missing_ok=True)  # ssh-keygen will not replace one
    run("ssh-keygen", "-q", "-Y", "sign", "-f", str(key), "-n", NAMESPACE, str(path))
    return signature


def public_key(key: Path) -> Path:
    """The key's public half (the private key may have a passphrase)."""
    public = key.with_name(key.name + ".pub")
    if not public.is_file():
        raise BundleError(f"no public key next to the signing key: {public}")
    return public


# ----------------------------------------------------------------------------- steps
class Builder:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.engine = "podman" if shutil.which("podman") else "docker"
        self.image = args.image

    def engine_run(self, image: str, *command: str, entrypoint: str | None = None) -> str:
        options = ["--rm", "--network", "none", "--read-only", "--cap-drop", "ALL"]
        if entrypoint is not None:
            options += ["--entrypoint", entrypoint]
        return run(self.engine, "run", *options, image, *command)

    def image_id(self, ref: str) -> str:
        return (
            run(self.engine, "image", "inspect", "--format", "{{.Id}}", ref)
            .strip()
            .removeprefix("sha256:")
        )

    def source(self) -> dict[str, Any]:
        commit = run("git", "rev-parse", "HEAD").strip()
        dirty = bool(run("git", "status", "--porcelain").strip())
        if dirty and not self.args.allow_dirty:
            raise BundleError("the working tree has uncommitted changes (commit, or --allow-dirty)")
        epoch = int(run("git", "show", "-s", "--format=%ct", "HEAD").strip())
        return {"commit": commit, "dirty": dirty, "commit_time": epoch}

    def save_image(self, ref: str, tag: str, archive: Path) -> None:
        run(self.engine, "tag", ref, tag)
        # The archive depends only on the image and its tag: reuse it when rebuilding.
        cache = ROOT / self.args.dist / ".image-cache"
        cached = (
            cache / f"{tag.replace('/', '_').replace(':', '_')}-{self.image_id(ref)[:16]}.tar.gz"
        )
        if cached.is_file():
            os.link(cached, archive)
            return
        raw = archive.with_suffix("")  # .tar
        save = [self.engine, "save", "-o", str(raw), tag]
        if self.engine == "podman":
            save[2:2] = ["--format", "docker-archive"]
        run(*save)
        with raw.open("rb") as src, gzip.GzipFile(archive, "wb", compresslevel=6, mtime=0) as dst:
            shutil.copyfileobj(src, dst, 1 << 20)
        raw.unlink()
        cache.mkdir(exist_ok=True)
        os.link(archive, cached)

    def upstream_refs(self) -> dict[str, str]:
        compose = (ROOT / "deploy" / "compose.yaml").read_text()
        refs = {}
        for name in UPSTREAM:
            found = set(
                re.findall(rf"image: (docker\.io/\S*/{name}:\S+@sha256:[0-9a-f]{{64}})", compose)
            )
            if len(found) != 1:
                raise BundleError(
                    f"compose.yaml: expected one digest-pinned {name} image, found {found}"
                )
            refs[name] = found.pop()
        return refs

    def wheels(self, out: Path, app_id: str) -> int:
        out.mkdir()
        lock_text = (ROOT / "requirements.lock").read_text()
        run(
            str(ROOT / ".venv" / "bin" / "pip"), "download", "-q", "--no-deps", "--require-hashes",
            "--only-binary=:all:", *WHEEL_PLATFORM, "-r", str(ROOT / "requirements.lock"),
            "-d", str(out),
        )  # fmt: skip
        problems = check_wheels(sorted(out.glob("*.whl")), lock_hashes(lock_text))
        if problems:
            raise BundleError("wheels: " + "; ".join(problems))
        # The image replaces PyPI's OpenCV with the minimal build: ship that one instead.
        for wheel in out.glob("opencv_python_headless-*.whl"):
            wheel.unlink()
        stage = f"localhost/ngocr-opencv-stage:{app_id[:12]}"
        run(
            self.engine,
            "build",
            "-q",
            "-f",
            "deploy/Dockerfile",
            "--target",
            "opencv",
            "-t",
            stage,
            ".",
        )
        container = run(self.engine, "create", stage).strip()
        try:
            listing = run(self.engine, "run", "--rm", "--network", "none", stage, "ls", "/wheels")
            wheel = next(w for w in listing.split() if w.endswith(".whl"))
            run(self.engine, "cp", f"{container}:/wheels/{wheel}", str(out / wheel))
        finally:
            run(self.engine, "rm", container)
        shipped = self.engine_run(self.image, "cat", "/opt/ngocr/opencv-wheel.sha256").split()[0]
        if sha256_file(out / wheel) != shipped:
            raise BundleError(
                "the cached OpenCV stage no longer matches the image (rebuild with `make images`)"
            )
        # The application itself, from the same source the image installed (built in a copy,
        # so no build directories appear in the checkout or the image's build context).
        with tempfile.TemporaryDirectory() as tmp:
            for item in ("pyproject.toml", "README.md"):
                shutil.copy2(ROOT / item, Path(tmp) / item)
            shutil.copytree(
                ROOT / "src", Path(tmp) / "src", ignore=shutil.ignore_patterns("__pycache__")
            )
            run(
                str(ROOT / ".venv" / "bin" / "pip"), "wheel", "-q", "--no-deps",
                "--no-build-isolation", "--no-index", "-w", str(out), tmp,
            )  # fmt: skip
        shutil.copy2(ROOT / "requirements.lock", out / "requirements.lock")
        return len(list(out.glob("*.whl")))

    def os_packages(self, out: Path, images: dict[str, str]) -> None:
        out.mkdir()
        listing = self.engine_run(
            images["app"], "-W", "-f", "${Package} ${Version}\n", entrypoint="dpkg-query"
        )
        (out / "app.txt").write_text("".join(sorted(listing.splitlines(keepends=True))))
        for name in UPSTREAM:
            listing = self.engine_run(images[name], "info", "-v", entrypoint="apk")
            (out / f"{name}.txt").write_text("".join(sorted(listing.splitlines(keepends=True))))

    def build(self) -> Path:
        args = self.args
        key = Path(args.signing_key).expanduser()
        if not key.is_file():
            raise BundleError(f"no signing key at {key} (run `make signing-key`)")
        source = self.source()
        app_id = self.image_id(self.image)
        version = self.engine_run(
            self.image, "python", "-c", "import newgen_ocr; print(newgen_ocr.__version__)"
        ).strip()
        source_version = re.search(
            r'__version__ = "([^"]+)"', (ROOT / "src/newgen_ocr/__init__.py").read_text()
        )
        if source_version is None or version != source_version.group(1):
            raise BundleError(
                f"the image is version {version}; the source is not (rebuild the image)"
            )
        release = args.release or version
        if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?", release):
            raise BundleError(f"release {release!r} is not a version")

        problems, gates = check_gates(ROOT / args.reports, app_id)
        for command in (
            [str(ROOT / ".venv/bin/python"), "scripts/fetch_models.py", "--verify"],
            [str(ROOT / ".venv/bin/python"), "scripts/check_licenses.py", "requirements.lock"],
            [str(ROOT / ".venv/bin/python"), "scripts/check_npm_licenses.py"],
        ):
            try:
                run(*command)
            except BundleError as exc:
                problems.append(str(exc))
        if problems:
            raise BundleError("staging gates not met:\n  - " + "\n  - ".join(problems))

        dist = ROOT / args.dist
        dist.mkdir(exist_ok=True)
        name = f"newgen-ocr-{release}"
        work = Path(tempfile.mkdtemp(prefix="bundle-", dir=dist))
        bundle = work / name
        bundle.mkdir()
        try:
            self.assemble(bundle, release, version, source, app_id, gates, key)
            tar = dist / f"{name}.tar"
            deterministic_tar(bundle, tar, source["commit_time"])
            signature = sign(key, tar)
            signers = dist / "newgen-ocr.allowed_signers"
            public = " ".join(public_key(key).read_text().split()[:2])
            signers.write_text(f'{PRINCIPAL} namespaces="{NAMESPACE}" {public}\n')
            fingerprint = run("ssh-keygen", "-l", "-f", str(public_key(key))).split()[1]
        finally:
            shutil.rmtree(work, ignore_errors=True)
        sys.stdout.write(
            json.dumps(
                {
                    "bundle": str(tar.relative_to(ROOT)),
                    "signature": str(signature.relative_to(ROOT)),
                    "allowed_signers": str(signers.relative_to(ROOT)),
                    "signing_key_fingerprint": fingerprint,
                    "bytes": tar.stat().st_size,
                    "sha256": sha256_file(tar),
                    "gates": gates,
                },
                indent=2,
            )
            + "\n"
        )
        return tar

    def assemble(
        self,
        bundle: Path,
        release: str,
        version: str,
        source: dict[str, Any],
        app_id: str,
        gates: dict[str, Any],
        key: Path,
    ) -> None:
        from alembic.script import ScriptDirectory

        from newgen_ocr.db.__main__ import alembic_config  # the venv's copy of the same source

        heads = ScriptDirectory.from_config(alembic_config()).get_heads()
        if len(heads) != 1:
            raise BundleError(f"expected one migration head, found {heads}")

        # Images: pinned upstream images by digest, the application by ID.
        (bundle / "images").mkdir()
        refs = {"app": self.image, **self.upstream_refs()}
        images = []
        for name, ref in refs.items():
            if name != "app":
                try:
                    self.image_id(ref)
                except BundleError:
                    run(self.engine, "pull", "-q", ref)  # staging only: by digest
            tag = f"localhost/ngocr/{name}:{release}"
            archive = bundle / "images" / f"{name}.tar.gz"
            self.save_image(ref, tag, archive)
            images.append({"name": name, "source": ref, "id": self.image_id(ref), "tag": tag,
                           "archive": f"images/{name}.tar.gz"})  # fmt: skip
        (bundle / "images.txt").write_text(
            "".join(f"{i['name']} {i['archive']} {i['id']} {i['tag']}\n" for i in images)
        )

        # Configuration templates and the production tools.
        config = bundle / "config"
        for src, dst in (
            ("deploy/compose.yaml", "compose.yaml"),
            ("deploy/nginx/nginx.conf", "nginx/nginx.conf"),
            ("deploy/ops/backup.sh", "ops/backup.sh"),
            ("deploy/ops/restore.sh", "ops/restore.sh"),
            ("deploy/egress/nftables-egress.nft", "egress/nftables-egress.nft"),
            ("deploy/bundle/site.env.example", "site.env.example"),
        ):
            (config / dst).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / src, config / dst)
        (config / "compose.images.yaml").write_text(compose_images(images))
        shutil.copy2(ROOT / "deploy/bundle/ngocrctl", bundle / "ngocrctl")
        shutil.copy2(ROOT / "deploy/bundle/INSTALL.md", bundle / "INSTALL.md")
        (bundle / "scripts").mkdir()
        for script in ("probe_egress.py", "smoke_ocr.py", "smoke_api.py"):
            shutil.copy2(ROOT / "deploy/bundle" / script, bundle / "scripts" / script)
        shutil.copy2(ROOT / "scripts/fetch_models.py", bundle / "scripts/fetch_models.py")
        write_smoke_sample(bundle / "scripts")

        # Models (also inside the image; checked against each other at install).
        shutil.copytree(ROOT / "models", bundle / "models", ignore=shutil.ignore_patterns(".*"))
        shutil.copy2(ROOT / "models.lock.json", bundle / "models.lock.json")
        run(
            str(ROOT / ".venv/bin/python"), "scripts/fetch_models.py", "--verify",
            "--lock", str(bundle / "models.lock.json"), "--models-dir", str(bundle / "models"),
        )  # fmt: skip

        wheel_count = self.wheels(bundle / "wheels", app_id)
        shutil.copytree(
            ROOT / "src/newgen_ocr/db/migrations/versions", bundle / "migrations",
            ignore=shutil.ignore_patterns("__pycache__"),
        )  # fmt: skip
        self.os_packages(bundle / "os-packages", {i["name"]: i["tag"] for i in images})

        # Licences, SBOMs and the evidence the gates produced.
        licenses = bundle / "licenses"
        licenses.mkdir()
        for doc in ("LICENSE", "docs/THIRD_PARTY_NOTICES.md", "docs/MODEL_INVENTORY.md"):
            if (ROOT / doc).is_file():
                shutil.copy2(ROOT / doc, licenses / Path(doc).name)
        (bundle / "sbom").mkdir()
        for report in ("sbom-runtime.cdx.json", "sbom-image.cdx.json"):
            shutil.copy2(ROOT / self.args.reports / report, bundle / "sbom" / report)
        (bundle / "evidence").mkdir()
        for report in ("trivy-image.json", "offline-test.json", "pytest.xml"):
            shutil.copy2(ROOT / self.args.reports / report, bundle / "evidence" / report)
        docs = bundle / "docs"
        docs.mkdir()
        for doc in (
            "OFFLINE_DEPLOYMENT.md",
            "OPERATIONS_RUNBOOK.md",
            "SECURITY_REVIEW.md",
            "THREAT_MODEL.md",
        ):
            shutil.copy2(ROOT / "docs" / doc, docs / doc)
        shutil.copy2(ROOT / "CHANGELOG.md", docs / "CHANGELOG.md")

        created = datetime.fromtimestamp(source["commit_time"], tz=UTC).isoformat()
        (bundle / "release.env").write_text(
            f"NGOCR_RELEASE={release}\nNGOCR_VERSION={version}\nNGOCR_SCHEMA_HEAD={heads[0]}\n"
            f"NGOCR_COMMIT={source['commit']}\nNGOCR_DIRTY={str(source['dirty']).lower()}\n"
            f"NGOCR_CREATED={created}\n"
        )
        manifest = {
            "format": 1,
            "product": "newgen-ocr",
            "release": release,
            "version": version,
            "created": created,
            "source": source,
            "schema_head": heads[0],
            "images": images,
            "wheels": wheel_count,
            "models": len(json.loads((ROOT / "models.lock.json").read_text())["models"]),
            "gates": gates,
            "signing": {
                "namespace": NAMESPACE,
                "principal": PRINCIPAL,
                "key_fingerprint": run("ssh-keygen", "-l", "-f", str(public_key(key))).split()[1],
            },
        }
        files = write_sums(bundle)  # everything above, MANIFEST.json included below
        manifest["files"] = len(files) + 1
        (bundle / "MANIFEST.json").write_text(json.dumps(manifest, indent=2) + "\n")
        write_sums(bundle)
        sign(key, bundle / "SHA256SUMS")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", required=True)
    parser.add_argument("--release", help="release name (default: the image's version)")
    parser.add_argument(
        "--signing-key", default=os.environ.get("NGOCR_SIGNING_KEY", str(DEFAULT_KEY))
    )
    parser.add_argument("--reports", default="reports")
    parser.add_argument("--dist", default="dist")
    parser.add_argument(
        "--allow-dirty", action="store_true", help="rehearsal only; recorded in the bundle"
    )
    args = parser.parse_args()
    try:
        Builder(args).build()
    except BundleError as exc:
        sys.stderr.write(f"bundle: {exc}\n")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
