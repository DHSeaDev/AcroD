"""Regression tests for defects found by the independent review of the Organizer."""
import os, sys, threading, time
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import pytest
from PySide6.QtWidgets import QMessageBox
from pypdf import PdfReader
import acrod as H
from test_organizer_engine import make_pdf, widths
from test_organizer_panel import app, panel, folder, pump, open_row, select, sha, _iso  # noqa: F401


# ---- engine ---------------------------------------------------------------
def test_racing_reservations_never_share_a_name(tmp_path):
    got, errs = [], []
    def go():
        try:
            got.append(H._reserve_destination(str(tmp_path / "a.pdf")))
        except Exception as e:
            errs.append(e)
    ts = [threading.Thread(target=go) for _ in range(24)]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert not errs and len(set(got)) == 24 and all(os.path.exists(g) for g in got)


def test_two_saves_of_one_source_make_two_files(tmp_path):
    src = tmp_path / "a.pdf"; make_pdf(str(src), 3)
    r1 = H.organize_pdf(str(src), [(1, 0), (0, 0), (2, 0)])
    r2 = H.organize_pdf(str(src), [(2, 0), (1, 0), (0, 0)])
    assert r1["path"] != r2["path"]
    assert widths(r1["path"]) == [101, 100, 102] and widths(r2["path"]) == [102, 101, 100]


def test_failed_save_leaves_no_reserved_empty_file(tmp_path, monkeypatch):
    src = tmp_path / "a.pdf"; make_pdf(str(src), 3)
    monkeypatch.setattr(H.os, "replace", lambda *a, **k: (_ for _ in ()).throw(OSError("boom")))
    with pytest.raises(OSError):
        H.organize_pdf(str(src), [(1, 0), (0, 0), (2, 0)])
    monkeypatch.undo()
    out = tmp_path / "Reorganized"
    assert not out.exists() or os.listdir(out) == []


def test_very_long_source_name_still_saves(tmp_path):
    name = "x" * 236 + ".pdf"                      # 240 chars
    src = tmp_path / name; make_pdf(str(src), 2)
    r = H.organize_pdf(str(src), [(1, 0), (0, 0)])
    assert widths(r["path"]) == [101, 100]


def test_rotate_only_keeps_bookmarks_and_reorder_drops_them(tmp_path):
    src = tmp_path / "a.pdf"; make_pdf(str(src), 3, outline=True)
    r = H.organize_pdf(str(src), [(0, 90), (1, 0), (2, 0)])
    assert r["dropped_bookmarks"] is False and len(PdfReader(r["path"]).outline) == 3
    r = H.organize_pdf(str(src), [(1, 0), (0, 0), (2, 0)])
    assert r["dropped_bookmarks"] is True and not PdfReader(r["path"]).outline


def test_encryption_is_flagged(tmp_path):
    src = tmp_path / "a.pdf"; make_pdf(str(src), 2, password="owner")
    assert H.organize_pdf(str(src), [(1, 0), (0, 0)])["was_encrypted"] is True


def test_incomplete_backup_aborts_before_touching_the_original(tmp_path, monkeypatch):
    src = tmp_path / "a.pdf"; make_pdf(str(src), 3)
    before = sha(str(src))
    real = H.shutil.copy2
    def short(a, b, **k):
        real(a, b); open(b, "r+b").truncate(10)
    monkeypatch.setattr(H.shutil, "copy2", short)
    with pytest.raises(H.PdfOpError, match="backup"):
        H.organize_pdf(str(src), [(1, 0), (0, 0), (2, 0)], replace=True)
    assert sha(str(src)) == before


# ---- panel ----------------------------------------------------------------
def test_skip_is_ignored_while_a_save_runs(app, panel):
    open_row(app, panel, 0, 4)
    panel._begin_op(panel._cur_path)
    assert not panel.skip_btn.isEnabled()
    panel.skip_next()
    assert os.path.basename(panel._cur_path) == "a.pdf"
    panel._end_op()


def test_save_completion_is_booked_to_the_file_that_was_saved(app, panel, folder):
    open_row(app, panel, 0, 4)
    select(panel, 0); panel.delete_pages()
    panel.save_and_next()
    assert panel._op_path == str(folder / "a.pdf")
    assert pump(app, lambda: panel._file_state.get(str(folder / "a.pdf")) == "saved")
    assert str(folder / "b.pdf") not in panel._file_state or panel._file_state[str(folder / "b.pdf")] != "saved"


def test_skip_with_save_choice_saves_and_moves_on(app, panel, folder, monkeypatch):
    open_row(app, panel, 0, 4)
    select(panel, 0); panel.delete_pages()
    monkeypatch.setattr(H.Msg, "question", staticmethod(lambda *a, **k: QMessageBox.Save))
    panel.skip_next()
    assert pump(app, lambda: (folder / "Reorganized" / "a.pdf").exists() and os.path.basename(panel._cur_path) == "b.pdf")


def test_skipped_file_gets_its_mark(app, panel):
    open_row(app, panel, 0, 4)
    panel.skip_next()
    assert pump(app, lambda: os.path.basename(panel._cur_path) == "b.pdf")
    assert panel.file_list.item(0).text().startswith("– a.pdf")


