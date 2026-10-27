"""The Page Organizer's full-size reader, through the real widgets: how it opens,
that it shows (and edits) the SAME page list as the grid, what it draws, and that
it shuts its threads down."""
import os, sys, time
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import pytest
from PySide6.QtCore import Qt, QPoint, QEvent
from PySide6.QtGui import QKeyEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox
from pypdf import PdfWriter
import acrod as H
from test_organizer_panel import app, _iso, pump, sha, select  # noqa: F401
from test_organizer_engine import widths, rots
from test_reader_engine import make_text_pdf


@pytest.fixture
def folder(tmp_path):
    d = tmp_path / "pdfs"
    d.mkdir()
    make_text_pdf(str(d / "a.pdf"), pages=5, text="ALPHA")
    make_text_pdf(str(d / "b.pdf"), pages=3, text="BRAVO")
    make_text_pdf(str(d / "c.pdf"), pages=2, text="CHARLIE")
    return d


@pytest.fixture
def panel(app, folder):
    p = H.OrganizerPanel()
    p.resize(1200, 800)
    p.show()
    p.folder_row.set_path(str(folder))
    assert pump(app, lambda: p.file_list.count() == 3 and len(p._counts) == 3)
    yield p
    p.stop_for_shutdown()
    p.stop_background()
    p.close()


def open_row(app, p, row, pages):
    p.file_list.setCurrentRow(row)
    assert pump(app, lambda: len(p._pages) == pages and len(p._thumbs) == pages)


def read_row(app, p, row, pages):
    """Open a PDF the way a person does: select it, then double-click it."""
    open_row(app, p, row, pages)
    p._on_file_double_clicked(p.file_list.item(row))
    assert p._reader_active()
    assert pump(app, lambda: p.reader.is_loaded())
    return p.reader


def drawn(app, r, pages=1):
    assert pump(app, lambda: len([k for k in r.canvas._cache._d]) >= pages)


def key(widget, k, mods=Qt.NoModifier):
    QApplication.sendEvent(widget, QKeyEvent(QEvent.KeyPress, k, mods))


# ------------------------------------------------------------------ opening
def test_double_click_a_pdf_opens_the_reader_on_that_pdf(app, panel, folder):
    r = read_row(app, panel, 0, 5)
    assert panel.stack.currentWidget() is r
    assert r.title.text() == "a.pdf"
    assert r.canvas.count() == 5
    assert r.canvas.current_page() == 0


def test_double_click_through_the_real_signal(app, panel):
    open_row(app, panel, 1, 3)
    panel.file_list.itemDoubleClicked.emit(panel.file_list.item(1))
    assert panel._reader_active()


def test_double_click_a_page_opens_at_that_page(app, panel):
    open_row(app, panel, 0, 5)
    panel.grid.itemDoubleClicked.emit(panel.grid.item(3))
    assert panel._reader_active()
    assert pump(app, lambda: panel.reader.is_loaded() and panel.reader.current_page() == 3)


def test_enter_opens_from_the_list_and_from_the_grid(app, panel):
    open_row(app, panel, 0, 5)
    key(panel.file_list, Qt.Key_Return)
    assert panel._reader_active()
    panel.close_reader()
    select(panel, 2)
    key(panel.grid, Qt.Key_Enter)
    assert panel._reader_active()
    assert pump(app, lambda: panel.reader.is_loaded() and panel.reader.current_page() == 2)


def test_ctrl_enter_is_still_save_not_reader(app, panel):
    open_row(app, panel, 0, 5)
    key(panel.grid, Qt.Key_Return, Qt.ControlModifier)
    assert not panel._reader_active()


def test_double_click_on_a_file_that_was_not_opened_does_nothing(app, panel):
    open_row(app, panel, 0, 5)
    panel._on_file_double_clicked(panel.file_list.item(1))          # b.pdf is not the open PDF
    assert not panel._reader_active()


def test_nothing_to_read_means_no_reader(app, panel):
    assert panel.open_reader() is False
    assert panel.reader is None


def test_the_reader_draws_real_page_pixels(app, panel):
    r = read_row(app, panel, 0, 5)
    drawn(app, r)
    img = next(iter(r.canvas._cache._d.values()))[0]
    assert img.width() > 100 and img.height() > 100
    dark = sum(1 for x in range(0, img.width(), 3) for y in range(0, img.height(), 3)
               if img.pixelColor(x, y).lightness() < 100)
    assert dark > 20                                                # the text is on it, not a blank page
    snap = r.grab()
    assert not snap.isNull()


def test_it_opens_with_the_folder_search_word_highlighted(app, panel):
    panel.search_edit.setText("alpha")
    panel._search_text = "alpha"
    open_row(app, panel, 0, 5)
    panel._on_file_double_clicked(panel.file_list.item(0))
    assert pump(app, lambda: panel.reader.canvas._hits and len(panel.reader.canvas._hit_list()) == 5)
    assert panel.reader.find_edit.text() == "alpha"


