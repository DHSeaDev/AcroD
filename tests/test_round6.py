"""Round 6: Delete page, position labels, the Reorganized folder, no 'Number files from',
and the Help search box."""
import os, sys
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QCheckBox, QSpinBox, QLabel
from pypdf import PdfReader
import acrod as H
from test_organizer_engine import widths
from test_organizer_panel import app, panel, folder, pump, open_row, select, _iso  # noqa: F401
from test_organizer_v2 import order
from test_help_current import help_panel  # noqa: F401


def labels(p):
    return [p.grid.item(r).text().split("\n")[0] for r in range(p.grid.count())]


# ---- labels follow the position ---------------------------------------------
def test_labels_always_read_in_order_after_moves(app, panel):
    open_row(app, panel, 0, 4)
    assert labels(panel) == ["Page 1", "Page 2", "Page 3", "Page 4"]
    select(panel, 1); panel.move_later()                  # old page 2 now sits third
    assert order(panel) == [0, 2, 1, 3]
    assert labels(panel) == ["Page 1", "Page 2", "Page 3", "Page 4"]
    assert panel.grid.item(2).text().split("\n")[1].startswith("was page 2")
    assert "was page" not in panel.grid.item(0).text()    # an unmoved page says nothing


def test_page_one_moved_right_takes_the_number_of_its_new_place(app, panel):
    open_row(app, panel, 0, 4)
    select(panel, 0); panel.move_later(); panel.move_later()
    assert order(panel) == [1, 2, 0, 3]
    assert labels(panel) == ["Page 1", "Page 2", "Page 3", "Page 4"]
    assert "was page 1" in panel.grid.item(2).text()


def test_labels_renumber_after_a_delete_and_an_undo(app, panel):
    open_row(app, panel, 0, 4)
    select(panel, 1); panel.delete_pages()
    assert labels(panel) == ["Page 1", "Page 2", "Page 3"]
    assert "was page 3" in panel.grid.item(1).text() and "was page 4" in panel.grid.item(2).text()
    panel.undo_pages()
    assert labels(panel) == ["Page 1", "Page 2", "Page 3", "Page 4"]
    assert not any("was page" in panel.grid.item(r).text() for r in range(4))


# ---- Delete page ------------------------------------------------------------
def test_delete_button_menu_and_key_all_delete(app, panel):
    open_row(app, panel, 0, 4); panel.show(); pump(app, secs=0.2)
    assert panel.tools["del"].text() == "Delete page"
    select(panel, 0); panel.tools["del"].click()
    assert order(panel) == [1, 2, 3] and panel.grid.count() == 3
    m = panel._build_grid_menu(panel.grid.visualItemRect(panel.grid.item(0)).center())
    assert "Delete this page" in [a.text() for a in m.actions()]
    next(a for a in m.actions() if a.text() == "Delete this page").trigger(); pump(app, secs=0.1)
    assert order(panel) == [2, 3]
    select(panel, 0); panel.grid.setFocus()
    QTest.keyClick(panel.grid, Qt.Key_Delete)
    assert order(panel) == [3]


def test_delete_never_empties_the_pdf_and_says_why(app, panel):
    open_row(app, panel, 0, 4)
    select(panel, 0, 1, 2, 3); panel.delete_pages()
    assert order(panel) == [0, 1, 2, 3] and "at least one page" in panel.status.text()


def test_deleted_pages_are_gone_from_the_saved_copy_and_the_original_is_untouched(app, panel, folder):
    src = str(folder / "a.pdf"); import hashlib
    before = hashlib.sha1(open(src, "rb").read()).hexdigest()
    open_row(app, panel, 0, 4)
    select(panel, 1, 2); panel.delete_pages()
    panel.save_and_next()
    out = folder / "Reorganized" / "a.pdf"
    assert pump(app, lambda: out.exists() and not panel._busy)
    assert widths(str(out)) == [100, 103]
    assert hashlib.sha1(open(src, "rb").read()).hexdigest() == before
    assert not (folder / "Organized").exists()


def test_delete_is_one_undo_step_and_reset_brings_everything_back(app, panel):
    open_row(app, panel, 0, 4)
    select(panel, 0); panel.rotate_right()
    select(panel, 2); panel.delete_pages()
    assert len(panel._undo) == 2
    panel.reset_pages()
    assert order(panel) == [0, 1, 2, 3] and all(p["rot"] == 0 for p in panel._pages)
    panel.undo_pages()                                      # undo the reset
    assert order(panel) == [0, 1, 3] and panel._pages[0]["rot"] == 90


def test_info_line_counts_pages_in_the_file_and_pages_kept(app, panel):
    open_row(app, panel, 0, 4)
    select(panel, 3); panel.delete_pages()
    assert "4 pages in the file" in panel.info_label.text() and "3 pages will be saved" in panel.info_label.text()


