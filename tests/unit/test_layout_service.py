"""Layout service policy with fake models: regions, tables, evidence checks, review, storage."""

from __future__ import annotations

import io
import json

import pytest

from newgen_ocr.audit import AuditLog
from newgen_ocr.config import Settings
from newgen_ocr.errors import AppError, ErrorCode
from newgen_ocr.ingestion.service import IngestionService, UploadInfo
from newgen_ocr.layout import geometry as g
from newgen_ocr.layout.models import (
    CellBoxSource,
    CellTextSource,
    KeyValueSource,
    LayoutReviewReason,
    PageLayoutStatus,
    RegionType,
    RulingCheck,
    TableKind,
    TableStatus,
    UnsupportedKind,
)
from newgen_ocr.layout.service import LayoutPolicy, LayoutService
from newgen_ocr.ocr.engine import OcrEngine
from newgen_ocr.ocr.models import OcrLine, PageOcrStatus
from newgen_ocr.storage.local import DocumentStore
from tests.fixtures.layout import (
    TABLE,
    TITLE,
    FakeLayoutModels,
    cell_box,
    draw_page,
    line,
    ocr_run,
    png,
    region,
    text_box,
    tokens,
)
from tests.fixtures.ocr import FakeEngine

R = RegionType
WORDS = [[f"r{r}c{c}" for c in range(3)] for r in range(3)]


@pytest.fixture
def store(settings: Settings) -> DocumentStore:
    return DocumentStore(settings.data_dir)


def _document(
    settings: Settings,
    store: DocumentStore,
    lines: list[OcrLine],
    ink: list | None = None,  # type: ignore[type-arg]
    **page: bool,
) -> str:
    boxes = [ln.bbox for ln in lines if ln.text != "Title"] if ink is None else ink
    image = draw_page(boxes, **page)
    manifest = IngestionService(settings, store, AuditLog(settings.data_dir)).ingest(
        io.BytesIO(png(image)), UploadInfo("t.png", None)
    )
    store.write_ocr_run(manifest.document_id, "auto", ocr_run(manifest.document_id, lines))
    return manifest.document_id


def _table_lines() -> list[OcrLine]:
    lines = [line("Title", TITLE)]
    lines += [line(WORDS[r][c], text_box(r, c)) for r in range(3) for c in range(3)]
    return lines


def _grid_models(**kw: object) -> FakeLayoutModels:
    models = FakeLayoutModels(
        regions=[region(R.SECTION_TITLE, TITLE), region(R.TABLE, TABLE)],
        structure_tokens=tokens([["", "", ""]] * 3),
        cell_boxes=[cell_box(r, c) for r in range(3) for c in range(3)],
    )
    for key, value in kw.items():
        setattr(models, key, value)
    return models


def _service(
    store: DocumentStore,
    settings: Settings,
    models: FakeLayoutModels,
    policy: LayoutPolicy | None = None,
    **engines: OcrEngine,
) -> LayoutService:
    return LayoutService(store, AuditLog(settings.data_dir), models, dict(engines), policy)


def test_verified_wired_table(store: DocumentStore, settings: Settings) -> None:
    doc = _document(settings, store, _table_lines())
    run = _service(store, settings, _grid_models()).analyze(doc, "auto")
    page = run.pages[0]
    assert page.status is PageLayoutStatus.OK
    assert not page.review.needs_review
    assert [r.type for r in page.regions] == [R.SECTION_TITLE, R.TABLE]
    (table,) = page.tables
    assert table.status is TableStatus.OK
    assert table.ruling_check is RulingCheck.VERIFIED
    assert table.cell_boxes is CellBoxSource.RULINGS
    assert (table.rows, table.cols, table.ruling_rows, table.ruling_cols) == (3, 3, 3, 3)
    assert [c.text for c in table.cells] == [w for row in WORDS for w in row]
    assert all(c.text_source is CellTextSource.OCR_LINES for c in table.cells)
    assert table.cells[4].line_indices == [5]
    assert table.grid == [[0, 1, 2], [3, 4, 5], [6, 7, 8]]
    assert table.html.startswith("<table><tr><td>r0c0</td>")
    assert page.regions[1].table_id == table.id == 0 and table.region_id == 1
    assert page.text == "Title\n\nr0c0\tr0c1\tr0c2\nr1c0\tr1c1\tr1c2\nr2c0\tr2c1\tr2c2"
    # Stored, listed and audited.
    assert store.list_layout_runs(doc) == ["auto"]
    assert store.read_layout_run(doc, "auto").pages[0].tables[0].cells[0].text == "r0c0"
    events = (settings.data_dir / "audit" / "audit.jsonl").read_text().splitlines()
    last = json.loads(events[-1])
    assert (last["event"], last["tables"], last["ocr_run"]) == ("layout.completed", 1, "auto")


