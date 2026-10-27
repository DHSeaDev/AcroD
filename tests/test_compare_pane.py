"""The B pane: a read-only second PDF beside the one being built. Pages are identified by width
(a.pdf = 100.., b.pdf = 200.., c.pdf = 300..)."""
import os, sys
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import hashlib
import pytest
from PySide6.QtWidgets import QMessageBox
import acrod as H
from test_organizer_panel import app, _iso, pump, select, sha  # noqa: F401
from test_page_clipboard import mk, widths, folder, panel, open_row, save, _clip  # noqa: F401


def open_b(app, p, name, pages):
    assert p.open_b(os.path.join(os.path.dirname(p._cur_path), name))
    assert pump(app, lambda: p.b_pane.grid.count() == pages and len(p._b_thumbs) == pages)


def bselect(p, *rows):
    g = p.b_pane.grid
    g.clearSelection()
    for r in rows:
        g.item(r).setSelected(True)


def md5(path):
    return hashlib.md5(open(path, "rb").read()).hexdigest()


def test_b_is_hidden_until_asked_and_shows_its_pages(app, panel):
    open_row(app, panel, 0, 4)
    assert not panel.b_pane.isVisible()
    open_b(app, panel, "b.pdf", 3)
    assert panel.b_pane.isVisible() and "b.pdf" in panel.b_pane.title.text() and "3 pages" in panel.b_pane.title.text()
    assert panel.compare_btn.text() == "Hide B"


def test_insert_from_b_after_selected_page_and_saved(app, panel, monkeypatch):
    open_row(app, panel, 0, 4)
    open_b(app, panel, "b.pdf", 3)
    bselect(panel, 0, 2)
    select(panel, 1)
    panel.b_insert(False)
    assert len(panel._pages) == 6 and panel._is_dirty()
    out = save(app, panel, monkeypatch)
    assert widths(out) == [100, 101, 200, 202, 102, 103]


def test_insert_before(app, panel, monkeypatch):
    open_row(app, panel, 0, 4)
    open_b(app, panel, "b.pdf", 3)
    bselect(panel, 1)
    select(panel, 0)
    panel.b_insert(True)
    out = save(app, panel, monkeypatch)
    assert widths(out) == [201, 100, 101, 102, 103]


def test_b_copy_goes_to_the_clipboard_for_paste(app, panel):
    open_row(app, panel, 0, 4)
    open_b(app, panel, "c.pdf", 2)
    bselect(panel, 1)
    panel.b_copy()
    assert len(H._PAGE_CLIP) == 1 and H._PAGE_CLIP.items[0]["sidx"] == 1
    select(panel, 0)
    panel.paste_pages()
    assert len(panel._pages) == 5


def test_nothing_selected_in_b_says_so_and_changes_nothing(app, panel):
    open_row(app, panel, 0, 4)
    open_b(app, panel, "b.pdf", 3)
    bselect(panel)
    panel.b_insert(False)
    panel.b_copy()
    assert len(panel._pages) == 4 and len(H._PAGE_CLIP) == 0 and "Click one or more pages in B" in panel.status.text()


def test_drag_from_b_inserts_at_the_drop_position(app, panel, monkeypatch):
    open_row(app, panel, 0, 4)
    open_b(app, panel, "b.pdf", 3)
    panel._on_b_dropped([2], 1, panel._b_serial)
    out = save(app, panel, monkeypatch)
    assert widths(out) == [100, 202, 101, 102, 103]


def test_a_drag_from_a_closed_or_replaced_b_is_refused(app, panel):
    open_row(app, panel, 0, 4)
    open_b(app, panel, "b.pdf", 3)
    old = panel._b_serial
    open_b(app, panel, "c.pdf", 2)
    panel._on_b_dropped([0], 1, old)
    assert len(panel._pages) == 4 and "no longer shown" in panel.status.text()


def test_b_file_is_never_modified(app, panel, monkeypatch):
    open_row(app, panel, 0, 4)
    bpath = os.path.join(os.path.dirname(panel._cur_path), "b.pdf")
    before = md5(bpath)
    open_b(app, panel, "b.pdf", 3)
    bselect(panel, 0, 1, 2); select(panel, 0)
    panel.b_insert(False)
    save(app, panel, monkeypatch)
    assert md5(bpath) == before


