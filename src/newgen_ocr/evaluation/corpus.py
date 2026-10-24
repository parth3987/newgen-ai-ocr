"""Synthetic baseline corpus: printed pages with exact ground truth (docs/ACCURACY_EVALUATION.md).

Text comes from ``evaluation/corpus/text/<lang>.txt`` (16 document-style lines per
language). Pages are rendered with Noto fonts through Pillow's raqm layout engine, which
performs proper complex-script shaping (conjuncts, matras) for Indic scripts, then optionally
degraded. Results on this corpus are labelled synthetic; they do not predict accuracy on real
scans.
"""

from __future__ import annotations

import hashlib
import io
import json
from dataclasses import asdict, dataclass
from pathlib import Path

import cv2
import numpy as np
import numpy.typing as npt
from PIL import Image, ImageDraw, ImageFont, features

Gray = npt.NDArray[np.uint8]

DEFAULT_FONT_DIR = Path("/usr/share/fonts/truetype/noto")
LANGUAGE_FONTS: dict[str, tuple[str, str]] = {
    "en": ("NotoSans-Regular.ttf", "NotoSerif-Regular.ttf"),
    "hi": ("NotoSansDevanagari-Regular.ttf", "NotoSerifDevanagari-Regular.ttf"),
    "mr": ("NotoSansDevanagari-Regular.ttf", "NotoSerifDevanagari-Regular.ttf"),
    "te": ("NotoSansTelugu-Regular.ttf", "NotoSerifTelugu-Regular.ttf"),
    "ta": ("NotoSansTamil-Regular.ttf", "NotoSerifTamil-Regular.ttf"),
    "kn": ("NotoSansKannada-Regular.ttf", "NotoSerifKannada-Regular.ttf"),
}
# condition -> render DPI
CONDITIONS: dict[str, int] = {"clean": 300, "scan": 200, "lowres": 150}


@dataclass(frozen=True)
class Sample:
    id: str
    language: str
    font: str
    condition: str
    dpi: int
    image: str  # file name relative to the corpus directory
    truth: str  # file name relative to the corpus directory
    sha256: str


def load_lines(text_dir: Path, language: str) -> list[str]:
    return [
        line.strip()
        for line in (text_dir / f"{language}.txt").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def render_page(
    lines: list[str], font_path: Path, dpi: int, line_fonts: list[Path] | None = None
) -> Gray:
    """A4 page, 1-inch margins, ~11 pt text, 1.6x line spacing. ``line_fonts`` gives each
    line its own font (mixed scripts: a font per script, so no glyph is missing)."""
    if not features.check("raqm"):
        raise RuntimeError("Pillow raqm layout is required to shape Indic scripts correctly")
    width, height = round(8.27 * dpi), round(11.69 * dpi)
    img = Image.new("L", (width, height), 255)
    draw = ImageDraw.Draw(img)
    size = round(dpi * 11 / 72)
    loaded: dict[Path, ImageFont.FreeTypeFont] = {}
    y = dpi
    for index, line in enumerate(lines):
        path = line_fonts[index] if line_fonts else font_path
        if path not in loaded:
            loaded[path] = ImageFont.truetype(str(path), size, layout_engine=ImageFont.Layout.RAQM)
        font = loaded[path]
        # Ground truth must be fully visible: keep at least half an inch of right margin.
        if dpi + font.getlength(line) > width - dpi / 2:
            raise ValueError(f"corpus line too long for the page: {line[:30]!r}")
        draw.text((dpi, y), line, fill=0, font=font)
        y += round(size * 1.6)
    return np.asarray(img, dtype=np.uint8)


def degrade_scan(page: Gray, seed: int) -> Gray:
    """Typical office scan: slight skew, blur, sensor noise, JPEG compression."""
    rng = np.random.default_rng(seed)
    h, w = page.shape
    matrix = cv2.getRotationMatrix2D((w / 2, h / 2), 1.2, 1.0)
    out = cv2.warpAffine(page, matrix, (w, h), flags=cv2.INTER_LINEAR, borderValue=255)
    out = cv2.GaussianBlur(out, (0, 0), 0.7)
    out = np.clip(out + rng.normal(0, 8, out.shape), 0, 255).astype(np.uint8)
    _, encoded = cv2.imencode(".jpg", out, [cv2.IMWRITE_JPEG_QUALITY, 70])
    return np.asarray(cv2.imdecode(encoded, cv2.IMREAD_GRAYSCALE), dtype=np.uint8)


def build(text_dir: Path, out_dir: Path, font_dir: Path = DEFAULT_FONT_DIR) -> list[Sample]:
    out_dir.mkdir(parents=True, exist_ok=True)
    samples: list[Sample] = []
    for language, fonts in LANGUAGE_FONTS.items():
        lines = load_lines(text_dir, language)
        truth = "\n".join(lines)
        for font_name in fonts:
            font_path = font_dir / font_name
            if not font_path.is_file():
                raise FileNotFoundError(f"font not found: {font_path}")
            for condition, dpi in CONDITIONS.items():
                page = render_page(lines, font_path, dpi)
                if condition == "scan":
                    page = degrade_scan(page, seed=len(samples))
                sample_id = f"{language}-{font_name.split('-')[0]}-{condition}"
                buffer = io.BytesIO()
                Image.fromarray(page).save(buffer, "PNG", dpi=(dpi, dpi))
                (out_dir / f"{sample_id}.png").write_bytes(buffer.getvalue())
                (out_dir / f"{sample_id}.txt").write_text(truth + "\n", encoding="utf-8")
                samples.append(
                    Sample(
                        id=sample_id,
                        language=language,
                        font=font_name,
                        condition=condition,
                        dpi=dpi,
                        image=f"{sample_id}.png",
                        truth=f"{sample_id}.txt",
                        sha256=hashlib.sha256(buffer.getvalue()).hexdigest(),
                    )
                )
    (out_dir / "manifest.json").write_text(
        json.dumps([asdict(s) for s in samples], indent=2), encoding="utf-8"
    )
    return samples


def load_manifest(corpus_dir: Path) -> list[Sample]:
    data = json.loads((corpus_dir / "manifest.json").read_text(encoding="utf-8"))
    return [Sample(**item) for item in data]