# ------------------------------------------------------------------ same list as the grid
def test_reader_shows_the_plan_not_the_file_on_disk(app, panel):
    open_row(app, panel, 0, 5)
    select(panel, 1); panel.move_later()          # order 0,2,1,3,4
    select(panel, 0); panel.rotate_right()
    select(panel, 4); panel.delete_pages()
    panel.open_reader()
    assert pump(app, lambda: panel.reader.is_loaded())
    assert panel.reader.canvas.pages() == [(0, 90), (2, 0), (1, 0), (3, 0)]


def test_reader_rotate_changes_the_same_plan(app, panel):
    r = read_row(app, panel, 0, 5)
    r.select_positions([1])
    r.rotateRequested.emit(90)
    assert panel._plan()[1] == (1, 90) and panel._is_dirty()
    assert r.canvas.pages()[1] == (1, 90)
    r.rotateRequested.emit(-90)
    assert panel._plan()[1] == (1, 0)


def test_with_nothing_selected_the_tools_act_on_the_page_on_screen(app, panel):
    r = read_row(app, panel, 0, 5)
    r.go_to(2)
    assert pump(app, lambda: r.current_page() == 2)
    r.rotateRequested.emit(90)
    assert [p["rot"] for p in panel._pages] == [0, 0, 90, 0, 0]
    r.deleteRequested.emit()
    assert [p["idx"] for p in panel._pages] == [0, 1, 3, 4]
    assert r.canvas.count() == 4


def test_reader_delete_undo_and_ctrl_z_round_trip(app, panel):
    r = read_row(app, panel, 0, 5)
    r.select_positions([0, 1])
    r.deleteRequested.emit()
    assert r.canvas.count() == 3 and panel._is_dirty()
    assert r.tools["undo"].isEnabled()
    r.undoRequested.emit()
    assert r.canvas.count() == 5 and not panel._is_dirty()
    r.select_positions([4])
    r.deleteRequested.emit()
    panel.undo_pages()                                   # the window's Ctrl+Z hook, not the reader's button
    assert r.canvas.count() == 5


def test_cannot_delete_every_page_from_the_reader(app, panel):
    r = read_row(app, panel, 2, 2)
    r.select_positions([0, 1])
    r.deleteRequested.emit()
    assert r.canvas.count() == 2 and len(panel._pages) == 2
    assert "at least one page" in panel.status.text()


def test_grid_edits_while_the_reader_is_open_reach_the_reader(app, panel):
    r = read_row(app, panel, 0, 5)
    panel._pages[0]["rot"] = 180                         # any code path that rebuilds the grid
    panel._rebuild_grid()
    assert r.canvas.pages()[0] == (0, 180)


def test_the_page_being_read_stays_in_view_across_a_reorder(app, panel):
    r = read_row(app, panel, 0, 5)
    r.go_to(3)
    assert pump(app, lambda: r.current_page() == 3)
    select(panel, 4)
    panel.move_earlier()                                 # idx 4 now sits at position 3; idx 3 at 4
    assert pump(app, lambda: r.canvas.pages()[3][0] == 4)
    # the page being read stays under the reader's eye: it is the same page (idx 3), now at position 4
    assert r.canvas.pages()[r.current_page()][0] == 3 and r.current_page() == 4


def test_selection_is_carried_to_the_grid_when_leaving(app, panel):
    r = read_row(app, panel, 0, 5)
    r.select_positions([1, 3])
    panel.close_reader()
    assert not panel._reader_active()
    assert sorted(panel._selected_positions()) == [1, 3]


def test_selection_in_the_grid_is_carried_into_the_reader(app, panel):
    open_row(app, panel, 0, 5)
    select(panel, 2, 4)
    panel._on_file_double_clicked(panel.file_list.item(0))
    assert pump(app, lambda: panel.reader.is_loaded())
    assert panel.reader.selection() == [2, 4]


def test_click_ctrl_click_and_shift_click_select_pages(app, panel):
    r = read_row(app, panel, 0, 5)
    drawn(app, r)
    lay, c = r.canvas._lay, r.canvas
    vp = c.viewport()

    def point(pos):
        sy = c.verticalScrollBar().value()
        return QPoint(c._page_x(lay.widths[pos], vp.width()) + 5, lay.tops[pos] - sy + 5)

    c.go_to(0)
    pump(app, secs=0.1)
    QTest.mouseClick(vp, Qt.LeftButton, Qt.NoModifier, point(0))
    assert r.selection() == [0]
    # pages are bigger than the window: scroll each into view before clicking
    c.go_to(2); pump(app, secs=0.1)
    QTest.mouseClick(vp, Qt.LeftButton, Qt.ControlModifier, point(2))
    assert r.selection() == [0, 2]
    c.go_to(4); pump(app, secs=0.1)
    QTest.mouseClick(vp, Qt.LeftButton, Qt.ShiftModifier, point(4))
    assert r.selection() == [2, 3, 4]                    # range from the last anchor
    QTest.mouseClick(vp, Qt.LeftButton, Qt.NoModifier, QPoint(2, 2))   # the grey margin clears it
    assert r.selection() == []


