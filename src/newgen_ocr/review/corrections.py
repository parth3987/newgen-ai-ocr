"""Applying reviewer corrections to an extraction result (ADR-0028).

A correction is typed like the page ("1,180.00", "14/03/2024") and normalised with the
field's own rules, so a corrected value is held to the same format and checksum rules as an
extracted one. The extraction result itself is never modified: corrections are layered on
top, then the record is validated and the checks are run again.
"""

from __future__ import annotations

from datetime import date

from newgen_ocr.errors import AppError, ErrorCode
from newgen_ocr.extraction.checks import run_checks
from newgen_ocr.extraction.extract import Options, normalize_field
from newgen_ocr.extraction.models import (
    CellValue,
    ExtractionResult,
    FieldResult,
    FieldStatus,
    TableResult,
    Value,
)
from newgen_ocr.extraction.schema import DocumentSchema, FieldType
from newgen_ocr.extraction.validation import validate
from newgen_ocr.review.models import (
    AppliedCorrection,
    Change,
    ReviewedResult,
    ReviewedValue,
    ReviewItem,
)

_UNDECIDED = (FieldStatus.AMBIGUOUS, FieldStatus.CONFLICT, FieldStatus.INVALID)


def _invalid(message: str) -> AppError:
    return AppError(ErrorCode.INVALID_CORRECTION, message)


def target(
    change: Change, result: ExtractionResult, schema: DocumentSchema
) -> tuple[FieldType, str | None, Value, FieldStatus | None, str | None, float | None, str | None]:
    """Type, pattern, extracted value, status, raw text, confidence and method at a location."""
    if change.field is not None:
        found = next((f for f in result.fields if f.name == change.field), None)
        if found is None:
            raise _invalid(f"no field {change.field!r} in this document type")
        spec = schema.field(change.field)
        return (
            spec.type,
            spec.pattern,
            found.value,
            found.status,
            found.raw,
            found.confidence,
            found.method.value if found.method else None,
        )
    table = next((t for t in result.tables if t.name == change.table), None)
    if table is None:
        raise _invalid(f"no table {change.table!r} in this document type")
    if change.record is None:
        if table.status != "extracted":
            raise _invalid("the table was not found; it cannot be confirmed")
        return FieldType.TEXT, None, None, None, None, None, None
    record = next((r for r in table.records if r.index == change.record), None)
    columns = {c.name: c for c in schema.table(table.name).columns}
    if record is None or change.column not in columns:
        raise _invalid("no such table cell")
    cell = record.values.get(change.column or "") or CellValue(status=FieldStatus.MISSING)
    confidence = cell.evidence.confidence if cell.evidence else None
    return (
        columns[change.column or ""].type,
        None,
        cell.value,
        cell.status,
        cell.raw,
        confidence,
        None,
    )


def new_value(
    change: Change,
    field_type: FieldType,
    pattern: str | None,
    current: Value,
    status: FieldStatus | None,
    options: Options,
) -> Value:
    """The value a change sets, validated with the field's normaliser."""
    if change.action == "clear":
        return None
    if change.action == "confirm":
        if status in _UNDECIDED:
            raise _invalid("an undecided value cannot be confirmed; set or clear it")
        return current
    norm = normalize_field(field_type, change.value or "", options, pattern)
    if norm.ambiguous:
        raise _invalid("the value has several readings: " + ", ".join(norm.ambiguous[:5]))
    if not norm.ok:
        raise _invalid(
            f"not a valid {field_type.value}: " + ", ".join(norm.errors or ["unreadable"])
        )
    return norm.value


def _field_with(f: FieldResult, c: AppliedCorrection) -> FieldResult:
    out = f.model_copy()
    out.value = c.value
    out.status = FieldStatus.MISSING if c.value is None else FieldStatus.EXTRACTED
    return out


def effective(
    result: ExtractionResult, item: ReviewItem
) -> tuple[list[FieldResult], list[TableResult]]:
    fields = [
        _field_with(f, item.corrections[loc])
        if (loc := f"fields.{f.name}") in item.corrections
        else f
        for f in result.fields
    ]
    tables = []
    for t in result.tables:
        cells = {
            loc: c for loc, c in item.corrections.items() if loc.startswith(f"tables.{t.name}.")
        }
        if not cells:
            tables.append(t)
            continue
        copy = t.model_copy(deep=True)
        for record in copy.records:
            for column in list(record.values):
                c = cells.get(f"tables.{t.name}.{record.index}.{column}")
                if c is not None:
                    record.values[column] = CellValue(
                        status=FieldStatus.MISSING if c.value is None else FieldStatus.EXTRACTED,
                        value=c.value,
                        raw=record.values[column].raw,
                        evidence=record.values[column].evidence,
                        notes=[f"corrected by {c.actor}"],
                    )
        tables.append(copy)
    return fields, tables


def unresolved(item: ReviewItem) -> list[str]:
    """Locations a person must still decide on before the document can be approved."""
    out = []
    for d in item.assessment.fields:
        if not d.verified and f"fields.{d.field}" not in item.corrections:
            out.append(f"fields.{d.field}")
    for t in item.assessment.tables:
        if not t.verified and f"tables.{t.table}" not in item.corrections:
            out.append(f"tables.{t.table}")
    if item.schema_id is None:
        out.append("document_type")
    return out


def reviewed(
    result: ExtractionResult, item: ReviewItem, schema: DocumentSchema | None, today: date
) -> ReviewedResult:
    decisions = {d.field: d for d in item.assessment.fields}
    if schema is None:
        fields, tables = result.fields, result.tables
        report, record, checks = result.validation, result.record, result.checks
    else:
        fields, tables = effective(result, item)
        report, record = validate(schema, fields, tables)
        checks = run_checks(schema, fields, tables, today)
    values: dict[str, ReviewedValue] = {}
    for f in fields:
        c = item.corrections.get(f"fields.{f.name}")
        d = decisions.get(f.name)
        if c is not None:
            source = {"set": "corrected", "clear": "cleared", "confirm": "confirmed"}[c.action]
        elif d is not None and d.verified:
            source = "verified"
        elif f.status in _UNDECIDED:
            source = "unresolved"
        else:
            source = "extracted"
        values[f.name] = ReviewedValue(
            value=f.value if f.status is FieldStatus.EXTRACTED else None,
            status=f.status,
            source=source,
            corrected_by=c.actor if c is not None else None,
        )
    return ReviewedResult(
        document_id=item.document_id,
        layout_run=item.layout_run,
        schema_id=item.schema_id,
        status=item.status,
        revision=item.revision,
        record=record,
        fields=values,
        tables={
            t.name: [
                {
                    name: cell.value if cell.status is FieldStatus.EXTRACTED else None
                    for name, cell in r.values.items()
                }
                for r in t.records
            ]
            for t in tables
        },
        validation=report,
        checks=checks,
        unresolved=unresolved(item),
        decision=item.decision,
    )