def test_structure_disagreeing_with_rulings_is_routed_to_review(
    store: DocumentStore, settings: Settings
) -> None:
    doc = _document(settings, store, _table_lines())
    models = _grid_models(
        structure_tokens=tokens([["", ""]] * 3),
        cell_boxes=[g.box(100, 200 + 80 * r, 400, 280 + 80 * r) for r in range(3) for _ in "ab"],
    )
    page = _service(store, settings, models).analyze(doc, "auto").pages[0]
    table = page.tables[0]
    assert table.ruling_check is RulingCheck.MISMATCH
    assert table.status is TableStatus.NEEDS_REVIEW
    assert table.cell_boxes is CellBoxSource.MODEL
    assert LayoutReviewReason.TABLE_STRUCTURE_MISMATCH in page.review.reasons


def test_invalid_structure_is_unsupported_and_text_kept(
    store: DocumentStore, settings: Settings
) -> None:
    doc = _document(settings, store, _table_lines())
    models = _grid_models(structure_tokens=tokens([["", ""], [""]]))
    page = _service(store, settings, models).analyze(doc, "auto").pages[0]
    table = page.tables[0]
    assert table.status is TableStatus.UNSUPPORTED
    assert table.cells == [] and table.html == ""
    assert page.unsupported[0].kind is UnsupportedKind.TABLE_STRUCTURE
    assert page.unsupported[0].region_id == 1
    assert LayoutReviewReason.TABLE_STRUCTURE_INVALID in page.review.reasons
    assert "r1c1" in page.regions[1].text  # no cells invented, no text lost


def test_box_count_mismatch_is_unsupported(store: DocumentStore, settings: Settings) -> None:
    doc = _document(settings, store, _table_lines())
    models = _grid_models(cell_boxes=[cell_box(0, 0)])
    page = _service(store, settings, models).analyze(doc, "auto").pages[0]
    assert page.tables[0].status is TableStatus.UNSUPPORTED


def test_low_structure_score_is_reviewed(store: DocumentStore, settings: Settings) -> None:
    doc = _document(settings, store, _table_lines())
    page = _service(store, settings, _grid_models(score=0.5)).analyze(doc, "auto").pages[0]
    assert LayoutReviewReason.TABLE_LOW_CONFIDENCE in page.review.reasons


def test_ruling_check_can_be_disabled(store: DocumentStore, settings: Settings) -> None:
    doc = _document(settings, store, _table_lines())
    policy = LayoutPolicy(ruling_check=False)
    table = (
        _service(store, settings, _grid_models(), policy).analyze(doc, "auto").pages[0].tables[0]
    )
    assert table.ruling_check is RulingCheck.DISABLED
    assert table.cell_boxes is CellBoxSource.MODEL


def test_wireless_merged_column_is_detected(store: DocumentStore, settings: Settings) -> None:
    # The model sees 2 columns where there are 3: columns 0 and 1 share one wide cell, so
    # two separate OCR lines sit side by side in it.
    doc = _document(settings, store, _table_lines(), ruled=False)
    models = _grid_models(
        kind=TableKind.WIRELESS,
        structure_tokens=tokens([["", ""]] * 3),
        cell_boxes=[b for r in range(3) for b in (cell_box(r, 0, col_span=2), cell_box(r, 2))],
    )
    page = _service(store, settings, models).analyze(doc, "auto").pages[0]
    table = page.tables[0]
    assert table.ruling_check is RulingCheck.NOT_APPLICABLE
    assert table.cells[0].text == "r0c0 r0c1"
    assert table.status is TableStatus.NEEDS_REVIEW
    assert LayoutReviewReason.TABLE_STRUCTURE_MISMATCH in page.review.reasons


def test_spanning_line_cells_are_reread(store: DocumentStore, settings: Settings) -> None:
    # One OCR line covers cells (0,0) and (0,1); the boundary falls in whitespace.
    span = g.box(120, 225, 480, 255)
    lines = [line("Title", TITLE), line("left right", span)]
    ink = [g.box(120, 225, 200, 255), g.box(320, 225, 400, 255)]
    doc = _document(settings, store, lines, ink=ink, ruled=False)
    models = _grid_models(
        kind=TableKind.WIRELESS,
        structure_tokens=tokens([["", ""]]),
        cell_boxes=[cell_box(0, 0), cell_box(0, 1)],
    )
    engine = FakeEngine("paddle", "cell text")
    page = _service(store, settings, models, paddle=engine).analyze(doc, "auto").pages[0]
    table = page.tables[0]
    assert [c.text for c in table.cells] == ["cell text", "cell text"]
    assert all(c.text_source is CellTextSource.CELL_OCR for c in table.cells)
    assert table.cells[0].line_indices == [1]
    assert table.status is TableStatus.OK
    assert any("spanned" in issue for issue in table.issues)
    assert len(engine.calls) == 2


