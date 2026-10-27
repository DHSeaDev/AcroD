"""A drop says what it contains: how many PDFs, how big, and what was skipped and why."""
import os, sys
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import pytest
from pypdf import PdfWriter
from PySide6.QtWidgets import QMessageBox
import acrod as H
from test_organizer_panel import app, pump  # noqa: F401


@pytest.fixture(autouse=True)
def _iso(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "appdata"))
    H._reset_ui_state_cache()


def mk(path, pages=2):
    w = PdfWriter()
    for i in range(pages):
        w.add_blank_page(width=100, height=100)
    with open(path, "wb") as f:
        w.write(f)
    return str(path)


@pytest.mark.parametrize("n,txt", [(0, "0 B"), (1023, "1023 B"), (1024, "1.0 KB"), (1536, "1.5 KB"),
                                   (5 * 1024 * 1024, "5.0 MB"), (300 * 1024, "300 KB"), (3 << 30, "3.0 GB")])
def test_human_size(n, txt):
    assert H.human_size(n) == txt


def test_classify_every_kind(tmp_path):
    good, good2 = mk(tmp_path / "a.pdf"), mk(tmp_path / "b.PDF")
    (tmp_path / "doc.docx").write_text("x")
    (tmp_path / "folder").mkdir()
    (tmp_path / "fake.pdf").write_text("this is not a pdf at all")
    info = H.classify_drop([good, str(tmp_path / "doc.docx"), str(tmp_path / "folder"),
                            str(tmp_path / "fake.pdf"), str(tmp_path / "gone.pdf"), good2])
    assert info["pdfs"] == [good, good2]
    assert dict(info["skipped"]) == {"doc.docx": "is not a PDF", "folder": "is a folder, not a PDF",
                                     "fake.pdf": "does not look like a PDF inside", "gone.pdf": "was not found"}
    assert info["bytes"] == os.path.getsize(good) + os.path.getsize(good2)


def test_too_large_is_named(tmp_path, monkeypatch):
    p = mk(tmp_path / "big.pdf")
    monkeypatch.setattr(H, "PDF_FILE_MAX", 10)
    info = H.classify_drop([p])
    assert info["pdfs"] == [] and "too large or damaged" in info["skipped"][0][1]


def test_describe(tmp_path):
    a = mk(tmp_path / "a.pdf")
    (tmp_path / "x.txt").write_text("1")
    txt = H.describe_drop(H.classify_drop([a, str(tmp_path / "x.txt")]))
    assert txt.startswith("1 PDF, ") and "Skipped: x.txt (is not a PDF)." in txt
    assert H.describe_drop({"pdfs": [], "skipped": [], "bytes": 0}) == "No PDFs."
    many = {"pdfs": [], "skipped": [(f"f{i}", "is not a PDF") for i in range(8)], "bytes": 0}
    assert "and 3 more" in H.describe_drop(many)


def test_splitter_confirm_lists_skips(app, tmp_path, monkeypatch):
    a = mk(tmp_path / "a.pdf", 3); (tmp_path / "n.txt").write_text("1")
    seen = []
    monkeypatch.setattr(H.Msg, "question", lambda self, t, text, *x, **k: (seen.append(text), QMessageBox.No)[1])
    p = H.SplitterPanel(); p.show()
    p.handle_drop([a, str(tmp_path / "n.txt")])
    assert seen and "1 PDF, " in seen[0] and "n.txt (is not a PDF)" in seen[0]
    crit = []
    monkeypatch.setattr(H.Msg, "critical", lambda self, t, text, *x, **k: crit.append(text))
    p.handle_drop([str(tmp_path / "n.txt")])
    assert crit and "n.txt (is not a PDF)" in crit[0]
    p.stop_for_shutdown(); p.close()


def test_splitter_summary_after_run(app, tmp_path, monkeypatch):
    a = mk(tmp_path / "a.pdf", 3); (tmp_path / "n.txt").write_text("1")
    monkeypatch.setattr(H.Msg, "question", lambda *a_, **k: QMessageBox.Yes)
    shown = []
    for kind in ("information", "warning", "critical"):
        monkeypatch.setattr(H.Msg, kind, lambda self, t, text, *x, **k: shown.append(text))
    p = H.SplitterPanel(); p.show()
    p.handle_drop([a, str(tmp_path / "n.txt")])
    assert pump(app, lambda: not p._busy and shown)
    assert "Split: 1" in shown[-1] and "Skipped: 1 (n.txt (is not a PDF))" in shown[-1]
    p.stop_for_shutdown(); p.close()


def test_merger_confirm_shows_size_and_extras(app, tmp_path, monkeypatch):
    base, ref, other = mk(tmp_path / "base.pdf"), mk(tmp_path / "ref.pdf"), mk(tmp_path / "other.pdf")
    seen, infos = [], []
    monkeypatch.setattr(H.Msg, "question", lambda self, t, text, *x, **k: (seen.append(text), QMessageBox.No)[1])
    monkeypatch.setattr(H.Msg, "information", lambda self, t, text, *x, **k: infos.append(text))
    p = H.MergerPanel(); p.show(); p.reference_path = ref
    p.handle_drop([base, other])
    assert infos and "other.pdf" in infos[0] and "Not merged" in infos[0]
    assert seen and "base.pdf (" in seen[0] and " B)" in seen[0]
    crit = []
    monkeypatch.setattr(H.Msg, "critical", lambda self, t, text, *x, **k: crit.append(text))
    (tmp_path / "x.docx").write_text("1")
    p.handle_drop([str(tmp_path / "x.docx")])
    assert crit and "is not a PDF" in crit[0]
    p.stop_for_shutdown(); p.close()
