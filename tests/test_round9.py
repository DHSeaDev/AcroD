"""Round 9: the Page Organizer list stays live (own extracts, outside changes, refresh triggers)."""
import os, sys, time
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
import acrod as H
from test_organizer_engine import make_pdf
from test_organizer_panel import app, panel, folder, pump, open_row, select, sha  # noqa: F401
from test_organizer_v2 import order


def names(p):
    return [os.path.basename(p._row_path(p.file_list.item(i))) for i in range(p.file_list.count())]


def text_of(p, name):
    return next(p.file_list.item(i).text() for i in range(p.file_list.count())
                if os.path.basename(p._row_path(p.file_list.item(i))) == name)


def extract(app, p, out_dir, name, *rows, delete=False):
    def answer(n):
        p._extract_delete = delete
        return (str(out_dir), name)
    p._choose_extract_target = answer
    select(p, *rows); p.extract_selected()
    assert pump(app, lambda: not p._busy)


def sync(app, p, force=True):
    p._last_sync = 0.0
    p._sync_list(force=force)
    assert pump(app, lambda: not p._sync_running)
    app.processEvents()


@pytest.fixture(autouse=True)
def _default_refresh_setting():
    H.save_settings({"organizer_auto_refresh": "focus", "organizer_replace_original": False})
    yield
    H.save_settings({"organizer_auto_refresh": "focus", "organizer_replace_original": False})


# ---- own extracts show up -----------------------------------------------------
def test_extract_into_the_source_folder_appears_in_the_list(app, panel, folder):
    open_row(app, panel, 0, 4)
    extract(app, panel, folder, "a_Extract", 1, 2)
    assert names(panel) == ["a.pdf", "a_Extract.pdf", "b.pdf", "c.pdf"]       # natural order, right under a.pdf
    t = text_of(panel, "a_Extract.pdf")
    assert t.startswith("+ ") and "2 pages" in t                                # count known without a worker
    assert "It is in the list now" in panel.status.text()
    assert panel._row_of[str(folder / "a_Extract.pdf")] == 1


def test_extract_into_reorganized_is_not_listed(app, panel, folder):
    out = folder / "Reorganized"; out.mkdir()
    open_row(app, panel, 0, 4)
    extract(app, panel, out, "x", 0)
    assert names(panel) == ["a.pdf", "b.pdf", "c.pdf"]
    assert "in the list" not in panel.status.text()


def test_second_extract_with_the_same_name_is_numbered_and_listed(app, panel, folder):
    open_row(app, panel, 0, 4)
    extract(app, panel, folder, "dup", 0)
    extract(app, panel, folder, "dup", 1)
    assert names(panel) == [d["name"] for d in H.pdf_folder_listing(str(folder))]
    assert sorted(names(panel)) == sorted(["a.pdf", "b.pdf", "c.pdf", "dup.pdf", "dup (1).pdf"])


def test_extracted_file_can_be_opened_and_extracted_from_again(app, panel, folder):
    open_row(app, panel, 0, 4)
    extract(app, panel, folder, "a_Extract", 0, 1, 2)
    row = names(panel).index("a_Extract.pdf")
    open_row(app, panel, row, 3)
    extract(app, panel, folder, "second", 0)
    assert "second.pdf" in names(panel)
    from pypdf import PdfReader
    assert len(PdfReader(str(folder / "second.pdf")).pages) == 1


def test_a_refresh_after_an_own_extract_does_not_duplicate_or_relabel_it(app, panel, folder):
    open_row(app, panel, 0, 4)
    extract(app, panel, folder, "a_Extract", 0)
    sync(app, panel)
    assert names(panel).count("a_Extract.pdf") == 1
    assert text_of(panel, "a_Extract.pdf").startswith("+ ")


def test_save_and_next_does_not_walk_into_a_fresh_extract(app, panel, folder, monkeypatch):
    open_row(app, panel, 0, 4)
    extract(app, panel, folder, "a_Extract", 0)
    open_row(app, panel, 0, 4)
    select(panel, 0); panel.move_later()
    panel.save_and_next()
    assert pump(app, lambda: panel._cur_path.endswith("b.pdf") and len(panel._pages) == 3)


def test_the_extracted_row_is_still_openable_by_clicking_it(app, panel, folder):
    open_row(app, panel, 0, 4)
    extract(app, panel, folder, "a_Extract", 0, 1)
    panel._ask_save = lambda *a, **k: H.QMessageBox.Discard
    open_row(app, panel, names(panel).index("a_Extract.pdf"), 2)


