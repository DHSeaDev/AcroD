"""The Help tab describes the build it ships in (1.12.0), as rendered."""
import os, sys, re
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import pytest
from PySide6.QtWidgets import QLabel
import acrod as H
from test_organizer_panel import app, _iso  # noqa: F401


@pytest.fixture
def help_panel(app, _iso):
    w = H.MainWindow(); w.show(); app.processEvents()
    i = next(i for i in range(w.tabs.count()) if w.tabs.tabToolTip(i) == "Help")
    w.tabs.setCurrentIndex(i); app.processEvents()
    yield w.tabs.widget(i).panel
    w.close()


def faq_text():
    return "\n".join(q + "\n" + a for q, a in H.HelpPanel.FAQ)


def rendered(panel):
    return "\n".join(l.text() for l in panel.findChildren(QLabel))


def test_help_has_no_literal_double_ampersand(help_panel):
    assert "&&" not in rendered(help_panel)            # QLabel shows it literally


def test_help_mentions_the_1_4_1_features(help_panel):
    t = rendered(help_panel).lower()
    for needle in ("gold bar", "right-click", "asks where to save", "report only" if False else "just reports",
                   "page organizer's output folder name",
                   "save & next pdf", "open output folder on the page organizer"):
        assert needle in t, needle
    assert "beside the title" not in t           # Pagey no longer sits beside the Optimizer title


SHORTCUT_KEYS = ("Ctrl+1 to Ctrl+8", "Ctrl+O", "Ctrl+Return", "Ctrl+Z", "Ctrl+Shift+Z", "Esc", "F1", "F5")


def test_help_shortcut_rows_match_the_window(help_panel):
    t = rendered(help_panel)
    assert not hasattr(H.MainWindow, "SHORTCUTS")   # one source of truth: _install_shortcuts + this FAQ
    for key in SHORTCUT_KEYS:
        assert key in t, key


def test_help_does_not_claim_extract_is_automatic():
    t = faq_text().lower()
    assert not re.search(r"extract[^.]*automatic", t)
    assert "extract is different" in t


def test_help_names_every_tab_by_its_real_title(help_panel):
    first = H.HelpPanel.FAQ[0][1]
    for title in ("PDF Merger", "PDF Splitter", "PDF Optimizer", "Page Organizer", "Bulk Renamer", "Bulk File Mover"):
        assert title in first


def test_every_settings_advanced_field_is_listed_in_the_faq():
    t = faq_text().lower()
    for needle in ("splitter's folder name", "optimizer's output folder name", "page organizer's output folder name",
                   "thumbnail size", "replace the original", "overwritten-file backups", "which tab opens on launch"):
        assert needle in t, needle


def test_version_shown_in_help_is_the_build_version(help_panel):
    assert H.APP_VERSION == "1.12.1" and H.APP_VERSION in rendered(help_panel)


def test_help_explains_the_security_refusals():
    t = faq_text().lower()
    for needle in ("refuse my regular expression", "(a+)+", "open file not start a program", ".exe",
                   "leading '", "expand to an enormous size", "safety check"):
        assert needle in t, needle


def test_settings_regex_sheet_lists_the_refused_shape():
    assert any("Refused" in r[1] for r in H.SettingsPanel.REGEX_ROWS)
