"""Round 5: dragged pages must not vanish, Page Organizer undo, the mascot icon,
and Recent paths clearing when the window closes."""
import os, sys
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import pytest
from PySide6.QtCore import Qt, QPoint, QPointF, QMimeData, QByteArray
from PySide6.QtGui import QDropEvent, QColor
import acrod as H
from test_organizer_panel import app, panel, folder, pump, open_row, select, _iso  # noqa: F401
from test_organizer_v2 import order, point_before, point_after

ROOT = os.path.join(os.path.dirname(__file__), "..")


# ---- dragging never deletes the dragged pages ------------------------------
class FakeDrag:
    """Offscreen Qt cannot run a real OS drag, so this stands in for QDrag: it
    records how the drag was started and performs the drop on the grid the way
    Qt would: the drop offers exactly the actions the drag was started with. It
    returns the action the drop finished with - Qt acts on exactly that value,
    and a MoveAction makes QAbstractItemView delete the dragged items."""
    target = None
    started = []

    def __init__(self, source):
        self.source = source

    def setMimeData(self, m):
        self.mime = m

    def setPixmap(self, pm):
        pass

    def exec(self, supported, default=Qt.IgnoreAction):
        FakeDrag.started.append((supported, default))
        ev = QDropEvent(QPointF(FakeDrag.target), supported, self.mime, Qt.LeftButton, Qt.NoModifier)
        self.source.dropEvent(ev)
        return ev.dropAction()


def run_drag(monkeypatch, panel, rows, point):
    monkeypatch.setattr(H, "QDrag", FakeDrag)
    FakeDrag.target, FakeDrag.started = point, []
    g = panel.grid
    g.clearSelection()
    for r in rows:
        g.item(r).setSelected(True)
    g.startDrag(Qt.CopyAction | Qt.MoveAction)


@pytest.mark.parametrize("src,where", [(3, "before0"), (0, "after3"), (1, "before3"), (2, "before0")])
def test_dragged_pages_reorder_and_stay_visible(app, panel, monkeypatch, src, where):
    open_row(app, panel, 0, 4); panel.show(); pump(app, secs=0.2)
    g = panel.grid
    pt = point_before(g, int(where[-1])) if where.startswith("before") else point_after(g, int(where[-1]))
    run_drag(monkeypatch, panel, [src], pt)
    assert FakeDrag.started and all(a == Qt.CopyAction for a, _d in FakeDrag.started)
    assert g.count() == 4, "a dragged page vanished from the grid"
    assert sorted(order(panel)) == [0, 1, 2, 3]
    assert [g.item(r).data(H.OrganizerPanel._ROLE_IDX) for r in range(4)] == order(panel)
    assert order(panel) != [0, 1, 2, 3]
    assert all(not g.item(r).isHidden() and not g.item(r).icon().isNull() for r in range(4))


def test_a_multi_page_drag_keeps_every_page(app, panel, monkeypatch):
    open_row(app, panel, 0, 4); panel.show(); pump(app, secs=0.2)
    run_drag(monkeypatch, panel, [0, 2], point_after(panel.grid, 3))
    assert panel.grid.count() == 4 and order(panel) == [1, 3, 0, 2]


def test_the_drag_is_copy_only_so_qt_can_never_delete_the_source_items(app, panel, monkeypatch):
    open_row(app, panel, 0, 4); panel.show(); pump(app, secs=0.2)
    run_drag(monkeypatch, panel, [1], point_after(panel.grid, 3))
    assert FakeDrag.started == [(Qt.CopyAction, Qt.CopyAction)]
    g = panel.grid
    assert g.supportedDragActions() == Qt.CopyAction and g.supportedDropActions() == Qt.CopyAction
    assert g.defaultDropAction() == Qt.CopyAction


def test_a_drop_finishes_as_copy_and_accepted(app, panel):
    open_row(app, panel, 0, 4); panel.show(); pump(app, secs=0.2)
    mime = QMimeData()
    mime.setData(H._PageGrid.MIME, QByteArray(b"0"))
    pt = point_after(panel.grid, 3)
    ev = QDropEvent(QPointF(pt), Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier)
    panel.grid.dropEvent(ev)
    assert ev.dropAction() == Qt.CopyAction and ev.isAccepted()
    assert order(panel) == [1, 2, 3, 0]


