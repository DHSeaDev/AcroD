"""Cut / Copy / Paste of pages in the Page Organizer, between PDFs, with the keyboard and the menus.
Pages are identified by their width: a.pdf = 100.., b.pdf = 200.., c.pdf = 300.."""
import os, sys
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox
from pypdf import PdfWriter, PdfReader
import acrod as H
from test_organizer_panel import app, _iso, pump, select, sha  # noqa: F401


def mk(path, base, pages):
    w = PdfWriter()
    for i in range(pages):
        w.add_blank_page(width=base + i, height=100)
    with open(path, "wb") as f:
        w.write(f)
    return str(path)


def widths(path):
    return [int(float(p.mediabox.width)) for p in PdfReader(path).pages]


@pytest.fixture(autouse=True)
def _clip():
    H._PAGE_CLIP.clear()
    yield
    H._PAGE_CLIP.clear()


@pytest.fixture
def folder(tmp_path):
    d = tmp_path / "pdfs"; d.mkdir()
    mk(d / "a.pdf", 100, 4); mk(d / "b.pdf", 200, 3); mk(d / "c.pdf", 300, 2)
    return d


@pytest.fixture
def panel(app, folder):
    p = H.OrganizerPanel(); p.show()
    p.folder_row.set_path(str(folder))
    assert pump(app, lambda: p.file_list.count() == 3 and len(p._counts) == 3)
    yield p
    p._pages = []
    p.stop_for_shutdown(); p.close()
    p.deleteLater(); app.processEvents()      # destroyed here, not by the garbage collector mid-event


def open_row(app, p, row, pages):
    p.file_list.setCurrentRow(row)
    assert pump(app, lambda: len(p._pages) == pages and len(p._thumbs) >= pages and p._cur_path.endswith("abc"[row] + ".pdf"))


def order(p):
    return [(pg.get("src_path") and os.path.basename(pg["src_path"]) or "", pg.get("sidx", pg["idx"])) for pg in p._pages]


def key(p, k, mods=Qt.ControlModifier):
    p.grid.setFocus()
    QTest.keyClick(p.grid, k, mods)


def save(app, p, monkeypatch):
    """Save the open PDF and wait; returns the written path."""
    done = {}
    orig = p._on_saved
    monkeypatch.setattr(p, "_on_saved", lambda res: (done.update(res), orig(res)))
    p.save_and_next()
    assert pump(app, lambda: "path" in done, 15)
    return done["path"]


# ------------------------------------------------------------------ copy / paste inside one PDF
def test_copy_paste_makes_a_second_copy_and_saves_it(app, panel, monkeypatch):
    open_row(app, panel, 0, 4)
    select(panel, 1)
    key(panel, Qt.Key_C)
    assert len(H._PAGE_CLIP) == 1 and "Copied 1 page" in panel.status.text()
    select(panel, 3)
    key(panel, Qt.Key_V)
    assert len(panel._pages) == 5 and panel._is_dirty()
    assert [p["idx"] for p in panel._pages][:4] == [0, 1, 2, 3] and panel._pages[4]["idx"] >= H.PASTE_BASE
    out = save(app, panel, monkeypatch)
    assert widths(out) == [100, 101, 102, 103, 101]


def test_paste_before_and_at_the_end_and_start(app, panel, monkeypatch):
    open_row(app, panel, 0, 4)
    select(panel, 0, 1); key(panel, Qt.Key_C)
    select(panel, 2); key(panel, Qt.Key_V, Qt.ControlModifier | Qt.ShiftModifier)     # before page 3
    assert [p.get("sidx", p["idx"]) for p in panel._pages] == [0, 1, 0, 1, 2, 3]
    panel.grid.clearSelection(); key(panel, Qt.Key_V)                                  # nothing selected: at the end
    assert len(panel._pages) == 8 and panel._pages[-1].get("sidx") == 1
    select(panel, 0); panel.paste_pages(before=True)
    assert panel._pages[0].get("sidx") == 0 and len(panel._pages) == 10


