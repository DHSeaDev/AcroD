import os, sys
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import pytest
import acrod as H
from PySide6.QtTest import QTest


@pytest.fixture
def app(monkeypatch, tmp_path):
    from PySide6.QtWidgets import QApplication
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    H._reset_ui_state_cache()
    return QApplication.instance() or QApplication([])


def _window():
    w = H.MainWindow(); w.resize(1360, 860); w.show()
    return w


def _panels(w):
    out = []
    for i in range(w.tabs.count()):
        w.tabs.setCurrentIndex(i)
        QTest.qWait(150)                  # let the page lay out before measuring it
        p = w.panel_at(i)
        if p is not None and getattr(p, "inspector", None) is not None:
            out.append(p)
    return out


def _x(panel):
    return panel.inspector.x()          # relative to the widget that holds it (the tool's page)


def _w(panel):
    return panel.inspector.parentWidget().width()


def test_defaults_are_right_and_open():
    assert H.ADVANCED_DEFAULTS["actions_side"] == "right"
    assert H.ADVANCED_DEFAULTS["actions_collapsed"] is False
    assert H._valid_setting("actions_side", "left") and not H._valid_setting("actions_side", "top")
    assert H._valid_setting("actions_collapsed", True) and not H._valid_setting("actions_collapsed", 1)


def test_bad_saved_values_fall_back(app):
    H.update_ui_state(advanced={"actions_side": "sideways", "actions_collapsed": "yes"})
    assert H.setting("actions_side") == "right" and H.setting("actions_collapsed") is False


def test_every_tool_puts_actions_on_the_right_by_default(app):
    w = _window()
    try:
        ps = _panels(w)
        assert len(ps) >= 6
        for p in ps:
            assert _x(p) > _w(p) // 2, type(p).__name__
    finally:
        w.close()


def test_side_setting_moves_every_tool_live_even_unbuilt_ones(app):
    w = _window()
    try:
        w.tabs.setCurrentIndex(0)
        H.save_settings({"actions_side": "left"}); H.refresh_inspectors()
        for p in _panels(w):                       # builds the rest lazily AFTER the change
            assert _x(p) < _w(p) // 2, type(p).__name__
        H.save_settings({"actions_side": "right"}); H.refresh_inspectors()
        for p in _panels(w):
            assert _x(p) > _w(p) // 2
    finally:
        w.close()


def test_collapse_shrinks_to_a_rail_and_keeps_buttons_alive(app):
    w = _window()
    try:
        p = _panels(w)[0]
        w.tabs.setCurrentIndex(0); QTest.qWait(150)
        open_w = p.inspector.width()
        w.toggle_actions()
        assert p.inspector.width() == H._RAIL_WIDTH < open_w
        assert not p.inspector.body.isVisible() and p.inspector.toggle_btn.isVisible()
        assert H.setting("actions_collapsed") is True
        w.toggle_actions()
        assert p.inspector.width() == open_w and p.inspector.body.isVisible()
    finally:
        w.close()


def test_rail_button_and_settings_controls_stay_in_step(app):
    w = _window()
    try:
        w.tabs.setCurrentIndex(w.tabs.count() - 2)           # Settings
        st = w.panel_at(w.tabs.currentIndex())
        assert st.side_buttons["right"].isChecked() and not st.collapse_box.isChecked()
        _panels(w)[0].inspector.toggle_btn.click()
        assert st.collapse_box.isChecked()
        st.side_buttons["left"].setChecked(True)
        assert H.setting("actions_side") == "left"
        st.collapse_box.setChecked(False)
        assert H.setting("actions_collapsed") is False
        st._reset_advanced()
        assert H.setting("actions_side") == "right" and st.side_buttons["right"].isChecked()
    finally:
        w.close()


def test_settings_controls_all_have_tips(app):
    w = _window()
    try:
        w.tabs.setCurrentIndex(w.tabs.count() - 2)
        st = w.panel_at(w.tabs.currentIndex())
        for b in list(st.side_buttons.values()) + [st.collapse_box]:
            assert b.toolTip().strip()
    finally:
        w.close()


def test_rail_button_is_an_icon_not_chevron_text(tmp_path, monkeypatch):
    import acrod as H
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    H._reset_ui_state_cache()
    for side in ("right", "left"):
        for open_ in (True, False):
            assert not H.panel_toggle_icon(side, open_).isNull()
