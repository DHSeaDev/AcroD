"""Round 7: Extract can delete the extracted pages from the PDF afterwards (Acrobat-style)."""
import os, sys
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from PySide6.QtWidgets import QDialogButtonBox
from PySide6.QtTest import QTest
from PySide6.QtCore import Qt
from pypdf import PdfReader
import acrod as H
from test_organizer_engine import widths
from test_organizer_panel import app, panel, folder, pump, open_row, select, sha  # noqa: F401
from test_organizer_v2 import order


def answer_for(panel, out, delete):
    def fn(n):
        panel._extract_delete = delete
        return (str(out), "ex")
    return fn


def run_extract(app, panel, out, delete, *rows):
    panel._choose_extract_target = answer_for(panel, out, delete)
    select(panel, *rows)
    panel.extract_selected()
    assert pump(app, lambda: not panel._busy)


def test_dialog_checkbox_off_by_default_and_reports_choice(app, tmp_path):
    d = H.ExtractDialog(None, str(tmp_path), "x", 2)
    assert d.delete_box.isEnabled() and not d.delete_box.isChecked() and d.delete_after() is False
    d.delete_box.setChecked(True)
    assert d.delete_after() is True


def test_dialog_checkbox_unavailable_when_every_page_selected(app, tmp_path):
    d = H.ExtractDialog(None, str(tmp_path), "x", 4, can_delete=False)
    assert not d.delete_box.isEnabled()
    d.delete_box.setChecked(True)
    assert d.delete_after() is False


def test_choose_target_passes_can_delete_from_page_count(app, panel, monkeypatch):
    open_row(app, panel, 0, 4)
    seen = []
    class Spy(H.ExtractDialog):
        def exec(self):
            seen.append(self.delete_box.isEnabled())
            self.delete_box.setChecked(self.delete_box.isEnabled())
            return H.QDialog.Accepted
    monkeypatch.setattr(H, "ExtractDialog", Spy)
    assert panel._choose_extract_target(2) is not None and panel._extract_delete is True
    assert panel._choose_extract_target(4) is not None and panel._extract_delete is False
    assert seen == [True, False]


def test_extract_with_delete_removes_pages_and_undo_restores(app, panel, folder, tmp_path):
    open_row(app, panel, 0, 4)
    before = sha(str(folder / "a.pdf"))
    out = tmp_path / "out"; out.mkdir()
    run_extract(app, panel, out, True, 1, 2)
    assert order(panel) == [0, 3] and panel.grid.count() == 2
    assert len(PdfReader(str(out / "ex.pdf")).pages) == 2
    assert "Deleted them" in panel.status.text() and "Undo" in panel.status.text()
    assert sha(str(folder / "a.pdf")) == before                     # original untouched
    panel.undo_pages()
    assert order(panel) == [0, 1, 2, 3]


def test_extract_without_delete_keeps_every_page(app, panel, tmp_path):
    open_row(app, panel, 0, 4)
    out = tmp_path / "out"; out.mkdir()
    run_extract(app, panel, out, False, 1)
    assert order(panel) == [0, 1, 2, 3] and "Deleted" not in panel.status.text()
    assert not panel._undo


def test_extract_cancel_deletes_nothing(app, panel):
    open_row(app, panel, 0, 4)
    def cancel(n):
        panel._extract_delete = True
        return None
    panel._choose_extract_target = cancel
    select(panel, 1); panel.extract_selected()
    assert order(panel) == [0, 1, 2, 3] and panel._extract_pending is None


def test_failed_extract_deletes_nothing(app, panel, tmp_path, monkeypatch):
    open_row(app, panel, 0, 4)
    def boom(*a, **k): raise H.PdfOpError("disk full")
    monkeypatch.setattr(H, "extract_pages", boom)
    panel._choose_extract_target = answer_for(panel, tmp_path, True)
    monkeypatch.setattr(H.Msg, "warning", staticmethod(lambda *a, **k: None), raising=False)
    panel._notify = lambda *a, **k: None
    select(panel, 1); panel.extract_selected()
    assert pump(app, lambda: not panel._busy)
    assert order(panel) == [0, 1, 2, 3] and panel._extract_pending is None


def test_delete_after_extract_keeps_moves_and_rotations(app, panel, tmp_path):
    open_row(app, panel, 0, 4)
    select(panel, 0); panel.move_later()                           # [1,0,2,3]
    select(panel, 3); panel.rotate_right()
    out = tmp_path / "out"; out.mkdir()
    run_extract(app, panel, out, True, 0)                          # position 0 is original page 2
    assert order(panel) == [0, 2, 3]
    assert panel._pages[2]["rot"] == 90


