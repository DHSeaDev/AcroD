"""Help entries offer a button to the tab they are about."""
import os, sys
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import pytest
from PySide6.QtWidgets import QPushButton
import acrod as H
from test_organizer_panel import app, pump  # noqa: F401


@pytest.fixture(autouse=True)
def _iso(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "appdata"))
    H._reset_ui_state_cache()


def test_mentioned_in_order_and_overview_has_none():
    assert H.help_tabs_mentioned("Use the Page Organizer, then the PDF Merger.") == ["Page Organizer", "PDF Merger"]
    assert H.help_tabs_mentioned("nothing here") == []
    overview = "PDF Merger PDF Splitter PDF Optimizer Page Organizer Bulk Renamer Bulk File Mover"
    assert H.help_tabs_mentioned(overview) == []
    assert H.help_tabs_mentioned("page organizer and PAGE ORGANIZER") == ["Page Organizer"]


def test_every_faq_with_tabs_gets_working_buttons(app):
    w = H.MainWindow(); w.show()
    help_i = w.tabs.count() - 1
    w.tabs.setCurrentIndex(help_i)
    hp = w.panel_at(help_i) or w.tabs.widget(help_i).ensure()
    with_nav = [(ql, al) for ql, al, _h in hp._faq_rows if al.nav is not None]
    assert len(with_nav) >= 10                                     # many entries name a tab
    ql, al = with_nav[0]
    assert al.isHidden() and al.nav.isHidden()                     # closed: neither shows
    ql.toggle()
    assert not al.isHidden() and not al.nav.isHidden()             # open: both show
    ql.toggle()
    assert al.isHidden() and al.nav.isHidden()
    seen = set()
    for _q, a_ in with_nav:
        for btn in a_.nav.findChildren(QPushButton):
            target = btn.text().replace("Go to ", "")
            w.tabs.setCurrentIndex(help_i)
            btn.click()
            assert w.tabs.tabBar().title(w.tabs.currentIndex()) == target
            seen.add(target)
    assert len(seen) >= 4                                          # not just one tab
    for _i, p in w.panels():
        if hasattr(p, "stop_for_shutdown"):
            p.stop_for_shutdown()
    w.close()


def test_search_open_answers_show_their_buttons(app):
    w = H.MainWindow(); w.show()
    hp = w.tabs.widget(w.tabs.count() - 1).ensure()
    hp.search_edit.setText("overwrote")
    pump(app, lambda: False, 0.1)
    shown = [(ql, al) for ql, al, _h in hp._faq_rows if not ql.isHidden()]
    assert shown and all(not al.isHidden() for _q, al in shown)
    assert all(al.nav is None or not al.nav.isHidden() for _q, al in shown)
    hp.search_edit.setText("zzzzzz")
    assert all(al.nav is None or al.nav.isHidden() for _q, al, _h in hp._faq_rows)
    for _i, p in w.panels():
        if hasattr(p, "stop_for_shutdown"):
            p.stop_for_shutdown()
    w.close()


def test_go_tab_unknown_and_unhosted(app):
    hp = H.HelpPanel()
    assert hp._go_tab("PDF Merger") is False                       # no window with tabs
    w = H.MainWindow(); w.show()
    h = w.tabs.widget(w.tabs.count() - 1).ensure()
    assert h._go_tab("No Such Tab") is False
    assert h._go_tab("Bulk Renamer") is True
    w.close()
