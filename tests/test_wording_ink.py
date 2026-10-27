import os, re, sys
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import pytest
import acrod as H

SRC = open(os.path.join(os.path.dirname(__file__), "..", "acrod.py"), encoding="utf-8").read()


def test_source_uses_the_word_profile_not_client_or_company():
    assert not re.search(r"client|company", SRC, re.I)


def test_no_court_or_filing_wording_in_the_source():
    assert not re.search(r"court|filing|summons|attorney|clerk|affidavit", SRC, re.I)


def test_mascot_is_ink_and_follows_the_accent_only(monkeypatch):
    a = H.mascot_svg("curious", "#112233")
    b = H.mascot_svg("curious", "#445566")
    assert H._MASCOT_INK in a and "#112233" in a and "#112233" not in b and "#445566" in b
    assert a.replace("#112233", "#445566") == b          # nothing else depends on the accent
    assert 'stroke="#fff"' in a                           # white die-cut border for dark grounds


@pytest.fixture
def app(monkeypatch, tmp_path):
    from PySide6.QtWidgets import QApplication
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    H._reset_ui_state_cache()
    return QApplication.instance() or QApplication([])


@pytest.mark.parametrize("theme", list(H.THEMES))
def test_ink_tab_strip_uses_pills_and_has_no_divider_lines(app, theme):
    H.apply_theme(app, theme, "Normal")
    shell = H.TrayShell()
    css = shell.styleSheet()
    p = H.THEMES[theme]
    assert "QFrame#pillSel" in css and "border-bottom:2px solid" not in css and "border-right" not in css
    assert f"color:{p['accent']}" in css
    assert "border-radius:10px" not in css and "padding:10px 12px" not in css   # old filled cards are gone
    assert shell.brand.parent() is shell.top_bar and shell.verbs_w.parent() is shell.top_bar


def test_pills_follow_the_current_tool_and_the_pointer(app):
    from PySide6.QtCore import QEvent
    from PySide6.QtTest import QTest
    H.apply_theme(app, "Vermilion", "Normal")
    w = H.MainWindow(); w.resize(1360, 860); w.show()
    try:
        sh = w.shell
        sh.verb_buttons["split"].click(); QTest.qWait(400)
        cur = sh.verb_buttons["split"].geometry()
        assert sh._pill_sel.isVisible() and sh._pill_sel.x() == cur.x() and sh._pill_sel.width() == cur.width()
        other = sh.verb_buttons["move"]
        H.QApplication.sendEvent(other, QEvent(QEvent.Enter)); QTest.qWait(400)
        assert sh._pill_hov.isVisible() and sh._pill_hov.x() == other.geometry().x()
        H.QApplication.sendEvent(sh.verbs_w, QEvent(QEvent.Leave))
        assert not sh._pill_hov.isVisible()
    finally:
        w.close()
