"""The web application (ADR-0030): the React build, served from this origin under /app.

Built at staging with ``make frontend`` into ``newgen_ocr/webapp/dist`` and shipped in the
package; production never runs Node. Every file is local: the Content-Security-Policy
allows only this origin's scripts, styles, images and API calls, so the browser itself
refuses any CDN, analytics or other external request. Unknown paths under /app return the
application (client-side routes); files under /app/assets are content-hashed and cached.
"""

from __future__ import annotations

import mimetypes
from importlib import resources
from importlib.resources.abc import Traversable
from typing import Annotated

from fastapi import APIRouter, Path
from fastapi.responses import HTMLResponse, RedirectResponse, Response

router = APIRouter(include_in_schema=False)

CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' blob: data:; "
    "connect-src 'self'; font-src 'self'; form-action 'self'; frame-ancestors 'none'; "
    "base-uri 'none'"
)
HEADERS = {
    "Content-Security-Policy": CSP,
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
    "Cross-Origin-Opener-Policy": "same-origin",
}
_TYPES = {".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8"}

NOT_BUILT = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<title>New Gen AI OCR</title></head><body>
<h1>The web application is not built</h1>
<p>Run <code>make frontend</code> (staging; needs Node and the npm lock), then restart.</p>
</body></html>"""


def _dist() -> Traversable:
    return resources.files("newgen_ocr.webapp").joinpath("dist")


def _index() -> Response:
    index = _dist().joinpath("index.html")
    if not index.is_file():
        return HTMLResponse(NOT_BUILT, status_code=503, headers=HEADERS)
    # The shell changes with every build; never cache it.
    return HTMLResponse(index.read_bytes(), headers={**HEADERS, "Cache-Control": "no-store"})


@router.get("/")
def root() -> RedirectResponse:
    return RedirectResponse("/app/", status_code=307)


@router.get("/review")
def old_review_page() -> RedirectResponse:
    """The Phase 8 review page moved into the web application."""
    return RedirectResponse("/app/review", status_code=308)


@router.get("/app")
@router.get("/app/")
def app_index() -> Response:
    return _index()


@router.get("/app/assets/{name}")
def asset(name: Annotated[str, Path(pattern=r"^[A-Za-z0-9_.-]{1,128}$")]) -> Response:
    if name.startswith("."):
        return Response(status_code=404)
    path = _dist().joinpath("assets", name)
    if not path.is_file():
        return Response(status_code=404)
    suffix = "." + name.rsplit(".", 1)[-1] if "." in name else ""
    media = _TYPES.get(suffix) or mimetypes.guess_type(name)[0] or "application/octet-stream"
    return Response(
        path.read_bytes(),
        media_type=media,
        headers={**HEADERS, "Cache-Control": "public, max-age=31536000, immutable"},
    )


@router.get("/app/{route:path}")
def client_route(route: str) -> Response:
    """Client-side routes (``/app/documents/…``) load the application shell."""
    return _index()