def test_right_click_menu_offers_page_actions(app, panel):
    r = read_row(app, panel, 0, 5)
    r.select_positions([1])
    m = panel._build_reader_menu()
    labels = [a.text() for a in m.actions() if a.text()]
    assert labels[:2] == ["Rotate this page right", "Rotate this page left"]
    assert "Delete this page" in labels and "Back to the page grid" in labels
    [a for a in m.actions() if a.text() == "Rotate this page right"][0].trigger()
    assert panel._plan()[1] == (1, 90)
    r.select_positions([0, 1])
    labels = [a.text() for a in panel._build_reader_menu().actions() if a.text()]
    assert "Delete these 2 pages" in labels
    undo = [a for a in panel._build_reader_menu().actions() if a.text() == "Undo the last change"][0]
    assert undo.isEnabled()
    panel._busy = True
    assert panel._build_reader_menu() is None               # a running save: no edits offered
    panel._busy = False


# ------------------------------------------------------------------ leaving / Esc / next PDF
def test_back_button_and_escape_return_to_the_grid(app, panel):
    r = read_row(app, panel, 0, 5)
    r.backRequested.emit()
    assert not panel._reader_active() and panel.stack.currentIndex() == 0
    panel._on_file_double_clicked(panel.file_list.item(0))
    assert pump(app, lambda: panel.reader.is_loaded())
    assert panel._escape_pressed() is True
    assert not panel._reader_active()
    assert panel._escape_pressed() is False                # in the grid Esc keeps its old meaning


def test_escape_clears_the_find_box_before_leaving(app, panel):
    r = read_row(app, panel, 0, 5)
    r.find_edit.setText("alpha")
    assert panel._escape_pressed() is True
    assert panel._reader_active() and r.find_edit.text() == ""
    assert panel._escape_pressed() is True
    assert not panel._reader_active()


def test_save_and_next_keeps_you_in_the_reader_on_the_next_pdf(app, panel, folder):
    r = read_row(app, panel, 0, 5)
    r.select_positions([0])
    r.rotateRequested.emit(90)
    r.saveRequested.emit()
    assert pump(app, lambda: os.path.basename(panel._cur_path) == "b.pdf" and len(panel._pages) == 3
                and panel._reader_active() and r.is_loaded() and r.title.text() == "b.pdf")
    assert r.canvas.count() == 3
    assert rots(str(folder / "Reorganized" / "a.pdf")) == [90, 0, 0, 0, 0]       # the reader's rotate is what was saved


def test_skip_keeps_you_in_the_reader_too(app, panel):
    r = read_row(app, panel, 0, 5)
    r.skipRequested.emit()
    assert pump(app, lambda: r.is_loaded() and r.title.text() == "b.pdf" and panel._reader_active())


def test_last_pdf_saved_returns_to_the_grid_with_the_done_message(app, panel):
    r = read_row(app, panel, 2, 2)
    r.skipRequested.emit()
    assert pump(app, lambda: not panel._reader_active())
    assert "last PDF" in panel.info_label.text()


def test_unsaved_changes_prompt_still_guards_other_pdfs(app, panel, monkeypatch):
    r = read_row(app, panel, 0, 5)
    r.select_positions([0]); r.rotateRequested.emit(90)
    asked = []
    monkeypatch.setattr(H.Msg, "question", lambda *a, **k: asked.append(a[1]) or QMessageBox.Cancel)
    panel.close_reader()
    panel.file_list.setCurrentRow(1)                     # clicking another PDF
    assert asked and os.path.basename(panel._cur_path) == "a.pdf"
    panel._on_file_double_clicked(panel.file_list.item(1))
    assert not panel._reader_active()                    # the refused switch did not open b.pdf's reader


# ------------------------------------------------------------------ the file changing underneath
def test_changed_on_disk_shows_the_banner_and_keeps_the_old_pages(app, panel, folder):
    r = read_row(app, panel, 0, 5)
    drawn(app, r)
    time.sleep(0.05)
    make_text_pdf(str(folder / "a.pdf"), pages=9, text="NEWER")
    panel.refresh_list()
    assert pump(app, lambda: r.stale_shown())
    assert "changed on disk" in r.banner.text()
    assert r.canvas.count() == 5                         # still the version that was opened


def test_reload_button_rereads_the_file(app, panel, folder, monkeypatch):
    r = read_row(app, panel, 0, 5)
    time.sleep(0.05)
    make_text_pdf(str(folder / "a.pdf"), pages=7, text="NEWER")
    panel.refresh_list()
    assert pump(app, lambda: r.stale_shown())
    r.reloadRequested.emit()
    assert pump(app, lambda: r.is_loaded() and r.canvas.count() == 7 and not r.stale_shown())


def test_reload_asks_before_discarding_changes(app, panel, folder, monkeypatch):
    r = read_row(app, panel, 0, 5)
    r.select_positions([0]); r.rotateRequested.emit(90)
    monkeypatch.setattr(H.Msg, "question", lambda *a, **k: QMessageBox.No)
    r.reloadRequested.emit()
    assert panel._plan()[0] == (0, 90)


