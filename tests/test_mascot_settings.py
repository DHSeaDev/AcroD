import os, sys
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import xml.etree.ElementTree as ET
import pytest
import acrod as H


@pytest.fixture
def app(monkeypatch, tmp_path):
    from PySide6.QtWidgets import QApplication
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    H._reset_ui_state_cache()
    return QApplication.instance() or QApplication([])


def test_mascot_svgs_are_valid_and_distinct(app):
    seen = set()
    for mood in H._MASCOT_MOODS:
        svg = H.mascot_svg(mood, "#5B54B8")
        root = ET.fromstring(svg)
        ids = [e.get("id") for e in root.iter() if e.get("id")]
        assert len(ids) == len(set(ids)) and all(i.startswith("pgy-" + mood) for i in ids)
        for e in root.iter():                      # no dangling url(#id)
            for v in e.attrib.values():
                if "url(#" in v:
                    assert v[v.index("#") + 1:v.index(")")] in ids
        seen.add(svg)
    assert len(seen) == len(H._MASCOT_MOODS)
    assert set(H._KIND_MOOD.values()) == set(H._MASCOT_MOODS)


def test_mascot_pixmaps_have_ink_and_differ(app):
    imgs = []
    for mood in H._MASCOT_MOODS:
        im = H.mascot_pixmap(mood, 84).toImage()
        opaque = sum(1 for x in range(0, im.width(), 4) for y in range(0, im.height(), 4)
                     if im.pixelColor(x, y).alpha() > 200)
        assert opaque > 300
        imgs.append(im.bits().tobytes() if hasattr(im.bits(), "tobytes") else bytes(im.bits()))
    assert len(set(imgs)) == 4


def test_msg_honours_patched_native_box(app, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Yes))
    assert H.Msg.question(None, "t", "x", QMessageBox.Yes | QMessageBox.No, QMessageBox.No) \
        == QMessageBox.Yes


def test_msg_falls_back_to_native_when_switched_off(app, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    H.save_settings({"mascot_dialogs": False})
    calls = []
    monkeypatch.setitem(H._NATIVE_BOX, "information",
                        lambda *a, **k: calls.append(a) or QMessageBox.Ok)
    monkeypatch.setattr(QMessageBox, "information",
                        H._NATIVE_BOX["information"])
    H.Msg.information(None, "t", "x")
    assert calls


def test_msg_builds_a_mascot_box(app, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    seen = {}
    monkeypatch.setattr(QMessageBox, "exec",
                        lambda self: seen.update(pm=not self.iconPixmap().isNull(),
                                                 text=self.text()) or int(QMessageBox.Yes))
    r = H.Msg.question(None, "t", "hello", QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
    assert seen == {"pm": True, "text": "hello"} and r == QMessageBox.Yes


def test_optimizer_settings_validated(app):
    assert H.setting("optimizer_flatten_dpi") == 120
    assert H.setting("optimizer_allow_flatten") is True
    assert H.setting("optimizer_open_when_done") is False
    assert H.save_settings({"optimizer_flatten_dpi": 5}) == ["optimizer_flatten_dpi"]
    assert H.save_settings({"optimizer_flatten_dpi": 400}) == ["optimizer_flatten_dpi"]
    assert H.save_settings({"optimizer_allow_flatten": "no"}) == ["optimizer_allow_flatten"]
    assert H.save_settings({"optimizer_flatten_dpi": 150, "optimizer_allow_flatten": False}) == []
    assert H.setting("optimizer_flatten_dpi") == 150 and H.setting("optimizer_allow_flatten") is False


def test_settings_panel_saves_immediately_and_resets(app):
    pnl = H.SettingsPanel()
    pnl.opt_dpi.setValue(144); pnl.opt_open_box.setChecked(True)
    pnl.opt_flat_box.setChecked(False); pnl.mascot_box.setChecked(False)
    assert (H.setting("optimizer_flatten_dpi"), H.setting("optimizer_open_when_done"),
            H.setting("optimizer_allow_flatten"), H.setting("mascot_dialogs")) == (144, True, False, False)
    pnl._reset_advanced()
    assert pnl.opt_dpi.value() == 120 and not pnl.opt_open_box.isChecked()
    assert pnl.opt_flat_box.isChecked() and pnl.mascot_box.isChecked()


def test_flatten_dpi_reaches_engine_options(app):
    H.save_settings({"optimizer_flatten_dpi": 96, "optimizer_allow_flatten": False})
    panel = H.OptimizerPanel()
    seen = {}
    import acrod
    orig = acrod.OptimizeOptions
    def spy(**kw):
        seen.update(kw); return orig(**kw)
    acrod.OptimizeOptions = spy
    try:
        import inspect
        src = inspect.getsource(H.OptimizerPanel)
    finally:
        acrod.OptimizeOptions = orig
    assert 'flatten_dpi=int(setting("optimizer_flatten_dpi"))' in src
    assert 'allow_flatten=bool(setting("optimizer_allow_flatten"))' in src
