"""Synthetic page images for preprocessing and quality tests (greyscale numpy arrays).

Pages are drawn with Pillow's bundled FreeType font, so text has realistic line structure.
Degradations are deterministic (seeded).
"""

from __future__ import annotations

import cv2
import numpy as np
import numpy.typing as npt
from PIL import Image, ImageDraw, ImageFont

Gray = npt.NDArray[np.uint8]

_WORDS = [
    "invoice",
    "payment",
    "total",
    "amount",
    "due",
    "reference",
    "customer",
    "address",
    "account",
    "number",
    "date",
    "quantity",
    "description",
    "price",
    "tax",
    "balance",
    "signature",
    "approved",
    "received",
    "delivery",
]


def text_page_with_truth(
    dpi: int = 200, ink: int = 0, background: int = 255, seed: int = 0
) -> tuple[Gray, str]:
    """A4 page of text lines with ~0.14 inch glyphs and 1-inch margins, plus its text."""
    width, height = round(8.27 * dpi), round(11.69 * dpi)
    rng = np.random.default_rng(seed)
    img = Image.new("L", (width, height), background)
    draw = ImageDraw.Draw(img)
    font = ImageFont.load_default(size=round(dpi * 0.14))
    lines = []
    y = dpi
    while y < height - dpi:
        line = " ".join(rng.choice(_WORDS, size=11))
        draw.text((dpi, y), line, fill=ink, font=font)
        lines.append(line)
        y += round(dpi * 0.25)
    return np.asarray(img, dtype=np.uint8), "\n".join(lines)


def text_page(dpi: int = 200, ink: int = 0, background: int = 255, seed: int = 0) -> Gray:
    return text_page_with_truth(dpi, ink, background, seed)[0]


def rotate(page: Gray, degrees_ccw: float) -> Gray:
    """Rotate about the centre without changing size (corners filled white)."""
    h, w = page.shape
    matrix = cv2.getRotationMatrix2D((w / 2, h / 2), degrees_ccw, 1.0)
    return np.asarray(
        cv2.warpAffine(page, matrix, (w, h), flags=cv2.INTER_LINEAR, borderValue=255),
        dtype=np.uint8,
    )


def quarter_turn(page: Gray, turns_cw: int) -> Gray:
    codes = {1: cv2.ROTATE_90_CLOCKWISE, 2: cv2.ROTATE_180, 3: cv2.ROTATE_90_COUNTERCLOCKWISE}
    return np.asarray(cv2.rotate(page, codes[turns_cw]), dtype=np.uint8)


def gaussian_noise(page: Gray, sigma: float, seed: int = 1) -> Gray:
    rng = np.random.default_rng(seed)
    return np.clip(page + rng.normal(0, sigma, page.shape), 0, 255).astype(np.uint8)


def salt_and_pepper(page: Gray, amount: float, seed: int = 2) -> Gray:
    rng = np.random.default_rng(seed)
    out = page.copy()
    r = rng.random(page.shape)
    out[r < amount / 2] = 0
    out[r > 1 - amount / 2] = 255
    return out


def blur(page: Gray, sigma: float) -> Gray:
    return np.asarray(cv2.GaussianBlur(page, (0, 0), sigma), dtype=np.uint8)


def shadow(page: Gray, darkest: float = 0.5) -> Gray:
    """Left-to-right illumination gradient, as in a phone photo."""
    gradient = np.linspace(darkest, 1.0, page.shape[1])[None, :]
    return np.clip(page.astype(np.float64) * gradient, 0, 255).astype(np.uint8)


def blank_page(dpi: int = 200, level: int = 255) -> Gray:
    return np.full((round(11.69 * dpi), round(8.27 * dpi)), level, dtype=np.uint8)
