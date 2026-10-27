"""Page Organizer tab + Optimizer report-only: behaviour through the real widgets."""
import hashlib, os, sys, time
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import pytest
from PySide6.QtWidgets import QApplication, QMessageBox
from pypdf import PdfReader
import acrod as H
from test_organizer_engine import make_pdf, widths


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _iso(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "appdata"))
    H._reset_ui_state_cache()


def pump(app, cond=None, secs=6.0):
    end = time.time() + secs
    while time.time() < end:
        app.processEvents()
        if cond is not None and cond():
            return True
        time.sleep(0.01)
    return cond is None


def sha(p):
    return hashlib.sha1(open(p, "rb").read()).hexdigest()


@pytest.fixture
def folder(tmp_path):
    d = tmp_path / "pdfs"
    d.mkdir()
    make_pdf(str(d / "a.pdf"), 4)
    make_pdf(str(d / "b.pdf"), 3, outline=True)
    make_pdf(str(d / "c.pdf"), 2)
    return d


@pytest.fixture
def panel(app, folder):
    p = H.OrganizerPanel()
    p.show()
    p.folder_row.set_path(str(folder))
    assert pump(app, lambda: p.file_list.count() == 3 and len(p._counts) == 3)
    yield p
    p.stop_for_shutdown()
    p.close()
    p.deleteLater()
    app.processEvents()


def open_row(app, p, row, pages):
    p.file_list.setCurrentRow(row)
    assert pump(app, lambda: len(p._pages) == pages and len(p._thumbs) == pages)


def select(p, *positions):
    p.grid.clearSelection()
    for r in positions:
        p.grid.item(r).setSelected(True)


def test_listing_counts_and_natural_order(panel):
    texts = [panel.file_list.item(i).text() for i in range(3)]
    assert texts[0].startswith("a.pdf") and "4 pages" in texts[0]
    assert "3 pages" in texts[1] and "2 pages" in texts[2]


def test_clicking_a_pdf_shows_every_page(app, panel):
    open_row(app, panel, 0, 4)
    assert panel.grid.count() == 4
    assert not panel._is_dirty()


def test_reorder_rotate_remove_then_save_and_next(app, panel, folder):
    src = str(folder / "a.pdf")
    before = sha(src)
    open_row(app, panel, 0, 4)
    select(panel, 1); panel.move_later()          # order 0,2,1,3
    select(panel, 2); panel.rotate_right()        # the page now at position 2 is idx 1
    select(panel, 3); panel.delete_pages()       # idx 3 out
    assert panel._plan() == [(0, 0), (2, 0), (1, 90)]
    panel.save_and_next()
    assert pump(app, lambda: os.path.basename(panel._cur_path) == "b.pdf" and len(panel._pages) == 3)
    out = str(folder / "Reorganized" / "a.pdf")
    assert widths(out) == [100, 102, 101]
    assert sha(src) == before                      # original untouched
    assert panel._file_state[src] == "saved"
    assert panel.file_list.item(0).text().startswith("✓ a.pdf")


def test_move_keeps_the_selection_and_stops_at_the_edge(app, panel):
    open_row(app, panel, 0, 4)
    select(panel, 0); panel.move_earlier()
    assert [p["idx"] for p in panel._pages] == [0, 1, 2, 3]
    select(panel, 3); panel.move_later()
    assert [p["idx"] for p in panel._pages] == [0, 1, 2, 3]
    select(panel, 0, 1); panel.move_later()
    assert [p["idx"] for p in panel._pages] == [2, 0, 1, 3]


def test_delete_then_undo_or_reset_restores(app, panel):
    open_row(app, panel, 0, 4)
    select(panel, 1); panel.delete_pages()
    assert panel._is_dirty() and [p["idx"] for p in panel._pages] == [0, 2, 3]
    panel.undo_pages()
    assert not panel._is_dirty() and [p["idx"] for p in panel._pages] == [0, 1, 2, 3]
    select(panel, 1); panel.delete_pages()
    panel.reset_pages()
    assert not panel._is_dirty() and [p["idx"] for p in panel._pages] == [0, 1, 2, 3]
    select(panel, 2); panel.rotate_left(); panel.move_earlier()
    panel.reset_pages()
    assert not panel._is_dirty() and [p["idx"] for p in panel._pages] == [0, 1, 2, 3]


