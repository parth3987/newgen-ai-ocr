"""Field and table extraction from explicit evidence (ADR-0026).

Candidates come from four sources, strongest first:
1. ``template``: lines inside a matched template's region for the field;
2. ``label`` / ``table_row``: text after one of the field's labels on a line (or on the
   following lines), or the value cell of a table row whose first cell is the label;
3. ``pattern``: values found by their format (GSTIN, PAN, IFSC, Aadhaar) in a section that
   belongs to exactly one field;
4. ``unattributed``: a shared label or a format match whose field cannot be decided (e.g. a
   GSTIN outside any seller/buyer section): never chosen, only reported.

A field is ``extracted`` only when every attributed candidate agrees. Disagreement is a
``conflict``; unreadable text is ``ambiguous`` or ``invalid``; nothing found is ``missing``.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field

from newgen_ocr.extraction.classify import fold, phrase
from newgen_ocr.extraction.document import DocumentView, Line, PageTable
from newgen_ocr.extraction.identifiers import AADHAAR_RE, GSTIN_RE, IFSC_RE, PAN_RE, mask_aadhaar
from newgen_ocr.extraction.models import (
    Candidate,
    CandidateMethod,
    CellValue,
    Evidence,
    EvidenceSource,
    FieldResult,
    FieldStatus,
    TableRecord,
    TableResult,
    Value,
)
from newgen_ocr.extraction.normalize import NORMALIZERS, Normalized, ascii_digits
from newgen_ocr.extraction.schema import DocumentSchema, FieldSpec, FieldType, TableSpec
from newgen_ocr.layout import geometry as g
from newgen_ocr.layout.models import TableCell, TableStatus

TIERS = {
    CandidateMethod.TEMPLATE: 0,
    CandidateMethod.LABEL: 1,
    CandidateMethod.TABLE_ROW: 1,
    CandidateMethod.SECTION: 1,
    CandidateMethod.PATTERN: 2,
    CandidateMethod.UNATTRIBUTED: 3,
}
SCAN_PATTERNS = {
    FieldType.GSTIN: GSTIN_RE,
    FieldType.PAN: PAN_RE,
    FieldType.IFSC: IFSC_RE,
    FieldType.AADHAAR: AADHAAR_RE,
}
_SEPARATORS = " \t:\uff1a-\u2013\u2014|=#"
_SEPARATOR_AFTER = re.compile(r"\s*[:\uff1a=|#\-\u2013]")


@dataclass(frozen=True)
class Options:
    day_first: bool = True
    mask_aadhaar: bool = True


@dataclass
class Found:
    fields: list[str]
    raw: str
    method: CandidateMethod
    evidence: list[Evidence]
    line: Line | None = None  # for section resolution


@dataclass
class Context:
    schema: DocumentSchema
    view: DocumentView
    options: Options
    labels: list[tuple[re.Pattern[str], int, list[str]]] = field(default_factory=list)
    headings: dict[int, str] = field(default_factory=dict)  # line position -> section

    def __post_init__(self) -> None:
        by_label: dict[str, list[str]] = {}
        for spec in self.schema.fields:
            for label in spec.labels:
                by_label.setdefault(fold(label).strip(), []).append(spec.name)
        # Longest labels first, so "grand total" wins over "total".
        self.labels = sorted(
            ((phrase(label), len(label), names) for label, names in by_label.items()),
            key=lambda item: -item[1],
        )
        sections = {
            name: [phrase(k) for k in keywords] for name, keywords in self.schema.sections.items()
        }
        for line in self.view.lines:
            head = _match_text(line.text).lstrip(" •*-\u2013\u2014>:")
            for name, patterns in sections.items():
                if any(p.match(head) for p in patterns):
                    self.headings[line.position] = name
                    break

    def section(self, line: Line) -> str | None:
        """The closest preceding section heading in the line's region, else on its page."""
        best: tuple[int, str] | None = None
        fallback: tuple[int, str] | None = None
        for position, name in self.headings.items():
            if position > line.position:
                continue
            heading = self.view.lines[position]
            if heading.page != line.page:
                continue
            same_region = heading.region_id is not None and heading.region_id == line.region_id
            if same_region and (best is None or position > best[0]):
                best = (position, name)
            if fallback is None or position > fallback[0]:
                fallback = (position, name)
        chosen = best or fallback
        return chosen[1] if chosen else None


