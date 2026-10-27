import os, sys
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import pytest
import acrod as H

TITLES = ["PDF Merger", "PDF Splitter", "PDF Optimizer", "Page Organizer", "Bulk Renamer",
          "Bulk File Mover", "Settings", "Help"]


@pytest.fixture
def win(monkeypatch, tmp_path):
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    H._reset_ui_state_cache()
    H.apply_theme(app, "Periwinkle", "Normal")
    w = H.MainWindow()
    w.show()
    w.apply_layout("classic")        # these tests measure the classic tab strip
    app.processEvents()
    yield app, w
    w.close()


def settle(app, w, width):
    w.resize(width, 640)
    for _ in range(3):
        app.processEvents()


@pytest.mark.parametrize("size", ["Small", "Normal", "Large"])
@pytest.mark.parametrize("width", [720, 800, 900, 1000, 1100, 1360, 1920])
def test_every_tab_is_reachable_without_scroll_arrows(win, size, width):
    app, w = win
    H.apply_theme(app, "Periwinkle", size)
    settle(app, w, width)
    tb = w.tabs.tabBar()
    assert tb.tabRect(tb.count() - 1).right() <= tb.width(), (size, width, tb._mode)
    assert tb.width() <= w.tabs.width()


def test_density_steps_down_as_the_window_narrows_and_back_up(win):
    app, w = win
    seen = []
    for width in (1360, 1100, 720, 1360):
        settle(app, w, width)
        seen.append(w.tabs.tabBar()._mode)
    assert seen == ["full", "tight", "compact", "full"], seen


def test_real_titles_survive_compact_mode(win):
    app, w = win
    settle(app, w, 720)
    tb = w.tabs.tabBar()
    assert tb._mode == "compact"
    assert [tb.title(i) for i in range(tb.count())] == TITLES
    assert [tb.tabToolTip(i) for i in range(tb.count())] == TITLES
    # only the selected tab keeps its words
    assert [i for i in range(tb.count()) if tb.tabText(i)] == [tb.currentIndex()]
    w.tabs.setCurrentIndex(4)
    app.processEvents()
    assert [i for i in range(tb.count()) if tb.tabText(i)] == [4]
    assert tb.tabText(4) == "Bulk Renamer"
    w._decorate_tabs()      # icons are keyed on the real title, not the empty text
    assert all(not tb.tabIcon(i).isNull() for i in range(tb.count()))
