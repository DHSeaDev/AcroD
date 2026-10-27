"""First-run guide, default-PDF registration, and the full-screen reader launch."""
import os, sys
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import pytest
from pypdf import PdfWriter
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QMessageBox
import acrod as H
from test_organizer_panel import app, pump  # noqa: F401


class FakeReg:
    def __init__(self):
        self.d = {}

    def set(self, path, name, value):
        self.d[(path, name)] = value

    def get(self, path, name):
        return self.d.get((path, name))

    def delete_value(self, path, name):
        self.d.pop((path, name), None)

    def delete_tree(self, path):
        for k in [k for k in self.d if k[0] == path or k[0].startswith(path + "\\")]:
            del self.d[k]


@pytest.fixture(autouse=True)
def _iso(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "appdata"))
    H._reset_ui_state_cache()
    H._AUTO.update(narrow=False, open=False)


def mk(path, pages=2):
    w = PdfWriter()
    for _ in range(pages):
        w.add_blank_page(width=100, height=100)
    with open(path, "wb") as f:
        w.write(f)
    return str(path)


EXE = r"C:\Users\x\AppData\Local\Programs\AcroD\AcroD.exe"


def test_registration_writes_only_what_windows_needs_and_undoes_cleanly():
    reg = FakeReg()
    ok, _m = H.register_pdf_handler(EXE, reg)
    assert ok
    assert reg.get(H._REG_PROG + r"\shell\open\command", "") == f'"{EXE}" "%1"'
    assert reg.get(H._REG_CAPS + r"\FileAssociations", ".pdf") == "AcroD.PDF"
    assert reg.get(H._REG_REGAPPS, "AcroD") == H._REG_CAPS
    assert H.pdf_handler_registered(EXE, reg)
    assert all(p.startswith("Software\\") for p, _n in reg.d)               # HKCU only, never machine-wide
    ok, _m = H.unregister_pdf_handler(reg)
    assert ok and reg.d == {} and not H.pdf_handler_registered(EXE, reg)


def test_a_blocked_registry_reports_and_never_raises():
    class Boom(FakeReg):
        def set(self, *a):
            raise PermissionError("no")
    ok, msg = H.register_pdf_handler(EXE, Boom())
    assert not ok and "Nothing was changed" in msg


def test_registration_is_offered_only_for_an_installed_windows_exe(monkeypatch):
    assert not H.pdf_handler_available()                                      # from source
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert H.pdf_handler_available()


def test_first_run_shows_until_the_guide_has_been_seen():
    assert H.first_run_pending()
    H.mark_first_run_done()
    assert not H.first_run_pending()
    H._reset_ui_state_cache()
    os.remove(H.ui_state_path())
    H._reset_ui_state_cache()
    H.update_ui_state(theme="Dark")                                           # an upgrade with a saved look still sees it once
    assert H.first_run_pending()
    H.mark_first_run_done()
    assert not H.first_run_pending()


def test_startup_log_says_which_build_ran_and_why(tmp_path):
    H.log_startup("first_run_pending=True forced=False pdf_launch=False -> guide=yes")
    H.log_startup("second")
    lines = open(os.path.join(H._log_dir(), "startup.log"), encoding="utf-8").read().splitlines()
    assert lines[-2].count(H.APP_VERSION) == 1 and "guide=yes" in lines[-2] and lines[-1].endswith("second")


def test_guide_walks_four_pages_and_registers_when_asked(app, monkeypatch):
    calls = []
    d = H.FirstRunDialog(register=lambda: (calls.append("reg") or (True, "ok")),
                         open_settings_page=lambda: calls.append("page"))
    d.can_register = True; d.pdf_box.setEnabled(True); d.pdf_box.setChecked(True)
    seen = [d.stack.currentIndex()]
    for _ in range(3):
        d.next_btn.click(); seen.append(d.stack.currentIndex())
    assert seen == [0, 1, 2, 3] and calls == ["reg", "page"]
    assert "registered" in d.ready_text.text()
    d.next_btn.click()
    assert d.result() == int(H.QDialog.DialogCode.Accepted) and not H.first_run_pending()


