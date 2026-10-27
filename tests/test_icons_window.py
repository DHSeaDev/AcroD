import os, sys
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import pytest
import acrod as H
from PySide6.QtWidgets import QApplication, QPushButton


@pytest.fixture(scope="module")
def app():
    a = QApplication.instance() or QApplication([])
    H.apply_theme(a, "Light", "Normal")
    return a


def test_every_mapped_glyph_exists():
    assert set(H._BUTTON_ICONS.values()) <= set(H._ICON_PATHS)
    assert set(H._TAB_ICONS.values()) <= set(H._ICON_PATHS)


def test_key_lookup_handles_ellipsis_caret_and_dynamic_text():
    assert H._icon_key_for("Choose PDFs to Split...") == "files"
    assert H._icon_key_for("Export Presets…") == "upload"
    assert H._icon_key_for("RENAME 12 FILES") == "tag"
    assert H._icon_key_for("Rename...") == "pencil"
    assert H._icon_key_for("Recent ▾") is None


def test_pixmaps_render_nonblank_in_each_colour(app):
    for key in H._ICON_PATHS:
        pm = H.glyph_pixmap(key, "#ff0000", 18)
        img = pm.toImage()
        ink = sum(1 for x in range(img.width()) for y in range(img.height())
                  if img.pixelColor(x, y).alpha() > 0)
        assert ink > 20, key


def test_decorate_sets_icon_and_survives_text_change(app):
    from PySide6.QtWidgets import QWidget, QVBoxLayout
    host = QWidget(); lay = QVBoxLayout(host)
    b = QPushButton("RENAME 0 FILES"); lay.addWidget(b)
    plain = QPushButton("Recent ▾"); lay.addWidget(plain)
    H.decorate_icons(host)
    assert not b.icon().isNull() and plain.icon().isNull()
    b.setText("RENAME 40 FILES"); H.decorate_icons(host)
    assert not b.icon().isNull()


def test_app_icon_renders(app):
    assert not H.app_icon().isNull()


def test_window_can_be_resized_small_and_floor_is_not_the_old_one(app):
    w = H.MainWindow()
    for i in range(w.tabs.count()):
        w.tabs.widget(i).ensure()
    assert w.minimumWidth() <= 720 and w.minimumHeight() <= 480
    w.show(); w.resize(760, 500); app.processEvents()
    assert (w.width(), w.height()) == (760, 500)
    # every tab keeps its content reachable: it lives in a scroll area
    from PySide6.QtWidgets import QScrollArea
    assert all(isinstance(w.tabs.widget(i).layout().itemAt(0).widget(), QScrollArea)
               for i in range(w.tabs.count()))
    w.close()                                           # a leftover shown MainWindow broke text-box tests that run later
    w.deleteLater()
    app.processEvents()