# ---- undo -------------------------------------------------------------------
def test_undo_button_exists_and_starts_disabled(app, panel):
    assert "undo" in panel.tools and panel.SHORTCUT_UNDO == "undo_pages"
    open_row(app, panel, 0, 4)
    assert not panel.tools["undo"].isEnabled()


def test_undo_takes_back_a_move_a_rotate_and_a_remove_in_order(app, panel):
    open_row(app, panel, 0, 4); panel.show(); pump(app, secs=0.2)
    select(panel, 1); panel.move_later()
    assert order(panel) == [0, 2, 1, 3] and panel.tools["undo"].isEnabled()
    select(panel, 0); panel.rotate_right()
    assert panel._pages[0]["rot"] == 90
    select(panel, 3); panel.delete_pages()
    assert order(panel) == [0, 2, 1]
    panel.undo_pages()
    assert order(panel) == [0, 2, 1, 3]
    assert panel._pages[0]["rot"] == 90                 # ONLY the remove was taken back
    panel.undo_pages()
    assert all(p["rot"] == 0 for p in panel._pages)
    panel.undo_pages()
    assert order(panel) == [0, 1, 2, 3] and not panel._is_dirty()
    assert not panel.tools["undo"].isEnabled()
    panel.undo_pages()                                  # nothing left: harmless
    assert order(panel) == [0, 1, 2, 3]


def test_undo_after_a_drag_drop_restores_the_order_and_the_grid(app, panel, monkeypatch):
    open_row(app, panel, 0, 4); panel.show(); pump(app, secs=0.2)
    run_drag(monkeypatch, panel, [3], point_before(panel.grid, 0))
    assert order(panel) == [3, 0, 1, 2]
    panel.undo_pages()
    assert order(panel) == [0, 1, 2, 3] and panel.grid.count() == 4
    assert [panel.grid.item(r).data(H.OrganizerPanel._ROLE_IDX) for r in range(4)] == [0, 1, 2, 3]


def test_reset_can_itself_be_undone_and_a_no_op_leaves_no_undo_step(app, panel):
    open_row(app, panel, 0, 4); panel.show(); pump(app, secs=0.2)
    select(panel, 0); panel.move_earlier()            # already first: nothing changes
    assert not panel._undo
    select(panel, 0); panel.rotate_left(); select(panel, 2); panel.move_later()
    panel.reset_pages()
    assert not panel._is_dirty()
    panel.undo_pages()
    assert panel._is_dirty() and order(panel) == [0, 1, 3, 2] and panel._pages[0]["rot"] == 270


def test_undo_history_is_dropped_when_another_pdf_opens(app, panel, monkeypatch):
    monkeypatch.setattr(panel, "_ask_save", lambda name: H.QMessageBox.Discard)
    open_row(app, panel, 0, 4); panel.show(); pump(app, secs=0.2)
    select(panel, 1); panel.move_later()
    assert panel._undo
    open_row(app, panel, 1, 3)
    assert panel._undo == [] and not panel.tools["undo"].isEnabled()


def test_undo_history_is_bounded(app, panel):
    open_row(app, panel, 0, 4); panel.show(); pump(app, secs=0.2)
    for _ in range(H.OrganizerPanel.UNDO_MAX + 25):
        select(panel, 0); panel.rotate_right()
    assert len(panel._undo) == H.OrganizerPanel.UNDO_MAX


def test_ctrl_shift_z_reaches_the_organizer_undo(app, panel):
    open_row(app, panel, 0, 4); panel.show(); pump(app, secs=0.2)
    select(panel, 1); panel.move_later()
    fn = getattr(panel, panel.SHORTCUT_UNDO)
    fn()
    assert order(panel) == [0, 1, 2, 3]


# ---- the D-knockout page mark is the app icon --------------------------------
def test_window_icon_is_the_d_page_mark_not_pagey(app):
    icon = H.app_icon()
    for px in (16, 32, 64, 256):
        img = icon.pixmap(px, px).toImage()
        assert img == H._svg_pixmap(H.APP_ICON_SVG, px, 1.0).toImage()
    img = icon.pixmap(64, 64).toImage()
    assert img != H._svg_pixmap(H.mascot_svg("happy"), 64, 1.0).toImage()
    c = img.pixelColor(32, 8)                       # the page above the knocked-out D
    assert c.alpha() == 255 and c.red() > 180 and c.blue() < 90   # vermilion
    assert img.pixelColor(2, 2).alpha() == 0        # outside the page is transparent
    assert img.pixelColor(20, 36).alpha() == 0      # the D stem is knocked out of the page