# ------------------------------------------------------------------ zoom and navigation
def test_zoom_ladder_and_combo_label(app, panel):
    r = read_row(app, panel, 0, 5)
    start = r.canvas.scale()
    r.zoom_in()
    assert r.canvas.scale() > start
    r._zoom_key(100)
    assert r.canvas.scale() == 1.0 and r.zoom_box.currentText() == "100%"
    r.zoom_in()
    assert r.zoom_box.currentText() == "125%"
    r.zoom_box.setCurrentIndex(1)                        # Fit page
    assert r.canvas.zoom_mode() == "page"
    assert r.canvas.scale() <= H.reader_fit_scale("width", 400, 300, 0, r.canvas.viewport().width(),
                                                  r.canvas.viewport().height()) + 1e-6


def test_ctrl_wheel_zooms_plain_wheel_scrolls(app, panel):
    from PySide6.QtGui import QWheelEvent
    from PySide6.QtCore import QPointF
    r = read_row(app, panel, 0, 5)
    r._zoom_key(100)
    vp = r.canvas.viewport()
    ev = QWheelEvent(QPointF(50, 50), QPointF(50, 50), QPoint(0, 0), QPoint(0, 120),
                     Qt.NoButton, Qt.ControlModifier, Qt.NoScrollPhase, False)
    QApplication.sendEvent(vp, ev)
    QApplication.sendEvent(r.canvas, ev)
    assert r.canvas.scale() > 1.0 or r.zoom_box.currentText() != "100%"


def test_page_box_strip_and_keys_navigate(app, panel):
    r = read_row(app, panel, 0, 5)
    r.page_spin.setValue(4)
    assert pump(app, lambda: r.current_page() == 3)
    r.strip.setCurrentRow(1)
    assert pump(app, lambda: r.current_page() == 1)
    r.canvas.setFocus()
    key(r.canvas, Qt.Key_End)
    assert pump(app, lambda: r.current_page() == 4)
    key(r.canvas, Qt.Key_Home)
    assert pump(app, lambda: r.current_page() == 0)
    assert r.page_spin.value() == 1 and r.of_label.text() == "of 5"


def test_default_zoom_setting_is_used(app, panel):
    H.save_settings({"reader_default_zoom": "100"})
    r = read_row(app, panel, 0, 5)
    assert r.canvas.scale() == 1.0 and r.zoom_box.currentText() == "100%"


def test_settings_validate_reader_values(app):
    assert H.setting("reader_default_zoom") == "width" and H.setting("reader_cache_mb") == 256
    assert H.save_settings({"reader_default_zoom": "huge"}) == ["reader_default_zoom"]
    assert H.save_settings({"reader_cache_mb": 5}) == ["reader_cache_mb"]
    assert H.save_settings({"reader_cache_mb": True}) == ["reader_cache_mb"]
    assert H.save_settings({"reader_cache_mb": 128, "reader_default_zoom": "page"}) == []
    assert H.setting("reader_cache_mb") == 128 and H.setting("reader_default_zoom") == "page"


# ------------------------------------------------------------------ find
def test_find_highlights_every_page_and_steps_through_matches(app, panel):
    r = read_row(app, panel, 0, 5)
    r.find_edit.setText("alpha")
    r._find_enter()
    assert pump(app, lambda: len(r.canvas._hit_list()) == 5 and r.find_count.text().startswith("1 of 5"))
    r._find_next()
    assert r.find_count.text().startswith("2 of 5")
    for _ in range(4):
        r._find_next()
    assert r.find_count.text().startswith("1 of 5")        # wrapped
    r._find_prev()
    assert r.find_count.text().startswith("5 of 5")
    assert pump(app, lambda: r.current_page() == 4)       # it scrolled to the match
    r.find_edit.setText("zzzz")
    r._find_enter()
    assert pump(app, lambda: r.find_count.text() == "No matches.")
    r.find_edit.setText("")
    r._find_enter()
    assert r.find_count.text() == "" and not r.canvas._hits


def test_find_survives_a_rotate_and_a_delete(app, panel):
    r = read_row(app, panel, 0, 5)
    r.find_edit.setText("alpha"); r._find_enter()
    assert pump(app, lambda: len(r.canvas._hit_list()) == 5)
    r.select_positions([1]); r.rotateRequested.emit(90)
    assert pump(app, lambda: len(r.canvas._hit_list()) == 5)      # the rotated page's box is recomputed
    r.select_positions([0]); r.deleteRequested.emit()
    assert pump(app, lambda: len(r.canvas._hit_list()) == 4)


