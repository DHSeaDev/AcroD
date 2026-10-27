"""Bringing pages in from other PDFs in the Page Organizer: PDF files dragged onto the page grid, PDFs copied in
Explorer and pasted, and the menu. Pages are identified by width (a.pdf = 100.., b.pdf = 200.., c.pdf = 300..)."""
import os, sys
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import pytest
from PySide6.QtCore import Qt, QMimeData, QUrl, QPointF
from PySide6.QtGui import QDropEvent, QDragEnterEvent, QDragMoveEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QFileDialog
from pypdf import PdfWriter
import acrod as H
from test_organizer_panel import app, pump, select  # noqa: F401
from test_page_clipboard import mk, widths, folder, panel, open_row, save, key, _clip  # noqa: F401


def urls_mime(*paths):
    md = QMimeData()
    md.setUrls([QUrl.fromLocalFile(str(p)) for p in paths])
    return md


def drag(grid, md, pos, kind=QDropEvent):
    ev = kind(QPointF(pos), Qt.CopyAction, md, Qt.LeftButton, Qt.NoModifier) if kind is QDropEvent \
        else kind(pos, Qt.CopyAction, md, Qt.LeftButton, Qt.NoModifier)
    QApplication.sendEvent(grid.viewport() if kind is not QDragEnterEvent else grid, ev)
    return ev


def drop_files_on_grid(p, md, pos):
    """Enter, move, then drop through the grid's own handlers, as the window system would."""
    g = p.grid
    enter = QDragEnterEvent(pos, Qt.CopyAction, md, Qt.LeftButton, Qt.NoModifier)
    g.dragEnterEvent(enter)
    move = QDragMoveEvent(pos, Qt.CopyAction, md, Qt.LeftButton, Qt.NoModifier)
    g.dragMoveEvent(move)
    shown = g._drop_at
    ev = QDropEvent(QPointF(pos), Qt.CopyAction, md, Qt.LeftButton, Qt.NoModifier)
    g.dropEvent(ev)
    return enter, move, ev, shown


