from __future__ import annotations

from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"
REAL_GUIDE_DOCS = FIXTURES / "guide_63013be" / "docs"
REAL_GUIDE_COMMIT = "63013be4867f4fc0b5145ee1b0d4dfc12c4d8fea"


@pytest.fixture(autouse=True)
def _collect_on_main_thread():
    """Free each test's leftover Tk windows on the main thread; if a GUI worker thread's
    garbage collection freed them instead, Tcl would abort the whole test run."""
    yield
    import gc

    gc.collect()


@pytest.fixture
def make_docs(tmp_path):
    """Write a throwaway docs/ tree: make_docs({"page.md": "...", "_include/x.md": "..."})."""

    def _make(files: dict[str, str]) -> Path:
        docs = tmp_path / "docs"
        for rel, text in files.items():
            path = docs / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        return docs

    return _make


@pytest.fixture(scope="session")
def real_snapshot():
    import guide_parser

    return guide_parser.build_snapshot(REAL_GUIDE_DOCS, REAL_GUIDE_COMMIT)