def test_guide_without_the_checkbox_changes_nothing_on_the_computer(app):
    calls = []
    d = H.FirstRunDialog(register=lambda: (calls.append("reg") or (True, "ok")), open_settings_page=lambda: calls.append("page"))
    d.pdf_box.setChecked(False)
    for _ in range(4):
        d.next_btn.click()
    assert calls == []


def test_guide_previews_the_look_and_skip_puts_it_back(app):
    d = H.FirstRunDialog()
    d.theme_buttons["Dark"].setChecked(True); d.side_buttons["left"].setChecked(True)
    QTest.qWait(80)
    assert H.load_ui_state()["theme"] == "Dark" and H.setting("actions_side") == "left"
    d.skip_btn.click()
    assert H.load_ui_state()["theme"] == H.DEFAULT_THEME and H.setting("actions_side") == "right"
    assert not H.first_run_pending()                                          # skipped is still seen: it never nags


def test_guide_keeps_the_chosen_look_when_finished(app):
    d = H.FirstRunDialog()
    d.pdf_box.setChecked(False)
    d.theme_buttons["Dark"].setChecked(True); QTest.qWait(80)
    for _ in range(4):
        d.next_btn.click()
    assert H.load_ui_state()["theme"] == "Dark"


@pytest.fixture
def win(app):
    w = H.MainWindow(); w.resize(1100, 760); w.show(); QTest.qWait(150)
    yield w
    w.close()


def test_a_launched_pdf_fills_the_screen_and_tucks_the_tray_away_for_the_session(win, tmp_path):
    win.shell._set_tray_wanted(True)
    assert win.shell.tray.isVisible()
    assert win.open_pdf_from_launch(mk(tmp_path / "a.pdf"))
    QTest.qWait(200)
    assert win.isMaximized()
    assert not win.shell.tray.isVisible()
    assert H.load_ui_state().get("tray_visible") is True                      # the saved choice is untouched
    assert H.setting("actions_collapsed") is False                            # Actions stays open


def test_turning_the_setting_off_leaves_the_window_alone(win, tmp_path):
    H.save_settings({"launch_reader_full": False})
    win.open_pdf_from_launch(mk(tmp_path / "a.pdf")); QTest.qWait(150)
    assert not win.isMaximized()


def test_settings_has_the_new_controls_and_reset_restores_them(win):
    win.tabs.setCurrentIndex(win.tabs.count() - 2); QTest.qWait(200)
    p = win.panel_at(win.tabs.count() - 2)
    p.launch_full_box.setChecked(False)
    assert H.setting("launch_reader_full") is False
    p._reset_advanced(); assert p.launch_full_box.isChecked()
    assert p.default_pdf_btn.toolTip() and p.guide_btn.toolTip()


def test_default_pdf_button_explains_itself_from_source(win):
    win.tabs.setCurrentIndex(win.tabs.count() - 2); QTest.qWait(200)
    p = win.panel_at(win.tabs.count() - 2)
    p.default_pdf_btn.click()
    assert "installed Windows program" in p.adv_status.text()


ROOT = os.path.join(os.path.dirname(__file__), "..")


def test_installer_script_writes_the_same_registry_keys_as_the_app():
    iss = open(os.path.join(ROOT, "AcroD.iss"), encoding="utf-8").read()
    assert "PrivilegesRequired=lowest" in iss and "HKLM" not in iss                      # per user, never machine-wide
    assert f'#define AppVersion "{H.APP_VERSION}"' in iss
    for path, _name, _value in H.pdf_handler_entries("X"):
        assert path.replace("\\", "\\") in iss.replace("{#AppExe}", "AcroD.exe"), path


def test_build_script_wires_the_splash_only_when_it_can_and_keeps_tkinter_out_otherwise():
    bat = open(os.path.join(ROOT, "BUILD  double click me.bat"), encoding="utf-8").read()
    assert "--splash" in bat and "!SPLASHARG!" in bat and "!TKARG!" in bat
    assert 'set "TKARG=--exclude-module tkinter"' in bat and 'import tkinter' in bat
    assert os.path.getsize(os.path.join(ROOT, "AcroD-splash.png")) > 2000


def test_command_line_runs_close_the_splash_and_the_splash_is_never_fatal():
    s = H.Splash()
    s.text("x"); s.close()