def gap_pos(g, row, right=False):
    r = g.visualItemRect(g.item(row))
    return r.center() + (type(r.center())(r.width() // 3, 0) if right else type(r.center())(-(r.width() // 3), 0))


def wait_thumbs(app, p):
    assert pump(app, lambda: all(p._thumbs.get(pg["idx"]) is not None for pg in p._pages))


# ------------------------------------------------------------------ dragging a PDF file onto the pages
def test_dropping_a_pdf_file_inserts_all_its_pages_at_the_gap(app, panel, folder, monkeypatch):
    open_row(app, panel, 0, 4)
    md = urls_mime(folder / "b.pdf")
    enter, move, ev, shown = drop_files_on_grid(panel, md, gap_pos(panel.grid, 2))   # left half of page 3 = gap before it
    assert enter.isAccepted() and move.isAccepted() and ev.isAccepted()
    assert shown == 2                                                                # the gold bar was shown there
    assert panel.grid._drop_at is None
    assert len(panel._pages) == 7 and panel._is_dirty()
    out = save(app, panel, monkeypatch)
    assert widths(out) == [100, 101, 200, 201, 202, 102, 103]


def test_drop_on_the_right_half_goes_after_that_page(app, panel, folder, monkeypatch):
    open_row(app, panel, 0, 4)
    drop_files_on_grid(panel, urls_mime(folder / "c.pdf"), gap_pos(panel.grid, 3, right=True))
    out = save(app, panel, monkeypatch)
    assert widths(out) == [100, 101, 102, 103, 300, 301]


def test_several_files_go_in_in_the_order_dropped(app, panel, folder, monkeypatch):
    open_row(app, panel, 0, 4)
    drop_files_on_grid(panel, urls_mime(folder / "c.pdf", folder / "b.pdf"), gap_pos(panel.grid, 0))
    out = save(app, panel, monkeypatch)
    assert widths(out) == [300, 301, 200, 201, 202, 100, 101, 102, 103]


def test_a_dropped_file_shows_pictures_and_never_changes_the_source(app, panel, folder):
    open_row(app, panel, 0, 4)
    src = folder / "b.pdf"
    before = src.read_bytes()
    drop_files_on_grid(panel, urls_mime(src), gap_pos(panel.grid, 0))
    wait_thumbs(app, panel)
    assert src.read_bytes() == before
    assert "Pasted 3 pages" in panel.status.text()


def test_undo_takes_a_dropped_file_back_out(app, panel, folder):
    open_row(app, panel, 0, 4)
    drop_files_on_grid(panel, urls_mime(folder / "b.pdf"), gap_pos(panel.grid, 1))
    assert len(panel._pages) == 7
    panel.undo_pages()
    assert len(panel._pages) == 4 and not panel._is_dirty()


def test_the_open_file_can_be_dropped_onto_itself(app, panel, folder, monkeypatch):
    open_row(app, panel, 0, 4)
    drop_files_on_grid(panel, urls_mime(folder / "a.pdf"), gap_pos(panel.grid, 3, right=True))
    out = save(app, panel, monkeypatch)
    assert widths(out) == [100, 101, 102, 103, 100, 101, 102, 103]


def test_a_drop_with_no_pdf_in_it_is_left_to_the_panel(app, panel, folder, tmp_path):
    open_row(app, panel, 0, 4)
    note = tmp_path / "note.txt"; note.write_text("x")
    g = panel.grid
    for md in (urls_mime(note), urls_mime(folder)):
        ev = QDragEnterEvent(gap_pos(g, 0), Qt.CopyAction, md, Qt.LeftButton, Qt.NoModifier)
        g.dragEnterEvent(ev)
        assert not ev.isAccepted()
    txt = QMimeData(); txt.setText("hello")
    ev = QDragEnterEvent(gap_pos(g, 0), Qt.CopyAction, txt, Qt.LeftButton, Qt.NoModifier)
    g.dragEnterEvent(ev)
    assert not ev.isAccepted() and len(panel._pages) == 4


def test_with_nothing_open_a_dropped_file_is_left_to_the_panel_to_open(app, panel, folder):
    assert not panel._pages
    md = urls_mime(folder / "b.pdf")                  # the event only points at it: keep it alive
    ev = QDragEnterEvent(panel.grid.rect().center(), Qt.CopyAction, md, Qt.LeftButton, Qt.NoModifier)
    panel.grid.dragEnterEvent(ev)
    assert not ev.isAccepted()


def test_a_bad_file_among_good_ones_is_named_and_the_good_ones_still_go_in(app, panel, folder, tmp_path):
    open_row(app, panel, 0, 4)
    bad = tmp_path / "bad.pdf"; bad.write_bytes(b"this is not a pdf at all")
    drop_files_on_grid(panel, urls_mime(bad, folder / "c.pdf"), gap_pos(panel.grid, 0))
    assert len(panel._pages) == 6
    assert "bad.pdf" in panel.status.text()


def test_only_bad_files_insert_nothing(app, panel, tmp_path):
    open_row(app, panel, 0, 4)
    bad = tmp_path / "bad.pdf"; bad.write_bytes(b"nope")
    panel.insert_pdf_files([str(bad), str(tmp_path / "gone.pdf")], at=0)
    assert len(panel._pages) == 4 and "Nothing was inserted" in panel.status.text()


def test_a_password_protected_pdf_is_refused_with_its_name(app, panel, tmp_path):
    open_row(app, panel, 0, 4)
    locked = tmp_path / "locked.pdf"
    w = PdfWriter(); w.add_blank_page(width=500, height=100); w.encrypt("secret")
    with open(locked, "wb") as f:
        w.write(f)
    panel.insert_pdf_files([str(locked)], at=0)
    assert len(panel._pages) == 4 and "locked.pdf" in panel.status.text()


def test_a_big_file_is_cut_at_the_limit_and_says_so(app, panel, tmp_path, monkeypatch):
    open_row(app, panel, 0, 4)
    monkeypatch.setattr(H, "CLIP_MAX_PAGES", 5)
    big = mk(tmp_path / "big.pdf", 400, 9)
    panel.insert_pdf_files([big], at=4)
    assert len(panel._pages) == 9 and "only the first 5 of 9 pages" in panel.status.text()


def test_the_page_limit_is_respected(app, panel, folder, monkeypatch):
    open_row(app, panel, 0, 4)
    monkeypatch.setattr(H, "PASTE_PAGES_MAX", 5)
    panel.insert_pdf_files([str(folder / "b.pdf")], at=0)
    assert len(panel._pages) == 4 and "up to 5 pages" in panel.status.text()


def test_a_file_that_changed_under_us_is_not_inserted(app, panel, folder, monkeypatch):
    open_row(app, panel, 0, 4)
    real = H.source_stamp
    seen = {"n": 0}

    def flip(path):
        st = real(path)
        if os.path.basename(path) == "b.pdf":
            seen["n"] += 1
            return (st[0] + seen["n"], st[1])         # differs on the second look (inside paste_pages)
        return st
    monkeypatch.setattr(H, "source_stamp", flip)
    panel.insert_pdf_files([str(folder / "b.pdf")], at=0)
    assert len(panel._pages) == 4 and "changed after" in panel.status.text()


def test_drops_are_ignored_while_busy(app, panel, folder):
    open_row(app, panel, 0, 4)
    panel._busy = True
    try:
        panel.insert_pdf_files([str(folder / "b.pdf")], at=0)
    finally:
        panel._busy = False
    assert len(panel._pages) == 4


# ------------------------------------------------------------------ PDFs copied in Explorer, pasted with Ctrl+V
def explorer_copy(*paths):
    QApplication.clipboard().setMimeData(urls_mime(*paths))


def test_pdf_copied_in_explorer_pastes_all_its_pages(app, panel, folder, monkeypatch):
    open_row(app, panel, 0, 4)
    explorer_copy(folder / "b.pdf")
    select(panel, 1)
    key(panel, Qt.Key_V)
    out = save(app, panel, monkeypatch)
    assert widths(out) == [100, 101, 200, 201, 202, 102, 103]


def test_explorer_paste_before_the_selected_page(app, panel, folder, monkeypatch):
    open_row(app, panel, 0, 4)
    explorer_copy(folder / "c.pdf")
    select(panel, 0)
    key(panel, Qt.Key_V, Qt.ControlModifier | Qt.ShiftModifier)
    out = save(app, panel, monkeypatch)
    assert widths(out) == [300, 301, 100, 101, 102, 103]


def test_pages_copied_in_the_app_win_over_an_older_explorer_copy(app, panel, folder, monkeypatch):
    open_row(app, panel, 0, 4)
    explorer_copy(folder / "b.pdf")
    select(panel, 2)
    key(panel, Qt.Key_C)                              # copying pages marks the clipboard as ours
    select(panel, 0)
    key(panel, Qt.Key_V)
    out = save(app, panel, monkeypatch)
    assert widths(out) == [100, 102, 101, 102, 103]


def test_an_explorer_copy_after_copying_pages_wins(app, panel, folder, monkeypatch):
    open_row(app, panel, 0, 4)
    select(panel, 2)
    key(panel, Qt.Key_C)
    explorer_copy(folder / "c.pdf")
    select(panel, 3)
    key(panel, Qt.Key_V)
    out = save(app, panel, monkeypatch)
    assert widths(out) == [100, 101, 102, 103, 300, 301]


def test_other_clipboard_content_falls_back_to_the_copied_pages(app, panel, folder):
    open_row(app, panel, 0, 4)
    select(panel, 1)
    key(panel, Qt.Key_C)
    QApplication.clipboard().setText("something typed elsewhere")      # replaces our mark, holds no files
    select(panel, 3)
    key(panel, Qt.Key_V)
    assert len(panel._pages) == 5


def test_non_pdf_files_on_the_clipboard_are_not_pasted(app, panel, tmp_path):
    open_row(app, panel, 0, 4)
    f = tmp_path / "x.txt"; f.write_text("x")
    explorer_copy(f)
    assert H.clipboard_pdf_paths() == []
    key(panel, Qt.Key_V)
    assert len(panel._pages) == 4 and "Nothing to paste" in panel.status.text()


def test_b_copy_and_file_menu_copy_mark_the_clipboard_as_pages(app, panel, folder):
    open_row(app, panel, 0, 4)
    explorer_copy(folder / "b.pdf")
    panel._copy_file_pages(str(folder / "c.pdf"))
    assert H.clipboard_pdf_paths() == [] and len(H._PAGE_CLIP) == 2


# ------------------------------------------------------------------ menus
def test_menus_offer_insert_from_a_pdf_and_paste_follows_the_clipboard(app, panel, folder):
    open_row(app, panel, 0, 4)
    g = panel.grid
    pos = g.visualItemRect(g.item(1)).center()
    texts = lambda m: {a.text().split("\t")[0]: a for a in m.actions()}
    page = texts(panel._build_grid_menu(pos))
    assert "Insert pages from a PDF file after this page…" in page and not page["Paste after this page"].isEnabled()
    empty = texts(panel._build_empty_menu())
    assert "Insert pages from a PDF file…" in empty and not empty["Paste at the end"].isEnabled()
    explorer_copy(folder / "b.pdf")
    assert texts(panel._build_grid_menu(pos))["Paste after this page"].isEnabled()
    assert texts(panel._build_empty_menu())["Paste at the end"].isEnabled()


def test_menu_pick_inserts_after_the_selected_page(app, panel, folder, monkeypatch):
    open_row(app, panel, 0, 4)
    monkeypatch.setattr(QFileDialog, "getOpenFileNames",
                        staticmethod(lambda *a, **k: ([str(folder / "c.pdf")], "")))
    select(panel, 2)
    panel.insert_from_files()
    assert widths(save(app, panel, monkeypatch)) == [100, 101, 102, 300, 301, 103]


def test_cancelling_the_file_dialog_changes_nothing(app, panel, monkeypatch):
    open_row(app, panel, 0, 4)
    monkeypatch.setattr(QFileDialog, "getOpenFileNames", staticmethod(lambda *a, **k: ([], "")))
    panel.insert_from_files()
    assert len(panel._pages) == 4 and not panel._is_dirty()


# ------------------------------------------------------------------ the whole round trip across files
def test_drag_pages_from_b_cut_one_paste_it_elsewhere_then_save_both(app, panel, folder, monkeypatch):
    """Drop a whole PDF, cut two of its pages, open another PDF, paste them: both saved files are right."""
    open_row(app, panel, 0, 4)
    drop_files_on_grid(panel, urls_mime(folder / "b.pdf"), gap_pos(panel.grid, 3, right=True))   # a: 100 101 102 103 200 201 202
    select(panel, 4, 5)
    key(panel, Qt.Key_X)                                                                          # cut 200 201
    out_a = save(app, panel, monkeypatch)
    assert widths(out_a) == [100, 101, 102, 103, 202]
    panel.file_list.setCurrentRow(2)
    assert pump(app, lambda: panel._cur_path.endswith("c.pdf") and len(panel._pages) == 2 and len(panel._thumbs) >= 2)
    select(panel, 0)
    key(panel, Qt.Key_V)
    assert widths(save(app, panel, monkeypatch)) == [300, 200, 201, 301]
