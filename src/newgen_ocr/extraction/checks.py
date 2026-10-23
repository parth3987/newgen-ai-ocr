"""Cross-field and arithmetic consistency checks (ADR-0026).

Checks use exact decimal arithmetic on extracted values. A check whose inputs are missing or
not valid is ``not_applicable``: no missing value is ever assumed to be zero, except the
``optional_terms`` a schema explicitly declares (e.g. round-off).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from newgen_ocr.extraction.models import (
    CheckResult,
    CheckStatus,
    FieldResult,
    FieldStatus,
    TableRecord,
    TableResult,
)
from newgen_ocr.extraction.normalize import format_amount
from newgen_ocr.extraction.schema import CheckSpec, DocumentSchema


class _Inputs:
    def __init__(self, fields: list[FieldResult], tables: list[TableResult]) -> None:
        self.fields = {f.name: f for f in fields}
        self.tables = {t.name: t for t in tables}

    def value(self, name: str) -> str | None:
        f = self.fields.get(name)
        if f is None or f.status is not FieldStatus.EXTRACTED or f.value is None:
            return None
        return str(f.value)

    def decimal(self, name: str) -> Decimal | None:
        value = self.value(name)
        return Decimal(value) if value is not None else None

    def day(self, name: str) -> date | None:
        value = self.value(name)
        return date.fromisoformat(value) if value is not None else None


def _cell(record: TableRecord, column: str) -> Decimal | None:
    cell = record.values.get(column)
    if cell is None or cell.status is not FieldStatus.EXTRACTED or cell.value is None:
        return None
    return Decimal(str(cell.value))


def _na(spec: CheckSpec, message: str, fields: list[str]) -> CheckResult:
    return CheckResult(
        id=spec.id,
        kind=spec.kind,
        status=CheckStatus.NOT_APPLICABLE,
        message=message,
        fields=fields,
    )


def _compare(
    spec: CheckSpec, expected: Decimal, actual: Decimal, fields: list[str], what: str
) -> CheckResult:
    difference = actual - expected
    ok = abs(difference) <= spec.tolerance
    return CheckResult(
        id=spec.id,
        kind=spec.kind,
        status=CheckStatus.PASSED if ok else CheckStatus.FAILED,
        message=f"{what}: expected {format_amount(expected)}, found {format_amount(actual)}"
        + ("" if ok else f" (difference {format_amount(difference)}, tolerance {spec.tolerance})"),
        fields=fields,
        expected=format_amount(expected),
        actual=format_amount(actual),
        difference=format_amount(difference),
    )


def run_check(spec: CheckSpec, data: _Inputs, today: date) -> CheckResult:
    kind = spec.kind
    if kind == "field_sum":
        names = [spec.target or "", *spec.terms, *spec.negative_terms]
        if any(data.decimal(n) is None for n in names):
            missing = [n for n in names if data.decimal(n) is None]
            return _na(spec, "missing: " + ", ".join(missing), names)
        total = sum(((data.decimal(n) or Decimal(0)) for n in spec.terms), Decimal(0))
        total += sum(((data.decimal(n) or Decimal(0)) for n in spec.optional_terms), Decimal(0))
        total -= sum(((data.decimal(n) or Decimal(0)) for n in spec.negative_terms), Decimal(0))
        used = names + [n for n in spec.optional_terms if data.decimal(n) is not None]
        result = _compare(
            spec, total, data.decimal(spec.target or "") or Decimal(0), used, spec.target or ""
        )
        zero = [n for n in spec.optional_terms if data.decimal(n) is None]
        if zero and result.status is CheckStatus.FAILED:
            result.message += "; counted as 0 because not extracted: " + ", ".join(zero)
        return result

    if kind == "equal":
        a, b = (data.value(n) for n in spec.fields)
        if a is None or b is None:
            return _na(spec, "both fields are needed", spec.fields)
        ok = a == b
        return CheckResult(
            id=spec.id, kind=kind, status=CheckStatus.PASSED if ok else CheckStatus.FAILED,
            message=f"{spec.fields[0]} {'=' if ok else '!='} {spec.fields[1]}",
            fields=spec.fields, expected=a, actual=b,
        )  # fmt: skip

    if kind == "date_order":
        first, second = (data.day(n) for n in spec.fields)
        if first is None or second is None:
            return _na(spec, "both dates are needed", spec.fields)
        ok = first <= second
        return CheckResult(
            id=spec.id, kind=kind, status=CheckStatus.PASSED if ok else CheckStatus.FAILED,
            message=f"{spec.fields[0]} {first} {'<=' if ok else '>'} {spec.fields[1]} {second}",
            fields=spec.fields,
        )  # fmt: skip

    if kind == "not_future":
        when = data.day(spec.fields[0])
        if when is None:
            return _na(spec, "date is needed", spec.fields)
        ok = when <= today
        return CheckResult(
            id=spec.id, kind=kind, status=CheckStatus.PASSED if ok else CheckStatus.FAILED,
            message=f"{spec.fields[0]} {when} {'is not' if ok else 'is'} after today ({today})",
            fields=spec.fields,
        )  # fmt: skip

    if kind == "gstin_pan":
        gstin, pan = (data.value(n) for n in spec.fields)
        if gstin is None or pan is None:
            return _na(spec, "GSTIN and PAN are needed", spec.fields)
        ok = gstin[2:12] == pan
        return CheckResult(
            id=spec.id, kind=kind, status=CheckStatus.PASSED if ok else CheckStatus.FAILED,
            message="PAN matches GSTIN characters 3-12" if ok else "PAN differs from GSTIN",
            fields=spec.fields, expected=gstin[2:12], actual=pan,
        )  # fmt: skip

    if kind == "gst_tax_type":
        seller, buyer, cgst, sgst, igst = spec.fields
        s, b = data.value(seller), data.value(buyer)
        if s is None or b is None:
            return _na(spec, "both GSTINs are needed to know the supply type", [seller, buyer])
        present = {n: (data.decimal(n) or Decimal(0)) != 0 for n in (cgst, sgst, igst)}
        intra = s[:2] == b[:2]
        ok = (
            (present[cgst] and present[sgst] and not present[igst])
            if intra
            else (present[igst] and not present[cgst] and not present[sgst])
        )
        expected = "CGST + SGST" if intra else "IGST"
        found = " + ".join(n for n, p in present.items() if p) or "no tax"
        return CheckResult(
            id=spec.id, kind=kind, status=CheckStatus.PASSED if ok else CheckStatus.FAILED,
            message=f"{'intra' if intra else 'inter'}-state supply (states {s[:2]}, {b[:2]}): "
            f"expected {expected}, found {found}",
            fields=spec.fields, expected=expected, actual=found,
        )  # fmt: skip

    table = data.tables.get(spec.table or "")
    if table is None or table.status != "extracted" or not table.records:
        return _na(spec, f"table {spec.table} has no records", [spec.table or ""])

    if kind == "row_product":
        failing, checked = [], 0
        for record in table.records:
            target = _cell(record, spec.target or "")
            factors = [_cell(record, f) for f in spec.factors]
            if target is None or any(f is None for f in factors):
                continue
            product = Decimal(1)
            for f in factors:
                product *= f or Decimal(0)
            checked += 1
            if abs(product - target) > spec.tolerance:
                failing.append(record.index)
        if not checked:
            return _na(spec, "no row has all the values", [spec.table or ""])
        return CheckResult(
            id=spec.id, kind=kind, status=CheckStatus.FAILED if failing else CheckStatus.PASSED,
            message=f"{checked - len(failing)} of {checked} rows: {spec.target} = "
            + " x ".join(spec.factors),
            fields=[f"{spec.table}.{spec.target}"], rows=failing,
        )  # fmt: skip

    if kind == "column_sum":
        values = [_cell(r, spec.column or "") for r in table.records]
        if any(v is None for v in values):
            return _na(spec, f"some {spec.column} values are missing", [spec.target or ""])
        target = data.decimal(spec.target or "")
        if target is None:
            return _na(spec, f"{spec.target} is missing", [spec.target or ""])
        total = sum(((v or Decimal(0)) for v in values), Decimal(0))
        return _compare(
            spec,
            total,
            target,
            [spec.target or "", f"{spec.table}.{spec.column}"],
            spec.target or "",
        )

    credits = [_cell(r, spec.credit or "") for r in table.records]
    debits = [_cell(r, spec.debit or "") for r in table.records]

    if kind == "balance":
        opening, closing = (data.decimal(n) for n in spec.fields)
        if opening is None or closing is None:
            return _na(spec, "opening and closing balances are needed", spec.fields)
        want = opening + sum((c or Decimal(0) for c in credits), Decimal(0))
        want -= sum((d or Decimal(0) for d in debits), Decimal(0))
        return _compare(spec, want, closing, spec.fields, "closing balance")

    if kind == "running_balance":
        previous = data.decimal(spec.fields[0])
        if previous is None:
            return _na(spec, "opening balance is needed", spec.fields)
        failing = []
        for record, credit, debit in zip(table.records, credits, debits, strict=True):
            balance = _cell(record, spec.balance or "")
            if balance is None:
                return _na(spec, f"row {record.index} has no balance", spec.fields)
            want = previous + (credit or Decimal(0)) - (debit or Decimal(0))
            if abs(want - balance) > spec.tolerance:
                failing.append(record.index)
            previous = balance
        return CheckResult(
            id=spec.id, kind=kind, status=CheckStatus.FAILED if failing else CheckStatus.PASSED,
            message=f"{len(table.records) - len(failing)} of {len(table.records)} rows reconcile",
            fields=[*spec.fields, f"{spec.table}.{spec.balance}"], rows=failing,
        )  # fmt: skip

    # rows_within_period
    start, end = (data.day(n) for n in spec.fields)
    if start is None or end is None:
        return _na(spec, "statement period is needed", spec.fields)
    failing = []
    for record in table.records:
        cell = record.values.get(spec.date or "")
        if cell is None or cell.status is not FieldStatus.EXTRACTED or cell.value is None:
            continue
        if not start <= date.fromisoformat(str(cell.value)) <= end:
            failing.append(record.index)
    return CheckResult(
        id=spec.id, kind=kind, status=CheckStatus.FAILED if failing else CheckStatus.PASSED,
        message=f"{len(failing)} transaction dates outside {start} to {end}",
        fields=spec.fields, rows=failing,
    )  # fmt: skip


def run_checks(
    schema: DocumentSchema, fields: list[FieldResult], tables: list[TableResult], today: date
) -> list[CheckResult]:
    data = _Inputs(fields, tables)
    return [run_check(spec, data, today) for spec in schema.checks]