def test_extract_delete_is_one_undo_step(app, panel, tmp_path):
    open_row(app, panel, 0, 4)
    out = tmp_path / "out"; out.mkdir()
    run_extract(app, panel, out, True, 0, 1, 2)
    assert order(panel) == [3] and len(panel._undo) == 1


def test_saved_copy_lacks_extracted_pages(app, panel, folder, tmp_path, monkeypatch):
    open_row(app, panel, 0, 4)
    out = tmp_path / "out"; out.mkdir()
    run_extract(app, panel, out, True, 1)
    monkeypatch.setattr(panel, "_ask_save", lambda *a, **k: "save", raising=False)
    panel.save_and_next()
    assert pump(app, lambda: (folder / "Reorganized" / "a.pdf").exists() and not panel._busy)
    assert len(PdfReader(str(folder / "Reorganized" / "a.pdf")).pages) == 3
    assert len(PdfReader(str(folder / "a.pdf")).pages) == 4


def test_drop_by_idx_never_empties(app, panel):
    open_row(app, panel, 0, 4)
    assert panel._drop_pages_by_idx({0, 1, 2, 3}) == 0 and panel.grid.count() == 4
    assert panel._drop_pages_by_idx(set()) == 0
    assert panel._drop_pages_by_idx({9}) == 0


def test_help_mentions_delete_after_extract(app):
    text = " ".join(a for _q, a in H.HelpPanel.FAQ)
    assert "Delete these pages from the PDF after extracting" in text


def test_a_previous_yes_does_not_leak_into_the_next_extract(app, panel, tmp_path):
    open_row(app, panel, 0, 4)
    out = tmp_path / "out"; out.mkdir()
    run_extract(app, panel, out, True, 0)                          # deletes page 1
    assert order(panel) == [1, 2, 3]
    panel._choose_extract_target = lambda n: (str(out), "ex2")    # a chooser that never touches the flag
    select(panel, 0); panel.extract_selected()
    assert pump(app, lambda: not panel._busy)
    assert order(panel) == [1, 2, 3]


def test_pages_are_not_deleted_from_a_different_pdf_than_was_extracted(app, panel):
    open_row(app, panel, 0, 4)
    panel._extract_pending = {1}
    panel._op_path = "some-other-file.pdf"
    panel._on_saved({"kind": "extract", "pages": 1, "path": "x.pdf"})
    assert order(panel) == [0, 1, 2, 3] and panel._extract_pending is None
    assert "not deleted" in panel.status.text()


# ---- toolbar placement and keyboard undo --------------------------------------
def _row_y(panel, key):
    b = panel.tools[key]
    return b.mapTo(panel, b.rect().center()).y()


def test_all_eight_buttons_are_one_line_at_a_normal_width_and_never_clip(app, panel):
    assert len(panel.tools) == 8
    for width in (1360, 1100, 1000):
        panel.resize(width, 700); panel.show(); pump(app, secs=0.3)
        # the tools now sit in two columns inside the Actions column: never clipped, never outside it
        assert len({_row_y(panel, k) for k in panel.tools}) == 4, width
        for k, b in panel.tools.items():
            need = b.fontMetrics().horizontalAdvance(b.text().replace("&", "")) + 12
            assert b.width() >= need, (width, k, b.width(), need)
            assert panel.inspector.isAncestorOf(b)
        right = max(b.mapTo(panel, b.rect().topRight()).x() for b in panel.tools.values())
        assert right <= panel.inspector.mapTo(panel, panel.inspector.rect().topRight()).x() + 1


def test_flow_layout_is_one_line_when_wide_and_wraps_rather_than_clips_when_narrow(app):
    from PySide6.QtWidgets import QWidget, QPushButton
    host = QWidget(); fl = H.FlowLayout(host, spacing=6)
    for t in ("Alpha one", "Beta two", "Gamma three", "Delta four"):
        fl.addWidget(QPushButton(t, host))
    host.resize(900, 200); host.show(); pump(app, secs=0.2)
    assert len({b.y() for b in host.findChildren(QPushButton)}) == 1
    host.resize(150, 200); pump(app, secs=0.2)
    assert len({b.y() for b in host.findChildren(QPushButton)}) > 1
    assert all(b.x() + b.width() <= 150 or b.x() == 0 for b in host.findChildren(QPushButton))
    host.close()


