"""Every interactive control on every tab, and the Extract dialog, has a hover tip."""
import os, sys, tempfile
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import pytest
from PySide6.QtWidgets import QApplication, QPushButton, QSpinBox, QLineEdit
import acrod as H
from test_organizer_panel import app, _iso  # noqa: F401


def ident(w):
    return (type(w).__name__, getattr(w, "text", lambda: "")() if not hasattr(w, "toPlainText") else "",
            w.accessibleName(), getattr(w, "placeholderText", lambda: "")())


@pytest.fixture
def win(app, _iso):
    w = H.MainWindow(); w.show(); app.processEvents()
    yield w
    w.close()


def test_every_control_on_every_tab_has_a_tip(app, win):
    bad = {}
    for i in range(win.tabs.count()):
        win.tabs.setCurrentIndex(i); app.processEvents()
        panel = win.tabs.widget(i).panel
        miss = [ident(w) for w in H.untipped(panel)]       # as built, nothing applied by the test
        if miss:
            bad[type(panel).__name__] = miss
    assert not bad, bad


def test_tooltip_check_can_fail(app):
    """The instrument: a control nobody described must be reported."""
    from PySide6.QtWidgets import QWidget, QVBoxLayout
    host = QWidget(); lay = QVBoxLayout(host)
    lay.addWidget(QPushButton("Zorp the frobnicator")); lay.addWidget(QPushButton("Cancel"))
    miss = H.apply_tooltips(host)
    assert [m.text() for m in miss] == ["Zorp the frobnicator"]
    assert host.findChildren(QPushButton)[1].toolTip()


def test_extract_dialog_controls_have_tips(app, tmp_path):
    d = H.ExtractDialog(None, str(tmp_path), "a_Extract", 2)
    assert not [w for w in d.findChildren(QPushButton) + d.findChildren(QLineEdit) if not w.toolTip()]
    assert d.folder_edit.toolTip() and d.name_edit.toolTip()
    assert d.buttons.button(d.buttons.StandardButton.Ok).toolTip()


def test_tab_strip_has_a_tip_per_tab(app, win):
    assert all(win.tabs.tabToolTip(i) for i in range(win.tabs.count()))


# ---- the Optimizer title stays plain (Pagey is for message boxes only) -------------------------------------------
def _optimizer(app, win):
    idx = next(i for i in range(win.tabs.count()) if win.tabs.tabToolTip(i) == "PDF Optimizer")
    win.tabs.setCurrentIndex(idx); app.processEvents()
    return win.tabs.widget(idx).panel


def test_optimizer_title_has_no_mascot_beside_it(app, win):
    p = _optimizer(app, win)
    assert not hasattr(p, "mascot")
    assert not any(l.accessibleName() == "Pagey the paper mascot" for l in p.findChildren(H.QLabel))