# ---- outside changes ----------------------------------------------------------
def test_pdf_added_outside_appears_after_refresh_with_a_page_count(app, panel, folder):
    open_row(app, panel, 0, 4)
    make_pdf(str(folder / "b2.pdf"), 5)
    sync(app, panel)
    assert names(panel) == [d["name"] for d in H.pdf_folder_listing(str(folder))]   # same order as a fresh load
    assert names(panel) == ["a.pdf", "b2.pdf", "b.pdf", "c.pdf"]
    assert pump(app, lambda: "5 pages" in text_of(panel, "b2.pdf"))
    assert text_of(panel, "b2.pdf").startswith("+ ")
    assert panel._cur_path.endswith("a.pdf") and len(panel._pages) == 4          # open PDF undisturbed


def test_pdf_removed_outside_disappears_but_the_open_one_is_kept(app, panel, folder):
    open_row(app, panel, 0, 4)
    os.remove(folder / "c.pdf")
    sync(app, panel)
    assert names(panel) == ["a.pdf", "b.pdf"] and "removed" in panel.status.text()
    os.remove(folder / "a.pdf")
    sync(app, panel)
    assert "a.pdf" in names(panel) and text_of(panel, "a.pdf").startswith("! ")
    assert len(panel._pages) == 4
    assert "no longer in the folder" in panel.status.text()
    panel._set_status("marker", "")
    sync(app, panel)                                                         # reported once, not on every refresh
    assert panel.status.text() == "marker"


def test_pdf_changed_outside_updates_its_count(app, panel, folder):
    open_row(app, panel, 0, 4)
    time.sleep(0.02)
    make_pdf(str(folder / "b.pdf"), 7)
    sync(app, panel)
    assert pump(app, lambda: "7 pages" in text_of(panel, "b.pdf"))


def test_open_pdf_changed_outside_is_flagged_and_its_grid_kept(app, panel, folder):
    open_row(app, panel, 0, 4)
    select(panel, 0); panel.move_later()
    before = order(panel)
    time.sleep(0.02)
    make_pdf(str(folder / "a.pdf"), 6)
    sync(app, panel)
    assert text_of(panel, "a.pdf").startswith("! ") and "changed on disk" in panel.status.text()
    assert order(panel) == before and len(panel._undo) == 1                      # edits and Undo history untouched


def test_unchanged_folder_changes_nothing_and_says_nothing(app, panel, folder):
    open_row(app, panel, 0, 4)
    panel._set_status("marker", "")
    sync(app, panel)
    assert panel.status.text() == "marker" and names(panel) == ["a.pdf", "b.pdf", "c.pdf"]


def test_added_pdf_matches_an_active_search(app, panel, folder):
    open_row(app, panel, 0, 4)
    panel.search_edit.setText("zzzz"); panel.run_search()
    assert pump(app, lambda: len(panel._matches) == 3)
    make_pdf(str(folder / "d.pdf"), 2)
    sync(app, panel)
    assert pump(app, lambda: str(folder / "d.pdf") in panel._matches)


def test_replace_original_save_updates_the_row_without_a_false_changed_alarm(app, panel, folder):
    H.save_settings({"organizer_replace_original": True})
    open_row(app, panel, 0, 4)
    select(panel, 1); panel.delete_pages()
    panel._ask_save = lambda *a, **k: H.QMessageBox.Save
    panel.save_and_next()
    assert pump(app, lambda: not panel._busy and panel._cur_path.endswith("b.pdf"))
    assert "3 pages" in text_of(panel, "a.pdf")
    st = os.stat(folder / "a.pdf")
    assert panel._row_sig[str(folder / "a.pdf")] == (st.st_size, st.st_mtime_ns)   # our own write is not "an outside change"
    sync(app, panel)
    assert not text_of(panel, "a.pdf").startswith("! ")
    assert "changed on disk" not in panel.status.text()


# ---- triggers -------------------------------------------------------------------
def test_throttle_blocks_a_second_automatic_read_within_the_gap(app, panel, folder, monkeypatch):
    open_row(app, panel, 0, 4)
    panel._last_sync = time.monotonic()
    panel._sync_list()
    assert not panel._sync_running
    panel._sync_list(force=True)
    assert panel._sync_running
    assert pump(app, lambda: not panel._sync_running)