def test_every_page_removed_is_refused(app, panel, folder):
    open_row(app, panel, 0, 4)
    select(panel, 0, 1, 2, 3); panel.delete_pages()
    assert len(panel._pages) == 4                      # refused: nothing was deleted
    assert "at least one page" in panel.status.text()
    panel.save_and_next()
    assert not (folder / "Reorganized").exists()


def test_save_with_no_changes_just_moves_on(app, panel, folder):
    open_row(app, panel, 0, 4)
    panel.save_and_next()
    assert pump(app, lambda: os.path.basename(panel._cur_path) == "b.pdf")
    assert not (folder / "Reorganized").exists()


def test_unsaved_changes_prompt_and_cancel_keeps_you_here(app, panel, monkeypatch):
    open_row(app, panel, 0, 4)
    select(panel, 1); panel.rotate_right()
    monkeypatch.setattr(H.Msg, "question", staticmethod(lambda *a, **k: QMessageBox.Cancel))
    panel.file_list.setCurrentRow(1)
    assert os.path.basename(panel._cur_path) == "a.pdf"
    assert panel.file_list.currentRow() == 0
    assert panel._is_dirty()


def test_discard_moves_on_without_writing(app, panel, folder, monkeypatch):
    open_row(app, panel, 0, 4)
    select(panel, 1); panel.rotate_right()
    monkeypatch.setattr(H.Msg, "question", staticmethod(lambda *a, **k: QMessageBox.Discard))
    panel.file_list.setCurrentRow(1)
    assert pump(app, lambda: os.path.basename(panel._cur_path) == "b.pdf")
    assert not (folder / "Reorganized").exists()


def test_stale_thumbnails_from_a_previous_file_are_ignored(app, panel):
    open_row(app, panel, 0, 4)
    old = panel._thumb_token
    open_row(app, panel, 2, 2)
    panel._on_thumb(old, 0, None, True)
    panel._on_total(old, 9)
    assert len(panel._pages) == 2 and panel._thumbs.get(0) is not None


def test_bookmark_notice_once_and_original_kept(app, panel, folder, monkeypatch):
    shown = []
    monkeypatch.setattr(H.Msg, "information", staticmethod(lambda *a, **k: shown.append(a[1])))
    open_row(app, panel, 1, 3)
    select(panel, 0); panel.move_later()
    panel.save_and_next()
    assert pump(app, lambda: (folder / "Reorganized" / "b.pdf").exists() and os.path.basename(panel._cur_path) == "c.pdf")
    assert shown == ["Bookmarks Not Kept"]
    assert not PdfReader(str(folder / "Reorganized" / "b.pdf")).outline
    assert PdfReader(str(folder / "b.pdf")).outline


def test_extract_leaves_the_original_and_the_grid_alone(app, panel, folder, monkeypatch):
    monkeypatch.setattr(panel, "_choose_extract_target", lambda n: (str(folder / "Reorganized"), "a_Extract"))
    (folder / "Reorganized").mkdir(exist_ok=True)
    src = str(folder / "a.pdf"); before = sha(src)
    open_row(app, panel, 0, 4)
    select(panel, 3, 1); panel.extract_selected()
    assert pump(app, lambda: (folder / "Reorganized" / "a_Extract.pdf").exists() and not panel._busy)
    assert widths(str(folder / "Reorganized" / "a_Extract.pdf")) == [101, 103]
    assert sha(src) == before and len(panel._pages) == 4 and os.path.basename(panel._cur_path) == "a.pdf"


def test_changed_source_is_refused_not_overwritten(app, panel, folder, monkeypatch):
    shown = []
    monkeypatch.setattr(H.Msg, "warning", staticmethod(lambda *a, **k: shown.append(a[1])))
    src = folder / "a.pdf"
    open_row(app, panel, 0, 4)
    select(panel, 0); panel.rotate_right()
    time.sleep(0.02)
    make_pdf(str(src), 6)                       # someone else replaced it
    panel.save_and_next()
    assert pump(app, lambda: not panel._busy)
    assert not (folder / "Reorganized").exists()
    assert panel.status.property("kind") == "error" and shown == ["Could Not Save"]
    assert panel._plan() and not panel._busy         # still editable, nothing lost