def test_toolbar_does_not_widen_the_minimum_window(app, panel):
    assert panel.minimumSizeHint().width() <= 1000


def test_new_organizer_settings_validate_and_default():
    assert H.setting("organizer_undo_depth") == 100 and H.setting("organizer_extract_delete_default") is False
    assert H.save_settings({"organizer_undo_depth": 3}) == ["organizer_undo_depth"]
    assert H.save_settings({"organizer_undo_depth": 201}) == ["organizer_undo_depth"]
    assert H.save_settings({"organizer_extract_delete_default": "yes"}) == ["organizer_extract_delete_default"]
    assert H.save_settings({"organizer_undo_depth": 7, "organizer_extract_delete_default": True}) == []
    assert H.setting("organizer_undo_depth") == 7 and H.setting("organizer_extract_delete_default") is True
    H.save_settings({"organizer_undo_depth": 100, "organizer_extract_delete_default": False})


def test_undo_depth_setting_limits_history(app, panel):
    H.save_settings({"organizer_undo_depth": 5})
    try:
        open_row(app, panel, 0, 4)
        for _ in range(12):
            select(panel, 0); panel.rotate_right(); panel.rotate_left(); panel.move_later()
        assert len(panel._undo) == 5
    finally:
        H.save_settings({"organizer_undo_depth": 100})


def test_extract_dialog_starts_ticked_when_the_setting_says_so(app, tmp_path):
    H.save_settings({"organizer_extract_delete_default": True})
    try:
        assert H.ExtractDialog(None, str(tmp_path), "x", 2).delete_after() is True
        d = H.ExtractDialog(None, str(tmp_path), "x", 4, can_delete=False)
        assert d.delete_after() is False and not d.delete_box.isChecked()   # greyed out AND unticked
    finally:
        H.save_settings({"organizer_extract_delete_default": False})


def test_settings_tab_has_the_new_controls_and_saves_them(app):
    sp = H.SettingsPanel() if hasattr(H, "SettingsPanel") else None
    assert sp is not None
    assert sp.adv["organizer_undo_depth"].value() == 100
    sp.adv["organizer_undo_depth"].setValue(40)
    sp.adv["organizer_extract_delete_default"].setChecked(True)
    sp._save_advanced()
    try:
        assert H.setting("organizer_undo_depth") == 40 and H.setting("organizer_extract_delete_default") is True
        sp._reset_advanced()
        assert H.setting("organizer_undo_depth") == 100 and not sp.adv["organizer_extract_delete_default"].isChecked()
    finally:
        H.save_settings({"organizer_undo_depth": 100, "organizer_extract_delete_default": False})


def _window_on_organizer(app, panel_folder):
    from PySide6.QtCore import Qt
    w = H.MainWindow(); w.show(); w.activateWindow()
    idx = next(i for i in range(w.tabs.count()) if w.tabs.tabBar().title(i) == "Page Organizer")
    w.tabs.setCurrentIndex(idx); app.processEvents()
    p = w.panel_at(idx)
    p.folder_row.set_path(str(panel_folder))
    assert pump(app, lambda: p.file_list.count() == 3 and len(p._counts) == 3)
    return w, p


def test_ctrl_z_and_ctrl_shift_z_undo_page_changes(app, folder):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    w, p = _window_on_organizer(app, folder)
    try:
        open_row(app, p, 0, 4)
        for mods in (Qt.ControlModifier, Qt.ControlModifier | Qt.ShiftModifier):
            select(p, 1); p.delete_pages()
            assert order(p) == [0, 2, 3]
            p.grid.setFocus(); app.processEvents()
            QTest.keyClick(p.grid, Qt.Key_Z, mods); app.processEvents()
            assert order(p) == [0, 1, 2, 3], mods
    finally:
        w.close()


def test_ctrl_z_in_a_text_box_undoes_typing_not_pages(app, folder):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    w, p = _window_on_organizer(app, folder)
    try:
        open_row(app, p, 0, 4)
        select(p, 1); p.delete_pages()
        box = p.search_edit if hasattr(p, "search_edit") else None
        if box is None:
            from PySide6.QtWidgets import QLineEdit
            box = next(e for e in p.findChildren(QLineEdit) if e.isVisible() and not e.isReadOnly())
        box.setFocus(); box.setText(""); QTest.keyClicks(box, "abc"); app.processEvents()
        QTest.keyClick(box, Qt.Key_Z, Qt.ControlModifier); app.processEvents()
        assert order(p) == [0, 2, 3] and box.text() != "abc"
    finally:
        p.reset_pages()                       # clean, so closing does not ask about unsaved changes
        w.close()


