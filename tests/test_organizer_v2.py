"""Organizer v2: real drag-and-drop reorder, right-click menus, extract destination dialog."""
import os, sys, time
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import pytest
from PySide6.QtCore import Qt, QPoint, QPointF, QMimeData, QUrl, QByteArray
from PySide6.QtGui import QDropEvent, QDragEnterEvent
from PySide6.QtWidgets import QDialog
from pypdf import PdfReader
import acrod as H
from test_organizer_engine import make_pdf, widths
from test_organizer_panel import app, panel, folder, pump, open_row, select, sha, _iso  # noqa: F401


def order(p):
    return [x["idx"] for x in p._pages]


def drop(panel, rows, point):
    mime = QMimeData()
    mime.setData(H._PageGrid.MIME, QByteArray(",".join(map(str, rows)).encode()))
    ev = QDropEvent(QPointF(point), Qt.MoveAction, mime, Qt.LeftButton, Qt.NoModifier)
    panel.grid.dropEvent(ev)
    return ev


def point_before(grid, row):
    r = grid.visualItemRect(grid.item(row)); return QPoint(r.left() + 3, r.center().y())


def point_after(grid, row):
    r = grid.visualItemRect(grid.item(row)); return QPoint(r.right() - 3, r.center().y())


# ---- drag and drop --------------------------------------------------------
def test_drop_moves_a_page_to_the_gap_it_was_dropped_in(app, panel):
    open_row(app, panel, 0, 4); panel.show(); pump(app, secs=0.2)
    drop(panel, [0], point_before(panel.grid, 3))           # before page 4
    assert order(panel) == [1, 2, 0, 3]
    drop(panel, [3], point_before(panel.grid, 0))            # last page to the very front
    assert order(panel) == [3, 1, 2, 0]
    drop(panel, [0], point_after(panel.grid, 3))             # and back to the very end
    assert order(panel) == [1, 2, 0, 3]


def test_dragging_a_multi_selection_moves_them_together_in_order(app, panel):
    open_row(app, panel, 0, 4); panel.show(); pump(app, secs=0.2)
    drop(panel, [0, 2], point_after(panel.grid, 3))
    assert order(panel) == [1, 3, 0, 2]
    assert panel._is_dirty()


def test_dropping_where_it_already_is_changes_nothing(app, panel):
    open_row(app, panel, 0, 4); panel.show(); pump(app, secs=0.2)
    drop(panel, [1], point_before(panel.grid, 1))
    drop(panel, [1], point_after(panel.grid, 1))
    assert order(panel) == [0, 1, 2, 3] and not panel._is_dirty()


def test_drop_stays_in_step_with_the_grid_and_the_saved_plan(app, panel, folder):
    open_row(app, panel, 0, 4); panel.show(); pump(app, secs=0.2)
    drop(panel, [3], point_before(panel.grid, 0))
    assert [panel.grid.item(r).data(panel._ROLE_IDX) for r in range(4)] == order(panel) == [3, 0, 1, 2]
    panel.save_and_next()
    assert pump(app, lambda: (folder / "Reorganized" / "a.pdf").exists() and not panel._busy)
    assert widths(str(folder / "Reorganized" / "a.pdf")) == [103, 100, 101, 102]


def test_foreign_or_garbled_drops_are_refused(app, panel):
    open_row(app, panel, 0, 4); panel.show()
    mime = QMimeData(); mime.setUrls([QUrl.fromLocalFile("/tmp/x.txt")])     # not a PDF: left to the panel
    ev = QDropEvent(QPointF(5, 5), Qt.MoveAction, mime, Qt.LeftButton, Qt.NoModifier)
    panel.grid.dropEvent(ev)
    assert not ev.isAccepted() and order(panel) == [0, 1, 2, 3]
    gone = QMimeData(); gone.setUrls([QUrl.fromLocalFile("/tmp/does-not-exist-x.pdf")])   # a PDF file drop is the grid's
    panel.grid.dropEvent(QDropEvent(QPointF(5, 5), Qt.CopyAction, gone, Qt.LeftButton, Qt.NoModifier))
    assert order(panel) == [0, 1, 2, 3] and "Nothing was inserted" in panel.status.text()
    drop(panel, [99, -1], QPoint(5, 5))
    assert order(panel) == [0, 1, 2, 3]
    bad = QMimeData(); bad.setData(H._PageGrid.MIME, QByteArray(b"x,y"))
    panel.grid.dropEvent(QDropEvent(QPointF(5, 5), Qt.MoveAction, bad, Qt.LeftButton, Qt.NoModifier))
    assert order(panel) == [0, 1, 2, 3]


