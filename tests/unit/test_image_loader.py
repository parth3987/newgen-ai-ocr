from __future__ import annotations

import io
from pathlib import Path

import pytest
from PIL import Image

from newgen_ocr.errors import AppError, ErrorCode
from newgen_ocr.imaging.loader import ImageLimits, frame_count, iter_pages, open_image
from newgen_ocr.ingestion.formats import DocumentFormat
from tests.fixtures.builders import image_bytes

LIMITS = ImageLimits(max_pages=10, max_page_pixels=4_000_000)


def _load(
    tmp_path: Path, data: bytes, fmt: DocumentFormat, limits: ImageLimits = LIMITS
) -> list[Image.Image]:
    path = tmp_path / "img"
    path.write_bytes(data)
    with open_image(path, fmt) as img:
        return list(iter_pages(img, frame_count(img, limits), limits))


def _save(img: Image.Image, fmt: str, **kwargs: object) -> bytes:
    buf = io.BytesIO()
    img.save(buf, fmt, **kwargs)
    return buf.getvalue()


@pytest.mark.parametrize(
    ("fmt", "pil_fmt"),
    [(DocumentFormat.JPEG, "JPEG"), (DocumentFormat.PNG, "PNG"), (DocumentFormat.TIFF, "TIFF")],
)
def test_single_page_images(tmp_path: Path, fmt: DocumentFormat, pil_fmt: str) -> None:
    [page] = _load(tmp_path, image_bytes(pil_fmt, (200, 100)), fmt)
    assert page.size == (200, 100)
    assert page.mode == "RGB"
    assert page.convert("L").getpixel((10, 50)) < 40
    assert page.convert("L").getpixel((190, 50)) > 215


def test_multipage_tiff_preserves_frame_order(tmp_path: Path) -> None:
    frames = [Image.new("L", (100 + 10 * i, 50), 255) for i in range(4)]
    data = _save(frames[0], "TIFF", save_all=True, append_images=frames[1:])
    pages = _load(tmp_path, data, DocumentFormat.TIFF)
    assert [p.width for p in pages] == [100, 110, 120, 130]


def test_rgba_is_composited_on_white(tmp_path: Path) -> None:
    img = Image.new("RGBA", (10, 10), (0, 0, 0, 0))
    [page] = _load(tmp_path, _save(img, "PNG"), DocumentFormat.PNG)
    assert page.mode == "RGB"
    assert page.getpixel((5, 5)) == (255, 255, 255)


def test_sixteen_bit_greyscale_is_scaled_not_clipped(tmp_path: Path) -> None:
    img = Image.new("I;16", (10, 10), 32768)  # mid-grey in 16 bits
    [page] = _load(tmp_path, _save(img, "PNG"), DocumentFormat.PNG)
    assert page.mode == "L"
    assert 120 <= page.getpixel((5, 5)) <= 135


def test_bilevel_becomes_greyscale(tmp_path: Path) -> None:
    img = Image.new("1", (10, 10), 1)
    [page] = _load(tmp_path, _save(img, "TIFF", compression="group4"), DocumentFormat.TIFF)
    assert page.mode == "L"
    assert page.getpixel((5, 5)) == 255


def test_exif_orientation_applied(tmp_path: Path) -> None:
    img = Image.new("RGB", (40, 20), "white")
    exif = Image.Exif()
    exif[0x0112] = 6  # rotate 90 CW on display
    [page] = _load(tmp_path, _save(img, "JPEG", exif=exif), DocumentFormat.JPEG)
    assert page.size == (20, 40)


@pytest.mark.parametrize(
    ("data", "fmt"),
    [
        (image_bytes("JPEG")[:200], DocumentFormat.JPEG),
        (image_bytes("PNG")[:60], DocumentFormat.PNG),
        (b"II*\x00" + b"\x00" * 20, DocumentFormat.TIFF),
        (b"\x89PNG\r\n\x1a\n" + b"garbage" * 10, DocumentFormat.PNG),
    ],
)
def test_corrupt_images_rejected(tmp_path: Path, data: bytes, fmt: DocumentFormat) -> None:
    with pytest.raises(AppError) as exc:
        _load(tmp_path, data, fmt)
    assert exc.value.code is ErrorCode.CORRUPT_DOCUMENT


def test_only_the_detected_format_decoder_is_used(tmp_path: Path) -> None:
    # A GIF can never be decoded, even if it reached the loader labelled as PNG.
    with pytest.raises(AppError) as exc:
        _load(tmp_path, image_bytes("GIF"), DocumentFormat.PNG)
    assert exc.value.code is ErrorCode.CORRUPT_DOCUMENT


def test_pixel_limit(tmp_path: Path) -> None:
    data = _save(Image.new("1", (3000, 3000), 1), "PNG")  # 9 MP, small file
    with pytest.raises(AppError) as exc:
        _load(tmp_path, data, DocumentFormat.PNG)
    assert exc.value.code is ErrorCode.PAGE_TOO_LARGE


def test_page_limit(tmp_path: Path) -> None:
    frames = [Image.new("L", (10, 10)) for _ in range(3)]
    data = _save(frames[0], "TIFF", save_all=True, append_images=frames[1:])
    with pytest.raises(AppError) as exc:
        _load(tmp_path, data, DocumentFormat.TIFF, ImageLimits(max_pages=2, max_page_pixels=10**6))
    assert exc.value.code is ErrorCode.TOO_MANY_PAGES


def test_pillow_decompression_bomb_guard_maps_to_page_too_large(tmp_path: Path) -> None:
    # Above twice Pillow's MAX_IMAGE_PIXELS, Image.open itself refuses the file.
    data = _save(Image.new("1", (14_000, 14_000), 1), "PNG")
    with pytest.raises(AppError) as exc:
        _load(tmp_path, data, DocumentFormat.PNG, ImageLimits(max_pages=1, max_page_pixels=10**9))
    assert exc.value.code is ErrorCode.PAGE_TOO_LARGE
