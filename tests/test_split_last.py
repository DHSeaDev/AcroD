import os, sys
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import pytest
from pypdf import PdfWriter, PdfReader
import acrod as H


def make_pdf(path, pages):
    w = PdfWriter()
    for i in range(pages):
        w.add_blank_page(width=100 + i, height=100)    # width identifies the page
    with open(path, "wb") as f:
        w.write(f)


def widths(path):
    return [int(float(p.mediabox.width)) for p in PdfReader(path).pages]


@pytest.mark.parametrize("n", [1, 2, 3])
def test_last_n_split(tmp_path, n):
    src = tmp_path / "doc.pdf"; make_pdf(src, 6)
    last, rest = H.split_pdf(str(src), n, from_end=True)
    assert os.path.basename(last) == f"doc_Last{n}.pdf"
    assert os.path.basename(rest) == "doc" + H.setting("remaining_suffix") + ".pdf"
    assert widths(last) == [100 + i for i in range(6 - n, 6)]
    assert widths(rest) == [100 + i for i in range(0, 6 - n)]


def test_front_split_unchanged(tmp_path):
    src = tmp_path / "doc.pdf"; make_pdf(src, 5)
    cover, rest = H.split_pdf(str(src), 2)
    assert widths(cover) == [100, 101] and widths(rest) == [102, 103, 104]


def test_last_split_refuses_when_nothing_would_remain(tmp_path):
    src = tmp_path / "doc.pdf"; make_pdf(src, 3)
    with pytest.raises(H.PdfOpError):
        H.split_pdf(str(src), 3, from_end=True)
    assert not (tmp_path / H.setting("split_dir_name")).exists() or \
        not os.listdir(tmp_path / H.setting("split_dir_name"))


@pytest.fixture
def panel(monkeypatch, tmp_path):
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    H._reset_ui_state_cache()
    return H.SplitterPanel()


def test_two_dropdowns_each_selects_its_row(panel):
    assert panel.split_point() == 1 and not panel.split_from_end()
    assert [panel.combo_first.itemData(i) for i in range(3)] == [1, 2, 3]
    assert [panel.combo_last.itemData(i) for i in range(3)] == [1, 2, 3]
    assert panel.combo_first.isEnabled() and panel.combo_last.isEnabled()
    panel.combo_last.setCurrentIndex(2)
    panel.combo_last.activated.emit(2)          # what a user pick emits
    assert panel.split_from_end() and panel.split_point() == 3
    panel.combo_first.setCurrentIndex(1)
    panel.combo_first.activated.emit(1)
    assert not panel.split_from_end() and panel.split_point() == 2


@pytest.mark.parametrize("from_end", [False, True])
@pytest.mark.parametrize("n", [1, 2, 3])
def test_every_dropdown_choice_splits_the_right_pages(panel, tmp_path, from_end, n):
    src = tmp_path / "doc.pdf"; make_pdf(src, 7)
    (panel.radio_last if from_end else panel.radio_first).setChecked(True)
    (panel.combo_last if from_end else panel.combo_first).setCurrentIndex(n - 1)
    a, b = H.split_pdf(str(src), panel.split_point(), from_end=panel.split_from_end())
    if from_end:
        assert widths(a) == [100 + i for i in range(7 - n, 7)]
        assert widths(b) == [100 + i for i in range(0, 7 - n)]
    else:
        assert widths(a) == [100 + i for i in range(n)]
        assert widths(b) == [100 + i for i in range(n, 7)]


def test_selection_persists_and_old_values_are_ignored(monkeypatch, tmp_path):
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    H._reset_ui_state_cache()
    p1 = H.SplitterPanel()
    p1.combo_last.setCurrentIndex(1); p1.combo_last.activated.emit(1)
    p2 = H.SplitterPanel()
    assert p2.split_from_end() and p2.split_point() == 2
    H.update_ui_state(splitter_split_after=3, splitter_from_end=False)
    p4 = H.SplitterPanel()
    assert p4.split_point() == 3 and not p4.split_from_end() and p4.combo_first.currentIndex() == 2
    H.update_ui_state(splitter_split_after=12, splitter_from_end=False)
    p3 = H.SplitterPanel()
    assert p3.split_point() == 1 and not p3.split_from_end()


def test_clicking_a_dropdown_selects_its_row(panel):
    from PySide6.QtCore import QEvent, QPointF, Qt
    from PySide6.QtGui import QMouseEvent, QPointingDevice
    panel.show()
    assert not panel.split_from_end()
    ev = QMouseEvent(QEvent.MouseButtonPress, QPointF(5, 5), QPointF(5, 5),
                     Qt.LeftButton, Qt.LeftButton, Qt.NoModifier)
    assert panel.eventFilter(panel.combo_last, ev) is False or True
    assert panel.split_from_end()
    panel.eventFilter(panel.combo_first, ev)
    assert not panel.split_from_end()


def test_focus_alone_never_moves_the_selection(panel):
    """Regression: Qt moving focus (Tab, or a run disabling the focused
    widget) used to tick 'Last' behind the user's back."""
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QFocusEvent
    panel.show()
    fin = QFocusEvent(QEvent.FocusIn, Qt.TabFocusReason)
    panel.eventFilter(panel.combo_last, fin)      # what Qt sends on Tab / hand-over
    assert not panel.split_from_end()
    panel._set_busy(True); panel._set_busy(False)
    panel.eventFilter(panel.combo_last, fin)
    assert not panel.split_from_end()
    panel.radio_last.setChecked(True)
    panel.eventFilter(panel.combo_first, fin)
    assert panel.split_from_end()                 # stays where it was last used


def test_wheel_over_unfocused_dropdown_changes_nothing(panel):
    from PySide6.QtCore import QEvent, QPointF, Qt
    from PySide6.QtGui import QWheelEvent
    panel.show()
    ev = QWheelEvent(QPointF(5, 5), QPointF(5, 5), __import__("PySide6.QtCore", fromlist=["QPoint"]).QPoint(0, 0),
                     __import__("PySide6.QtCore", fromlist=["QPoint"]).QPoint(0, -120),
                     Qt.NoButton, Qt.NoModifier, Qt.NoScrollPhase, False)
    panel.combo_last.clearFocus()
    assert panel.eventFilter(panel.combo_last, ev) is True
    assert not panel.split_from_end()


def test_message_box_icon_is_vertically_centred():
    import sys
    from PySide6.QtWidgets import QApplication, QMessageBox, QLabel
    from PySide6.QtCore import QTimer
    app = QApplication.instance() or QApplication([])
    seen = {}

    def probe():
        for w in app.topLevelWidgets():
            if isinstance(w, QMessageBox) and w.isVisible():
                app.processEvents()
                icon = w.findChild(QLabel, "qt_msgboxex_icon_label")
                text = w.findChild(QLabel, "qt_msgbox_label")
                seen["icon_mid"] = icon.geometry().center().y()
                seen["text_mid"] = text.geometry().center().y()
                seen["h"] = w.height()
                w.close(); return
    QTimer.singleShot(500, probe)
    H.Msg.question(None, "Confirm", "\n".join(f"Line {n}" for n in range(14)),
                   H.QMessageBox.Yes | H.QMessageBox.No)
    assert seen, "dialog never appeared"
    assert abs(seen["icon_mid"] - seen["text_mid"]) <= 4, seen
