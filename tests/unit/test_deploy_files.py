"""Deployment files stay consistent with the locks and policies (ADR-0031)."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _block(text: str, package: str) -> tuple[str, set[str]]:
    match = re.search(rf"^{package}==(\S+) \\\n((?:    --hash=\S+(?: \\)?\n?)+)", text, re.M)
    assert match, f"{package} not pinned"
    return match.group(1), set(re.findall(r"sha256:[0-9a-f]{64}", match.group(2)))


def test_opencv_build_numpy_matches_the_lock() -> None:
    """OpenCV's bindings are compiled against exactly the NumPy that ships."""
    shipped = _block((ROOT / "requirements.lock").read_text(), "numpy")
    build = _block((ROOT / "deploy" / "opencv-build.lock").read_text(), "numpy")
    assert build == shipped


def test_images_are_pinned_by_digest() -> None:
    files = [ROOT / "deploy" / "compose.yaml", ROOT / "deploy" / "Dockerfile", ROOT / "Makefile"]
    for path in files:
        for ref in re.findall(
            r"(?:docker\.io|ghcr\.io)/[\w./-]+:[\w.-]+(?:@sha256:[0-9a-f]{64})?", path.read_text()
        ):
            assert "@sha256:" in ref, f"{path.name}: {ref} is not pinned by digest"


def test_compose_hardening() -> None:
    compose = (ROOT / "deploy" / "compose.yaml").read_text()
    # The database and queue never publish ports; only the ingress does.
    assert compose.count("ports:") == 1
    assert "no-new-privileges:true" in compose and "cap_drop: [ALL]" in compose
    assert compose.count("internal: true") == 2  # backend and edge


def test_nginx_tls_policy() -> None:
    conf = (ROOT / "deploy" / "nginx" / "nginx.conf").read_text()
    assert "ssl_protocols TLSv1.2 TLSv1.3;" in conf
    assert "ssl_session_tickets off;" in conf and "server_tokens off;" in conf
    assert "Strict-Transport-Security" in conf and "$request_uri" not in conf  # no query in logs


def test_compose_secrets_follow_the_site_path() -> None:
    """An installed release reads the site's secrets; a checkout reads deploy/secrets/."""
    compose = (ROOT / "deploy" / "compose.yaml").read_text()
    files = re.findall(r"^    file: (\S+)$", compose.split("\nsecrets:\n")[-1], re.M)
    assert len(files) == 6
    assert all(f.startswith("${NGOCR_SECRETS_PATH:-./secrets}/") for f in files)
