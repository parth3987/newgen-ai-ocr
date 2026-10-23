"""Pydantic validation of extracted records against their document-type schema (ADR-0026).

A record model is generated per schema: required fields are required, every value has its
Python type (dates, decimals, constrained identifiers). Validation never fills a gap: a
missing required field is a ``missing_required`` issue, and the record keeps ``null``.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, create_model

from newgen_ocr.extraction.models import (
    FieldResult,
    FieldStatus,
    TableResult,
    ValidationIssue,
    ValidationReport,
    Value,
)
from newgen_ocr.extraction.schema import DocumentSchema, FieldType

_TYPES: dict[FieldType, Any] = {
    FieldType.DATE: dt.date,
    FieldType.AMOUNT: Decimal,
    FieldType.NUMBER: Decimal,
    FieldType.PERCENT: Annotated[Decimal, Field(ge=0, le=100)],
    FieldType.GSTIN: Annotated[str, Field(pattern=r"^\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z]$")],
    FieldType.PAN: Annotated[str, Field(pattern=r"^[A-Z]{5}\d{4}[A-Z]$")],
    FieldType.IFSC: Annotated[str, Field(pattern=r"^[A-Z]{4}0[A-Z0-9]{6}$")],
    FieldType.AADHAAR: Annotated[str, Field(pattern=r"^(XXXX XXXX \d{4}|[2-9]\d{11})$")],
    FieldType.PINCODE: Annotated[str, Field(pattern=r"^[1-9]\d{5}$")],
    FieldType.PHONE: Annotated[str, Field(pattern=r"^\+91[6-9]\d{9}$")],
    FieldType.GENDER: Annotated[str, Field(pattern=r"^(male|female|transgender)$")],
}
_STRICT = ConfigDict(extra="forbid")


def _type(kind: FieldType) -> Any:
    return _TYPES.get(kind, Annotated[str, Field(min_length=1)])


_MODELS: dict[tuple[str, str, str], type[BaseModel]] = {}


def record_model(schema: DocumentSchema) -> type[BaseModel]:
    key = (schema.id, schema.version, "")
    if key in _MODELS:
        return _MODELS[key]
    fields: dict[str, Any] = {
        f.name: (_type(f.type), ...) if f.required else (_type(f.type) | None, None)
        for f in schema.fields
    }
    _MODELS[key] = create_model(f"{schema.id}_record", __config__=_STRICT, **fields)
    return _MODELS[key]


def row_model(schema: DocumentSchema, table: str) -> type[BaseModel]:
    key = (schema.id, schema.version, table)
    if key in _MODELS:
        return _MODELS[key]
    spec = schema.table(table)
    fields: dict[str, Any] = {
        c.name: (_type(c.type), ...) if c.required else (_type(c.type) | None, None)
        for c in spec.columns
    }
    _MODELS[key] = create_model(f"{schema.id}_{table}_row", __config__=_STRICT, **fields)
    return _MODELS[key]


def _issues(error: ValidationError, prefix: list[str | int]) -> list[ValidationIssue]:
    out = []
    for item in error.errors():
        code = "missing_required" if item["type"] == "missing" else "invalid_value"
        out.append(
            ValidationIssue(code=code, location=[*prefix, *item["loc"]], message=str(item["msg"]))
        )
    return out


def validate(
    schema: DocumentSchema, fields: list[FieldResult], tables: list[TableResult]
) -> tuple[ValidationReport, dict[str, Value]]:
    """Validate the record and table rows. Returns the report and the record (nulls kept)."""
    issues: list[ValidationIssue] = []
    present = {
        f.name: f.value for f in fields if f.status is FieldStatus.EXTRACTED and f.value is not None
    }
    record: dict[str, Value] = {f.name: present.get(f.name) for f in fields}
    try:
        record_model(schema).model_validate(present)
    except ValidationError as exc:
        issues += _issues(exc, ["fields"])
    for f in fields:
        if f.status in (FieldStatus.AMBIGUOUS, FieldStatus.CONFLICT, FieldStatus.INVALID):
            issues.append(
                ValidationIssue(
                    code=f"field_{f.status.value}",
                    location=["fields", f.name],
                    message="; ".join(f.errors + f.notes) or f.status.value,
                )
            )
    for table in tables:
        if table.status != "extracted":
            continue
        model = row_model(schema, table.name)
        for rec in table.records:
            row = {
                name: cell.value
                for name, cell in rec.values.items()
                if cell.status is FieldStatus.EXTRACTED and cell.value is not None
            }
            try:
                model.model_validate(row)
            except ValidationError as exc:
                issues += _issues(exc, ["tables", table.name, rec.index])
            for name, cell in rec.values.items():
                if cell.status in (FieldStatus.AMBIGUOUS, FieldStatus.INVALID):
                    issues.append(
                        ValidationIssue(
                            code=f"cell_{cell.status.value}",
                            location=["tables", table.name, rec.index, name],
                            message="; ".join(cell.errors + cell.notes) or cell.status.value,
                        )
                    )
    return ValidationReport(valid=not issues, issues=issues), record