def test_drops_are_ignored_while_saving(app, panel):
    open_row(app, panel, 0, 4); panel.show(); pump(app, secs=0.2)
    panel._begin_op(panel._cur_path)
    drop(panel, [0], point_after(panel.grid, 3))
    assert order(panel) == [0, 1, 2, 3]
    panel._end_op()


def test_insertion_index_beyond_the_last_page_is_the_end(app, panel):
    open_row(app, panel, 0, 4); panel.show(); pump(app, secs=0.2)
    last = panel.grid.visualItemRect(panel.grid.item(3))
    far = QPoint(last.right() - 2, last.bottom() + 12)      # in the empty space below the last page, right half
    assert panel.grid.itemAt(far) is None
    assert panel.grid.insertion_index(far) == 4
    assert panel.grid.insertion_index(QPoint(last.left() + 2, last.bottom() + 12)) == 3


def test_drag_payload_carries_the_selected_rows(app, panel):
    open_row(app, panel, 0, 4)
    select(panel, 3, 1)
    mime = panel.grid.mimeData(panel.grid.selectedItems())
    assert bytes(mime.data(H._PageGrid.MIME)).decode() == "1,3"


def test_dropping_a_folder_or_pdf_from_explorer_opens_it(app, panel, tmp_path):
    other = tmp_path / "other"; other.mkdir(); make_pdf(str(other / "z1.pdf"), 1); make_pdf(str(other / "z2.pdf"), 3)
    mime = QMimeData(); mime.setUrls([QUrl.fromLocalFile(str(other / "z2.pdf"))])
    ev = QDropEvent(QPointF(5, 5), Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier)
    panel.dropEvent(ev)
    assert ev.isAccepted() and panel.folder_row.path() == str(other)
    assert pump(app, lambda: os.path.basename(panel._cur_path) == "z2.pdf" and len(panel._pages) == 3)
    junk = QMimeData(); junk.setUrls([QUrl.fromLocalFile(str(tmp_path / "notes.txt"))])
    (tmp_path / "notes.txt").write_text("x")
    ev2 = QDropEvent(QPointF(5, 5), Qt.CopyAction, junk, Qt.LeftButton, Qt.NoModifier)
    panel.dropEvent(ev2)
    assert not ev2.isAccepted()


# ---- right-click menus ----------------------------------------------------
def labels(menu):
    return [a.text() for a in menu.actions() if a.text()]


def run(menu, text):
    [a for a in menu.actions() if a.text() == text][0].trigger()


def test_page_menu_offers_every_action_and_acts_on_the_clicked_page(app, panel):
    open_row(app, panel, 0, 4); panel.show(); pump(app, secs=0.2)
    m = panel._build_grid_menu(panel.grid.visualItemRect(panel.grid.item(2)).center())
    assert panel.grid.selectedItems() == [panel.grid.item(2)]     # right-click selected it
    for want in ("Rotate this page right", "Rotate this page left", "Turn this page upside down",
                 "Delete this page", "Move to the start", "Move earlier", "Move later",
                 "Move to the end", "Extract to a new PDF…", "Select all pages",
                 "Select blank pages", "Reset this PDF"):
        assert want in labels(m), want
    run(m, "Rotate this page right")
    assert panel._pages[2]["rot"] == 90
    m = panel._build_grid_menu(panel.grid.visualItemRect(panel.grid.item(2)).center())
    run(m, "Turn this page upside down")
    assert panel._pages[2]["rot"] == 270
    run(m, "Move to the start")
    assert order(panel) == [2, 0, 1, 3]