def test_swap_makes_b_the_open_pdf(app, panel, monkeypatch):
    open_row(app, panel, 0, 4)
    open_b(app, panel, "b.pdf", 3)
    panel.swap_ab()
    assert pump(app, lambda: panel._cur_path.endswith("b.pdf") and len(panel._pages) == 3)
    assert pump(app, lambda: "a.pdf" in panel.b_pane.title.text() and panel.b_pane.grid.count() == 4)
    assert panel.file_list.currentRow() == 1


def test_swap_asks_before_dropping_unsaved_changes(app, panel, monkeypatch):
    open_row(app, panel, 0, 4)
    open_b(app, panel, "b.pdf", 3)
    select(panel, 0); panel.delete_pages()
    assert panel._is_dirty()
    asked = []
    monkeypatch.setattr(H.Msg, "question", staticmethod(lambda *a, **k: (asked.append(1), QMessageBox.No)[1]))
    panel.swap_ab()
    assert asked and panel._cur_path.endswith("a.pdf") and len(panel._pages) == 3


def test_swap_needs_both_sides(app, panel):
    open_row(app, panel, 0, 4)
    panel.swap_ab()
    assert "both A and B" in panel.status.text() and panel._cur_path.endswith("a.pdf")


def test_closing_b_clears_and_stops_it(app, panel):
    open_row(app, panel, 0, 4)
    open_b(app, panel, "b.pdf", 3)
    panel.toggle_compare()
    assert not panel.b_pane.isVisible() and panel._b_path == "" and panel.b_pane.grid.count() == 0
    assert panel.compare_btn.text() == "Compare…"


def test_a_missing_or_bad_b_file_is_reported_not_raised(app, panel, tmp_path):
    open_row(app, panel, 0, 4)
    assert not panel.open_b(str(tmp_path / "gone.pdf"))
    assert "not there" in panel.status.text()
    bad = tmp_path / "bad.pdf"; bad.write_bytes(b"not a pdf")
    assert not panel.open_b(str(bad))
    assert panel._b_path == ""


def test_swap_refuses_a_b_from_another_folder(app, panel, tmp_path):
    open_row(app, panel, 0, 4)
    other = mk(tmp_path / "elsewhere.pdf", 500, 2)
    assert panel.open_b(other)
    assert pump(app, lambda: panel.b_pane.grid.count() == 2)
    panel.swap_ab()
    assert "another folder" in panel.status.text()
    assert panel._cur_path.endswith("a.pdf") and panel._b_path.endswith("elsewhere.pdf")


def test_pages_taken_from_b_are_not_rotated(app, panel, monkeypatch):
    from pypdf import PdfReader
    open_row(app, panel, 0, 4)
    open_b(app, panel, "b.pdf", 3)
    bselect(panel, 0); select(panel, 0)
    panel.b_insert(False)
    out = save(app, panel, monkeypatch)
    assert [int(p.get("/Rotate", 0) or 0) % 360 for p in PdfReader(out).pages] == [0] * 5


# ------------------------------------------------------------------ reader with pasted pages
def paste_from_b(app, panel):
    open_row(app, panel, 0, 4)
    open_b(app, panel, "b.pdf", 3)
    bselect(panel, 0); select(panel, 0)
    panel.b_insert(False)
    assert panel._has_pasted()


def test_reader_opens_on_a_preview_when_pages_are_pasted(app, panel):
    paste_from_b(app, panel)
    assert panel.open_reader(0)
    assert pump(app, lambda: panel._reader_active() and panel._preview_path)
    tmp = panel._preview_path
    assert os.path.isfile(tmp) and widths(tmp) == [100, 200, 101, 102, 103]
    panel.close_reader()
    assert not os.path.exists(tmp) and panel._preview_path == ""


def test_preview_reader_refuses_edits_and_leaves_pages_alone(app, panel):
    paste_from_b(app, panel)
    panel.open_reader(0)
    assert pump(app, lambda: panel._reader_active())
    panel.reader.select_positions([1], notify=False)
    panel._reader_delete()
    panel._reader_rotate(90)
    assert len(panel._pages) == 5 and all(p["rot"] == 0 for p in panel._pages) and "read-only" in panel.status.text()
    panel.close_reader()


