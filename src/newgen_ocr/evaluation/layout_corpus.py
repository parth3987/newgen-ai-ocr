"""Synthetic layout and table test set with exact ground truth (docs/ACCURACY_EVALUATION.md).

Pages are drawn with Noto fonts (raqm shaping, as in ``corpus.py``) so every table cell,
region and key-value pair has a known position and text:

- tables: fully ruled (wired), header-rule only and unruled (wireless); merged cells by
  colspan, rowspan and a two-level header; a two-column field/value table; cell text in all
  six languages;
- a two-column page with header, title, paragraphs, footer and page number (reading order);
- a key-value page with true pairs and lookalikes that must not become pairs.

Each page is rendered clean (300 DPI) and as a degraded scan (200 DPI, ``degrade_scan``).
Ground-truth boxes are in the clean render's frame; the pipeline's deskew brings scans back
to it. Results on this set are synthetic: they do not predict accuracy on real documents.
"""

from __future__ import annotations

import io
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont, features

from newgen_ocr.evaluation.corpus import (
    DEFAULT_FONT_DIR,
    LANGUAGE_FONTS,
    Gray,
    degrade_scan,
    load_lines,
)

CONDITIONS: dict[str, int] = {"clean": 300, "scan": 200}
PAGE_W_IN, PAGE_H_IN = 8.27, 11.69


@dataclass(frozen=True)
class CellSpec:
    row: int
    col: int
    text: str
    row_span: int = 1
    col_span: int = 1
    header: bool = False


@dataclass(frozen=True)
class TableSpec:
    id: str
    language: str
    rows: int
    col_widths: tuple[float, ...]  # inches
    cells: tuple[CellSpec, ...]
    rules: str = "full"  # "full" | "header" (one rule under the header) | "none"


@dataclass
class TruthCell:
    row: int
    col: int
    row_span: int
    col_span: int
    text: str
    bbox: list[float]  # page pixels [x0, y0, x1, y1]


@dataclass
class TruthTable:
    bbox: list[float]
    rows: int
    cols: int
    rules: str
    cells: list[TruthCell]


@dataclass
class TruthBlock:
    kind: str  # header | footer | page_number | title | paragraph
    text: str
    bbox: list[float]


@dataclass
class LayoutSample:
    id: str
    kind: str  # "table" | "columns" | "key_value"
    language: str
    condition: str
    dpi: int
    image: str
    tables: list[TruthTable] = field(default_factory=list)
    # Flow blocks in reading order, then page furniture.
    blocks: list[TruthBlock] = field(default_factory=list)
    columns: int = 1
    key_values: list[list[str]] = field(default_factory=list)
    not_key_values: list[str] = field(default_factory=list)  # lines that must not be pairs


def _words(text_dir: Path, language: str, count: int) -> list[str]:
    """Distinct short words from the language's corpus text (validated, shaped text)."""
    seen: list[str] = []
    for line in load_lines(text_dir, language):
        for word in line.replace("।", " ").replace(".", " ").replace(",", " ").split():
            if 2 <= len(word) <= 9 and word not in seen and not word.isdigit():
                seen.append(word)
    if len(seen) < count:
        raise ValueError(f"not enough words for {language}")
    return seen[:count]