def test_right_click_inside_a_selection_keeps_the_whole_selection(app, panel):
    open_row(app, panel, 0, 4); panel.show(); pump(app, secs=0.2)
    select(panel, 1, 2)
    m = panel._build_grid_menu(panel.grid.visualItemRect(panel.grid.item(2)).center())
    assert len(panel.grid.selectedItems()) == 2 and "Rotate these 2 pages right" in labels(m)
    run(m, "Move to the end")
    assert order(panel) == [0, 3, 1, 2]
    m = panel._build_grid_menu(panel.grid.visualItemRect(panel.grid.item(3)).center())
    assert "Delete these 2 pages" in labels(m)
    run(m, "Delete these 2 pages")
    assert order(panel) == [0, 3] and panel.grid.count() == 2         # whole selection acted on


def test_no_page_menu_on_empty_space_or_while_saving(app, panel):
    open_row(app, panel, 0, 4); panel.show(); pump(app, secs=0.2)
    vp = panel.grid.viewport()
    empty = next(QPoint(x, y) for y in range(vp.height() - 4, 0, -8) for x in range(vp.width() - 4, 0, -8)
                 if panel.grid.itemAt(QPoint(x, y)) is None)          # a spot with no page under it
    em = panel._build_grid_menu(empty)                               # empty space: paste / select-all menu only
    assert em is not None and not any(t.startswith(("Rotate", "Delete", "Cut")) for t in labels(em))
    panel._begin_op(panel._cur_path)
    assert panel._build_grid_menu(panel.grid.visualItemRect(panel.grid.item(0)).center()) is None
    assert panel._build_file_menu(panel.file_list.visualItemRect(panel.file_list.item(0)).center()) is None
    panel._end_op()


def test_file_menu_opens_skips_and_copies(app, panel, folder):
    pos = panel.file_list.visualItemRect(panel.file_list.item(1)).center()
    m = panel._build_file_menu(pos)
    assert labels(m) == ["Open this PDF here", "Read this PDF full size", "Skip it",
                         "Copy all its pages (to paste into another PDF)",
                         "Compare: show it in B beside this PDF", "Send to",
                         "Open the PDF in your PDF viewer", "Show it in its folder", "Copy path"]
    run(m, "Skip it")
    assert panel.file_list.item(1).text().startswith("– b.pdf")
    run(m, "Open this PDF here")
    assert pump(app, lambda: os.path.basename(panel._cur_path) == "b.pdf")
    run(m, "Copy path")
    assert QApplicationClip() == str(folder / "b.pdf")
    last = panel.file_list.visualItemRect(panel.file_list.item(panel.file_list.count() - 1))
    blank = QPoint(3, last.bottom() + 20)
    assert panel.file_list.itemAt(blank) is None
    assert panel._build_file_menu(blank) is None


def QApplicationClip():
    from PySide6.QtWidgets import QApplication
    return QApplication.clipboard().text()


# ---- extract: choose destination and name ----------------------------------
def test_extract_engine_honours_folder_and_name_and_never_overwrites(tmp_path):
    src = tmp_path / "a.pdf"; make_pdf(str(src), 4)
    dest = tmp_path / "elsewhere"; dest.mkdir()
    r = H.extract_pages(str(src), [3, 1], dest_dir=str(dest), file_name="Exhibit B")
    assert r["path"] == str(dest / "Exhibit B.pdf") and widths(r["path"]) == [103, 101]
    r2 = H.extract_pages(str(src), [0], dest_dir=str(dest), file_name="Exhibit B.PDF")
    assert r2["path"] == str(dest / "Exhibit B (1).pdf") and (dest / "Exhibit B.pdf").exists()
    assert not (tmp_path / "Reorganized").exists()                  # nothing automatic


