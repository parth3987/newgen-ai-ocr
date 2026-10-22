"""Key-value pairs from explicit evidence only (ADR-0025).

There is no key-value model in the approved stack, so pairs are emitted only when the page
states the relation itself:

- ``SEPARATOR``: one OCR line of the form "Key: value" (ASCII or full-width colon). The key
  must be short and contain a letter; times ("10:30"), ratios and URLs are not pairs.
- ``TABLE_ROW``: a row of a two-column table whose cells are single (no spans), with a
  non-empty left cell.

Anything else stays plain text. Keys and values are copied verbatim from the OCR text;
per-word coordinates are never estimated, so a separator pair carries its line's box.
"""

from __future__ import annotations

import re

from newgen_ocr.layout.geometry import union
from newgen_ocr.layout.models import KeyValuePair, KeyValueSource, Table, TableStatus
from newgen_ocr.ocr.models import OcrLine

MAX_KEY_WORDS = 6
MAX_KEY_CHARS = 40
_SEPARATOR = re.compile("[:\uff1a]")  # ASCII or full-width colon


def split_pair(text: str) -> tuple[str, str] | None:
    """(key, value) for a "Key: value" line, else None."""
    match = _SEPARATOR.search(text)
    if match is None:
        return None
    start = match.start()
    before, after = text[:start], text[start + 1 :]
    if before[-1:].isdigit() and after[:1].isdigit():
        return None  # a time or ratio, e.g. 10:30
    if after.startswith("//"):
        return None  # a URL scheme
    key, value = before.strip(), after.strip()
    if not key or not value:
        return None
    if len(key) > MAX_KEY_CHARS or len(key.split()) > MAX_KEY_WORDS:
        return None
    if not any(ch.isalpha() for ch in key):
        return None
    return key, value


def from_lines(lines: list[OcrLine], line_indices: list[int], region_id: int) -> list[KeyValuePair]:
    pairs = []
    for index in line_indices:
        line = lines[index]
        parsed = split_pair(line.text)
        if parsed is None:
            continue
        pairs.append(
            KeyValuePair(
                key=parsed[0],
                value=parsed[1],
                source=KeyValueSource.SEPARATOR,
                region_id=region_id,
                line_index=index,
                bbox=line.bbox,
            )
        )
    return pairs


def from_table(table: Table) -> list[KeyValuePair]:
    if table.status is TableStatus.UNSUPPORTED or table.cols != 2:
        return []
    if any(c.row_span != 1 or c.col_span != 1 for c in table.cells):
        return []
    pairs = []
    for row in range(table.rows):
        left, right = (table.cells[table.grid[row][c]] for c in (0, 1))
        if not left.text.strip() or left.header or left.bbox is None or right.bbox is None:
            continue
        pairs.append(
            KeyValuePair(
                key=left.text.strip(),
                value=right.text.strip(),
                source=KeyValueSource.TABLE_ROW,
                region_id=table.region_id,
                bbox=union([left.bbox, right.bbox]),
                key_bbox=left.bbox,
                value_bbox=right.bbox,
                table_id=table.id,
                row=row,
            )
        )
    return pairs