def test_exe_icon_file_ships_and_the_build_uses_it():
    from PIL import Image
    ico = Image.open(os.path.join(ROOT, "AcroD.ico"))
    assert {(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)} <= set(ico.info["sizes"])
    ico.size = (256, 256); ico.load()
    assert ico.convert("RGBA").getpixel((128, 60))[:3] == (217, 72, 43)   # the vermilion page, not Pagey
    bat = open(os.path.join(ROOT, "BUILD  double click me.bat"), encoding="utf-8").read()
    assert "AcroD.ico" in bat and "--icon" in bat and "!ICONARG!" in bat


# ---- Recent paths clear on close --------------------------------------------
def test_closing_the_window_clears_every_recent_list_and_remembered_folder(app):
    for key in ("organizer_folders", "renamer_folders", "mover_sources", "mover_destinations", "merger_references"):
        H.push_recent(key, "C:/Some/Path")
    H.update_ui_state(merger_last_reference="a.pdf", splitter_last_dir="d", renamer_last_folder="r",
                      mover_last_source="s", mover_last_dest="t", organizer_last_folder="o")
    H.save_settings({"undo_depth": 7})
    w = H.MainWindow(); w.show(); w.tabs.setCurrentIndex(2); app.processEvents()
    w.close(); app.processEvents()
    H._reset_ui_state_cache()
    state = H.load_ui_state()
    assert "recent" not in state
    for key in H.REMEMBERED_PATH_KEYS:
        assert key not in state, key
    for key in ("organizer_folders", "renamer_folders", "mover_sources", "mover_destinations", "merger_references"):
        assert H.recent_paths(key) == []
    assert state.get("last_tab") == 2 and "window_geometry" in state     # not a settings wipe
    assert H.setting("undo_depth") == 7


def test_clear_recent_paths_is_safe_on_an_empty_or_odd_state(app):
    H.clear_recent_paths()
    H.update_ui_state(recent="garbage")
    H.clear_recent_paths()
    assert "recent" not in H.load_ui_state()


def test_a_cancelled_close_keeps_the_recent_paths(app, monkeypatch):
    H.push_recent("organizer_folders", "C:/keep/me")
    w = H.MainWindow(); w.show(); app.processEvents()
    idx = next(i for i in range(w.tabs.count()) if w.tabs.tabToolTip(i) == "Page Organizer")
    w.tabs.setCurrentIndex(idx); app.processEvents()
    org = w.panel_at(idx)
    assert isinstance(org, H.OrganizerPanel)
    org._pages = [{"idx": 0, "rot": 90, "deleted": False, "blank": False, "match": False}]
    monkeypatch.setattr(H.Msg, "question", staticmethod(lambda *a, **k: H.QMessageBox.No))
    w.close(); app.processEvents()
    assert H.recent_paths("organizer_folders") == ["C:/keep/me"]
    org._pages = []
    w.close()
    H._reset_ui_state_cache()
    assert H.recent_paths("organizer_folders") == []


# ---- toolbar: Remove / restore and Select blank pages are right-click only ---
def test_toolbar_has_delete_but_no_select_blank_button_and_the_menu_has_both(app, panel):
    open_row(app, panel, 0, 4); panel.show(); pump(app, secs=0.2)
    assert set(panel.tools) == {"rotl", "rotr", "del", "up", "down", "undo", "ext", "reset"}
    texts = {b.text() for b in panel.tools.values()}
    assert "Delete page" in texts and "Remove / restore" not in texts and "Select blank pages" not in texts
    m = panel._build_grid_menu(panel.grid.visualItemRect(panel.grid.item(0)).center())
    labels = [a.text() for a in m.actions()]
    assert "Delete this page" in labels and "Select blank pages" in labels
    select(panel, 2); panel.delete_pages()
    assert order(panel) == [0, 1, 3]