def table_specs(text_dir: Path) -> list[TableSpec]:
    def grid(rows: list[list[str]], header: bool = True) -> tuple[CellSpec, ...]:
        return tuple(
            CellSpec(r, c, text, header=header and r == 0)
            for r, row in enumerate(rows)
            for c, text in enumerate(row)
        )

    specs = [
        TableSpec(
            "en-simple",
            "en",
            5,
            (2.0, 1.6, 1.6),
            grid(
                [
                    ["Product", "Units", "Price"],
                    ["Notebook", "120", "45.00"],
                    ["Pencil box", "75", "30.50"],
                    ["Marker set", "40", "110.00"],
                    ["Eraser", "300", "5.25"],
                ]
            ),
        ),
        TableSpec(
            "en-colspan",
            "en",
            5,
            (1.6, 1.3, 1.3, 1.3),
            (
                CellSpec(0, 0, "Region", header=True),
                CellSpec(0, 1, "Sales (units)", col_span=2, header=True),
                CellSpec(0, 3, "Returns", header=True),
                *_shift(
                    grid(
                        [
                            ["North", "1200", "1350", "40"],
                            ["South", "980", "1010", "25"],
                            ["East", "1430", "1500", "31"],
                            ["West", "870", "920", "18"],
                        ],
                        header=False,
                    ),
                    1,
                ),
            ),
        ),
        TableSpec(
            "en-rowspan",
            "en",
            5,
            (1.6, 1.9, 1.3),
            (
                CellSpec(0, 0, "Zone", header=True),
                CellSpec(0, 1, "City", header=True),
                CellSpec(0, 2, "Units", header=True),
                CellSpec(1, 0, "North", row_span=2),
                CellSpec(1, 1, "Delhi"),
                CellSpec(1, 2, "120"),
                CellSpec(2, 1, "Chandigarh"),
                CellSpec(2, 2, "80"),
                CellSpec(3, 0, "South", row_span=2),
                CellSpec(3, 1, "Chennai"),
                CellSpec(3, 2, "140"),
                CellSpec(4, 1, "Bengaluru"),
                CellSpec(4, 2, "95"),
            ),
        ),
        TableSpec(
            "en-two-level-header",
            "en",
            5,
            (1.6, 1.1, 1.3, 1.1, 1.3),
            (
                CellSpec(0, 0, "Item", row_span=2, header=True),
                CellSpec(0, 1, "Quarter 1", col_span=2, header=True),
                CellSpec(0, 3, "Quarter 2", col_span=2, header=True),
                CellSpec(1, 1, "Units", header=True),
                CellSpec(1, 2, "Value", header=True),
                CellSpec(1, 3, "Units", header=True),
                CellSpec(1, 4, "Value", header=True),
                *(
                    CellSpec(r, c, t)
                    for r, row in enumerate(
                        [
                            ["Paper", "20", "5000", "25", "6250"],
                            ["Toner", "3", "5400", "4", "7200"],
                            ["Stapler", "5", "600", "2", "240"],
                        ],
                        start=2,
                    )
                    for c, t in enumerate(row)
                ),
            ),
        ),
        TableSpec(
            "en-header-rule",
            "en",
            5,
            (1.9, 1.3, 1.5, 1.5),
            grid(
                [
                    ["Item", "Quantity", "Unit price", "Total"],
                    ["Paper A4", "20", "250.00", "5000.00"],
                    ["Toner", "3", "1800.00", "5400.00"],
                    ["Stapler", "5", "120.00", "600.00"],
                    ["Folder", "50", "15.00", "750.00"],
                ]
            ),
            rules="header",
        ),
        TableSpec(
            "en-unruled",
            "en",
            4,
            (2.0, 1.8, 1.6),
            grid(
                [
                    ["Name", "Department", "Extension"],
                    ["A. Kumar", "Accounts", "2201"],
                    ["S. Rao", "Logistics", "2214"],
                    ["M. Iyer", "Sales", "2230"],
                ]
            ),
            rules="none",
        ),
        TableSpec(
            "en-field-value",
            "en",
            4,
            (2.2, 3.0),
            grid(
                [
                    ["Invoice number", "INV-2024-0042"],
                    ["Customer", "Sharma Traders"],
                    ["Due date", "30 April 2024"],
                    ["Amount", "11000.00"],
                ],
                header=False,
            ),
        ),
    ]
    for language in ("hi", "mr", "te", "ta", "kn"):
        w = _words(text_dir, language, 9)
        specs.append(
            TableSpec(
                f"{language}-simple",
                language,
                4,
                (2.0, 2.0, 1.4),
                grid(
                    [
                        [w[0], w[1], w[2]],
                        [w[3], w[4], "120"],
                        [w[5], w[6], "75"],
                        [w[7], w[8], "40"],
                    ]
                ),
            )
        )
    w = _words(text_dir, "hi", 6)
    specs.append(
        TableSpec(
            "hi-colspan",
            "hi",
            4,
            (1.8, 1.4, 1.4),
            (
                CellSpec(0, 0, w[0], header=True),
                CellSpec(0, 1, w[1], col_span=2, header=True),
                CellSpec(1, 0, w[2]),
                CellSpec(1, 1, "1200"),
                CellSpec(1, 2, "1350"),
                CellSpec(2, 0, w[3]),
                CellSpec(2, 1, "980"),
                CellSpec(2, 2, "1010"),
                CellSpec(3, 0, w[4]),
                CellSpec(3, 1, "1430"),
                CellSpec(3, 2, "1500"),
            ),
        )
    )
    return specs