# ------------------------------------------------------------------ hostile / odd files
def test_huge_page_is_drawn_within_the_pixel_budget(app, tmp_path):
    p = tmp_path / "pdfs"; p.mkdir()
    w = PdfWriter(); w.add_blank_page(width=2, height=14400)
    with open(p / "tall.pdf", "wb") as f:
        w.write(f)
    panel = H.OrganizerPanel(); panel.show()
    try:
        panel.folder_row.set_path(str(p))
        assert pump(app, lambda: panel.file_list.count() == 1)
        open_row(app, panel, 0, 1)
        panel._on_file_double_clicked(panel.file_list.item(0))
        assert pump(app, lambda: panel.reader.is_loaded())
        panel.reader.zoom_box.setCurrentIndex(len(panel.reader.ZOOMS) - 1)       # 400%
        assert pump(app, lambda: len(panel.reader.canvas._cache) >= 1)
        img = next(iter(panel.reader.canvas._cache._d.values()))[0]
        assert img.width() * img.height() <= H.READER_MAX_PX * 1.05
    finally:
        panel.stop_for_shutdown(); panel.stop_background(); panel.close()


def test_unreadable_pdf_says_so_instead_of_a_blank_reader(app, panel, folder):
    r = read_row(app, panel, 0, 5)
    panel.close_reader()
    with open(folder / "a.pdf", "r+b") as f:             # damage the file AFTER the grid has its pages
        f.truncate(40)
    panel.open_reader()
    assert pump(app, lambda: "could not be read" in r.status.text().lower() or r.canvas._message not in ("", "Opening…"))


# ------------------------------------------------------------------ threads
def test_closing_the_reader_and_the_window_leaves_no_running_thread(app, panel):
    r = read_row(app, panel, 0, 5)
    drawn(app, r)
    r.find_edit.setText("alpha"); r._find_enter()
    panel.close_reader()
    assert panel.stop_background(3000) is True
    assert not any(w.isRunning() for w in list(H._ALIVE_WORKERS))


def test_opening_another_pdf_retires_the_old_renderer(app, panel):
    r = read_row(app, panel, 0, 5)
    first = r.canvas._worker
    r.skipRequested.emit()
    assert pump(app, lambda: r.is_loaded() and r.title.text() == "b.pdf")
    assert r.canvas._worker is not first
    assert pump(app, lambda: not first.isRunning())


def test_stop_background_stops_the_reader_even_mid_render(app, panel):
    r = read_row(app, panel, 0, 5)
    for pos in range(5):
        r.go_to(pos)
    assert panel.stop_background(3000) is True
    assert r.canvas._worker is None or not r.canvas._worker.isRunning()


# ------------------------------------------------------------------ gaps found by the mutation pass
def test_the_reader_never_flashes_the_grid_between_pdfs(app, panel):
    r = read_row(app, panel, 0, 5)
    seen = []
    panel.stack.currentChanged.connect(seen.append)
    r.skipRequested.emit()
    assert pump(app, lambda: r.is_loaded() and r.title.text() == "b.pdf")
    assert 0 not in seen                                   # the organizer page was never shown in between


