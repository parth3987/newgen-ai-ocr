"""No LLM, no network: extraction imports nothing that can call out and opens no sockets."""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

from newgen_ocr.evaluation.extraction_corpus import build, load_manifest
from tests.conftest import ROOT

PACKAGE = ROOT / "src" / "newgen_ocr" / "extraction"
ALLOWED = {"pydantic", "openpyxl", "newgen_ocr"}
STDLIB = set(sys.stdlib_module_names)
NETWORK_STDLIB = {"socket", "ssl", "http", "urllib", "ftplib", "smtplib", "xmlrpc", "asyncio"}


def test_extraction_imports_only_local_libraries() -> None:
    for path in PACKAGE.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                names = [node.module]
            for name in names:
                top = name.split(".")[0]
                assert top in ALLOWED or (top in STDLIB and top not in NETWORK_STDLIB), (
                    f"{path.name} imports {name}"
                )


_PROBE = """
import sys
from pathlib import Path
attempts = []
sys.addaudithook(lambda e, a: attempts.append(e) if e.startswith("socket.") else None)
from newgen_ocr.config import Settings
from newgen_ocr.storage.local import DocumentStore
from newgen_ocr.audit import AuditLog
from newgen_ocr.extraction.schema import SchemaRegistry
from newgen_ocr.extraction.service import ExtractionService
from newgen_ocr.extraction import export
from tests.fixtures.extraction import INVOICE, ITEMS, store_document, table
data = Path(sys.argv[1])
settings = Settings(data_dir=data, _env_file=None)
store = DocumentStore(data)
doc = store_document(settings, store, INVOICE, [table(ITEMS)])
result = ExtractionService(store, AuditLog(data), SchemaRegistry()).extract(doc, "auto")
export.to_xlsx(result); export.fields_csv(result)
print("STATUS", result.status.value)
print("SOCKET_EVENTS", len(attempts))
"""


def test_extraction_opens_no_sockets(tmp_path: Path) -> None:
    (tmp_path / "data").mkdir()
    result = subprocess.run(  # noqa: S603
        [sys.executable, "-c", _PROBE, str(tmp_path / "data")],
        capture_output=True,
        text=True,
        cwd=ROOT,
        timeout=300,
        check=False,
    )
    assert "STATUS complete" in result.stdout, result.stderr[-2000:]
    assert "SOCKET_EVENTS 0" in result.stdout


def test_schema_documents_are_current() -> None:
    result = subprocess.run(
        [sys.executable, "scripts/generate_schemas.py", "--check"],
        capture_output=True,
        text=True,
        cwd=ROOT,
        timeout=120,
        check=False,
    )
    assert result.returncode == 0, result.stdout


def test_extraction_corpus_ground_truth(tmp_path: Path) -> None:
    samples = build(tmp_path)
    assert len(samples) == 2 * 13
    assert load_manifest(tmp_path)[0].id == "gst-intra-clean"
    types = {s.document_type for s in samples}
    assert types == {"gst_invoice", "receipt", "bank_statement", "pan_card", "aadhaar_card", None}
    unregistered = next(s for s in samples if s.id == "gst-unregistered-clean")
    assert unregistered.fields["buyer_gstin"] is None  # must stay missing, never invented