def test_search_marks_files_and_pages_and_filter_hides_the_rest(app, panel, folder):
    from reportlab.pdfgen import canvas
    c = canvas.Canvas(str(folder / "c.pdf"))
    for i in range(3):
        c.drawString(72, 700, "needle here" if i == 1 else "nothing"); c.showPage()
    c.save()
    panel.search_edit.setText("NEEDLE"); panel.run_search()
    assert pump(app, lambda: "contain" in panel.status.text())
    assert panel._matches[str(folder / "c.pdf")]["pages"] == [1]
    assert "no text" in panel.file_list.item(0).text()
    panel.only_matches.setChecked(True)
    hidden = [panel.file_list.item(i).isHidden() for i in range(3)]
    assert hidden == [True, True, False]
    open_row(app, panel, 2, 3) if False else None
    panel.only_matches.setChecked(False)
    panel.clear_search()
    assert not any(panel.file_list.item(i).isHidden() for i in range(3))


def test_next_skips_hidden_rows(app, panel, folder):
    panel.file_list.item(1).setHidden(True)
    open_row(app, panel, 0, 4)
    panel.skip_next()
    assert pump(app, lambda: os.path.basename(panel._cur_path) == "c.pdf")


def test_replace_setting_keeps_a_backup(app, panel, folder):
    H.save_settings({"organizer_replace_original": True})
    src = str(folder / "a.pdf")
    open_row(app, panel, 0, 4)
    select(panel, 0); panel.delete_pages()
    panel.save_and_next()
    assert pump(app, lambda: os.path.basename(panel._cur_path) == "b.pdf")
    assert len(PdfReader(src).pages) == 3
    assert len(PdfReader(str(folder / "Reorganized" / "Originals" / "a.pdf")).pages) == 4


def test_shutdown_while_loading_does_not_hang_or_raise(app, folder):
    p = H.OrganizerPanel(); p.show()
    p.folder_row.set_path(str(folder))
    p.file_list.setCurrentRow(0)
    assert p.stop_for_shutdown(wait_ms=8000)
    app.processEvents(); p.close()


# ---- window-level: tab placement and saved-position migration ------------
def test_tab_is_third_and_old_saved_positions_shift(app, tmp_path):
    H.save_settings({"default_tab": 4})              # was Bulk File Mover
    H.update_ui_state(last_tab=6)                    # was Help
    H._reset_ui_state_cache()
    w = H.MainWindow(); w.show()
    try:
        tb = w.tabs.tabBar()
        assert tb.title(3) == "Page Organizer" and w.tabs.count() == 8
        assert H.setting("default_tab") == 5 and w.tabs.currentIndex() == 5
        assert H.load_ui_state()["tab_layout"] == 2
    finally:
        w.close()
    H.save_settings({"default_tab": -1}); H.update_ui_state(last_tab=6)
    w = H.MainWindow(); w.show()
    try:
        assert w.tabs.currentIndex() == 6            # migrated once, not again
    finally:
        w.close()


# ---- Optimizer report-only ----------------------------------------------
def test_inspect_is_read_only_and_reports_hidden_items(tmp_path):
    import pikepdf
    src = tmp_path / "x.pdf"
    make_pdf(str(src), 2)
    with pikepdf.open(str(src), allow_overwriting_input=True) as pdf:
        pdf.docinfo["/Author"] = "someone"
        pdf.save(str(src))
    before, mt = sha(str(src)), os.stat(src).st_mtime_ns
    rep = H.inspect_pdf(str(src))
    assert rep["pages"] == 2 and rep["would_remove"]
    assert sha(str(src)) == before and os.stat(src).st_mtime_ns == mt
    assert os.listdir(tmp_path) == ["x.pdf"]
    txt = H.format_inspect_report(rep)
    assert "WOULD REMOVE" in txt and "Nothing was written" in txt


def test_inspect_password_pdf_is_a_plain_error(tmp_path):
    src = tmp_path / "p.pdf"
    make_pdf(str(src), 1, password="owner")
    # owner-only passwords open; a user password must not
    from pypdf import PdfWriter
    w = PdfWriter(); w.add_blank_page(100, 100); w.encrypt(user_password="secret")
    with open(tmp_path / "u.pdf", "wb") as f:
        w.write(f)
    with pytest.raises(H.PdfOpError, match="password"):
        H.inspect_pdf(str(tmp_path / "u.pdf"))


