"""Field-specific verification gates (ADR-0028).

A value is ``verified`` only when every gate passes:

1. it was extracted (not missing, ambiguous, in conflict or invalid);
2. the document type is reliable (classified, template or requested);
3. its evidence pages were not routed to review by OCR or layout;
4. no business-rule check involving it failed;
5. the second OCR engine does not disagree with it;
6. its OCR confidence reaches the threshold **calibrated for that field** on held-out
   ground truth. No calibration, no threshold: OCR confidence alone never verifies a value.

An optional field reported missing is verified only when "missing" judgements for it are
calibrated as reliable and no page of the document was routed to review. Tables are
verified when they have no flagged cells, every applicable check over them passes and at
least one does. A document is eligible for
automatic acceptance when every field and table is verified and nothing else is wrong.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from newgen_ocr.extraction.extract import Options
from newgen_ocr.extraction.models import (
    CheckStatus,
    ClassificationStatus,
    ExtractionResult,
    ExtractionStatus,
    FieldResult,
    FieldStatus,
    Value,
)
from newgen_ocr.extraction.schema import CheckSpec, DocumentSchema, FieldType
from newgen_ocr.layout.models import LayoutReviewReason, LayoutRun
from newgen_ocr.ocr.models import OcrPage, OcrRun, ReviewReason
from newgen_ocr.review.calibration import Observation, lookup, same_value
from newgen_ocr.review.engines import check_value
from newgen_ocr.review.models import (
    Assessment,
    Calibration,
    CalibrationKind,
    EngineAgreement,
    EngineCheck,
    FieldDecision,
    FieldReviewReason,
    TableDecision,
)

# Page reasons judged per value instead of per page: engine disagreement (the field-level
# cross-check replaces the page-level text comparison) and low-confidence lines (each
# value's own confidence is compared with its calibrated threshold).
PER_VALUE_OCR_REASONS = {ReviewReason.ENGINE_DISAGREEMENT, ReviewReason.LOW_CONFIDENCE_LINES}
RELIABLE_TYPES = {
    ClassificationStatus.CLASSIFIED,
    ClassificationStatus.TEMPLATE,
    ClassificationStatus.REQUESTED,
}
_STATUS_REASON = {
    FieldStatus.AMBIGUOUS: FieldReviewReason.AMBIGUOUS,
    FieldStatus.CONFLICT: FieldReviewReason.CONFLICT,
    FieldStatus.INVALID: FieldReviewReason.INVALID,
}
# Reasons that do not depend on the confidence threshold: an observation without any other
# reason is "gated" for calibration.
THRESHOLD_REASONS = {
    FieldReviewReason.NOT_CALIBRATED,
    FieldReviewReason.BELOW_THRESHOLD,
    FieldReviewReason.MISSING_UNVERIFIED,
}


def check_fields(spec: CheckSpec) -> set[str]:
    names = {*spec.terms, *spec.optional_terms, *spec.negative_terms, *spec.fields}
    if spec.target and spec.table is None:
        names.add(spec.target)
    if spec.kind == "column_sum" and spec.target:
        names.add(spec.target)
    return names


@dataclass
class Sources:
    """Pages the OCR or layout stage routed to review, with why. Table-structure reasons
    concern the table only (its decision covers them), so they do not flag the page's
    other values."""

    flagged: dict[int, list[str]] = field(default_factory=dict)
    tables: dict[int, list[str]] = field(default_factory=dict)

    @classmethod
    def of(cls, ocr: OcrRun | None, layout: LayoutRun | None) -> Sources:
        out = cls()
        for page in ocr.pages if ocr else []:
            reasons = [r.value for r in page.review.reasons if r not in PER_VALUE_OCR_REASONS]
            if reasons:
                out.flagged.setdefault(page.page_number, []).extend(reasons)
                out.tables.setdefault(page.page_number, []).extend(reasons)
        for lpage in layout.pages if layout else []:
            for r in lpage.review.reasons:
                if r is LayoutReviewReason.UNCLASSIFIED_TEXT:
                    continue
                if r.value.startswith("table_"):
                    out.tables.setdefault(lpage.page_number, []).append(r.value)
                else:
                    out.flagged.setdefault(lpage.page_number, []).append(r.value)
                    out.tables.setdefault(lpage.page_number, []).append(r.value)
        return out


@dataclass
class Policy:
    calibration: Calibration | None
    auto_accept: bool = False
    options: Options = field(default_factory=Options)

    def masks(self, schema: DocumentSchema) -> bool:
        return self.options.mask_aadhaar and any(f.type is FieldType.AADHAAR for f in schema.fields)


def _decide_field(
    f: FieldResult,
    schema: DocumentSchema,
    result: ExtractionResult,
    policy: Policy,
    sources: Sources,
    engine: EngineCheck,
    passed: list[str],
    failed: list[str],
) -> FieldDecision:
    decision = FieldDecision(
        field=f.name,
        type=f.type,
        required=f.required,
        status=f.status,
        verified=False,
        confidence=f.confidence,
        engine=engine,
        checks_passed=passed,
        checks_failed=failed,
    )
    reasons, details = decision.reasons, decision.details
    if result.classification.status not in RELIABLE_TYPES:
        reasons.append(FieldReviewReason.DOCUMENT_TYPE)
    if f.status is FieldStatus.MISSING:
        if f.required:
            reasons.append(FieldReviewReason.REQUIRED_MISSING)
        else:
            found = lookup(
                policy.calibration,
                schema.id,
                result.schema_version,
                f.name,
                f.type,
                CalibrationKind.MISSING,
            )
            decision.calibration_key = found.key
            # An absence has no evidence page: any flagged page of the document may be
            # where the value was not read.
            if sources.flagged:
                reasons.append(FieldReviewReason.SOURCE_FLAGGED)
                details.append(f"{f.name}: absent, and pages {sorted(sources.flagged)} flagged")
            if found.threshold is None:
                reasons.append(FieldReviewReason.MISSING_UNVERIFIED)
                details.append(f"{f.name}: absence not calibrated ({found.detail})")
        decision.verified = not reasons
        return decision
    if f.status in _STATUS_REASON:
        reasons.append(_STATUS_REASON[f.status])
        return decision
    pages = sorted({e.page_number for e in f.evidence} & set(sources.flagged))
    if pages:
        reasons.append(FieldReviewReason.SOURCE_FLAGGED)
        details.append(
            f"{f.name}: page {pages[0]} flagged ({', '.join(sources.flagged[pages[0]])})"
        )
    if failed:
        reasons.append(FieldReviewReason.CHECK_FAILED)
        details.append(f"{f.name}: failed {', '.join(failed)}")
    if engine.status is EngineAgreement.DISAGREE:
        reasons.append(FieldReviewReason.ENGINE_DISAGREEMENT)
        details.append(f"{f.name}: {engine.engine} read {engine.readings or '(no value)'}")
    found = lookup(
        policy.calibration, schema.id, result.schema_version, f.name, f.type, CalibrationKind.VALUE
    )
    decision.calibration_key = found.key
    decision.threshold = found.threshold
    if found.threshold is None:
        reasons.append(FieldReviewReason.NOT_CALIBRATED)
        details.append(f"{f.name}: {found.detail}")
    elif f.confidence is None:
        reasons.append(FieldReviewReason.NO_CONFIDENCE)
    elif f.confidence < found.threshold:
        reasons.append(FieldReviewReason.BELOW_THRESHOLD)
        details.append(f"{f.name}: confidence {f.confidence} < {found.threshold}")
    decision.verified = not reasons
    return decision


def _row_checked_columns(schema: DocumentSchema, table: str, passed: list[str]) -> set[str]:
    """Columns that a passing row-level check confirmed on every row."""
    out: set[str] = set()
    for spec in schema.checks:
        if spec.table != table or spec.id not in passed:
            continue
        if spec.kind == "row_product":
            out |= {*spec.factors, spec.target or ""}
        elif spec.kind == "running_balance":
            out |= {spec.credit or "", spec.debit or "", spec.balance or ""}
    return out - {""}


def _decide_table(
    name: str,
    result: ExtractionResult,
    sources: Sources,
    passed: list[str],
    failed: list[str],
    required: bool,
    engine_pages: dict[int, OcrPage],
    engine_name: str | None,
    policy: Policy,
    schema: DocumentSchema,
) -> TableDecision:
    table = next(t for t in result.tables if t.name == name)
    decision = TableDecision(
        table=name,
        status=table.status,
        verified=False,
        checks_passed=passed,
        checks_failed=failed,
    )
    if table.status != "extracted":
        if required:
            decision.reasons.append("required_table_missing")
        decision.verified = not required
        return decision
    columns = {c.name: c for c in schema.table(name).columns}
    corroborated = _row_checked_columns(schema, name, passed)
    pages = set()
    for record in table.records:
        pages.add(record.page_number)
        for column, cell in record.values.items():
            if cell.status in (FieldStatus.AMBIGUOUS, FieldStatus.INVALID):
                decision.cells_flagged.append(f"{record.index}.{column}")
            elif cell.status is FieldStatus.EXTRACTED and cell.evidence is not None:
                check = check_value(
                    cell.value,
                    columns[column].type,
                    [cell.evidence],
                    engine_pages,
                    engine_name,
                    policy.options,
                    mask=policy.masks(schema),
                )
                if check.status is not EngineAgreement.DISAGREE:
                    continue
                if column in corroborated:
                    decision.engine_disagreements_corroborated += 1
                else:
                    decision.engine_disagreements += 1
                    decision.cells_flagged.append(f"{record.index}.{column}")
    if decision.cells_flagged:
        decision.reasons.append("cells_flagged")
    if decision.engine_disagreements:
        decision.reasons.append("engine_disagreement")
    if table.issues:
        decision.reasons.append("layout_issues")
    if pages & set(sources.tables):
        decision.reasons.append("source_flagged")
    if failed:
        decision.reasons.append("check_failed")
    if not passed:
        decision.reasons.append("no_passing_check")  # nothing corroborates the rows
    decision.verified = not decision.reasons
    return decision


def assess(
    result: ExtractionResult,
    schema: DocumentSchema | None,
    policy: Policy,
    ocr: OcrRun | None = None,
    layout: LayoutRun | None = None,
    engine_pages: dict[int, OcrPage] | None = None,
    engine_name: str | None = None,
) -> Assessment:
    calibration_id = policy.calibration.id if policy.calibration else None
    out = Assessment(
        calibration_id=calibration_id, auto_accept_enabled=policy.auto_accept, eligible=False
    )
    if schema is None:
        out.reasons.append(f"document_type_{result.classification.status.value}")
        return out
    engine_pages = engine_pages or {}
    sources = Sources.of(ocr, layout)
    status = {c.id: c.status for c in result.checks}
    by_field: dict[str, tuple[list[str], list[str]]] = {f.name: ([], []) for f in schema.fields}
    by_table: dict[str, tuple[list[str], list[str]]] = {t.name: ([], []) for t in schema.tables}
    for spec in schema.checks:
        outcome = status.get(spec.id)
        targets = [by_field[n] for n in check_fields(spec) if n in by_field]
        if spec.table:
            targets.append(by_table[spec.table])
        for passed, failed in targets:
            if outcome is CheckStatus.PASSED:
                passed.append(spec.id)
            elif outcome is CheckStatus.FAILED:
                failed.append(spec.id)
    specs = {s.name: s for s in schema.fields}
    for f in result.fields:
        engine = (
            check_value(
                f.value,
                FieldType(f.type),
                f.evidence,
                engine_pages,
                engine_name,
                policy.options,
                specs[f.name].pattern,
                mask=policy.masks(schema),
            )
            if f.status is FieldStatus.EXTRACTED
            else EngineCheck(status=EngineAgreement.UNAVAILABLE, engine=engine_name)
        )
        out.fields.append(
            _decide_field(f, schema, result, policy, sources, engine, *by_field[f.name])
        )
    for table in schema.tables:
        out.tables.append(
            _decide_table(
                table.name,
                result,
                sources,
                *by_table[table.name],
                table.required,
                engine_pages,
                engine_name,
                policy,
                schema,
            )
        )
    if result.classification.status not in RELIABLE_TYPES:
        out.reasons.append(f"document_type_{result.classification.status.value}")
    if not result.validation.valid:
        out.reasons.append("validation_failed")
    if any(c.status is CheckStatus.FAILED for c in result.checks):
        out.reasons.append("check_failed")
    if result.status is ExtractionStatus.UNCLASSIFIED:
        out.reasons.append("unclassified")
    out.eligible = (
        not out.reasons
        and all(d.verified for d in out.fields)
        and all(d.verified for d in out.tables)
    )
    return out


def observations(
    result: ExtractionResult, assessment: Assessment, truth: dict[str, Value]
) -> list[Observation]:
    """Calibration observations: each field of a labelled document with its gate outcome."""
    out: list[Observation] = []
    if result.schema_id is None:
        return out
    values = {f.name: f for f in result.fields}
    for d in assessment.fields:
        if d.field not in truth:
            continue
        expected = truth[d.field]
        gated = not (set(d.reasons) - THRESHOLD_REASONS)
        if d.status is FieldStatus.EXTRACTED:
            out.append(
                Observation(
                    schema_id=result.schema_id,
                    field=d.field,
                    type=d.type,
                    kind=CalibrationKind.VALUE,
                    gated=gated,
                    confidence=d.confidence,
                    correct=expected is not None and same_value(values[d.field].value, expected),
                )
            )
        elif d.status is FieldStatus.MISSING and not d.required:
            out.append(
                Observation(
                    schema_id=result.schema_id,
                    field=d.field,
                    type=d.type,
                    kind=CalibrationKind.MISSING,
                    gated=gated,
                    confidence=None,
                    correct=expected is None,
                )
            )
    return out
