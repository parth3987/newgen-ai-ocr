"""Configurable document-type schemas: classification rules, fields, tables, checks, templates.

Schemas are JSON files (built-ins in ``extraction/schemas/``, more from
``NGOCR_EXTRACTION_SCHEMA_DIR``) validated on load: field and column names, regular
expressions and every reference made by a check must resolve, or loading fails.
docs/EXTRACTION.md documents the format and the built-in schemas.
"""

from __future__ import annotations

import json
import re
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, model_validator

NAME = r"^[a-z][a-z0-9_]{0,47}$"
BUILTIN_DIR = Path(__file__).parent / "schemas"


class FieldType(StrEnum):
    TEXT = "text"
    NAME = "name"
    ADDRESS = "address"
    IDENTIFIER = "identifier"
    DATE = "date"
    AMOUNT = "amount"
    NUMBER = "number"
    PERCENT = "percent"
    GSTIN = "gstin"
    PAN = "pan"
    AADHAAR = "aadhaar"
    IFSC = "ifsc"
    PINCODE = "pincode"
    PHONE = "phone"
    GENDER = "gender"


class FieldSpec(BaseModel):
    name: str = Field(pattern=NAME)
    type: FieldType
    required: bool = False
    description: str = ""
    # Labels that introduce the value on the page, in any supported language. A label shared
    # by several fields is resolved by ``section``.
    labels: list[str] = []
    # Section (see DocumentSchema.sections) the value must be in, e.g. "seller" / "buyer".
    section: str | None = None
    # Also find values anywhere on the page by their format (identifiers with checksums).
    scan: bool = False
    # Regular expression for identifier values (first capture group or whole match).
    pattern: str | None = None
    # Read from the field's section block when unlabelled: "first" = the first line under the
    # section heading (a party name), "rest" = the following lines (an address).
    block: Literal["first", "rest"] | None = None
    # Lines after the label that may continue the value (addresses).
    max_lines: int = Field(default=1, ge=1, le=8)
    # Personal data: masked in results and exports where the type supports it (Aadhaar).
    sensitive: bool = False


class ColumnSpec(BaseModel):
    name: str = Field(pattern=NAME)
    type: FieldType
    headers: list[str] = Field(min_length=1)
    required: bool = False


class TableSpec(BaseModel):
    name: str = Field(pattern=NAME)
    description: str = ""
    # A document of this type is expected to have the table (missing -> review).
    required: bool = False
    columns: list[ColumnSpec] = Field(min_length=1)
    # Rows whose first non-empty cell starts with one of these are summaries, not records.
    summary_labels: list[str] = ["total", "sub total", "subtotal", "grand total"]


CheckKind = Literal[
    "row_product",
    "column_sum",
    "field_sum",
    "equal",
    "date_order",
    "not_future",
    "gstin_pan",
    "gst_tax_type",
    "balance",
    "running_balance",
    "rows_within_period",
]


class CheckSpec(BaseModel):
    id: str = Field(pattern=NAME)
    kind: CheckKind
    description: str = ""
    table: str | None = None
    target: str | None = None  # field (or column for row_product)
    terms: list[str] = []  # field_sum: fields added (all must be present)
    optional_terms: list[str] = []  # field_sum: fields counted as 0 when missing
    negative_terms: list[str] = []  # field_sum: fields subtracted (e.g. discount)
    factors: list[str] = []  # row_product: columns multiplied
    column: str | None = None  # column_sum
    fields: list[str] = []  # equal / date_order / not_future / gstin_pan / tax type / period
    credit: str | None = None  # balance checks: columns
    debit: str | None = None
    balance: str | None = None
    date: str | None = None  # rows_within_period: column
    tolerance: Decimal = Decimal("0.01")


class Keyword(BaseModel):
    text: str = Field(min_length=1)
    weight: float = 1.0


class PatternRule(BaseModel):
    regex: str
    weight: float = 1.0


class ClassificationSpec(BaseModel):
    keywords: list[Keyword] = []
    patterns: list[PatternRule] = []
    # Table header words that suggest this type (each matched header adds its weight).
    table_headers: list[Keyword] = []
    min_score: float = 3.0


class TemplateSpec(BaseModel):
    """A known layout (e.g. one vendor's invoice): anchors identify it; regions locate fields.

    Regions are fractions of the page width/height: [x0, y0, x1, y1]."""

    id: str = Field(pattern=NAME)
    description: str = ""
    anchors: list[str] = Field(min_length=1)
    page: int = Field(default=1, ge=1)
    regions: dict[str, tuple[float, float, float, float]] = {}