def _match_text(text: str) -> str:
    """Same length as ``text``: positions found here index the original text."""
    out = ascii_digits(text).replace("\u2019", "'")
    lowered = out.lower()
    return lowered if len(lowered) == len(out) else out


# ------------------------------------------------------------ candidates from lines


def _label_spans(ctx: Context, text: str) -> list[tuple[int, int, list[str]]]:
    lowered = _match_text(text)
    taken: list[tuple[int, int, list[str]]] = []
    for pattern, _, names in ctx.labels:
        for match in pattern.finditer(lowered):
            start, end = match.span()
            if all(end <= s or start >= e for s, e, _ in taken):
                taken.append((start, end, names))
    taken.sort()
    # Labels must start the line (after bullets). A later label on the same line counts only
    # when a separator follows it ("Invoice No: 7  Date: 1/2/24"); otherwise it is part of
    # the value ("Bank Name: Coastal Bank").
    lead = len(lowered) - len(lowered.lstrip(" •*-\u2013\u2014>"))
    if not taken or taken[0][0] > lead:
        return []
    kept = [taken[0]]
    for start, end, names in taken[1:]:
        if _SEPARATOR_AFTER.match(lowered, end):
            kept.append((start, end, names))
    return kept


def _is_label_line(ctx: Context, line: Line) -> bool:
    return bool(_label_spans(ctx, line.text)) or line.position in ctx.headings


def _following(ctx: Context, line: Line, count: int) -> list[Line]:
    """Up to ``count`` next lines on the page (same region if known), stopping at labels."""
    out: list[Line] = []
    for other in ctx.view.lines[line.position + 1 :]:
        if len(out) == count or other.page != line.page:
            break
        if line.region_id is not None and other.region_id != line.region_id:
            break
        if _is_label_line(ctx, other):
            break
        out.append(other)
    return out


def from_lines(ctx: Context, table_regions: set[tuple[int, int]]) -> list[Found]:
    found: list[Found] = []
    specs = {f.name: f for f in ctx.schema.fields}
    for line in ctx.view.lines:
        if (line.page, line.region_id) in table_regions:
            continue  # table text is read cell by cell (from_tables)
        text = ascii_digits(line.text)
        spans = _label_spans(ctx, line.text)
        for i, (_, end, names) in enumerate(spans):
            stop = spans[i + 1][0] if i + 1 < len(spans) else len(text)
            value = text[end:stop].strip(_SEPARATORS)
            lines = [line]
            max_lines = max(specs[n].max_lines for n in names)
            if not value or max_lines > 1:
                extra = _following(ctx, line, max_lines if not value else max_lines - 1)
                if not value:
                    extra = extra[:max_lines]
                parts = [value, *(ln.text.strip() for ln in extra)]
                value = ", ".join(p.rstrip(", ") for p in parts if p.rstrip(", "))
                lines += extra
            if value:
                found.append(
                    Found(
                        names, value, CandidateMethod.LABEL, [ln.evidence() for ln in lines], line
                    )
                )
    return found


def from_sections(ctx: Context) -> list[Found]:
    """Unlabelled lines under a section heading ("Seller:" / "Bill To:"): the first is the
    ``block: first`` field (a name), the following ones the ``block: rest`` field (address)."""
    found: list[Found] = []
    for position, section in sorted(ctx.headings.items()):
        heading = ctx.view.lines[position]
        block: list[Line] = []
        for other in ctx.view.lines[position + 1 :]:
            if other.page != heading.page or other.position in ctx.headings:
                break
            if heading.region_id is not None and other.region_id != heading.region_id:
                break
            if not _label_spans(ctx, other.text):
                block.append(other)
        for spec in ctx.schema.fields:
            if spec.section != section or spec.block is None:
                continue
            lines = block[:1] if spec.block == "first" else block[1 : 1 + spec.max_lines]
            if lines:
                raw = ", ".join(ln.text.strip().rstrip(",") for ln in lines)
                found.append(
                    Found(
                        [spec.name],
                        raw,
                        CandidateMethod.SECTION,
                        [ln.evidence() for ln in lines],
                        heading,
                    )
                )
    return found


def from_scan(ctx: Context) -> list[Found]:
    found: list[Found] = []
    by_type: dict[FieldType, list[str]] = {}
    for spec in ctx.schema.fields:
        if spec.scan and spec.type in SCAN_PATTERNS:
            by_type.setdefault(spec.type, []).append(spec.name)
    for line in ctx.view.lines:
        text = ascii_digits(line.text)
        upper = text.upper()
        for kind, names in by_type.items():
            pattern = SCAN_PATTERNS[kind]
            matches = list(pattern.finditer(upper))
            if not matches and kind is not FieldType.AADHAAR:
                matches = list(pattern.finditer(re.sub(r"\s+", "", upper)))
            for match in matches:
                found.append(
                    Found(names, match.group(1), CandidateMethod.PATTERN, [line.evidence()], line)
                )
    return found