def test_report_only_run_writes_nothing_and_shows_the_report(app, folder, monkeypatch):
    monkeypatch.setattr(H.Msg, "question", staticmethod(lambda *a, **k: QMessageBox.No))
    p = H.OptimizerPanel(); p.show()
    try:
        before = {f: sha(str(folder / f)) for f in os.listdir(folder)}
        p.report_only_box.setChecked(True)
        assert not p.ceiling_box.isEnabled() and not p.force_flatten_box.isEnabled()
        paths = [str(folder / f) for f in sorted(before)] + [str(folder / "gone.pdf")]
        p._start(paths, [])            # what handle_drop calls after expanding the drop
        assert pump(app, lambda: not p._busy and p._worker is None)
        assert len(p._reports) == 3 and len(p._failed) == 1 and not p._results
        assert {f: sha(str(folder / f)) for f in os.listdir(folder)} == before
        assert not (folder / "Optimized").exists()
        row = [r for r in range(p.table.rowCount()) if p._path_at(r).endswith("a.pdf")][0]
        p._show_detail(row, 0)
        assert "WOULD REMOVE" in p.detail_label.text()
        assert p.save_button.isEnabled() and not p.open_button.isEnabled()
        p.report_only_box.setChecked(False)
        p.force_flatten_box.setChecked(True)
        p.report_only_box.setChecked(True)
        assert not p.force_flatten_box.isChecked()      # a report is not a flatten
        p.report_only_box.setChecked(False)
        assert p.ceiling_box.isEnabled() and p.force_flatten_box.isEnabled()
        p.force_flatten_box.setChecked(True)
        assert not p.ceiling_box.isEnabled()
    finally:
        p.close()


def test_rotate_directions_are_not_swapped(app, panel):
    open_row(app, panel, 0, 4)
    select(panel, 0); panel.rotate_right()
    select(panel, 1); panel.rotate_left()
    assert panel._pages[0]["rot"] == 90 and panel._pages[1]["rot"] == 270
    select(panel, 0); panel.rotate_right(); panel.rotate_right(); panel.rotate_right()
    assert panel._pages[0]["rot"] == 0


def test_a_save_in_flight_blocks_switching_files(app, panel):
    open_row(app, panel, 0, 4)
    panel._busy = True
    panel.file_list.setCurrentRow(2)
    assert panel.file_list.currentRow() == 0 and os.path.basename(panel._cur_path) == "a.pdf"
    panel._busy = False


def test_bookmark_notice_is_shown_once_across_several_files(app, panel, folder, monkeypatch):
    shown = []
    monkeypatch.setattr(H.Msg, "information", staticmethod(lambda *a, **k: shown.append(a[1])))
    make_pdf(str(folder / "a.pdf"), 4, outline=True)
    panel._load_folder(str(folder))
    assert pump(app, lambda: panel.file_list.count() == 3 and len(panel._counts) == 3)
    for row in (0, 1):
        open_row(app, panel, row, 4 if row == 0 else 3)
        select(panel, 0); panel.move_later()
        panel.save_and_next()
        assert pump(app, lambda r=row: panel._file_state.get(str(folder / ("a.pdf" if r == 0 else "b.pdf"))) == "saved")
        assert pump(app, lambda r=row: len(panel._pages) == (3 if r == 0 else 2))
    assert shown == ["Bookmarks Not Kept"]


def test_search_hit_pages_are_flagged_in_the_open_grid(app, panel, folder):
    from reportlab.pdfgen import canvas
    c = canvas.Canvas(str(folder / "a.pdf"))
    for i in range(4):
        c.drawString(72, 700, "needle" if i == 2 else "hay"); c.showPage()
    c.save()
    panel._load_folder(str(folder))
    assert pump(app, lambda: panel.file_list.count() == 3 and len(panel._counts) == 3)
    open_row(app, panel, 0, 4)
    panel.search_edit.setText("needle"); panel.run_search()
    assert pump(app, lambda: "contain" in panel.status.text())
    assert [p["match"] for p in panel._pages] == [False, False, True, False]
    assert "match" in panel.grid.item(2).text() and "match" not in panel.grid.item(1).text()
    panel.clear_search()
    assert not any(p["match"] for p in panel._pages)


@pytest.mark.parametrize("old,new", [(0, 0), (1, 1), (2, 2), (3, 4), (6, 7)])
def test_migration_boundary_for_both_saved_positions(app, old, new):
    H.save_settings({"default_tab": old})
    H._reset_ui_state_cache()
    w = H.MainWindow(); w.show()
    try:
        assert H.setting("default_tab") == new and w.tabs.currentIndex() == new
    finally:
        w.close()
    H.save_settings({"default_tab": -1}); H.update_ui_state(last_tab=old, tab_layout=1)
    H._reset_ui_state_cache()
    w = H.MainWindow(); w.show()
    try:
        assert w.tabs.currentIndex() == new
    finally:
        w.close()
