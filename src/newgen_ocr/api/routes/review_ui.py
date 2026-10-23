"""The built-in review page (ADR-0028): static HTML, CSS and JS shipped with the package.

No CDN or external asset; a strict Content-Security-Policy allows only this origin's own
scripts, styles, API calls and images (``blob:`` for crops fetched with the token).
"""

from __future__ import annotations

from importlib import resources
from typing import Annotated

from fastapi import APIRouter, Path
from fastapi.responses import Response

from newgen_ocr.errors import AppError, ErrorCode

router = APIRouter(tags=["review-ui"], include_in_schema=False)

CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' blob:; "
    "connect-src 'self'; form-action 'none'; frame-ancestors 'none'; base-uri 'none'"
)
_FILES = {
    "review.js": "text/javascript; charset=utf-8",
    "review.css": "text/css; charset=utf-8",
}


def _asset(name: str) -> bytes:
    return resources.files("newgen_ocr.review").joinpath("ui", name).read_bytes()


def _response(content: bytes, media_type: str) -> Response:
    return Response(
        content=content,
        media_type=media_type,
        headers={
            "Content-Security-Policy": CSP,
            "X-Frame-Options": "DENY",
            "Referrer-Policy": "no-referrer",
        },
    )


@router.get("/review", response_class=Response)
def review_page() -> Response:
    return _response(_asset("index.html"), "text/html; charset=utf-8")


@router.get("/review/static/{name}", response_class=Response)
def review_asset(name: Annotated[str, Path(pattern=r"^review\.(js|css)$")]) -> Response:
    if name not in _FILES:
        raise AppError(ErrorCode.NOT_FOUND, "Not found")
    return _response(_asset(name), _FILES[name])