def from_template(ctx: Context, template_id: str | None) -> list[Found]:
    template = next((t for t in ctx.schema.templates if t.id == template_id), None)
    if template is None:
        return []
    size = ctx.view.page_sizes.get(template.page)
    if size is None:
        return []
    width, height, _ = size
    found = []
    for name, (x0, y0, x1, y1) in template.regions.items():
        box = g.box(x0 * width, y0 * height, x1 * width, y1 * height)
        lines = [
            ln
            for ln in ctx.view.lines
            if ln.page == template.page and g.contains_point(box, *g.centre(ln.bbox))
        ]
        if lines:
            evidence = []
            for ln in lines:
                ev = ln.evidence()
                ev.source = EvidenceSource.TEMPLATE_REGION
                evidence.append(ev)
            raw = ", ".join(ln.text for ln in lines)
            found.append(Found([name], raw, CandidateMethod.TEMPLATE, evidence, lines[0]))
    return found


# ------------------------------------------------------------ tables


def _cell_evidence(page: PageTable, cell: TableCell) -> Evidence:
    confidences = [page.lines_confidence.get(i) for i in cell.line_indices]
    known = [c for c in confidences if c is not None]
    return Evidence(
        page_number=page.page,
        source=EvidenceSource.TABLE_CELL,
        text=cell.text,
        bbox=cell.bbox or page.table.bbox,
        line_indices=list(cell.line_indices),
        table_id=page.table.id,
        row=cell.row,
        col=cell.col,
        confidence=min(known) if known else None,
    )


def _row_cells(table_cells: list[TableCell], grid: list[list[int]], row: int) -> list[TableCell]:
    seen: list[int] = []
    for index in grid[row]:
        if index not in seen:
            seen.append(index)
    return [table_cells[i] for i in seen]


def from_table_rows(ctx: Context) -> list[Found]:
    """(label, value) table rows: the first non-empty cell starts with a field label (the
    longest matching label wins, e.g. "CGST 6%" -> cgst) and the last cell holds the value."""
    found = []
    for page in ctx.view.tables:
        table = page.table
        if table.status is TableStatus.UNSUPPORTED:
            continue
        for row in range(table.rows):
            cells = [c for c in _row_cells(table.cells, table.grid, row) if c.text.strip()]
            if len(cells) < 2:
                continue
            key = _match_text(cells[0].text).strip(_SEPARATORS)
            names = next((n for pattern, _, n in ctx.labels if pattern.match(key)), None)
            if names:
                evidence = [_cell_evidence(page, cells[0]), _cell_evidence(page, cells[-1])]
                found.append(Found(names, cells[-1].text, CandidateMethod.TABLE_ROW, evidence))
    return found


def _header_rows(cells: list[TableCell]) -> set[int]:
    rows = {c.row + k for c in cells if c.header for k in range(c.row_span)}
    return rows or {0}


def _map_columns(spec: TableSpec, headers: dict[int, str]) -> dict[str, int]:
    scored = []
    for column in spec.columns:
        for synonym in column.headers:
            target = fold(synonym).strip()
            pattern = phrase(synonym)
            for col, text in headers.items():
                if text == target:
                    scored.append((1000 + len(target), column.name, col))
                elif pattern.search(text):
                    scored.append((len(target), column.name, col))
    mapping: dict[str, int] = {}
    used: set[int] = set()
    for _, name, col in sorted(scored, key=lambda s: -s[0]):
        if name not in mapping and col not in used:
            mapping[name] = col
            used.add(col)
    return mapping


def _normalize(
    spec_type: FieldType, raw: str, options: Options, pattern: str | None = None
) -> Normalized:
    if spec_type is FieldType.DATE:
        return NORMALIZERS["date"](raw, day_first=options.day_first)  # type: ignore[call-arg]
    if spec_type is FieldType.AADHAAR:
        return NORMALIZERS["aadhaar"](raw, mask=options.mask_aadhaar)  # type: ignore[call-arg]
    if spec_type is FieldType.IDENTIFIER:
        return NORMALIZERS["identifier"](raw, pattern)  # type: ignore[call-arg]
    return NORMALIZERS[spec_type.value](raw)