def test_spanning_line_without_engine_is_ambiguous(
    store: DocumentStore, settings: Settings
) -> None:
    span = g.box(120, 225, 480, 255)
    lines = [line("Title", TITLE), line("left right", span)]
    doc = _document(settings, store, lines, ink=[], ruled=False)
    models = _grid_models(
        kind=TableKind.WIRELESS,
        structure_tokens=tokens([["", ""]]),
        cell_boxes=[cell_box(0, 0), cell_box(0, 1)],
    )
    down = FakeEngine("paddle", unavailable="down")
    page = _service(store, settings, models, paddle=down).analyze(doc, "auto").pages[0]
    assert LayoutReviewReason.TABLE_CELL_TEXT_AMBIGUOUS in page.review.reasons
    assert [c.text for c in page.tables[0].cells] == ["", ""]


def test_unread_cell_with_marks_is_reviewed(store: DocumentStore, settings: Settings) -> None:
    lines = _table_lines()
    missing = lines.pop(5)  # page OCR missed cell (1,1), but its ink is on the page
    doc = _document(settings, store, lines, ink=[ln.bbox for ln in lines[1:]] + [missing.bbox])
    engine = FakeEngine("paddle", "")  # the crop re-read finds nothing either
    page = _service(store, settings, _grid_models(), paddle=engine).analyze(doc, "auto").pages[0]
    table = page.tables[0]
    assert table.cells[4].text == ""
    assert LayoutReviewReason.TABLE_CELL_UNREAD in page.review.reasons
    assert table.status is TableStatus.NEEDS_REVIEW


def test_empty_cell_is_filled_from_its_crop(store: DocumentStore, settings: Settings) -> None:
    lines = _table_lines()
    missing = lines.pop(5)
    doc = _document(settings, store, lines, ink=[ln.bbox for ln in lines[1:]] + [missing.bbox])
    engine = FakeEngine("paddle", "r1c1")
    page = _service(store, settings, _grid_models(), paddle=engine).analyze(doc, "auto").pages[0]
    cell = page.tables[0].cells[4]
    assert (cell.text, cell.text_source) == ("r1c1", CellTextSource.CELL_OCR)
    assert not page.review.needs_review
    # Ruling interiors are exact: crops are not padded into the borders.
    assert len(engine.calls) == 1


def test_lines_outside_cells_and_regions(store: DocumentStore, settings: Settings) -> None:
    lines = [*_table_lines(), line("stray", g.box(800, 600, 900, 630))]
    doc = _document(settings, store, lines)
    page = _service(store, settings, _grid_models()).analyze(doc, "auto").pages[0]
    stray = [r for r in page.regions if r.type is R.UNCLASSIFIED]
    assert [r.text for r in stray] == ["stray"]
    assert stray[0].label is None and stray[0].score is None
    assert LayoutReviewReason.UNCLASSIFIED_TEXT in page.review.reasons


def test_unassigned_text_inside_table(store: DocumentStore, settings: Settings) -> None:
    lines = _table_lines()
    doc = _document(settings, store, lines)
    models = _grid_models()
    models.cell_boxes = [cell_box(r, c) for r in range(3) for c in range(3)]
    models.cell_boxes[8] = g.box(0, 0, 0, 0)  # model gave no box for one cell
    policy = LayoutPolicy(ruling_check=False)
    page = _service(store, settings, models, policy).analyze(doc, "auto").pages[0]
    assert page.tables[0].cells[8].bbox is None
    assert LayoutReviewReason.TABLE_TEXT_UNASSIGNED in page.review.reasons


def test_unsupported_regions_are_recorded(store: DocumentStore, settings: Settings) -> None:
    formula, figure, chart = (
        g.box(100, 500, 400, 560),
        g.box(100, 600, 400, 700),
        g.box(500, 500, 900, 700),
    )
    lines = [
        line("x = y + 2", g.box(120, 510, 300, 550)),
        line("Fig label", g.box(120, 620, 300, 660)),
    ]
    doc = _document(settings, store, lines)
    models = FakeLayoutModels(
        regions=[region(R.FORMULA, formula), region(R.FIGURE, figure), region(R.CHART, chart)]
    )
    page = _service(store, settings, models).analyze(doc, "auto").pages[0]
    kinds = {u.kind for u in page.unsupported}
    assert kinds == {UnsupportedKind.FORMULA, UnsupportedKind.FIGURE_TEXT, UnsupportedKind.CHART}
    # Text inside a formula needs review; an empty chart and figure text do not.
    assert page.review.reasons == [LayoutReviewReason.UNSUPPORTED_STRUCTURE]