class _Canvas:
    def __init__(self, dpi: int) -> None:
        if not features.check("raqm"):
            raise RuntimeError("Pillow raqm layout is required to shape Indic scripts correctly")
        self.dpi = dpi
        self.width, self.height = round(PAGE_W_IN * dpi), round(PAGE_H_IN * dpi)
        self.image = Image.new("L", (self.width, self.height), 255)
        self.draw = ImageDraw.Draw(self.image)

    def font(self, language: str, points: float, bold: bool = False) -> ImageFont.FreeTypeFont:
        name = LANGUAGE_FONTS[language][0]
        if bold and language == "en":
            name = "NotoSans-Bold.ttf"
        return ImageFont.truetype(
            str(DEFAULT_FONT_DIR / name),
            round(self.dpi * points / 72),
            layout_engine=ImageFont.Layout.RAQM,
        )

    def text(self, x: float, y: float, text: str, font: ImageFont.FreeTypeFont) -> list[float]:
        self.draw.text((x, y), text, fill=0, font=font)
        x0, y0, x1, y1 = self.draw.textbbox((x, y), text, font=font)
        return [float(x0), float(y0), float(x1), float(y1)]

    def px(self, inches: float) -> float:
        return inches * self.dpi

    def array(self) -> Gray:
        return np.asarray(self.image, dtype=np.uint8)