def _cell_value(norm: Normalized, raw: str, evidence: Evidence | None) -> CellValue:
    if not raw.strip():
        return CellValue(status=FieldStatus.MISSING, evidence=evidence)
    if norm.ambiguous:
        status = FieldStatus.AMBIGUOUS
    elif norm.errors or norm.value is None:
        status = FieldStatus.INVALID
    else:
        status = FieldStatus.EXTRACTED
    return CellValue(
        status=status,
        value=norm.value if status is FieldStatus.EXTRACTED else None,
        raw=raw,
        evidence=evidence,
        notes=norm.notes + [f"reading: {a}" for a in norm.ambiguous],
        errors=norm.errors,
    )


def from_tables(ctx: Context, spec: TableSpec) -> TableResult:
    result = TableResult(
        name=spec.name,
        status="not_found",
        columns=[c.name for c in spec.columns],
        column_types={c.name: c.type.value for c in spec.columns},
    )
    required = {c.name for c in spec.columns if c.required}
    summary = [phrase(s) for s in spec.summary_labels]
    for page in ctx.view.tables:
        table = page.table
        if table.status is TableStatus.UNSUPPORTED or not table.cells:
            continue
        header_rows = _header_rows(table.cells)
        headers: dict[int, str] = {}
        for col in range(table.cols):
            parts: list[str] = []
            for row in sorted(header_rows):
                text = fold(table.cells[table.grid[row][col]].text).strip()
                if text and text not in parts:
                    parts.append(text)
            headers[col] = " ".join(parts)
        mapping = _map_columns(spec, headers)
        if not required <= set(mapping):
            continue
        result.status = "extracted"
        result.sources.append(
            {
                "page_number": page.page,
                "table_id": table.id,
                "layout_status": table.status.value,
                "mapped": sorted(f"{k}<-{headers[v]}" for k, v in mapping.items()),
            }
        )
        if table.status is TableStatus.NEEDS_REVIEW:
            result.issues.append(f"page {page.page} table {table.id}: layout routed it to review")
        header_texts = [headers[c] for c in range(table.cols)]
        for row in range(table.rows):
            if row in header_rows:
                continue
            cells = {name: table.cells[table.grid[row][col]] for name, col in mapping.items()}
            texts = [fold(c.text).strip() for c in _row_cells(table.cells, table.grid, row)]
            if not any(texts) or [t for t in texts] == header_texts:
                continue  # empty row, or a header repeated on a continuation page
            first = next(t for t in texts if t)
            values = {}
            for column in spec.columns:
                cell = cells.get(column.name)
                if cell is None:
                    values[column.name] = CellValue(status=FieldStatus.MISSING)
                    continue
                norm = _normalize(column.type, cell.text, ctx.options)
                value = _cell_value(norm, cell.text, _cell_evidence(page, cell))
                if cell.row_span > 1 and cell.row != row:
                    value.notes.append(
                        f"from a cell merged across rows {cell.row}-{cell.row + cell.row_span - 1}"
                    )
                values[column.name] = value
            record = TableRecord(
                index=0, page_number=page.page, table_id=table.id, row=row, values=values
            )
            if any(p.match(first) for p in summary):
                record.index = len(result.summary_rows)
                result.summary_rows.append(record)
            else:
                record.index = len(result.records)
                result.records.append(record)
    if result.status == "not_found":
        result.issues.append("no table with the required columns: " + ", ".join(sorted(required)))
    return result


# ------------------------------------------------------------ resolution


def _candidate(
    ctx: Context, spec: FieldSpec, found: Found, method: CandidateMethod
) -> tuple[Candidate, list[str]]:
    norm = _normalize(spec.type, found.raw, ctx.options, spec.pattern)
    if norm.absent:
        return Candidate(
            value=None, raw=found.raw, method=method, evidence=[], errors=["absent"]
        ), []
    candidate = Candidate(
        value=norm.value if not norm.ambiguous else None,
        raw=found.raw,
        method=method,
        evidence=found.evidence,
        alternatives=list(norm.ambiguous),
        errors=list(norm.errors),
    )
    return candidate, norm.notes


