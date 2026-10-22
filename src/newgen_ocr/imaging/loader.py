"""Safe decoding of uploaded images into normalised page images.

Only the Pillow plugin for the detected format is allowed to parse the file, so
decoders for formats we never accept (EPS, GIF, XBM, ...) are unreachable.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageOps

from newgen_ocr.errors import AppError, ErrorCode
from newgen_ocr.ingestion.formats import DocumentFormat

_PIL_FORMATS = {
    DocumentFormat.JPEG: ["JPEG"],
    DocumentFormat.PNG: ["PNG"],
    DocumentFormat.TIFF: ["TIFF"],
}


@dataclass(frozen=True)
class ImageLimits:
    max_pages: int
    max_page_pixels: int


def _corrupt() -> AppError:
    return AppError(ErrorCode.CORRUPT_DOCUMENT, "Image could not be decoded")


@contextmanager
def open_image(path: Path, fmt: DocumentFormat) -> Iterator[Image.Image]:
    try:
        img = Image.open(path, formats=_PIL_FORMATS[fmt])
    except Image.DecompressionBombError as exc:
        raise AppError(ErrorCode.PAGE_TOO_LARGE, "Image exceeds the pixel limit") from exc
    # Decoders parse untrusted bytes and raise a wide range of exception types on
    # malformed input; all of them mean "corrupt", and the cause is chained for logs.
    except Exception as exc:
        raise _corrupt() from exc
    try:
        yield img
    finally:
        img.close()


def frame_count(img: Image.Image, limits: ImageLimits) -> int:
    try:
        frames = int(getattr(img, "n_frames", 1))
    except Exception as exc:  # see open_image
        raise _corrupt() from exc
    if frames > limits.max_pages:
        raise AppError(
            ErrorCode.TOO_MANY_PAGES,
            f"Image has {frames} pages; the limit is {limits.max_pages}",
        )
    return frames


def _normalise(img: Image.Image) -> Image.Image:
    """Apply EXIF orientation and convert to 8-bit L or RGB."""
    img = ImageOps.exif_transpose(img)
    if img.mode in ("1", "L"):
        return img.convert("L")
    if img.mode.startswith("I"):
        # 16/32-bit integer greyscale: scale to 8 bits rather than clip.
        return img.point(lambda v: v * (1 / 256)).convert("L")
    if img.mode == "F":
        return img.convert("L")
    if img.mode in ("RGBA", "LA", "PA") or (img.mode == "P" and "transparency" in img.info):
        rgba = img.convert("RGBA")
        background = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
        return Image.alpha_composite(background, rgba).convert("RGB")
    return img.convert("RGB")


def iter_pages(img: Image.Image, frames: int, limits: ImageLimits) -> Iterator[Image.Image]:
    """Yield each frame as a decoded, normalised image, in file order."""
    for index in range(frames):
        try:
            img.seek(index)
            if img.width * img.height > limits.max_page_pixels:
                raise AppError(
                    ErrorCode.PAGE_TOO_LARGE, f"Page {index + 1} exceeds the pixel limit"
                )
            img.load()
            page = _normalise(img)
        except AppError:
            raise
        except Exception as exc:  # see open_image
            raise _corrupt() from exc
        yield page