def test_deleting_the_page_you_are_on_keeps_your_place(app, tmp_path):
    d = tmp_path / "mixed"; d.mkdir()
    w = PdfWriter()
    for h in (200, 900, 200, 900, 200, 900, 200):
        w.add_blank_page(width=300, height=h)
    with open(d / "m.pdf", "wb") as f:
        w.write(f)
    p = H.OrganizerPanel(); p.resize(1100, 700); p.show()
    try:
        p.folder_row.set_path(str(d))
        assert pump(app, lambda: p.file_list.count() == 1)
        r = read_row(app, p, 0, 7)
        c = r.canvas
        r.go_to(3)                                         # a tall page...
        assert pump(app, lambda: r.current_page() == 3)
        lay = c._lay
        c.verticalScrollBar().setValue(int(lay.tops[3] + 0.8 * lay.heights[3]) - c.viewport().height() // 3)
        assert pump(app, lambda: r.current_page() == 3)    # ...read near its bottom
        r.deleteRequested.emit()                           # no selection: the page on screen goes
        assert r.canvas.count() == 6
        assert pump(app, lambda: r.current_page() == 3)    # same place, now the next page - not skipped ahead
    finally:
        p.stop_for_shutdown(); p.stop_background(); p.close()


def test_selection_follows_pages_when_they_are_reordered(app, panel):
    r = read_row(app, panel, 0, 5)
    r.select_positions([1])
    select(panel, 1); panel.move_later()                   # the selected page now sits at position 2
    assert r.selection() == [2]
    assert r.canvas.pages()[2][0] == 1


def _fds_on(path):
    out = []
    try:
        for fd in os.listdir("/proc/self/fd"):
            try:
                if os.path.realpath(os.readlink(f"/proc/self/fd/{fd}")) == os.path.realpath(path):
                    out.append(fd)
            except OSError:
                pass
    except OSError:
        pytest.skip("no /proc")
    return out


@pytest.mark.parametrize("mem_max", [H.READER_MEM_FILE_MAX, 0])
def test_reading_never_holds_the_file_open(app, panel, folder, monkeypatch, mem_max):
    """Windows cannot replace a file somebody holds open, and Save can replace the
    original. Small PDFs are read into memory; a large one is opened per page."""
    monkeypatch.setattr(H, "READER_MEM_FILE_MAX", mem_max)
    r = read_row(app, panel, 0, 5)
    drawn(app, r, 2)
    img = next(iter(r.canvas._cache._d.values()))[0]
    assert img.width() > 100                                # large-file mode really draws
    time.sleep(0.2)
    assert _fds_on(str(folder / "a.pdf")) == []


def test_tools_are_disabled_while_a_save_is_running(app, panel):
    r = read_row(app, panel, 0, 5)
    r.select_positions([0]); r.rotateRequested.emit(90)
    assert r.tools["undo"].isEnabled() and r.save_btn.isEnabled()
    panel._busy = True
    panel._reader_sync_state()
    assert not any(b.isEnabled() for b in r.tools.values())
    assert not r.save_btn.isEnabled() and not r.skip_btn.isEnabled()
    r.rotateRequested.emit(90)                              # and the slot refuses too
    assert panel._plan()[0] == (0, 90)
    panel._busy = False
    panel._reader_sync_state()
    assert r.tools["rotl"].isEnabled()


# ---- review-panel findings -------------------------------------------------
def test_navigating_to_the_last_page_lands_on_it_even_when_the_scroll_range_clamps(app, tmp_path):
    d = tmp_path / "p"; d.mkdir()
    make_text_pdf(str(d / "a.pdf"), pages=10, text="ALPHA")
    p = H.OrganizerPanel(); p.resize(1200, 800); p.show()
    p.folder_row.set_path(str(d))
    assert pump(app, lambda: p.file_list.count() == 1)
    try:
        r = read_row(app, p, 0, 10)
        r.canvas.set_zoom(0.25)
        assert pump(app, lambda: r.canvas.verticalScrollBar().maximum() < r.canvas._lay.scroll_for(9))
        r.go_to(9)
        assert r.canvas.verticalScrollBar().value() < r.canvas._lay.scroll_for(9)   # clamped short
        assert r.current_page() == 9
        r.select_positions([])
        assert p._reader_targets() == [9]
    finally:
        p.stop_for_shutdown(); p.stop_background(); p.close()


def test_back_during_save_and_next_does_not_reopen_the_reader(app, panel):
    read_row(app, panel, 0, 5)
    panel._reader_want = (str(panel._cur_path), time.monotonic(), 0)
    panel.close_reader()
    assert panel._reader_want is None


def test_edit_made_while_the_reader_is_opening_is_shown(app, panel):
    open_row(app, panel, 0, 5)
    panel._on_file_double_clicked(panel.file_list.item(0))
    assert panel._reader_active()
    panel.reader.canvas._sizes = None          # still opening
    panel._pages.pop(1)
    panel._reader_pages_changed()
    assert panel.reader.canvas._pages == [tuple(x) for x in panel._reader_plan()]


def test_reader_display_size_survives_nan_and_huge_sizes():
    assert H.reader_display_size(float("nan"), 1e12, 0) == (612.0, 14400.0)
    assert H.reader_display_size(float("inf"), 5, 90) == (5.0, 14400.0)


def test_cache_budget_always_fits_one_page(app, monkeypatch):
    v = H.PdfReaderView()
    monkeypatch.setattr(H, "setting", lambda k, *a, **kw: 64 if k == "reader_cache_mb" else None)
    assert v.canvas._budget_px() >= H.READER_MAX_PX


# ---- Acrobat-user polish (phases B and C) -----------------------------------
def test_acrobat_zoom_shortcuts(app, panel):
    r = read_row(app, panel, 0, 5)
    key(r.canvas, Qt.Key_1, Qt.ControlModifier)
    assert r.canvas.scale() == 1.0 and r.zoom_box.currentText() == "100%"
    key(r.canvas, Qt.Key_0, Qt.ControlModifier)
    assert r.canvas.zoom_mode() == H.READER_FIT_PAGE and "Fit page (" in r.zoom_box.currentText()
    key(r.canvas, Qt.Key_2, Qt.ControlModifier)
    assert r.canvas.zoom_mode() == H.READER_FIT_WIDTH
    assert r.zoom_box.currentText().startswith("Fit width (") and r.zoom_box.currentText().endswith("%)")


def test_backspace_does_not_delete_but_delete_does(app, panel):
    r = read_row(app, panel, 0, 5)
    r.canvas.select_positions([1])
    key(r.canvas, Qt.Key_Backspace)
    assert len(panel._pages) == 5
    key(r.canvas, Qt.Key_Delete)
    assert len(panel._pages) == 4


def test_ctrl_g_focuses_the_page_box_and_prev_next_buttons_move(app, panel):
    r = read_row(app, panel, 0, 5)
    key(r.canvas, Qt.Key_G, Qt.ControlModifier)
    assert r.page_spin.hasFocus() or r.page_spin.lineEdit().hasFocus()
    r.next_btn.click()
    assert pump(app, lambda: r.current_page() == 1)
    r.prev_btn.click()
    assert pump(app, lambda: r.current_page() == 0)


def test_rotate_shortcuts(app, panel):
    r = read_row(app, panel, 0, 5)
    key(r.canvas, Qt.Key_R, Qt.ControlModifier | Qt.ShiftModifier)
    assert panel._reader_plan()[0][1] == 90
    key(r.canvas, Qt.Key_L, Qt.ControlModifier | Qt.ShiftModifier)
    assert panel._reader_plan()[0][1] == 0


def test_keyboard_only_page_selection(app, panel):
    r = read_row(app, panel, 0, 5)
    key(r.canvas, Qt.Key_Return)
    assert r.canvas.selection() == [0]
    key(r.canvas, Qt.Key_Down, Qt.ShiftModifier)
    assert r.canvas.selection() == [0, 1]
    key(r.canvas, Qt.Key_Down, Qt.ShiftModifier)
    assert r.canvas.selection() == [0, 1, 2]


def test_unsaved_changes_marker_follows_edits(app, panel):
    r = read_row(app, panel, 0, 5)
    assert r.dirty_label.isHidden()
    r.canvas.select_positions([0]); r.rotateRequested.emit(90)
    assert not r.dirty_label.isHidden()
    panel.undo_pages()
    assert r.dirty_label.isHidden()


def test_dragging_pans_and_does_not_select_but_clicking_does(app, panel):
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtCore import QPointF
    r = read_row(app, panel, 0, 5)
    c = r.canvas; r.canvas.set_zoom(2.0)
    vp = c.viewport(); y0 = c.verticalScrollBar().value()
    def ev(t, pt, btn=Qt.LeftButton, btns=Qt.LeftButton):
        e = QMouseEvent(t, QPointF(*pt), QPointF(*pt), btn, btns, Qt.NoModifier)
        QApplication.sendEvent(vp, e)
    ev(QEvent.MouseButtonPress, (200, 300)); ev(QEvent.MouseMove, (200, 200)); ev(QEvent.MouseButtonRelease, (200, 200), btns=Qt.NoButton)
    assert c.verticalScrollBar().value() > y0 and c.selection() == []
    ev(QEvent.MouseButtonPress, (200, 200)); ev(QEvent.MouseButtonRelease, (200, 200), btns=Qt.NoButton)
    assert c.selection() != []


def test_find_bar_is_hidden_until_asked_and_esc_closes_it_before_the_reader(app, panel):
    r = read_row(app, panel, 0, 5)
    assert r.find_bar.isHidden()
    key(r.canvas, Qt.Key_F, Qt.ControlModifier)
    assert not r.find_bar.isHidden() and r.find_edit.hasFocus()
    assert r.handle_escape() is True and r.find_bar.isHidden()
    assert r.handle_escape() is False


def test_tab_order_follows_the_visual_order(app, panel):
    r = read_row(app, panel, 0, 5)
    order = []
    w = r.back_btn
    for _ in range(40):
        order.append(w)
        w = w.nextInFocusChain()
        if w is r.back_btn:
            break
    pos = {x: i for i, x in enumerate(order)}
    seq = [r.back_btn, r.page_spin, r.zoom_box, r.tools["rotl"], r.tools["ext"], r.find_btn, r.canvas, r.save_btn, r.skip_btn]
    assert [pos[x] for x in seq] == sorted(pos[x] for x in seq)


def test_tool_tooltips_name_their_target(app, panel):
    r = read_row(app, panel, 0, 5)
    r.canvas.select_positions([2, 3])
    assert "2 selected pages" in r.tools["del"].toolTip()
    r.canvas.select_positions([])
    assert "page 1" in r.tools["rotl"].toolTip()


def test_page_navigation_sits_in_the_top_toolbar(app, panel):
    r = read_row(app, panel, 0, 5)
    top_y = r.back_btn.mapTo(r, r.back_btn.rect().topLeft()).y()
    assert abs(r.page_spin.mapTo(r, r.page_spin.rect().topLeft()).y() - top_y) < 12
    assert r.page_spin.width() >= 60


def test_ctrl_s_saves_through_the_main_window(app, tmp_path):
    w = H.MainWindow()
    try:
        assert H.OrganizerPanel.SHORTCUT_SAVE == "save_if_dirty"
        assert any(a.shortcut().toString() == "Ctrl+S" for a in w.actions())
    finally:
        w.close()


# ---- hardening (phase E) -----------------------------------------------------
def test_large_file_mode_refuses_to_draw_a_file_that_changed(app, tmp_path):
    pdfium = H._pdfium()
    f = tmp_path / "big.pdf"; make_text_pdf(str(f), pages=2, text="ONE")
    w = H._ReaderWorker(str(f), H.ReaderQueue())
    st = os.stat(f); w._stamp = (st.st_mtime_ns, st.st_size)
    assert w._with_doc(None, pdfium, lambda d: len(d)) == 2           # unchanged: draws
    make_text_pdf(str(f), pages=5, text="TWO")                          # replaced on disk
    with pytest.raises(RuntimeError):
        w._with_doc(None, pdfium, lambda d: len(d))


def test_large_file_worker_records_the_open_time_stamp(app, tmp_path, monkeypatch):
    monkeypatch.setattr(H, "READER_MEM_FILE_MAX", 0)
    f = tmp_path / "big.pdf"; make_text_pdf(str(f), pages=2, text="ONE")
    q = H.ReaderQueue(); w = H._ReaderWorker(str(f), q)
    got = []
    w.opened.connect(lambda *_a: got.append(1))
    w.start(); assert pump(app, lambda: bool(got)); w.cancel(); w.wait(3000)
    assert w._stamp is not None


def test_one_broken_page_size_does_not_fail_the_open():
    assert H.reader_display_size(None, "x", 0) == (612.0, 612.0)


def test_stand_in_pictures_are_bounded_by_the_memory_budget(app, panel, monkeypatch):
    from PySide6.QtGui import QImage
    r = read_row(app, panel, 0, 5)
    c = r.canvas
    monkeypatch.setattr(c, "_budget_px", lambda: 1000)
    for i in range(5):
        c._fallback[i] = (0, QImage(100, 100, QImage.Format_RGB888))     # 10 000 px each
    c._request_visible()
    assert sum(im.width() * im.height() for _r, im in c._fallback.values()) <= 1000 or len(c._fallback) == 0


def test_one_unreadable_page_gets_a_default_size_and_the_rest_still_open(app, tmp_path, monkeypatch):
    f = tmp_path / "x.pdf"; make_text_pdf(str(f), pages=1, text="X")

    class Pg:
        def get_size(self): return (200.0, 300.0)
        def close(self): pass

    class Doc:
        def __len__(self): return 3
        def __getitem__(self, i):
            if i == 1:
                raise RuntimeError("bad page")
            return Pg()
        def close(self): pass

    class Fake:
        PdfDocument = staticmethod(lambda src: Doc())

    monkeypatch.setattr(H, "_pdfium", lambda: Fake)
    q = H.ReaderQueue(); w = H._ReaderWorker(str(f), q)
    got = []
    w.opened.connect(lambda _q, sizes: got.append(sizes))
    w.start(); assert pump(app, lambda: bool(got)); w.cancel(); w.wait(3000)
    assert got[0] == [(200.0, 300.0), (612.0, 792.0), (200.0, 300.0)]


# ---- review-panel findings on the polish ------------------------------------
def test_ctrl_1_and_2_zoom_the_reader_instead_of_switching_tabs(app, tmp_path):
    from PySide6.QtTest import QTest
    d = tmp_path / "p"; d.mkdir(); make_text_pdf(str(d / "a.pdf"), pages=3, text="A")
    w = H.MainWindow(); w.resize(1200, 800); w.show()
    try:
        w.tabs.setCurrentIndex(3); pump(app, lambda: False, secs=0.3)
        p = w.tabs.widget(3).panel
        p.folder_row.set_path(str(d)); assert pump(app, lambda: p.file_list.count() == 1 and len(p._counts) == 1)
        p.file_list.setCurrentRow(0); assert pump(app, lambda: len(p._pages) == 3)
        p._on_file_double_clicked(p.file_list.item(0)); assert pump(app, lambda: p.reader.is_loaded())
        p.reader.canvas.setFocus()
        QTest.keyClick(p.reader.canvas, Qt.Key_1, Qt.ControlModifier)
        assert w.tabs.currentIndex() == 3 and p.reader.canvas.scale() == 1.0
        QTest.keyClick(p.reader.canvas, Qt.Key_2, Qt.ControlModifier)
        assert w.tabs.currentIndex() == 3 and p.reader.canvas.zoom_mode() == H.READER_FIT_WIDTH
        p.stop_for_shutdown(); p.stop_background()
    finally:
        w.close()


def test_ctrl_s_on_a_clean_pdf_does_not_skip_it(app, panel):
    open_row(app, panel, 0, 5)
    cur = panel._cur_path
    panel.save_if_dirty()
    assert panel._cur_path == cur and "Nothing to save" in panel.status.text()


def test_middle_click_does_not_change_the_selection(app, panel):
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtCore import QPointF
    r = read_row(app, panel, 0, 5)
    r.canvas.select_positions([0, 1])
    vp = r.canvas.viewport()
    QApplication.sendEvent(vp, QMouseEvent(QEvent.MouseButtonPress, QPointF(200, 200), QPointF(200, 200),
                                           Qt.MiddleButton, Qt.MiddleButton, Qt.NoModifier))
    assert r.canvas.selection() == [0, 1]


def test_mover_run_hook_respects_the_greyed_button(app, tmp_path, monkeypatch):
    m = H.MoverPanel(); calls = []
    monkeypatch.setattr(m, "execute_move", lambda: calls.append(1))
    try:
        m.run_if_ready(); assert calls == []
        m.source_row.set_path(str(tmp_path), remember=False); m.dest_row.set_path(str(tmp_path / "d"), remember=False)
        m.text_input.setPlainText("1"); m.run_if_ready(); assert calls == [1]
    finally:
        m.close()