def candidates_by_field(
    ctx: Context, founds: Iterable[Found]
) -> dict[str, list[tuple[Candidate, list[str]]]]:
    specs = {f.name: f for f in ctx.schema.fields}
    out: dict[str, list[tuple[Candidate, list[str]]]] = {f.name: [] for f in ctx.schema.fields}
    for found in founds:
        targets, method = found.fields, found.method
        if len(targets) > 1:
            section = ctx.section(found.line) if found.line is not None else None
            chosen = [n for n in targets if specs[n].section == section and section is not None]
            if len(chosen) == 1:
                targets = chosen
            else:
                method = CandidateMethod.UNATTRIBUTED
        elif method is CandidateMethod.PATTERN and found.line is not None:
            spec = specs[targets[0]]
            if spec.section is not None and ctx.section(found.line) != spec.section:
                method = CandidateMethod.UNATTRIBUTED
        for name in targets:
            candidate, notes = _candidate(ctx, specs[name], found, method)
            if candidate.errors == ["absent"]:
                continue  # text after a label with no value of the field's type
            out[name].append((candidate, notes))
    return out


def resolve(spec: FieldSpec, items: list[tuple[Candidate, list[str]]]) -> FieldResult:
    result = FieldResult(
        name=spec.name,
        type=spec.type.value,
        required=spec.required,
        status=FieldStatus.MISSING,
        sensitive=spec.sensitive,
        candidates=[c for c, _ in items],
    )
    attributed = [(c, n) for c, n in items if c.method is not CandidateMethod.UNATTRIBUTED]
    if not items:
        return result
    if not attributed:
        result.status = FieldStatus.AMBIGUOUS
        result.notes.append("value found but the field it belongs to cannot be decided")
        return result
    best_tier = min(TIERS[c.method] for c, _ in attributed)
    tier = [(c, n) for c, n in attributed if TIERS[c.method] == best_tier]
    lower = [(c, n) for c, n in attributed if TIERS[c.method] > best_tier]
    values: dict[Value, list[tuple[Candidate, list[str]]]] = {}
    for c, n in tier:
        if c.value is not None and not c.errors and not c.alternatives:
            values.setdefault(c.value, []).append((c, n))
    ambiguous = [c for c, _ in tier if c.alternatives]
    invalid = [c for c, _ in tier if (c.value is None and not c.alternatives) or c.errors]
    if len(values) > 1:
        result.status = FieldStatus.CONFLICT
        result.notes.append("different values: " + ", ".join(str(v) for v in values))
        return result
    if len(values) == 1:
        value, agreeing = next(iter(values.items()))
        contradicting = [c for c in ambiguous if value not in c.alternatives] + invalid
        contradicting += [
            c for c, _ in lower if c.value is not None and c.value != value and not c.errors
        ]
        if contradicting:
            result.status = FieldStatus.CONFLICT
            result.notes.append(f"{len(contradicting)} other candidate(s) disagree with {value}")
            return result
        best = agreeing[0][0]
        support = [c for c, _ in items if c.value == value]
        result.status = FieldStatus.EXTRACTED
        result.value = value
        result.raw = best.raw
        result.method = best.method
        result.evidence = [e for c in support for e in c.evidence]
        result.notes = sorted({note for _, notes in agreeing for note in notes})
        known = [e.confidence for e in result.evidence if e.confidence is not None]
        result.confidence = round(min(known), 4) if known else None
        return result
    if ambiguous:
        result.status = FieldStatus.AMBIGUOUS
        result.notes.append(
            "possible values: "
            + ", ".join(sorted({str(a) for c in ambiguous for a in c.alternatives}))
        )
        return result
    result.status = FieldStatus.INVALID
    result.raw = invalid[0].raw
    result.errors = sorted({e for c in invalid for e in c.errors})
    result.evidence = list(invalid[0].evidence)
    return result


def mask_text(text: str) -> str:
    return AADHAAR_RE.sub(lambda m: mask_aadhaar(re.sub(r"\D", "", m.group(1))), ascii_digits(text))


def extract_fields(
    schema: DocumentSchema, view: DocumentView, options: Options, template: str | None
) -> tuple[list[FieldResult], list[TableResult]]:
    ctx = Context(schema, view, options)
    table_regions = {
        (page.page, region_id) for page in view.tables for region_id in [page.table.region_id]
    }
    founds = from_template(ctx, template) + from_lines(ctx, table_regions)
    founds += from_sections(ctx) + from_table_rows(ctx) + from_scan(ctx)
    grouped = candidates_by_field(ctx, founds)
    fields = [resolve(spec, grouped[spec.name]) for spec in schema.fields]
    tables = [from_tables(ctx, spec) for spec in schema.tables]
    return fields, tables
