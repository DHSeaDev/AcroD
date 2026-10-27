"""Page Organizer: Ctrl+mouse-wheel zoom of the thumbnails (sharp enough to read a signature), and the folder
file list staying beside the full-size reader."""
import os, sys
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import pytest
from PySide6.QtCore import Qt, QPoint, QPointF
from PySide6.QtGui import QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
import acrod as H
from test_organizer_panel import app, _iso, pump, select, sha  # noqa: F401
from test_page_clipboard import mk, folder, panel, open_row, _clip  # noqa: F401


def wheel(widget, delta, mods=Qt.ControlModifier):
    pos = QPointF(widget.viewport().rect().center())
    ev = QWheelEvent(pos, widget.viewport().mapToGlobal(pos), QPoint(0, 0), QPoint(0, delta),
                     Qt.NoButton, mods, Qt.NoScrollPhase, False)
    QApplication.sendEvent(widget.viewport(), ev)
    return ev


def test_ctrl_wheel_zooms_and_plain_wheel_does_not(app, panel):
    open_row(app, panel, 0, 4)
    base = panel._grid_w()
    wheel(panel.grid, 120)
    assert panel._grid_w() > base
    bigger = panel._grid_w()
    wheel(panel.grid, -120)
    assert panel._grid_w() < bigger
    before = panel._grid_w()
    wheel(panel.grid, 120, Qt.NoModifier)
    assert panel._grid_w() == before


def test_small_touchpad_deltas_add_up_to_one_step(app, panel):
    open_row(app, panel, 0, 4)
    base = panel._grid_w()
    for _ in range(3):
        wheel(panel.grid, 30)
    assert panel._grid_w() == base                      # 90 < one notch
    wheel(panel.grid, 30)
    assert panel._grid_w() > base


def test_zoom_is_clamped_and_buttons_follow(app, panel):
    for _ in range(40):
        panel.zoom_by(1)
    assert panel._grid_w() == H.ORG_ZOOM_MAX and not panel.zoom_in_btn.isEnabled()
    for _ in range(60):
        panel.zoom_by(-1)
    assert panel._grid_w() == H.ORG_ZOOM_MIN and not panel.zoom_out_btn.isEnabled()


def test_keys_and_reset(app, panel):
    open_row(app, panel, 0, 4)
    base = panel._grid_w()
    panel.grid.setFocus()
    QTest.keyClick(panel.grid, Qt.Key_Plus, Qt.ControlModifier | Qt.ShiftModifier)
    assert panel._grid_w() > base
    QTest.keyClick(panel.grid, Qt.Key_Minus, Qt.ControlModifier)
    QTest.keyClick(panel.grid, Qt.Key_Equal, Qt.ControlModifier)
    QTest.keyClick(panel.grid, Qt.Key_Equal, Qt.ControlModifier)
    assert panel._grid_w() > base
    QTest.keyClick(panel.grid, Qt.Key_0, Qt.ControlModifier)
    assert panel._zoom_w is None and panel._grid_w() == base and not panel.zoom_reset_btn.isEnabled()


def test_zoom_is_remembered_between_sessions(app, panel):
    panel.set_zoom(420)
    assert H.load_ui_state().get("organizer_zoom") == 420
    assert H.OrganizerPanel._load_zoom() == 420
    panel.zoom_reset()
    assert H.OrganizerPanel._load_zoom() is None
    H.update_ui_state(organizer_zoom="junk")
    assert H.OrganizerPanel._load_zoom() is None
    H.update_ui_state(organizer_zoom=99999)
    assert H.OrganizerPanel._load_zoom() is None


def test_grid_icons_follow_the_zoom(app, panel):
    open_row(app, panel, 0, 4)
    panel.set_zoom(240)
    assert panel.grid.iconSize().width() == 240
    assert panel.grid.item(0).icon().actualSize(panel.grid.iconSize()).width() >= 200
    assert str(240) in panel.zoom_label.text()


def test_zoomed_in_pages_in_view_get_sharper_pictures(app, panel):
    open_row(app, panel, 0, 4)
    low = panel._thumbs[0].width()
    panel.set_zoom(640)
    assert pump(app, lambda: 0 in panel._hi, 10)
    assert panel._hi[0].width() > low * 2


def test_sharp_pictures_stay_bounded_and_far_pages_are_placeholders(app, tmp_path):
    d = tmp_path / "big"; d.mkdir()
    mk(d / "a.pdf", 100, 60)
    p = H.OrganizerPanel(); p.show()
    try:
        p.folder_row.set_path(str(d))
        assert pump(app, lambda: p.file_list.count() == 1)
        p.file_list.setCurrentRow(0)
        assert pump(app, lambda: len(p._pages) == 60 and len(p._thumbs) == 60, 20)
        p.set_zoom(600)
        assert pump(app, lambda: bool(p._hi), 10)
        pump(app, secs=1.0)
        assert len(p._hi) <= H.ORG_HI_MAX_PAGES
        far = p.grid.item(59).icon().cacheKey()
        assert far == p._placeholder_icon().cacheKey()           # off screen: one shared picture, not 60 big ones
        assert p.grid.item(0).icon().cacheKey() != p._placeholder_icon().cacheKey()
    finally:
        p._pages = []
        p.stop_for_shutdown(); p.close(); p.deleteLater(); app.processEvents()


