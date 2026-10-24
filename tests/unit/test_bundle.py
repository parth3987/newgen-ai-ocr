"""The offline bundle (ADR-0032): the builder's gates and the production verifier."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[2]
NGOCRCTL = ROOT / "deploy" / "bundle" / "ngocrctl"
IMAGE_ID = "a" * 64


def _load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


bb = _load("build_bundle")


# ----------------------------------------------------------------------------- builder
def test_lock_hashes_cover_every_pinned_package() -> None:
    text = (ROOT / "requirements.lock").read_text()
    hashes = bb.lock_hashes(text)
    assert len(hashes) == len(re.findall(r"^[A-Za-z0-9_.-]+==", text, re.M))
    assert all(hashes.values()) and "opencv-python-headless" in hashes


def test_wheels_must_match_the_lock(tmp_path: Path) -> None:
    good = tmp_path / "pillow-12.0.0-cp311-cp311-manylinux_2_28_x86_64.whl"
    good.write_bytes(b"pillow")
    lock = {"pillow": {hashlib.sha256(b"pillow").hexdigest()}, "numpy": {"0" * 64}}
    assert bb.check_wheels([good], lock) == ["numpy: no wheel"]
    good.write_bytes(b"tampered")
    stray = tmp_path / "evil-1.0-py3-none-any.whl"
    stray.write_bytes(b"x")
    problems = bb.check_wheels([good, stray], lock)
    assert any("hash not in the lock" in p for p in problems)
    assert any("evil" in p and "not in the lock" in p for p in problems)


def _reports(tmp_path: Path, **overrides: object) -> Path:
    reports = tmp_path / "reports"
    reports.mkdir()
    vuln = {"PkgName": "libx", "VulnerabilityID": "CVE-1", "Severity": "HIGH"}
    trivy = {
        "Metadata": {"ImageID": f"sha256:{IMAGE_ID}"},
        "Results": [{"Vulnerabilities": [vuln | {"FixedVersion": ""}]}],
    }
    sbom = {
        "metadata": {
            "component": {
                "properties": [
                    {"name": "aquasecurity:trivy:ImageID", "value": f"sha256:{IMAGE_ID}"}
                ]
            }
        },
        "components": [{}, {}],
    }
    files: dict[str, object] = {
        "trivy-image.json": trivy,
        "sbom-image.cdx.json": sbom,
        "sbom-runtime.cdx.json": {"components": [{}]},
        "offline-test.json": {"image_id": IMAGE_ID, "passed": True},
    }
    files.update(overrides)
    for name, content in files.items():
        if content is not None:
            (reports / name).write_text(json.dumps(content))
    junit = overrides.get(
        "junit",
        '<testsuites><testsuite tests="5" failures="0" errors="0" skipped="0"/></testsuites>',
    )
    (reports / "pytest.xml").write_text(str(junit))
    return reports


def test_gates_pass_for_the_shipped_image(tmp_path: Path) -> None:
    problems, summary = bb.check_gates(_reports(tmp_path), IMAGE_ID)
    assert problems == []
    assert summary["tests"]["tests"] == 5 and summary["trivy_fixable_high_critical"] == 0


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({"offline-test.json": {"image_id": "b" * 64, "passed": True}}, "not about this image"),
        ({"offline-test.json": {"image_id": IMAGE_ID, "passed": False}}, "offline test failed"),
        (
            {
                "trivy-image.json": {
                    "Metadata": {"ImageID": IMAGE_ID},
                    "Results": [
                        {
                            "Vulnerabilities": [
                                {
                                    "PkgName": "libssl",
                                    "VulnerabilityID": "CVE-2",
                                    "Severity": "CRITICAL",
                                    "FixedVersion": "3.1",
                                }
                            ]
                        }
                    ],
                }
            },
            "fixable HIGH/CRITICAL",
        ),
        ({"trivy-image.json": {"Metadata": {"ImageID": "c" * 64}}}, "trivy-image.json is about"),
        ({"sbom-image.cdx.json": None}, "sbom-image.cdx.json: missing"),
        ({"junit": '<testsuite tests="5" failures="1" errors="0"/>'}, "tests:"),
    ],
)
def test_gates_refuse(tmp_path: Path, overrides: dict[str, object], expected: str) -> None:
    problems, _ = bb.check_gates(_reports(tmp_path, **overrides), IMAGE_ID)
    assert any(expected in p for p in problems), problems


def test_bundle_tar_is_deterministic(tmp_path: Path) -> None:
    source = tmp_path / "newgen-ocr-1.0.0"
    (source / "config").mkdir(parents=True)
    (source / "ngocrctl").write_text("#!/bin/sh\n")
    (source / "config" / "compose.yaml").write_text("services: {}\n")
    first, second = tmp_path / "1.tar", tmp_path / "2.tar"
    bb.deterministic_tar(source, first, 1_700_000_000)
    (source / "config" / "compose.yaml").touch()  # a newer mtime changes nothing
    bb.deterministic_tar(source, second, 1_700_000_000)
    assert first.read_bytes() == second.read_bytes()
    import tarfile

    with tarfile.open(first) as tar:
        modes = {m.name: (m.mode, m.uid, m.mtime) for m in tar.getmembers()}
    assert modes["newgen-ocr-1.0.0/ngocrctl"] == (0o755, 0, 1_700_000_000)
    assert modes["newgen-ocr-1.0.0/config/compose.yaml"][0] == 0o644


def test_images_override_covers_every_service() -> None:
    compose = (ROOT / "deploy" / "compose.yaml").read_text()
    services = set(
        re.findall(
            r"^  ([a-z]+):\n", compose.split("\nservices:\n")[1].split("\nnetworks:\n")[0], re.M
        )
    )
    images = [
        {"name": n, "tag": f"localhost/ngocr/{n}:1.0.0"}
        for n in ("app", "postgres", "valkey", "nginx")
    ]
    override = bb.compose_images(images)
    listed = set(re.findall(r"^  ([a-z]+):$", override, re.M))
    assert listed == services
    assert override.count("pull_policy: never") == len(services)


def test_upstream_images_are_found_by_digest() -> None:
    builder = bb.Builder.__new__(bb.Builder)
    refs = builder.upstream_refs()
    assert set(refs) == {"postgres", "valkey", "nginx"}
    assert all("@sha256:" in ref for ref in refs.values())


# ----------------------------------------------------------------------------- verifier
def _shell() -> str:
    return shutil.which("dash") or "/bin/sh"  # POSIX sh, not bash


@pytest.fixture
def signed_bundle(tmp_path: Path) -> tuple[Path, Path]:
    key = tmp_path / "key"
    subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key)], check=True)  # noqa: S603, S607
    bundle = tmp_path / "newgen-ocr-1.0.0"
    (bundle / "config").mkdir(parents=True)
    shutil.copy2(NGOCRCTL, bundle / "ngocrctl")
    (bundle / "config" / "compose.yaml").write_text("services: {}\n")
    (bundle / "release.env").write_text("NGOCR_RELEASE=1.0.0\nNGOCR_DIRTY=false\n")
    (bundle / "MANIFEST.json").write_text("{}\n")
    bb.write_sums(bundle)
    bb.sign(key, bundle / "SHA256SUMS")
    signers = tmp_path / "allowed_signers"
    public = (tmp_path / "key.pub").read_text().strip()
    signers.write_text(f'{bb.PRINCIPAL} namespaces="{bb.NAMESPACE}" {public}\n')
    return bundle, signers


def _verify(bundle: Path, signers: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - our own script
        [_shell(), str(bundle / "ngocrctl"), "verify", "--signers", str(signers)],
        capture_output=True,
        text=True,
        check=False,
        env={"PATH": "/usr/bin:/bin", "NGOCR_ENGINE": "none"},
    )


def test_verify_accepts_the_signed_bundle(signed_bundle: tuple[Path, Path]) -> None:
    result = _verify(*signed_bundle)
    assert result.returncode == 0, result.stderr
    assert "Good" in result.stdout and "all 4 files match" in result.stdout


def test_verify_refuses_a_changed_file(signed_bundle: tuple[Path, Path]) -> None:
    bundle, signers = signed_bundle
    (bundle / "config" / "compose.yaml").write_text("services: {evil: {}}\n")
    result = _verify(bundle, signers)
    assert result.returncode == 1 and "CHECKSUM MISMATCH" in result.stderr


def test_verify_refuses_an_unlisted_file(signed_bundle: tuple[Path, Path]) -> None:
    bundle, signers = signed_bundle
    (bundle / "config" / "compose.override.yaml").write_text("services: {}\n")
    result = _verify(bundle, signers)
    assert result.returncode == 1 and "not in SHA256SUMS" in result.stderr


def test_verify_refuses_edited_sums_and_other_signers(
    signed_bundle: tuple[Path, Path], tmp_path: Path
) -> None:
    bundle, signers = signed_bundle
    # Re-hashing after a change does not help: the sums are signed.
    (bundle / "config" / "compose.yaml").write_text("services: {evil: {}}\n")
    bb.write_sums(bundle)
    assert "SIGNATURE CHECK FAILED" in _verify(bundle, signers).stderr
    # A key that is not the site's trusted one.
    other = tmp_path / "other"
    subprocess.run(  # noqa: S603
        ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(other)],  # noqa: S607
        check=True,
    )
    bb.sign(other, bundle / "SHA256SUMS")
    assert "SIGNATURE CHECK FAILED" in _verify(bundle, signers).stderr


def test_verify_refuses_links(signed_bundle: tuple[Path, Path]) -> None:
    bundle, signers = signed_bundle
    (bundle / "config" / "secrets").symlink_to("/etc")
    result = _verify(bundle, signers)
    assert result.returncode == 1 and "links" in result.stderr


def test_ngocrctl_is_posix_sh() -> None:
    result = subprocess.run(  # noqa: S603 - our own script
        [_shell(), "-n", str(NGOCRCTL)], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr
    assert NGOCRCTL.stat().st_mode & 0o111


def test_production_tools_never_pull_or_fetch() -> None:
    """Nothing on the production side can reach a registry or download anything."""
    code = [line for line in NGOCRCTL.read_text().splitlines() if not line.lstrip().startswith("#")]
    text = "\n".join(code)
    for forbidden in (
        "ENGINE pull",
        "curl",
        "wget",
        "pip install",
        "apt-get",
        "git clone",
        "--network bridge",
    ):
        assert forbidden not in text, forbidden
    # Every one-off container is offline, except the clients of the ingress (the smoke test
    # and the benchmark), which run only our scripts against the published HTTPS address.
    runs = re.findall(r"\$ENGINE run (?:[^\n]*\\\n)*[^\n]*", text)  # with continuations
    assert runs and all("--network none" in r or "--network host" in r for r in runs)
    host = [r for r in runs if "--network host" in r]
    assert len(host) == 2
    assert all("smoke_api.py" in r or "benchmark.py" in r for r in host)


@pytest.mark.parametrize(
    ("probe", "ok"),
    [
        ({"dns:pypi.org": "blocked (gaierror)", "control:postgres:5432": "REACHED"}, True),
        ({"tcp:1.1.1.1:443": "REACHED", "control:postgres:5432": "REACHED"}, False),
        (
            {"https:huggingface.co": "blocked (URLError)", "control:postgres:5432": "blocked (x)"},
            False,
        ),
        ({}, False),
    ],
)
def test_egress_verdict(probe: dict[str, str], ok: bool) -> None:
    """Anything reached outside fails the smoke test, and so does an unreachable control."""
    result = subprocess.run(  # noqa: S603 - our own script
        [_shell(), "-c", f'. "{NGOCRCTL}"; egress_ok "$1"', "sh", json.dumps(probe)],
        capture_output=True,
        text=True,
        check=False,
        env={"PATH": "/usr/bin:/bin", "NGOCRCTL_SOURCED": "1", "NGOCR_ENGINE": "none"},
    )
    assert (result.returncode == 0) is ok, result.stderr


def test_smoke_sample_has_a_truth_for_every_checked_field(tmp_path: Path) -> None:
    """A missing truth must never compare equal to a missing extraction (None == None)."""
    bb.write_smoke_sample(tmp_path)
    expected = json.loads((tmp_path / "smoke-invoice.json").read_text())
    assert set(expected) == set(bb.SMOKE_FIELDS)
    assert all(isinstance(v, str) and v for v in expected.values())
    assert (tmp_path / "smoke-invoice.png").read_bytes().startswith(b"\x89PNG")


@pytest.mark.models
@pytest.mark.tesseract
def test_offline_ocr_smoke_script(tmp_path: Path) -> None:
    """The script ngocrctl runs in the image: every enabled model, and the invoice's fields."""
    bb.write_smoke_sample(tmp_path)
    result = subprocess.run(  # noqa: S603 - our own script
        [sys.executable, "-", str(tmp_path)],
        stdin=(ROOT / "deploy" / "bundle" / "smoke_ocr.py").open(),
        capture_output=True,
        text=True,
        check=False,
        cwd=ROOT,
        timeout=900,
    )
    report = json.loads(result.stdout.strip().splitlines()[-1])
    assert result.returncode == 0 and report["passed"], report
    assert len(report["models"]) == 11
    assert all(report["extraction"]["matches"].values())