def test_ctrl_a_selects_every_page_and_delete_key_removes_them_but_one(app, panel):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    open_row(app, panel, 0, 4)
    panel.grid.setFocus(); app.processEvents()
    QTest.keyClick(panel.grid, Qt.Key_A, Qt.ControlModifier)
    assert len(panel.grid.selectedItems()) == 4
    QTest.keyClick(panel.grid, Qt.Key_Delete)
    assert order(panel) == [0, 1, 2, 3]                  # refuses to empty the PDF


def test_move_button_waits_for_its_inputs(app, tmp_path):
    m = H.MoverPanel(); m.show()
    try:
        assert not m.move_btn.isEnabled()
        m.source_row.set_path(str(tmp_path), remember=False)
        m.dest_row.set_path(str(tmp_path / "d"), remember=False)
        assert not m.move_btn.isEnabled()
        m.text_input.setPlainText("123")
        assert m.move_btn.isEnabled()
        m.text_input.setPlainText("  \n")
        assert not m.move_btn.isEnabled()
    finally:
        m.close()


def test_help_answers_start_closed_open_on_click_and_open_when_searched(app):
    h = H.HelpPanel(); h.resize(900, 700); h.show()
    try:
        ql, al, _hay = h._faq_rows[0]
        assert not ql.isHidden() and al.isHidden()
        ql.toggle(); assert not al.isHidden()
        ql.toggle(); assert al.isHidden()
        QTest.keyClick(ql, Qt.Key_Return); assert not al.isHidden()
        ql.toggle()
        h.search_edit.setText("overwrite")
        shown = [(q, a) for q, a, _ in h._faq_rows if not q.isHidden()]
        assert shown and all(not a.isHidden() for _q, a in shown)
        h.search_edit.setText("")
        assert all(a.isHidden() for _q, a, _h in h._faq_rows if not _q.expanded)
    finally:
        h.close()


def test_button_text_is_title_case_everywhere(app, tmp_path):
    r = H.RenamerPanel(); m = H.MoverPanel()
    try:
        assert r.rename_btn.text() == "Rename 0 Files" and r.undo_btn.text() == "Undo Last Rename"
        assert m.move_btn.text() == "Move Files Now"
    finally:
        r.close(); m.close()


def test_optimizer_run_again_sits_with_the_file_pickers(app):
    o = H.OptimizerPanel(); o.resize(1200, 800); o.show()
    try:
        assert o.again_button.parentWidget() is o.browse_button.parentWidget()
        assert o.inspector.isAncestorOf(o.again_button) and o.inspector.isAncestorOf(o.browse_button)
    finally:
        o.close()


def test_first_section_title_sits_at_the_same_spot_on_every_tab(app):
    from PySide6.QtWidgets import QLabel
    H.apply_theme(app)                                   # real fonts and button heights, as the user sees them
    w = H.MainWindow(); w.resize(1360, 860); w.show()
    try:
        ys, xs = {}, {}
        for i in range(7):                                  # Help has a search row above its first section
            w.tabs.setCurrentIndex(i); pump(app, secs=0.4)
            pn = w.tabs.widget(i).panel
            t = [l for l in pn.findChildren(QLabel) if l.isVisible() and l.objectName() == "sectionTitle"
                 and not (getattr(pn, "inspector", None) and pn.inspector.isAncestorOf(l))]
            first = min(t, key=lambda l: (l.mapTo(pn, l.rect().topLeft()).y(), l.mapTo(pn, l.rect().topLeft()).x()))
            g = first.mapTo(pn, first.rect().topLeft())
            ys[i] = g.y() + first.height() // 2; xs[i] = g.x()
        assert set(xs.values()) == {28}, xs
        assert max(ys.values()) - min(ys.values()) <= 1, ys
        w.tabs.setCurrentIndex(7); pump(app, secs=0.4)
        pn = w.tabs.widget(7).panel
        h = [l for l in pn.findChildren(QLabel) if l.isVisible() and l.objectName() == "sectionTitle"
             and not (getattr(pn, "inspector", None) and pn.inspector.isAncestorOf(l))][0]
        assert h.mapTo(pn, h.rect().topLeft()).x() == 28
    finally:
        w.close()