def test_preview_is_removed_when_another_pdf_is_opened(app, panel, monkeypatch):
    paste_from_b(app, panel)
    panel.open_reader(0)
    assert pump(app, lambda: panel._reader_active())
    tmp = panel._preview_path
    monkeypatch.setattr(panel, "_ask_save", lambda name: QMessageBox.Discard)
    panel.file_list.setCurrentRow(2)
    assert pump(app, lambda: panel._cur_path.endswith("c.pdf") and not os.path.exists(tmp))
    assert panel._preview_path == ""


def test_reader_still_opens_directly_without_pasted_pages(app, panel):
    open_row(app, panel, 0, 4)
    assert panel.open_reader(0) and panel._reader_active() and panel._preview_path == ""
    panel.close_reader()


# ---- dragging PDFs onto B
def _drop(widget, *paths):
    from PySide6.QtCore import QMimeData, QUrl, QPointF, Qt
    from PySide6.QtGui import QDropEvent
    md = QMimeData(); md.setUrls([QUrl.fromLocalFile(p) for p in paths])
    widget.dropEvent(QDropEvent(QPointF(5, 5), Qt.CopyAction, md, Qt.LeftButton, Qt.NoModifier))


def test_dropping_a_pdf_on_b_shows_it_there(app, panel):
    open_row(app, panel, 0, 4)
    b = os.path.join(os.path.dirname(panel._cur_path), "b.pdf")
    panel.b_pane.setVisible(True)
    _drop(panel.b_pane, b)
    assert pump(app, lambda: panel.b_pane.grid.count() == 3)
    assert "b.pdf" in panel.b_pane.title.text()


def test_dropping_a_row_from_the_list_carries_that_file(app, panel):
    open_row(app, panel, 0, 4)
    items = [panel.file_list.item(1)]
    md = panel.file_list.mimeData(items)
    assert [u.toLocalFile() for u in md.urls()] == [panel._row_path(items[0])]
    assert panel.file_list.count() >= 2                       # dragging never removes a row


def test_dropping_something_that_is_not_a_pdf_says_so_and_keeps_b(app, panel, tmp_path):
    open_row(app, panel, 0, 4)
    open_b(app, panel, "b.pdf", 3)
    txt = tmp_path / "x.txt"; txt.write_text("hi")
    _drop(panel.b_pane, str(txt))
    assert "b.pdf" in panel.b_pane.title.text()
    assert "Drop a PDF" in panel.status.text()


def test_dropping_several_pdfs_uses_the_first_and_says_so(app, panel):
    open_row(app, panel, 0, 4)
    d = os.path.dirname(panel._cur_path)
    panel.b_pane.setVisible(True)
    _drop(panel.b_pane, os.path.join(d, "b.pdf"), os.path.join(d, "c.pdf"))
    assert pump(app, lambda: panel.b_pane.grid.count() == 3)
    assert "b.pdf" in panel.b_pane.title.text()
    assert "c.pdf" not in panel.b_pane.title.text() and "not used" in panel.status.text()


def test_the_list_menu_offers_compare(app, panel):
    open_row(app, panel, 0, 4)
    pos = panel.file_list.visualItemRect(panel.file_list.item(1)).center()
    m = panel._build_file_menu(pos)
    assert any("show it in B" in a.text() for a in m.actions())
    assert any("File tray" in a.text() for sub in m.actions() if sub.menu() for a in sub.menu().actions())


def test_b_pane_accepts_a_file_drag_but_not_text(app, panel):
    from PySide6.QtCore import QMimeData, QUrl, QPoint, Qt
    from PySide6.QtGui import QDragEnterEvent
    assert panel.b_pane.acceptDrops()
    md = QMimeData(); md.setUrls([QUrl.fromLocalFile("/x/a.pdf")])
    ev = QDragEnterEvent(QPoint(5, 5), Qt.CopyAction, md, Qt.LeftButton, Qt.NoModifier)
    panel.b_pane.dragEnterEvent(ev)
    assert ev.isAccepted()
    md2 = QMimeData(); md2.setText("hello")
    ev2 = QDragEnterEvent(QPoint(5, 5), Qt.CopyAction, md2, Qt.LeftButton, Qt.NoModifier)
    panel.b_pane.dragEnterEvent(ev2)
    assert not ev2.isAccepted()