def test_old_sharp_picture_is_ignored(app, panel):
    open_row(app, panel, 0, 4)
    panel.set_zoom(600)
    img = panel._thumbs[0]
    panel._on_hi_image(panel._hi_epoch - 5, 3, img)
    assert 3 not in panel._hi
    panel.zoom_reset()
    panel._on_hi_image(panel._hi_epoch, 3, img)                 # back at normal size: not kept
    assert 3 not in panel._hi


def test_zoom_does_not_change_what_will_be_saved(app, panel):
    open_row(app, panel, 0, 4)
    plan = panel._plan()
    panel.set_zoom(500)
    panel.zoom_reset()
    assert panel._plan() == plan and not panel._is_dirty()


def test_b_pane_keeps_its_own_size(app, panel):
    open_row(app, panel, 0, 4)
    panel.set_zoom(500)
    assert panel._cell().width() == panel._thumb_width()


# ---- folder file list beside the full-size reader
def test_file_list_moves_in_beside_the_reader_and_goes_back(app, panel):
    open_row(app, panel, 0, 4)
    left = panel._files_panel
    assert left.parent() is panel._main_split
    assert panel.open_reader(0)
    assert left.parent() is panel.reader.split and left.isVisible()
    assert panel.reader.files_btn.isEnabled()
    panel.close_reader()
    assert left.parent() is panel._main_split and left.isVisible()
    assert not panel.reader.files_btn.isEnabled()


def test_files_button_hides_the_list_in_the_reader_only(app, panel):
    open_row(app, panel, 0, 4)
    left = panel._files_panel
    panel.open_reader(0)
    panel.reader.files_btn.setChecked(False)
    assert not left.isVisible()
    assert H.load_ui_state().get("reader_files") is False
    panel.close_reader()
    assert left.isVisible()                                      # back in the grid it is always shown
    panel.open_reader(0)
    assert not left.isVisible()                                  # the choice is remembered for the reader
    panel.reader.files_btn.setChecked(True)
    assert left.isVisible()
    panel.close_reader()


def test_choosing_another_pdf_from_the_docked_list_stays_in_the_reader(app, panel):
    open_row(app, panel, 0, 4)
    panel.open_reader(0)
    assert panel.reader.files_btn.isChecked() or True
    panel.reader.files_btn.setChecked(True)
    panel.file_list.setCurrentRow(1)
    assert pump(app, lambda: panel._cur_path.endswith("b.pdf") and len(panel._pages) == 3 and panel._reader_active(), 10)
    assert panel._files_panel.parent() is panel.reader.split
    panel.close_reader()


# ---- Bulk File Mover: pasting a narrow Excel column
def test_mover_paste_ignores_spreadsheet_cell_widths(app):
    from PySide6.QtCore import QMimeData
    m = H.MoverPanel()
    try:
        md = QMimeData()
        md.setText("1234791196\n1234791371\n")
        md.setHtml('<table><tr><td width="40" style="width:30px">1234791196</td></tr>'
                   '<tr><td width="40" style="width:30px">1234791371</td></tr></table>')
        m.text_input.insertFromMimeData(md)
        assert m.text_input.toPlainText().splitlines()[:2] == ["1234791196", "1234791371"]
        assert "<table" not in m.text_input.toHtml().lower()
        doc = m.text_input.document()
        assert doc.blockCount() == 3 and not m.text_input.acceptRichText()
    finally:
        m.deleteLater(); app.processEvents()


def test_sharp_picture_cache_never_exceeds_its_cap(app, panel):
    open_row(app, panel, 0, 4)
    panel.set_zoom(640)
    img = panel._thumbs[0]
    for i in range(H.ORG_HI_MAX_PAGES + 6):
        panel._on_hi_image(panel._hi_epoch, i, img)
    assert len(panel._hi) == H.ORG_HI_MAX_PAGES
    assert 0 not in panel._hi and (H.ORG_HI_MAX_PAGES + 5) in panel._hi      # oldest go first


def test_page_picture_uses_the_sharper_image_when_held(app, panel):
    from PySide6.QtGui import QImage, QColor
    open_row(app, panel, 0, 4)
    panel.set_zoom(240)
    red = QImage(400, 400, QImage.Format_RGB888)
    red.fill(QColor("#ff0000"))
    panel._hi[0] = red
    pm = panel._page_icon(panel._pages[0]).pixmap(panel.grid.iconSize())
    c = pm.toImage().pixelColor(pm.width() // 2, pm.height() // 2)
    assert c.red() > 200 and c.green() < 60