def test_overlapping_and_duplicate_regions(store: DocumentStore, settings: Settings) -> None:
    lines = [line("Customer: Sharma Traders", g.box(110, 110, 380, 140))]
    doc = _document(settings, store, lines)
    models = FakeLayoutModels(
        regions=[
            region(R.TEXT, g.box(100, 100, 400, 150), score=0.9),
            region(R.TEXT, g.box(105, 105, 395, 145), score=0.6),  # nested duplicate: dropped
            region(R.CAPTION, g.box(100, 100, 600, 160), score=0.7),  # overlaps, other type
        ]
    )
    page = _service(store, settings, models).analyze(doc, "auto").pages[0]
    assert [r.type for r in page.regions] == [R.TEXT, R.CAPTION]
    assert page.regions[0].line_indices == [0]  # the smaller, more specific region
    assert LayoutReviewReason.OVERLAPPING_REGIONS in page.review.reasons
    (pair,) = page.key_values
    assert (pair.key, pair.value, pair.source) == (
        "Customer",
        "Sharma Traders",
        KeyValueSource.SEPARATOR,
    )
    assert pair.region_id == 0


def test_two_column_table_gives_pairs(store: DocumentStore, settings: Settings) -> None:
    lines = [line("Title", TITLE)] + [
        line(t, text_box(r, c))
        for r, row in enumerate([["Amount", "100"]])
        for c, t in enumerate(row)
    ]
    doc = _document(settings, store, lines, ruled=False)
    models = _grid_models(
        kind=TableKind.WIRELESS,
        structure_tokens=tokens([["", ""]]),
        cell_boxes=[cell_box(0, 0), cell_box(0, 1)],
    )
    page = _service(store, settings, models).analyze(doc, "auto").pages[0]
    assert [(p.key, p.value, p.source) for p in page.key_values] == [
        ("Amount", "100", KeyValueSource.TABLE_ROW)
    ]


def test_pages_without_ocr_are_skipped(store: DocumentStore, settings: Settings) -> None:
    doc = _document(settings, store, _table_lines())
    run = store.read_ocr_run(doc, "auto")
    run.pages[0].status = PageOcrStatus.SKIPPED
    run.pages[0].skip_reason = "native_text"
    store.write_ocr_run(doc, "auto", run)
    page = _service(store, settings, _grid_models()).analyze(doc, "auto").pages[0]
    assert (page.status, page.skip_reason) == (PageLayoutStatus.SKIPPED, "native_text")
    run.pages[0].status = PageOcrStatus.UNSUPPORTED
    run.pages[0].skip_reason = None
    store.write_ocr_run(doc, "auto", run)
    page = _service(store, settings, _grid_models()).analyze(doc, "auto").pages[0]
    assert page.skip_reason == "ocr_unsupported"


def test_failures_are_per_page(store: DocumentStore, settings: Settings) -> None:
    doc = _document(settings, store, _table_lines())
    page = _service(store, settings, _grid_models(fail=True)).analyze(doc, "auto").pages[0]
    assert (page.status, page.error_code) == (PageLayoutStatus.FAILED, "LAYOUT_FAILED")
    run = store.read_ocr_run(doc, "auto")
    run.pages[0].image_width = 999  # OCR run no longer matches the stored image
    store.write_ocr_run(doc, "auto", run)
    page = _service(store, settings, _grid_models()).analyze(doc, "auto").pages[0]
    assert page.status is PageLayoutStatus.FAILED


def test_unavailable_models_and_missing_runs(store: DocumentStore, settings: Settings) -> None:
    doc = _document(settings, store, _table_lines())
    with pytest.raises(AppError) as exc:
        _service(store, settings, _grid_models(unavailable="no models")).analyze(doc, "auto")
    assert exc.value.code is ErrorCode.LAYOUT_UNAVAILABLE
    with pytest.raises(AppError) as exc:
        _service(store, settings, _grid_models()).analyze(doc, "tesseract")
    assert exc.value.code is ErrorCode.OCR_RUN_NOT_FOUND
    with pytest.raises(AppError) as exc:
        store.read_layout_run(doc, "auto")
    assert exc.value.code is ErrorCode.LAYOUT_RUN_NOT_FOUND


def test_overlay_png(store: DocumentStore, settings: Settings) -> None:
    doc = _document(settings, store, _table_lines())
    service = _service(store, settings, _grid_models(score=0.5))
    service.analyze(doc, "auto")
    png_bytes = service.overlay(doc, "auto", 1)
    assert png_bytes[:8] == b"\x89PNG\r\n\x1a\n"
    with pytest.raises(AppError) as exc:
        service.overlay(doc, "auto", 2)
    assert exc.value.code is ErrorCode.PAGE_NOT_FOUND
    _service(store, settings, _grid_models(fail=True)).analyze(doc, "auto")
    with pytest.raises(AppError) as exc:
        service.overlay(doc, "auto", 1)
    assert exc.value.code is ErrorCode.PAGE_IMAGE_NOT_AVAILABLE