def draw_table(canvas: _Canvas, spec: TableSpec, x: float, y: float) -> TruthTable:
    font = canvas.font(spec.language, 10)
    row_h = canvas.px(0.42)
    xs = [x]
    for width in spec.col_widths:
        xs.append(xs[-1] + canvas.px(width))
    ys = [y + row_h * r for r in range(spec.rows + 1)]
    pad = canvas.px(0.08)
    cells = []
    for cell in spec.cells:
        x0, x1 = xs[cell.col], xs[cell.col + cell.col_span]
        y0, y1 = ys[cell.row], ys[cell.row + cell.row_span]
        text_y = (y0 + y1) / 2 - font.size * 0.65
        canvas.text(x0 + pad, text_y, cell.text, font)
        if canvas.draw.textlength(cell.text, font=font) > x1 - x0 - 2 * pad:
            raise ValueError(f"cell text too wide in {spec.id}: {cell.text!r}")
        cells.append(
            TruthCell(cell.row, cell.col, cell.row_span, cell.col_span, cell.text, [x0, y0, x1, y1])
        )
        if spec.rules == "full":
            canvas.draw.rectangle([x0, y0, x1, y1], outline=0, width=max(2, canvas.dpi // 100))
    if spec.rules == "header":
        header_rows = max(c.row + c.row_span for c in spec.cells if c.header)
        line_y = ys[header_rows] - canvas.px(0.04)
        canvas.draw.line([(xs[0], line_y), (xs[-1], line_y)], fill=0, width=2)
    return TruthTable(
        [xs[0], ys[0], xs[-1], ys[-1]], spec.rows, len(spec.col_widths), spec.rules, cells
    )


def table_page(spec: TableSpec, text_dir: Path, dpi: int) -> tuple[Gray, LayoutSample]:
    canvas = _Canvas(dpi)
    margin = canvas.px(1.0)
    lines = load_lines(text_dir, spec.language)
    title_font = canvas.font(spec.language, 14, bold=True)
    body = canvas.font(spec.language, 10.5)
    blocks = [
        TruthBlock("title", lines[0], canvas.text(margin, canvas.px(1.0), lines[0], title_font))
    ]
    paragraph = lines[1:3]
    y = canvas.px(1.6)
    boxes = []
    for line in paragraph:
        boxes.append(canvas.text(margin, y, line, body))
        y += body.size * 1.6
    blocks.append(TruthBlock("paragraph", "\n".join(paragraph), _union(boxes)))
    table = draw_table(canvas, spec, margin, canvas.px(2.8))
    sample = LayoutSample(
        id="",
        kind="table",
        language=spec.language,
        condition="",
        dpi=dpi,
        image="",
        tables=[table],
        blocks=blocks,
    )
    return canvas.array(), sample


def columns_page(text_dir: Path, dpi: int) -> tuple[Gray, LayoutSample]:
    canvas = _Canvas(dpi)
    margin = canvas.px(1.0)
    small = canvas.font("en", 8)
    body = canvas.font("en", 10.5)
    title_font = canvas.font("en", 16, bold=True)
    header_left = canvas.text(margin, canvas.px(0.45), "Northwind Logistics - Annual Review", small)
    right_text = "Internal"
    header_right = canvas.text(
        canvas.width - margin - canvas.draw.textlength(right_text, font=small),
        canvas.px(0.45),
        right_text,
        small,
    )
    blocks = [
        TruthBlock(
            "title",
            "Operations Overview",
            canvas.text(margin, canvas.px(1.0), "Operations Overview", title_font),
        )
    ]
    paragraphs = [
        [
            "Freight volumes rose in every region",
            "as new routes opened in the second",
            "half of the year.",
        ],
        [
            "Warehouse utilisation stayed above",
            "eighty percent despite the added",
            "capacity in two cities.",
        ],
        [
            "Delivery times improved for most",
            "customers after the scheduling",
            "system was replaced.",
        ],
        [
            "Fuel costs remained the largest",
            "expense, followed by wages and",
            "vehicle maintenance.",
        ],
        ["Customer complaints fell by a", "third compared with the previous", "year."],
        ["The board approved a new depot", "in the eastern region, due to", "open next spring."],
    ]
    gutter = canvas.px(0.4)
    col_w = (canvas.width - 2 * margin - gutter) / 2
    for column in range(2):
        x = margin + column * (col_w + gutter)
        y = canvas.px(1.7)
        for paragraph in paragraphs[column * 3 : column * 3 + 3]:
            boxes = []
            for line in paragraph:
                if canvas.draw.textlength(line, font=body) > col_w:
                    raise ValueError(f"column line too wide: {line!r}")
                boxes.append(canvas.text(x, y, line, body))
                y += body.size * 1.5
            blocks.append(TruthBlock("paragraph", "\n".join(paragraph), _union(boxes)))
            y += body.size * 1.6
    blocks.append(TruthBlock("header", "Northwind Logistics - Annual Review", header_left))
    blocks.append(TruthBlock("header", right_text, header_right))
    number = "Page 7"
    blocks.append(
        TruthBlock(
            "page_number",
            number,
            canvas.text(
                (canvas.width - canvas.draw.textlength(number, font=small)) / 2,
                canvas.height - canvas.px(0.6),
                number,
                small,
            ),
        )
    )
    sample = LayoutSample(
        id="",
        kind="columns",
        language="en",
        condition="",
        dpi=dpi,
        image="",
        blocks=blocks,
        columns=2,
    )
    return canvas.array(), sample


def key_value_page(dpi: int) -> tuple[Gray, LayoutSample]:
    canvas = _Canvas(dpi)
    margin = canvas.px(1.0)
    body = canvas.font("en", 11)
    pairs = [
        ("Invoice No", "INV-2024-0042"),
        ("Date", "14 March 2024"),
        ("Customer", "Sharma Traders"),
        ("GSTIN", "29ABCDE1234F1Z5"),
        ("Amount due", "11000.00"),
    ]
    negatives = [
        "Delivery window 10:30 to 12:00",
        "Terms and conditions apply to all orders",
        "See https://example.org/terms for details",
    ]
    y = canvas.px(1.2)
    blocks = []
    for key, value in pairs:
        text = f"{key}: {value}"
        blocks.append(TruthBlock("paragraph", text, canvas.text(margin, y, text, body)))
        y += body.size * 1.8
    y += body.size * 2
    for text in negatives:
        blocks.append(TruthBlock("paragraph", text, canvas.text(margin, y, text, body)))
        y += body.size * 1.8
    sample = LayoutSample(
        id="",
        kind="key_value",
        language="en",
        condition="",
        dpi=dpi,
        image="",
        blocks=blocks,
        key_values=[[k, v] for k, v in pairs],
        not_key_values=negatives,
    )
    return canvas.array(), sample


def _shift(cells: tuple[CellSpec, ...], rows: int) -> tuple[CellSpec, ...]:
    return tuple(
        CellSpec(c.row + rows, c.col, c.text, c.row_span, c.col_span, c.header) for c in cells
    )


def _union(boxes: list[list[float]]) -> list[float]:
    return [
        min(b[0] for b in boxes),
        min(b[1] for b in boxes),
        max(b[2] for b in boxes),
        max(b[3] for b in boxes),
    ]


def build(text_dir: Path, out_dir: Path) -> list[LayoutSample]:
    out_dir.mkdir(parents=True, exist_ok=True)
    samples: list[LayoutSample] = []
    for condition, dpi in CONDITIONS.items():
        made = [(spec.id, table_page(spec, text_dir, dpi)) for spec in table_specs(text_dir)]
        made.append(("columns", columns_page(text_dir, dpi)))
        made.append(("key-value", key_value_page(dpi)))
        for name, (image, sample) in made:
            if condition == "scan":
                image = degrade_scan(image, seed=len(samples))
            sample.id = f"{name}-{condition}"
            sample.condition = condition
            sample.image = f"{sample.id}.png"
            buffer = io.BytesIO()
            Image.fromarray(image).save(buffer, "PNG", dpi=(dpi, dpi))
            (out_dir / sample.image).write_bytes(buffer.getvalue())
            samples.append(sample)
    (out_dir / "manifest.json").write_text(
        json.dumps([asdict(s) for s in samples], indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return samples


def load_manifest(corpus_dir: Path) -> list[dict[str, object]]:
    data: list[dict[str, object]] = json.loads(
        (corpus_dir / "manifest.json").read_text(encoding="utf-8")
    )
    return data
