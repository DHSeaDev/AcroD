"""Send To: files made in one tab open in another, through the window."""
import os, sys
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import pytest
from pypdf import PdfWriter, PdfReader
from PySide6.QtWidgets import QMessageBox
import acrod as H
from test_organizer_panel import app, pump  # noqa: F401


@pytest.fixture(autouse=True)
def _iso(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "appdata"))
    H._reset_ui_state_cache()


def mk(path, pages=3):
    w = PdfWriter()
    for i in range(pages):
        w.add_blank_page(width=100 + i, height=100)
    with open(path, "wb") as f:
        w.write(f)
    return str(path)


@pytest.fixture
def win(app):
    w = H.MainWindow(); w.show()
    yield w
    for _i, p in w.panels():
        if hasattr(p, "stop_for_shutdown"):
            p.stop_for_shutdown()
    w.close()


def tab_of(win, cls):
    for i in range(win.tabs.count()):
        h = win.tabs.widget(i)
        if getattr(h, "_factory", None) is cls:
            return i
    raise AssertionError(cls)


def labels(menu):
    return [a.text() for a in menu.actions()]


def test_menu_lists_every_other_tab_and_not_itself(win):
    m = H.build_send_menu(win, "merger", ["/x/a.pdf"])
    assert [t for t in labels(m) if t] == ["PDF Splitter", "PDF Optimizer", "Page Organizer", "Bulk Renamer",
                                           "File tray (keep them for later)"]
    assert all(a.isEnabled() for a in m.actions() if not a.isSeparator())
    assert not any(a.isEnabled() for a in H.build_send_menu(win, "merger", []).actions() if not a.isSeparator())     # nothing to send


def test_menu_is_disabled_without_a_host(app):
    from PySide6.QtWidgets import QWidget
    w = QWidget()
    m = H.build_send_menu(w, "splitter", ["/x/a.pdf"])
    assert m.actions() and not any(a.isEnabled() for a in m.actions() if not a.isSeparator())
    assert H.send_paths_from(w, "merger", ["/x/a.pdf"]) is False


def test_send_to_organizer_switches_tab_and_opens(win, tmp_path):
    f = mk(tmp_path / "a.pdf")
    assert H.send_paths_from(win.panel_at(0) or win, "organizer", [f]) is True
    assert win.tabs.currentIndex() == tab_of(win, H.OrganizerPanel)
    org = win.panel_at(tab_of(win, H.OrganizerPanel))
    assert pump(win and __import__("PySide6.QtWidgets", fromlist=["QApplication"]).QApplication.instance(),
                lambda: org._launch_index(os.path.abspath(f)) >= 0)


def test_send_to_renamer_opens_the_folder(win, tmp_path):
    f = mk(tmp_path / "a.pdf")
    assert win.send_paths("renamer", [f]) is True
    ren = win.panel_at(tab_of(win, H.RenamerPanel))
    assert ren.folder_row.path() == str(tmp_path)
    g = mk(tmp_path / "sub_b.pdf")
    os.makedirs(tmp_path / "other"); h = mk(tmp_path / "other" / "c.pdf")
    assert win.send_paths("renamer", [g, h]) is False and "different folders" in ren.status.text()


def test_send_to_splitter_goes_through_the_confirm(win, tmp_path, monkeypatch):
    f = mk(tmp_path / "a.pdf")
    seen = []
    monkeypatch.setattr(H.Msg, "question", lambda self, t, text, *x, **k: (seen.append(text), QMessageBox.No)[1])
    assert win.send_paths("splitter", [f]) is True
    assert seen and "a.pdf" not in seen[0] and "1 PDF" in seen[0]
    assert win.tabs.currentIndex() == tab_of(win, H.SplitterPanel)


def test_send_to_optimizer_and_merger(win, tmp_path, monkeypatch):
    f = mk(tmp_path / "a.pdf")
    called = []
    opt = win.panel_at(tab_of(win, H.OptimizerPanel)) or win.tabs.widget(tab_of(win, H.OptimizerPanel)).ensure()
    monkeypatch.setattr(opt, "handle_drop", lambda paths: called.append(list(paths)))
    assert win.send_paths("optimizer", [f]) is True and called == [[f]]
    mer = win.panel_at(0)
    monkeypatch.setattr(mer, "handle_drop", lambda paths: called.append(("m", list(paths))))
    g = mk(tmp_path / "b.pdf")
    assert win.send_paths("merger", [f, g]) is True and called[-1] == ("m", [f])


def test_rejections(win, tmp_path):
    (tmp_path / "x.txt").write_text("1")
    for key in ("merger", "splitter", "optimizer", "organizer"):
        assert win.send_paths(key, [str(tmp_path / "x.txt")]) is False, key
    assert win.send_paths("nowhere", ["/a.pdf"]) is False


def test_busy_target_refuses(win, tmp_path, monkeypatch):
    f = mk(tmp_path / "a.pdf")
    sp = win.tabs.widget(tab_of(win, H.SplitterPanel)).ensure()
    sp._busy = True
    assert win.send_paths("splitter", [f]) is False
    sp._busy = False


def test_split_then_send_the_pieces(win, app, tmp_path, monkeypatch):
    f = mk(tmp_path / "a.pdf", 3)
    monkeypatch.setattr(H.Msg, "question", lambda *a, **k: QMessageBox.Yes)
    for kind in ("information", "warning", "critical"):
        monkeypatch.setattr(H.Msg, kind, lambda *a, **k: QMessageBox.Ok)
    win.send_paths("splitter", [f])
    sp = win.panel_at(tab_of(win, H.SplitterPanel))
    assert pump(app, lambda: not sp._busy and len(sp._out_files) == 2)
    assert sp.send_btn.isEnabled()
    assert all(os.path.isfile(p) for p in sp._out_files)
    m = sp._build_send_menu()
    assert "Splitter" not in " ".join(labels(m)) and len([a for a in m.actions() if not a.isSeparator()]) == 5
    assert H.send_paths_from(sp, "organizer", sp._out_files) is True
    assert win.tabs.currentIndex() == tab_of(win, H.OrganizerPanel)


def test_merge_then_send(win, app, tmp_path, monkeypatch):
    base, ref = mk(tmp_path / "base.pdf", 2), mk(tmp_path / "ref.pdf", 1)
    monkeypatch.setattr(H.Msg, "question", lambda *a, **k: QMessageBox.Yes)
    monkeypatch.setattr(H.Msg, "information", lambda *a, **k: QMessageBox.Ok)
    mer = win.panel_at(0)
    assert not mer.send_btn.isEnabled()
    mer.reference_path = ref
    mer.handle_drop([base])
    assert pump(app, lambda: not mer._busy and mer._last_base == base)
    assert mer.send_btn.isEnabled()
    assert labels(mer._build_send_menu())[0] == "PDF Splitter"
    os.remove(base); mer._set_busy(False)
    assert not mer.send_btn.isEnabled()                              # nothing to send once the file is gone


def test_organizer_file_menu_has_send_to(win, app, tmp_path):
    mk(tmp_path / "a.pdf")
    org = win.tabs.widget(tab_of(win, H.OrganizerPanel)).ensure()
    org.folder_row.set_path(str(tmp_path))
    assert pump(app, lambda: org.file_list.count() == 1)
    m = org._build_file_menu(org.file_list.visualItemRect(org.file_list.item(0)).center())
    sub = [a for a in m.actions() if a.menu() is not None]
    assert sub and sub[0].text() == "Send to" and "Page Organizer" not in labels(sub[0].menu())
    assert "PDF Merger" in labels(sub[0].menu())
