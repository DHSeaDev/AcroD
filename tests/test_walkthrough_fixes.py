"""Findings from the naive-user walkthrough, each pinned by the thing the user sees."""
import os, sys
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import pytest
from pypdf import PdfWriter
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QMessageBox
import acrod as H
from test_organizer_panel import app, pump  # noqa: F401


@pytest.fixture(autouse=True)
def _iso(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "appdata"))
    H._reset_ui_state_cache()
    H._AUTO.update(narrow=False, open=False)


@pytest.fixture
def boxes(monkeypatch):
    seen = []

    def rec(kind):
        def f(parent, title, text, *a, **k):
            seen.append((kind, title, text))
            return QMessageBox.Yes if kind == "question" else QMessageBox.Ok
        return staticmethod(f)
    for k in ("question", "information", "warning", "critical"):
        monkeypatch.setattr(QMessageBox, k, rec(k))
    return seen


def mk(path, pages=2):
    w = PdfWriter()
    for _ in range(pages):
        w.add_blank_page(width=100, height=100)
    with open(path, "wb") as f:
        w.write(f)
    return str(path)


@pytest.fixture
def win(app):
    w = H.MainWindow(); w.resize(1360, 860); w.show()
    yield w
    w.close()


def tool(win, i):
    win.tabs.setCurrentIndex(i); QTest.qWait(150)
    return win.panel_at(i)


def test_merging_a_file_onto_itself_is_refused_before_asking(win, boxes, tmp_path):
    a = mk(tmp_path / "a.pdf")
    p = tool(win, 0)
    p._set_reference(a)
    p.handle_drop([a])
    assert [b[0] for b in boxes] == ["warning"] and "itself" in boxes[0][2]    # no "Overwrite? Continue?" first
    assert "reference PDF itself" in p.status.text()


def test_splitter_status_says_nothing_was_split_after_a_bad_drop(win, boxes, tmp_path):
    (tmp_path / "x.txt").write_text("x")
    p = tool(win, 1)
    p.handle_drop([str(tmp_path / "x.txt")])
    assert "Nothing was split" in p.status.text()


def test_organizer_with_no_pdf_open_says_so(win, boxes):
    p = tool(win, 3)
    p.rotate_left()
    assert "No PDF is open" in p.status.text()
    p.save_and_next()
    assert "No PDF is open" in p.status.text()


def test_renamer_wording(app, win, boxes, tmp_path):
    for n in ("a.pdf", "b.pdf", "123-Jones.pdf"):
        mk(tmp_path / n)
    p = tool(win, 4)
    p.receive_folder(str(tmp_path)); pump(app, lambda: p.table.rowCount() == 3)
    p.search_edit.setText("a.pdf"); p.replace_edit.setText("z.pdf"); QTest.qWait(500)
    assert "Filename only" in p.status.text() and "Filename + extension" in p.status.text()
    p.regex_box.setChecked(True); p.search_edit.setText("("); QTest.qWait(500)
    assert "not a valid regular expression" in p.status.text() and "Invalid regular" not in p.status.text()
    p.search_edit.setText(".*"); p.replace_edit.setText(""); QTest.qWait(500)
    note = p.table.item(0, 2).text()
    assert note.startswith("name clash") and "two files" not in note and note.count("share") == 0
    p.regex_box.setChecked(False); p.search_edit.setText("123"); p.replace_edit.setText("9"); QTest.qWait(500)
    assert p.rename_btn.text() == "Rename 1 File"
    assert [p.table.item(r, 2).text() for r in range(3)].count("no change") == 2


def test_settings_and_help_have_no_stale_pick_a_tool_line(win):
    tool(win, 6)
    assert win.shell.heading.text() == ""


def test_tray_wording_has_no_colour_name_and_merge_says_updated():
    src = open(os.path.join(os.path.dirname(__file__), "..", "acrod.py"), encoding="utf-8").read()
    assert "blue button" not in src
    p = H.plan_tray_feed("merge", [{"path": "/a/1.pdf", "kind": "pdf"}])
    assert "updated" in p["problem"] and "first is kept" not in p["problem"]


def test_actions_column_folds_in_a_narrow_window_and_opens_on_click(win):
    p = tool(win, 2)
    win.resize(800, 600); QTest.qWait(300)
    assert p.inspector.width() == H._RAIL_WIDTH
    p.inspector.toggle_btn.click(); QTest.qWait(100)
    assert p.inspector.width() > H._RAIL_WIDTH
    assert H.setting("actions_collapsed") is False                 # a squeeze is not a saved choice
    win.resize(1360, 860); QTest.qWait(300)
    assert p.inspector.width() > H._RAIL_WIDTH


def test_help_explains_the_actions_column(win):
    src = open(os.path.join(os.path.dirname(__file__), "..", "acrod.py"), encoding="utf-8").read()
    assert "how do I move or hide the Actions column" in src and "Ctrl+J" in src


def test_tray_is_hidden_by_default_and_its_text_is_short(win, tmp_path):
    shell = win.shell
    assert not shell.tray.isVisible()
    t = shell.tray
    assert "reuse them across tools" in t.empty_box.text()
    assert "1. Add files" not in t.drop_hint.text() and len(t.drop_hint.text()) < 110
