"""Deterministic synthetic test documents. No real documents are used in tests.

`build_pdf` hand-writes small, valid PDFs so tests control exactly what each page
contains: a text layer, an embedded JPEG "scan", an invisible OCR layer over a scan,
and optional standard-security-handler (RC4, 40-bit, R2) encryption.
"""

from __future__ import annotations

import hashlib
import io
import struct
from collections.abc import Sequence
from dataclasses import dataclass

from PIL import Image, ImageDraw

LETTER = (612, 792)


@dataclass(frozen=True)
class PageSpec:
    text: str = ""
    jpeg: bytes | None = None  # drawn full-page beneath any text
    invisible_text: bool = False  # text render mode 3, as scanners' OCR layers use
    size: tuple[int, int] = LETTER


# ---------------------------------------------------------------- encryption (test-only)

_PAD = bytes.fromhex("28BF4E5E4E758A4164004E56FFFA01082E2E00B6D0683E802F0CA9FE6453697A")
_FILE_ID = bytes(range(16))
_PERMISSIONS = -44


def _rc4(key: bytes, data: bytes) -> bytes:
    s = list(range(256))
    j = 0
    for i in range(256):
        j = (j + s[i] + key[i % len(key)]) % 256
        s[i], s[j] = s[j], s[i]
    out = bytearray()
    i = j = 0
    for byte in data:
        i = (i + 1) % 256
        j = (j + s[i]) % 256
        s[i], s[j] = s[j], s[i]
        out.append(byte ^ s[(s[i] + s[j]) % 256])
    return bytes(out)


def _padded(password: str) -> bytes:
    return (password.encode("latin-1") + _PAD)[:32]


class _Rc4Security:
    def __init__(self, user_password: str, owner_password: str) -> None:
        owner_key = hashlib.md5(_padded(owner_password or user_password)).digest()[:5]  # noqa: S324
        self.o_value = _rc4(owner_key, _padded(user_password))
        self.key = hashlib.md5(  # noqa: S324
            _padded(user_password) + self.o_value + struct.pack("<i", _PERMISSIONS) + _FILE_ID
        ).digest()[:5]
        self.u_value = _rc4(self.key, _PAD)

    def encrypt(self, obj_num: int, data: bytes) -> bytes:
        obj_key = hashlib.md5(  # noqa: S324
            self.key + obj_num.to_bytes(3, "little") + (0).to_bytes(2, "little")
        ).digest()[:10]
        return _rc4(obj_key, data)

    def dictionary(self) -> bytes:
        return (
            b"<< /Filter /Standard /V 1 /R 2 /Length 40 /O <"
            + self.o_value.hex().encode()
            + b"> /U <"
            + self.u_value.hex().encode()
            + b"> /P "
            + str(_PERMISSIONS).encode()
            + b" >>"
        )


# ---------------------------------------------------------------- PDF writer


def _escape(text: str) -> bytes:
    raw = text.encode("latin-1")
    return raw.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")


def _content_stream(page: PageSpec, image_name: bytes | None) -> bytes:
    width, height = page.size
    parts: list[bytes] = []
    if image_name is not None:
        parts.append(b"q %d 0 0 %d 0 0 cm /%s Do Q" % (width, height, image_name))
    if page.text:
        parts.append(b"BT /F1 12 Tf 14 TL 72 %d Td" % (height - 72))
        if page.invisible_text:
            parts.append(b"3 Tr")
        for line in page.text.split("\n"):
            parts.append(b"(" + _escape(line) + b") Tj T*")
        parts.append(b"ET")
    return b"\n".join(parts)


def build_pdf(
    pages: Sequence[PageSpec],
    *,
    user_password: str | None = None,
    owner_password: str | None = None,
) -> bytes:
    security = (
        _Rc4Security(user_password or "", owner_password or "")
        if user_password is not None or owner_password is not None
        else None
    )
    objects: dict[int, bytes] = {}
    next_num = 4  # 1 catalog, 2 page tree, 3 font
    page_nums: list[int] = []

    def stream(num: int, extra: bytes, data: bytes) -> bytes:
        if security is not None:
            data = security.encrypt(num, data)
        return b"<< " + extra + b" /Length %d >>\nstream\n" % len(data) + data + b"\nendstream"

    for page in pages:
        page_num, content_num = next_num, next_num + 1
        next_num += 2
        resources = b"/Font << /F1 3 0 R >>"
        image_name = None
        if page.jpeg is not None:
            image_num = next_num
            next_num += 1
            with Image.open(io.BytesIO(page.jpeg)) as img:
                w, h = img.size
            objects[image_num] = stream(
                image_num,
                b"/Type /XObject /Subtype /Image /Width %d /Height %d "
                b"/ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /DCTDecode" % (w, h),
                page.jpeg,
            )
            image_name = b"Im1"
            resources += b" /XObject << /Im1 %d 0 R >>" % image_num
        objects[content_num] = stream(content_num, b"", _content_stream(page, image_name))
        objects[page_num] = (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 %d %d] /Resources << %s >> "
            b"/Contents %d 0 R >>" % (page.size[0], page.size[1], resources, content_num)
        )
        page_nums.append(page_num)

    objects[1] = b"<< /Type /Catalog /Pages 2 0 R >>"
    kids = b" ".join(b"%d 0 R" % n for n in page_nums)
    objects[2] = b"<< /Type /Pages /Kids [%s] /Count %d >>" % (kids, len(page_nums))
    objects[3] = b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"
    encrypt_ref = b""
    if security is not None:
        objects[next_num] = security.dictionary()
        encrypt_ref = b" /Encrypt %d 0 R" % next_num
        next_num += 1

    out = io.BytesIO()
    out.write(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = {}
    for num in sorted(objects):
        offsets[num] = out.tell()
        out.write(b"%d 0 obj\n" % num + objects[num] + b"\nendobj\n")
    xref_at = out.tell()
    out.write(b"xref\n0 %d\n0000000000 65535 f \n" % next_num)
    for num in range(1, next_num):
        out.write(b"%010d 00000 n \n" % offsets[num])
    file_id = _FILE_ID.hex().encode()
    out.write(
        b"trailer\n<< /Size %d /Root 1 0 R%s /ID [<%s> <%s>] >>\nstartxref\n%d\n%%%%EOF\n"
        % (next_num, encrypt_ref, file_id, file_id, xref_at)
    )
    return out.getvalue()


# ---------------------------------------------------------------- images


def pattern_image(size: tuple[int, int] = (200, 100), mode: str = "RGB") -> Image.Image:
    """Left half black, right half white: easy to verify after rendering."""
    img = Image.new(mode, size, "white")
    ImageDraw.Draw(img).rectangle((0, 0, size[0] // 2 - 1, size[1] - 1), fill="black")
    return img


def image_bytes(fmt: str, size: tuple[int, int] = (200, 100), frames: int = 1) -> bytes:
    buf = io.BytesIO()
    imgs = [pattern_image(size) for _ in range(frames)]
    if frames > 1:
        imgs[0].save(buf, fmt, save_all=True, append_images=imgs[1:])
    else:
        imgs[0].save(buf, fmt)
    return buf.getvalue()


def jpeg_bytes(size: tuple[int, int] = (200, 100)) -> bytes:
    return image_bytes("JPEG", size)


PARAGRAPH = (
    "This invoice confirms delivery of the goods listed below.\n"
    "Payment is due within thirty days of the invoice date.\n"
    "Please quote the reference number on all correspondence."
)