@pytest.mark.parametrize("bad", ["", "  ", "a/b", "a:b", "con", "x.", "q?"])
def test_extract_engine_rejects_bad_names(tmp_path, bad):
    src = tmp_path / "a.pdf"; make_pdf(str(src), 2)
    with pytest.raises(H.PdfOpError, match="file name"):
        H.extract_pages(str(src), [0], dest_dir=str(tmp_path), file_name=bad)
    assert os.listdir(tmp_path) == ["a.pdf"]


def test_extract_engine_missing_folder_is_a_plain_error(tmp_path):
    src = tmp_path / "a.pdf"; make_pdf(str(src), 2)
    with pytest.raises(H.PdfOpError, match="folder"):
        H.extract_pages(str(src), [0], dest_dir=str(tmp_path / "nope"), file_name="x")


def test_dialog_validates_and_previews(app, tmp_path):
    (tmp_path / "taken.pdf").write_bytes(b"x")
    d = H.ExtractDialog(None, str(tmp_path), "new", 2)
    ok = d.buttons.button(H.QDialogButtonBox.Ok)
    assert ok.isEnabled() and str(tmp_path / "new.pdf") in d.note.text()
    d.name_edit.setText("taken.pdf")
    assert ok.isEnabled() and "numbered" in d.note.text() and d.result() == (str(tmp_path), "taken")
    d.name_edit.setText("bad/name"); assert not ok.isEnabled() and "cannot be used" in d.note.text()
    d.name_edit.setText("fine"); d.folder_edit.setText(str(tmp_path / "gone"))
    assert not ok.isEnabled() and "does not exist" in d.note.text()


def test_extract_asks_where_and_what_then_saves_exactly_there(app, panel, folder, tmp_path, monkeypatch):
    dest = tmp_path / "exhibits"; dest.mkdir()
    asked = []
    def answer(n):
        asked.append(n); return (str(dest), "Affidavit pages")
    monkeypatch.setattr(panel, "_choose_extract_target", answer)
    src = str(folder / "a.pdf"); before = sha(src)
    open_row(app, panel, 0, 4)
    select(panel, 2, 0); panel.extract_selected()
    assert pump(app, lambda: (dest / "Affidavit pages.pdf").exists() and not panel._busy)
    assert asked == [2] and widths(str(dest / "Affidavit pages.pdf")) == [100, 102]
    assert str(dest / "Affidavit pages.pdf") in panel.status.text()
    assert sha(src) == before and not (folder / "Reorganized").exists()
    assert H.load_ui_state()["organizer_extract_dir"] == str(dest)


def test_cancelling_the_extract_dialog_writes_nothing(app, panel, folder, monkeypatch):
    monkeypatch.setattr(panel, "_choose_extract_target", lambda n: None)
    open_row(app, panel, 0, 4); select(panel, 1); panel.extract_selected()
    assert not panel._busy and "cancelled" in panel.status.text() and not (folder / "Reorganized").exists()


def test_real_dialog_defaults_come_from_the_file_and_the_last_destination(app, panel, folder, monkeypatch):
    seen = {}
    class Spy(H.ExtractDialog):
        def exec(self):
            seen.update(folder=self.folder_edit.text(), name=self.name_edit.text()); return QDialog.Rejected
    monkeypatch.setattr(H, "ExtractDialog", Spy)
    open_row(app, panel, 0, 4)
    assert panel._choose_extract_target(1) is None
    assert seen == {"folder": str(folder), "name": "a_Extract"}   # no Reorganized folder yet -> beside the PDF
    (folder / "Reorganized").mkdir()
    panel._choose_extract_target(1)
    assert seen["folder"] == str(folder / "Reorganized")
    last = folder / "mine"; last.mkdir(); H.update_ui_state(organizer_extract_dir=str(last))
    panel._choose_extract_target(1)
    assert seen["folder"] == str(last)