def test_declining_a_folder_change_puts_the_old_path_back(app, panel, folder, tmp_path, monkeypatch):
    other = tmp_path / "other"; other.mkdir(); make_pdf(str(other / "z.pdf"), 1)
    open_row(app, panel, 0, 4)
    select(panel, 0); panel.rotate_right()
    monkeypatch.setattr(H.Msg, "question", staticmethod(lambda *a, **k: QMessageBox.Cancel))
    panel.folder_row.set_path(str(other))
    assert panel.folder_row.path() == str(folder) and panel.file_list.count() == 3


def test_folder_change_is_refused_while_saving(app, panel, folder, tmp_path):
    other = tmp_path / "other2"; other.mkdir(); make_pdf(str(other / "z.pdf"), 1)
    open_row(app, panel, 0, 4)
    panel._begin_op(panel._cur_path)
    assert not panel.folder_row.button.isEnabled()
    panel.folder_row.set_path(str(other))
    assert panel.folder_row.path() == str(folder) and panel.file_list.count() == 3
    panel._end_op()


def test_old_search_results_cannot_overwrite_a_newer_search(app, panel, folder):
    panel.search_edit.setText("old"); panel.run_search()
    old = panel._search_gen
    panel.search_edit.setText("new"); panel.run_search()
    assert panel._search_gen > old
    panel._on_search_result(old, str(folder / "a.pdf"), {"pages": [0], "hits": 9, "has_text": True})
    assert panel._matches.get(str(folder / "a.pdf"), {}).get("hits") != 9
    before = panel.status.text()
    panel._on_search_done(old)
    assert panel.status.text() == before


def test_escape_stops_a_search_and_ignores_its_late_results(app, panel, folder):
    panel.search_edit.setText("x"); panel.run_search()
    gen = panel._search_gen
    panel._cancel()
    assert panel._search_gen > gen
    panel._on_search_result(gen, str(folder / "a.pdf"), {"pages": [0], "hits": 3, "has_text": True})
    assert panel._matches.get(str(folder / "a.pdf"), {}).get("hits") != 3


def test_grid_that_disagrees_with_the_plan_is_rebuilt(app, panel):
    open_row(app, panel, 0, 4)
    panel.grid.takeItem(3)                       # simulate a desync
    panel._sync_from_grid()
    assert panel.grid.count() == 4 and [p["idx"] for p in panel._pages] == [0, 1, 2, 3]


def test_closing_with_unsaved_edits_asks_and_can_cancel(app, monkeypatch):
    w = H.MainWindow(); w.show()
    try:
        w.tabs.setCurrentIndex(3)
        org = [p for _i, p in w.panels() if isinstance(p, H.OrganizerPanel)][0]
        org._pages = [{"idx": 0, "rot": 90, "deleted": False, "blank": False, "match": False}]
        monkeypatch.setattr(H.Msg, "question", staticmethod(lambda *a, **k: QMessageBox.No))
        assert not w.close()
        org._pages = []
    finally:
        monkeypatch.undo()
        w.close()


def test_closing_stops_background_readers(app, folder):
    w = H.MainWindow(); w.show()
    w.tabs.setCurrentIndex(3)
    org = [p for _i, p in w.panels() if isinstance(p, H.OrganizerPanel)][0]
    org.folder_row.set_path(str(folder))
    org.file_list.setCurrentRow(0)
    assert w.close()
    assert not any(t.isRunning() for t in org._bg)


# ---- settings / migration -------------------------------------------------
def test_originals_is_not_a_valid_organizer_folder_name():
    assert H.save_settings({"organized_dir_name": "Originals"}) == ["organized_dir_name"]
    assert H.save_settings({"organized_dir_name": "originals "}) == ["organized_dir_name"]
    assert H.save_settings({"organized_dir_name": "Fixed"}) == []


@pytest.mark.parametrize("bad", ["x", 1.5, None, True, [1]])
def test_odd_tab_layout_value_does_not_crash_startup(app, bad):
    H.update_ui_state(tab_layout=bad, last_tab=5)
    H._reset_ui_state_cache()
    w = H.MainWindow(); w.show()
    try:
        assert w.tabs.count() == 8
    finally:
        w.close()


def test_report_csv_marks_signed_files(app, folder, tmp_path, monkeypatch):
    p = H.OptimizerPanel(); p.show()
    try:
        p.report_only_box.setChecked(True)
        src = str(folder / "a.pdf")
        p._start_report([src], [])
        assert pump(app, lambda: not p._busy and p._worker is None)
        p._reports[src]["signed"] = True
        out = tmp_path / "r.csv"
        monkeypatch.setattr(H.QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (str(out), "")))
        p._save_results()
        assert "SIGNED - optimizing would void it" in out.read_text(encoding="utf-8-sig")
    finally:
        p.close()


def test_completion_books_the_saved_file_even_if_the_view_moved(app, panel, folder):
    a, b = str(folder / "a.pdf"), str(folder / "b.pdf")
    open_row(app, panel, 1, 3)
    panel._begin_op(a)                      # a save of a.pdf is in flight ...
    pages_before = list(panel._pages)       # ... while b.pdf is what is on screen
    panel._on_saved({"kind": "organize", "pages": 3, "path": a, "dropped_bookmarks": False, "backup": None})
    assert panel._file_state.get(a) == "saved" and panel._file_state.get(b) != "saved"
    assert pages_before and (panel._pages or panel._cur_path != b)   # b's grid not wiped by a's save