def test_returning_to_the_window_syncs_unless_the_setting_is_never(app, panel, folder):
    panel.show(); app.processEvents()
    make_pdf(str(folder / "d.pdf"), 2)
    panel._last_sync = 0.0
    H.save_settings({"organizer_auto_refresh": "never"})
    panel._on_app_state(Qt.ApplicationActive)
    assert not panel._sync_running and "d.pdf" not in names(panel)
    H.save_settings({"organizer_auto_refresh": "focus"})
    panel._on_app_state(Qt.ApplicationActive)
    assert pump(app, lambda: "d.pdf" in names(panel))


def test_inactive_application_state_does_not_sync(app, panel, folder):
    panel.show(); app.processEvents()
    make_pdf(str(folder / "d.pdf"), 2)
    panel._last_sync = 0.0
    panel._on_app_state(Qt.ApplicationInactive)
    assert not panel._sync_running and "d.pdf" not in names(panel)


def test_timer_only_runs_in_timer_mode_while_visible(app, panel):
    panel.show(); app.processEvents()
    H.save_settings({"organizer_auto_refresh": "focus"}); panel._arm_timer()
    assert not panel._sync_timer.isActive()
    H.save_settings({"organizer_auto_refresh": "timer"}); panel._arm_timer()
    assert panel._sync_timer.isActive() and panel._sync_timer.interval() == 10000
    panel.hide(); app.processEvents()
    assert not panel._sync_timer.isActive()


def test_f5_and_the_refresh_button_both_refresh(app, folder):
    w = H.MainWindow(); w.show(); w.activateWindow()
    try:
        idx = next(i for i in range(w.tabs.count()) if w.tabs.tabBar().title(i) == "Page Organizer")
        w.tabs.setCurrentIndex(idx); app.processEvents()
        p = w.panel_at(idx)
        p.folder_row.set_path(str(folder))
        assert pump(app, lambda: p.file_list.count() == 3 and len(p._counts) == 3)
        make_pdf(str(folder / "d.pdf"), 2)
        p.setFocus(); QTest.keyClick(p, Qt.Key_F5)
        assert pump(app, lambda: "d.pdf" in names(p))
        make_pdf(str(folder / "e.pdf"), 2)
        p.refresh_btn.click()
        assert pump(app, lambda: "e.pdf" in names(p))
    finally:
        w.close()


def test_a_busy_panel_does_not_sync(app, panel, folder):
    open_row(app, panel, 0, 4)
    panel._busy = True
    try:
        panel._sync_list(force=True)
        assert not panel._sync_running
    finally:
        panel._busy = False


def test_a_listing_for_an_old_folder_is_ignored(app, panel, folder):
    open_row(app, panel, 0, 4)
    panel._on_listed(panel._token - 1, str(folder), [])
    assert names(panel) == ["a.pdf", "b.pdf", "c.pdf"]
    panel._on_listed(panel._token, str(folder) + "x", [])
    assert names(panel) == ["a.pdf", "b.pdf", "c.pdf"]


# ---- setting + helper ----------------------------------------------------------
def test_refresh_setting_validates_and_is_in_settings(app):
    assert H.setting("organizer_auto_refresh") == "focus"
    assert H.save_settings({"organizer_auto_refresh": "sometimes"}) == ["organizer_auto_refresh"]
    assert H.save_settings({"organizer_auto_refresh": "timer"}) == []
    sp = H.SettingsPanel()
    assert sp.adv["organizer_auto_refresh"].currentIndex() == 2
    sp.adv["organizer_auto_refresh"].setCurrentIndex(0)
    sp._save_advanced()
    assert H.setting("organizer_auto_refresh") == "never"
    sp._reset_advanced()
    assert H.setting("organizer_auto_refresh") == "focus" and sp.adv["organizer_auto_refresh"].currentIndex() == 1


def test_natural_key_and_listing_carry_mtime(tmp_path):
    for n in ("a10.pdf", "a2.pdf", "a1.pdf"):
        make_pdf(str(tmp_path / n), 1)
    lst = H.pdf_folder_listing(str(tmp_path))
    assert [d["name"] for d in lst] == ["a1.pdf", "a2.pdf", "a10.pdf"]
    assert all(isinstance(d["mtime_ns"], int) and d["size"] > 0 for d in lst)
    assert H.natural_name_key("A2.pdf") < H.natural_name_key("a10.pdf")


def test_help_explains_the_live_list(app):
    t = " ".join(a for _q, a in H.HelpPanel.FAQ)
    assert "Does the Page Organizer list update by itself?" in " ".join(q for q, _a in H.HelpPanel.FAQ)
    assert "F5" in t and "Page Organizer list refresh" in t