def test_extract_after_a_delete_uses_the_remaining_pages(app, panel, folder, monkeypatch):
    open_row(app, panel, 0, 4)
    select(panel, 0); panel.delete_pages()
    seen = {}
    monkeypatch.setattr(panel, "_choose_extract_target", lambda n: seen.setdefault("n", n) and None)
    select(panel, 0, 1); panel.extract_selected()
    assert seen["n"] == 2


# ---- Reorganized folder -------------------------------------------------------
def test_default_output_folder_is_reorganized_and_an_old_saved_name_follows_it(app):
    assert H.ORGANIZED_DIR_NAME == "Reorganized" and H.setting("organized_dir_name") == "Reorganized"
    H.save_settings({"organized_dir_name": "Organized"})     # what an older build saved
    assert H.setting("organized_dir_name") == "Reorganized"
    H.save_settings({"organized_dir_name": "Fixed Copies"})  # a deliberate choice is respected
    assert H.setting("organized_dir_name") == "Fixed Copies"


def test_save_writes_a_copy_into_reorganized_beside_the_pdf_and_never_into_the_original(app, panel, folder):
    import hashlib
    src = str(folder / "a.pdf"); before = hashlib.sha1(open(src, "rb").read()).hexdigest()
    open_row(app, panel, 0, 4)
    select(panel, 0); panel.move_later()
    panel.save_and_next()
    out = folder / "Reorganized" / "a.pdf"
    assert pump(app, lambda: out.exists() and not panel._busy)
    assert out.parent.parent == folder
    assert hashlib.sha1(open(src, "rb").read()).hexdigest() == before
    assert pump(app, lambda: panel._cur_path.endswith("b.pdf") and len(panel._pages) == 3)
    assert panel.status.text().startswith("Saved a.pdf to Reorganized. b.pdf: 3 pages")


# ---- Bulk Renamer: no 'Number files from' ------------------------------------
def test_renamer_has_no_numbering_controls(app):
    p = H.RenamerPanel(); p.show()
    assert not hasattr(p, "enum_box") and not hasattr(p, "enum_start") and not hasattr(p, "enum_pad")
    assert not any("number files" in c.text().lower() for c in p.findChildren(QCheckBox))
    assert not any(l.text().strip().lower() == "digits:" for l in p.findChildren(QLabel))
    r = p._current_rule() if hasattr(p, "_current_rule") else None
    if r is not None:
        assert r.enumerate_enabled is False
    p.close()


def test_a_saved_preset_that_still_says_increment_is_refused_not_renamed_literally(tmp_path):
    f = tmp_path / "scan.pdf"; f.write_bytes(b"%PDF-1.4")
    with pytest.raises(H.RenameRuleError, match="Numbering files was removed"):
        H.build_plan([str(f)], H.RenameRule(search="scan", replace="Doc_${increment}"))
    plan = H.build_plan([str(f)], H.RenameRule(search="scan", replace="Doc"))   # ordinary rules still work
    assert plan.rows[0].dst_name.startswith("Doc")


def test_no_text_in_the_app_still_advertises_numbering():
    src = open(H.__file__, encoding="utf-8").read()
    for line in src.splitlines():
        if "increment" in line.lower():
            assert "numbers" not in line.lower() or "removed" in line.lower()
    assert "Number files from" not in "\n".join(q + a for q, a in H.HelpPanel.FAQ)
    assert "number files from" not in H._TIPS


# ---- Help search ----------------------------------------------------------------
def test_help_has_a_search_box_that_filters_questions(app, help_panel):
    p = help_panel
    total = len(p._faq_rows)
    assert total > 10 and p.search_edit.accessibleName() == "Search Help"
    shown = lambda: sum(1 for ql, _a, _h in p._faq_rows if not ql.isHidden())
    assert shown() == total and p.search_count.text() == ""
    p.search_edit.setText("reorganized")
    assert 1 <= shown() < total and p.search_count.text() == f"{shown()} of {total}"
    for ql, al, hay in p._faq_rows:
        assert (not ql.isHidden()) == ("reorganized" in hay) and ql.isHidden() == al.isHidden()
    p.search_edit.setText("PAGE Undo")                     # any order, any case, every word
    hits = [h for ql, _a, h in p._faq_rows if not ql.isHidden()]
    assert hits and all("undo" in h and "page" in h for h in hits)
    p.search_edit.setText("zzzqqq")
    assert shown() == 0 and not p.no_match.isHidden()
    p.search_edit.setText("   ")
    assert shown() == total and p.no_match.isHidden() and p.search_count.text() == ""


def test_escape_clears_the_help_search_first(app, help_panel):
    p = help_panel; p.search_edit.setText("undo")
    win = p.window()
    win._escape()
    assert p.search_edit.text() == ""
    win._escape()                                           # a second Esc does nothing harmful
    assert p.search_edit.text() == ""


def test_help_search_finds_the_new_features(app, help_panel):
    p = help_panel
    for needle in ("delete", "reorganized", "recent", "undo"):
        p.search_edit.setText(needle)
        assert any(not ql.isHidden() for ql, _a, _h in p._faq_rows), needle