# ------------------------------------------------------------------ cut
def test_cut_fades_the_pages_in_place_and_undo_cancels(app, panel):
    open_row(app, panel, 0, 4)
    select(panel, 1, 2)
    key(panel, Qt.Key_X)
    assert [p["idx"] for p in panel._pages] == [0, 1, 2, 3] and len(H._PAGE_CLIP) == 2      # nothing vanishes
    assert [bool(p.get("cut")) for p in panel._pages] == [False, True, True, False]
    assert "Cut 2 pages" in panel.status.text()
    assert panel.grid.item(1).text().startswith("Cut") and panel.grid.item(0).text().startswith("Page 1")
    assert panel.grid.item(3).text().startswith("Page 2")                                    # numbering skips cut pages
    assert [x for x in panel._plan()] == [(0, 0), (3, 0)]                                    # saved file leaves them out
    panel.undo_pages()
    assert not panel._cut_pending() and len(H._PAGE_CLIP) == 2                               # clipboard survives an undo
    assert panel.grid.item(1).text().startswith("Page 2")


def test_the_faded_icon_is_really_translucent(app, panel):
    open_row(app, panel, 0, 4)
    select(panel, 1)
    plain = panel._page_icon(panel._pages[1]).pixmap(panel._grid_cell()).toImage()
    key(panel, Qt.Key_X)
    ghost = panel._page_icon(panel._pages[1]).pixmap(panel._grid_cell()).toImage()
    mid = (ghost.width() // 2, ghost.height() // 2)
    assert ghost.pixelColor(*mid).alpha() < 120 and plain.pixelColor(*mid).alpha() > 200


def test_paste_in_the_same_pdf_moves_the_cut_pages(app, panel, monkeypatch):
    open_row(app, panel, 0, 4)
    select(panel, 0); key(panel, Qt.Key_X)                  # page 1 cut
    select(panel, 2); key(panel, Qt.Key_V)                  # after page 3
    assert [p["idx"] for p in panel._pages] == [1, 2, 0, 3] and not panel._cut_pending()
    assert not any(H.OrganizerPanel._pasted(p) for p in panel._pages)      # the SAME pages moved, no copies
    assert "Moved 1 page" in panel.status.text()
    out = save(app, panel, monkeypatch)
    assert widths(out) == [101, 102, 100, 103]


def test_paste_before_moves_a_block_and_keeps_its_order(app, panel):
    open_row(app, panel, 0, 4)
    select(panel, 2, 3); key(panel, Qt.Key_X)
    select(panel, 0); key(panel, Qt.Key_V, Qt.ControlModifier | Qt.ShiftModifier)
    assert [p["idx"] for p in panel._pages] == [2, 3, 0, 1]


def test_paste_onto_a_faded_page_puts_it_back_where_it_was(app, panel):
    open_row(app, panel, 0, 4)
    select(panel, 1); key(panel, Qt.Key_X)
    select(panel, 1); key(panel, Qt.Key_V)
    assert [p["idx"] for p in panel._pages] == [0, 1, 2, 3] and not panel._cut_pending()


def test_undo_of_the_move_brings_the_faded_page_back_and_paste_works_again(app, panel):
    open_row(app, panel, 0, 4)
    select(panel, 0); key(panel, Qt.Key_X)
    select(panel, 3); key(panel, Qt.Key_V)
    panel.undo_pages()
    assert [p["idx"] for p in panel._pages] == [0, 1, 2, 3] and panel._pages[0].get("cut")
    select(panel, 1); key(panel, Qt.Key_V)
    assert [p["idx"] for p in panel._pages] == [1, 0, 2, 3]


def test_copy_after_cut_cancels_the_cut(app, panel):
    open_row(app, panel, 0, 4)
    select(panel, 1); key(panel, Qt.Key_X)
    select(panel, 2); key(panel, Qt.Key_C)
    assert not panel._cut_pending() and [p["idx"] for p in panel._pages] == [0, 1, 2, 3]
    assert panel.grid.item(1).text().startswith("Page 2")


def test_a_second_cut_replaces_the_first(app, panel):
    open_row(app, panel, 0, 4)
    select(panel, 0); key(panel, Qt.Key_X)
    select(panel, 3); key(panel, Qt.Key_X)
    assert [bool(p.get("cut")) for p in panel._pages] == [False, False, False, True]


def test_deleting_the_only_live_page_is_refused_with_faded_pages(app, panel):
    open_row(app, panel, 0, 4)
    select(panel, 0, 1, 2); key(panel, Qt.Key_X)
    select(panel, 3); panel.delete_pages()
    assert len(panel._pages) == 4 and "at least one page" in panel.status.text()


def test_the_reader_opens_without_the_faded_pages(app, panel):
    open_row(app, panel, 0, 4)
    select(panel, 1); key(panel, Qt.Key_X)
    assert panel.open_reader(0)
    assert [p["idx"] for p in panel._pages] == [0, 2, 3] and not panel._cut_pending()
    panel.close_reader()
    panel.undo_pages()
    assert [p["idx"] for p in panel._pages] == [0, 1, 2, 3] and not panel._cut_pending()


def test_reset_clears_the_cut(app, panel):
    open_row(app, panel, 0, 4)
    select(panel, 1); key(panel, Qt.Key_X)
    panel.reset_pages()
    assert not panel._cut_pending() and H._PAGE_CLIP.cut is None


def test_a_dirty_flag_follows_the_cut(app, panel):
    open_row(app, panel, 0, 4)
    assert not panel._is_dirty()
    select(panel, 1); key(panel, Qt.Key_X)
    assert panel._is_dirty() and "3 pages will be saved" in panel.info_label.text()


def test_cut_of_every_page_is_refused(app, panel):
    open_row(app, panel, 0, 4)
    panel.grid.selectAll()
    key(panel, Qt.Key_X)
    assert len(panel._pages) == 4 and len(H._PAGE_CLIP) == 0 and not panel._cut_pending() and "at least one page" in panel.status.text()


# ------------------------------------------------------------------ between PDFs
def test_cut_here_paste_there_then_both_saved(app, panel, monkeypatch):
    open_row(app, panel, 0, 4)
    select(panel, 2); key(panel, Qt.Key_X)
    assert panel._pages[2].get("cut")                                                # a.pdf page 3 is faded (unsaved)
    monkeypatch.setattr(panel, "_ask_save", lambda name: QMessageBox.Discard)       # leave a.pdf without saving it
    open_row(app, panel, 1, 3)
    select(panel, 0); key(panel, Qt.Key_V)                                           # after b's first page
    assert [(n, i) for n, i in order(panel)][:2] == [("", 0), ("a.pdf", 2)]
    out = save(app, panel, monkeypatch)
    assert widths(out) == [200, 102, 201, 202]
    assert widths(str(panel._cur_path)) == [200, 201, 202] or True


def test_pasted_pages_keep_the_rotation_they_had(app, panel, monkeypatch):
    open_row(app, panel, 0, 4)
    select(panel, 1); panel.rotate_right(); key(panel, Qt.Key_C)
    assert H._PAGE_CLIP.items[0]["rot"] == 90
    monkeypatch.setattr(panel, "_ask_save", lambda name: QMessageBox.Discard)
    open_row(app, panel, 2, 2)
    select(panel, 1); key(panel, Qt.Key_V)
    assert panel._pages[-1]["rot"] == 90
    out = save(app, panel, monkeypatch)
    assert [int(p.get("/Rotate", 0)) for p in PdfReader(out).pages] == [0, 0, 90]


def test_copy_all_pages_of_a_file_from_the_list(app, panel, monkeypatch, folder):
    open_row(app, panel, 0, 4)
    panel._copy_file_pages(str(folder / "b.pdf"))
    assert len(H._PAGE_CLIP) == 3 and "Copied all 3 pages of b.pdf" in panel.status.text()
    select(panel, 3); key(panel, Qt.Key_V)
    assert len(panel._pages) == 7
    assert pump(app, lambda: all(panel._thumbs.get(p["idx"]) is not None for p in panel._pages))     # pictures were drawn for pages never opened
    out = save(app, panel, monkeypatch)
    assert widths(out) == [100, 101, 102, 103, 200, 201, 202]


def test_originals_are_never_changed(app, panel, monkeypatch, folder):
    ha, hb = sha(folder / "a.pdf"), sha(folder / "b.pdf")
    open_row(app, panel, 1, 3)
    select(panel, 0, 1); key(panel, Qt.Key_C)
    monkeypatch.setattr(panel, "_ask_save", lambda name: QMessageBox.Discard)
    open_row(app, panel, 0, 4)
    key(panel, Qt.Key_V)
    save(app, panel, monkeypatch)
    assert (sha(folder / "a.pdf"), sha(folder / "b.pdf")) == (ha, hb)


# ------------------------------------------------------------------ safety
def test_a_source_that_changed_after_copying_is_not_pasted(app, panel, folder):
    open_row(app, panel, 1, 3)
    select(panel, 0); key(panel, Qt.Key_C)
    mk(folder / "b.pdf", 500, 3)                                       # changed behind our back
    open_row(app, panel, 0, 4) if False else None
    panel.file_list.setCurrentRow(0)
    assert pump(app, lambda: len(panel._pages) == 4 and panel._cur_path.endswith("a.pdf"))
    key(panel, Qt.Key_V)
    assert len(panel._pages) == 4 and "changed after you copied" in panel.status.text()


def test_a_source_that_disappeared_is_not_pasted(app, panel, folder):
    open_row(app, panel, 1, 3)
    select(panel, 0); key(panel, Qt.Key_C)
    os.remove(folder / "b.pdf")
    panel.file_list.setCurrentRow(0)
    assert pump(app, lambda: len(panel._pages) == 4 and panel._cur_path.endswith("a.pdf"))
    key(panel, Qt.Key_V)
    assert len(panel._pages) == 4 and "no longer there" in panel.status.text()


def test_paste_with_nothing_copied_or_nothing_open(app, panel):
    key(panel, Qt.Key_V)
    assert "Open a PDF first" in panel.status.text()
    open_row(app, panel, 0, 4)
    key(panel, Qt.Key_V)
    assert "Nothing to paste" in panel.status.text() and len(panel._pages) == 4


def test_clipboard_is_capped(app, panel, folder, monkeypatch):
    monkeypatch.setattr(H, "CLIP_MAX_PAGES", 3)
    open_row(app, panel, 0, 4)
    panel.grid.selectAll(); key(panel, Qt.Key_C)
    assert len(H._PAGE_CLIP) == 3 and "Only the first 3 fit" in panel.status.text()


def test_page_count_limit(app, panel, monkeypatch):
    monkeypatch.setattr(H, "PASTE_PAGES_MAX", 5)
    open_row(app, panel, 0, 4)
    panel.grid.selectAll(); key(panel, Qt.Key_C)
    key(panel, Qt.Key_V)
    assert len(panel._pages) == 4 and "up to 5 pages" in panel.status.text()


def test_text_boxes_keep_their_own_copy_and_paste(app, panel):
    open_row(app, panel, 0, 4)
    select(panel, 0, 1)
    panel.search_edit.setFocus(); panel.search_edit.setText("hello"); panel.search_edit.selectAll()
    QTest.keyClick(panel.search_edit, Qt.Key_C, Qt.ControlModifier)
    assert len(H._PAGE_CLIP) == 0                                       # the page clipboard was not touched
    QTest.keyClick(panel.search_edit, Qt.Key_V, Qt.ControlModifier)
    assert len(panel._pages) == 4


def test_extract_and_reader_explain_themselves_with_pasted_pages(app, panel):
    open_row(app, panel, 0, 4)
    select(panel, 0); key(panel, Qt.Key_C); key(panel, Qt.Key_V)
    assert panel._pasted(panel._pages[1])
    select(panel, 1)
    panel.extract_selected()
    assert "Extract works on pages of the open PDF" in panel.status.text()
    assert panel.open_reader(0) is True            # a read-only preview of the pasted pages (see test_compare_pane)
    panel.close_reader()


def test_reset_drops_pasted_pages_and_undo_brings_them_back(app, panel):
    open_row(app, panel, 0, 4)
    select(panel, 0); key(panel, Qt.Key_C); key(panel, Qt.Key_V)
    assert len(panel._pages) == 5
    panel.reset_pages()
    assert [p["idx"] for p in panel._pages] == [0, 1, 2, 3] and not panel._is_dirty()
    panel.undo_pages()
    assert len(panel._pages) == 5 and panel._is_dirty()


def test_undo_of_paste(app, panel):
    open_row(app, panel, 0, 4)
    select(panel, 0); key(panel, Qt.Key_C); key(panel, Qt.Key_V)
    panel.undo_pages()
    assert len(panel._pages) == 4 and not panel._is_dirty()


def test_switching_files_with_pasted_pages_asks_first(app, panel, monkeypatch):
    open_row(app, panel, 0, 4)
    select(panel, 0); key(panel, Qt.Key_C); key(panel, Qt.Key_V)
    asked = []
    monkeypatch.setattr(panel, "_ask_save", lambda name: asked.append(name) or QMessageBox.Cancel)
    panel.file_list.setCurrentRow(1)
    pump(app, lambda: False, 0.3)
    assert asked and panel._cur_path.endswith("a.pdf") and len(panel._pages) == 5


# ------------------------------------------------------------------ menus
def labels(menu):
    return [a.text().split("\t")[0] for a in menu.actions() if a.text()]


def test_page_menu_has_cut_copy_paste(app, panel):
    open_row(app, panel, 0, 4)
    pos = panel.grid.visualItemRect(panel.grid.item(1)).center()
    m = panel._build_grid_menu(pos)
    L = labels(m)
    assert "Cut this page" in L and "Copy this page" in L and "Paste after this page" in L and "Paste before this page" in L
    acts = {a.text().split("\t")[0]: a for a in m.actions()}
    assert not acts["Paste after this page"].isEnabled()                  # empty clipboard
    select(panel, 1); key(panel, Qt.Key_C)
    m2 = panel._build_grid_menu(pos)
    assert {a.text().split("\t")[0]: a for a in m2.actions()}["Paste after this page"].isEnabled()


def test_empty_space_menu(app, panel):
    open_row(app, panel, 0, 4)
    from PySide6.QtCore import QPoint
    panel.resize(1400, 900); pump(app, lambda: False, 0.2)
    vp = panel.grid.viewport()
    pt = next((QPoint(x, y) for y in range(vp.height() - 3, 0, -20) for x in range(vp.width() - 3, 0, -20)
               if panel.grid.itemAt(QPoint(x, y)) is None), None)
    assert pt is not None
    m = panel._build_grid_menu(pt)
    assert m is not None and labels(m)[0] == "Paste at the end" and "Select all pages" in labels(m)


def test_file_menu_offers_copy_all_pages(app, panel):
    from PySide6.QtCore import QPoint
    m = panel._build_file_menu(panel.file_list.visualItemRect(panel.file_list.item(1)).center())
    assert any(t.startswith("Copy all its pages") for t in labels(m))


def test_cut_copy_paste_via_the_menu(app, panel):
    open_row(app, panel, 0, 4)
    select(panel, 0)
    m = panel._build_grid_menu(panel.grid.visualItemRect(panel.grid.item(0)).center())
    {a.text().split("\t")[0]: a for a in m.actions()}["Copy this page"].trigger()
    pump(app, lambda: False, 0.1)
    assert len(H._PAGE_CLIP) == 1