class DocumentSchema(BaseModel):
    id: str = Field(pattern=NAME)
    version: str
    title: str
    description: str = ""
    classification: ClassificationSpec
    sections: dict[str, list[str]] = {}
    fields: list[FieldSpec]
    tables: list[TableSpec] = []
    checks: list[CheckSpec] = []
    templates: list[TemplateSpec] = []

    def field(self, name: str) -> FieldSpec:
        return next(f for f in self.fields if f.name == name)

    def table(self, name: str) -> TableSpec:
        return next(t for t in self.tables if t.name == name)

    @model_validator(mode="after")
    def _references(self) -> DocumentSchema:
        fields = {f.name for f in self.fields}
        if len(fields) != len(self.fields):
            raise ValueError("duplicate field names")
        tables = {t.name: {c.name for c in t.columns} for t in self.tables}
        for f in self.fields:
            if f.section is not None and f.section not in self.sections:
                raise ValueError(f"field {f.name}: unknown section {f.section!r}")
            if f.block is not None and f.section is None:
                raise ValueError(f"field {f.name}: block needs a section")
            if f.pattern is not None:
                _regex(f.pattern, f"field {f.name}")
        for rule in self.classification.patterns:
            _regex(rule.regex, "classification pattern")
        for check in self.checks:
            _check_references(check, fields, tables)
        for template in self.templates:
            unknown = set(template.regions) - fields
            if unknown:
                raise ValueError(f"template {template.id}: unknown fields {sorted(unknown)}")
        return self


def _regex(pattern: str, where: str) -> None:
    try:
        re.compile(pattern)
    except re.error as exc:
        raise ValueError(f"{where}: invalid regular expression ({exc})") from exc


def _check_references(check: CheckSpec, fields: set[str], tables: dict[str, set[str]]) -> None:
    def need_field(name: str | None) -> None:
        if name is None or name not in fields:
            raise ValueError(f"check {check.id}: unknown field {name!r}")

    def need_column(name: str | None) -> None:
        if check.table not in tables:
            raise ValueError(f"check {check.id}: unknown table {check.table!r}")
        if name is None or name not in tables[check.table]:
            raise ValueError(f"check {check.id}: unknown column {name!r}")

    kind = check.kind
    if kind == "row_product":
        need_column(check.target)
        if not check.factors:
            raise ValueError(f"check {check.id}: row_product needs factors")
        for name in check.factors:
            need_column(name)
    elif kind == "column_sum":
        need_column(check.column)
        need_field(check.target)
    elif kind == "field_sum":
        need_field(check.target)
        for name in check.terms + check.optional_terms + check.negative_terms:
            need_field(name)
    elif kind in ("balance", "running_balance"):
        need_column(check.credit)
        need_column(check.debit)
        if kind == "running_balance":
            need_column(check.balance)
        for name in check.fields:
            need_field(name)
    elif kind == "rows_within_period":
        need_column(check.date)
        for name in check.fields:
            need_field(name)
    else:
        if not check.fields:
            raise ValueError(f"check {check.id}: {kind} needs fields")
        for name in check.fields:
            need_field(name)
    expected = {"equal": 2, "date_order": 2, "not_future": 1, "gstin_pan": 2, "gst_tax_type": 5}
    if kind in expected and len(check.fields) != expected[kind]:
        raise ValueError(f"check {check.id}: {kind} needs {expected[kind]} fields")
    if kind in ("balance", "rows_within_period") and len(check.fields) != 2:
        raise ValueError(f"check {check.id}: {kind} needs 2 fields")


class SchemaRegistry:
    """Loaded, validated schemas by id. Built-ins load first; custom ids must not clash."""

    def __init__(self, custom_dir: Path | None = None) -> None:
        self.schemas: dict[str, DocumentSchema] = {}
        for directory in (BUILTIN_DIR, custom_dir):
            if directory is None:
                continue
            for path in sorted(directory.glob("*.json")):
                schema = DocumentSchema.model_validate(json.loads(path.read_text("utf-8")))
                if schema.id in self.schemas:
                    raise ValueError(f"duplicate schema id {schema.id!r} ({path.name})")
                self.schemas[schema.id] = schema

    def get(self, schema_id: str) -> DocumentSchema | None:
        return self.schemas.get(schema_id)
