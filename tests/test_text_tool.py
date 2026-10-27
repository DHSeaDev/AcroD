"""Floating text in the Page Organizer's reader: the engine (placement, rotation, searchable output, never touching the
original), the Text tool through real mouse and key events, undo, saving as a new _edited file, and that none of it
costs anything until it is used."""
import os, sys, io
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import pytest
from PySide6.QtCore import Qt, QPoint, QPointF, QEvent
from PySide6.QtGui import QMouseEvent, QKeyEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
import acrod as H
from test_organizer_panel import app, pump, sha, select  # noqa: F401
from test_reader_panel import folder, panel, read_row, drawn, key, open_row  # noqa: F401


# ------------------------------------------------------------------ helpers
def blank(path, size=(300, 400), rotate=0, pages=1):
    import pikepdf
    pdf = pikepdf.new()
    for _ in range(pages):
        pdf.add_blank_page(page_size=size)
    if rotate:
        pdf.pages[0].obj["/Rotate"] = rotate
    pdf.save(path)
    return path


def render(path, page=0, scale=1.0):
    import pypdfium2
    doc = pypdfium2.PdfDocument(path)
    try:
        return doc[page].render(scale=scale).to_pil().convert("L")
    finally:
        doc.close()


def dark_bbox(im, limit=128):
    w, h = im.size
    px = im.load()
    pts = [(x, y) for y in range(h) for x in range(w) if px[x, y] < limit]
    if not pts:
        return None
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    return min(xs), min(ys), max(xs), max(ys)


def page_text(path, page=0):
    import pypdfium2
    with H._PDFIUM_LOCK:   # a panel's render worker may still be using pdfium
        doc = pypdfium2.PdfDocument(path)
        try:
            tp = doc[page].get_textpage()
            return tp.get_text_range()
        finally:
            doc.close()


BOX = H.TextBox(x=0.5, y=0.25, w=0.4, h=0.05, text="HELLO", size=24, bold=True)


# ------------------------------------------------------------------ the engine
def test_clean_text_box_never_raises_and_clamps():
    b = H.clean_text_box({"x": "nan", "y": 5, "w": -3, "size": 9999, "color": "red", "align": "diagonal",
                          "text": None, "family": ""})
    assert 0.0 <= b.x <= 1.0 and 0.0 <= b.y <= 1.0 and b.w >= H.TEXT_MIN_W
    assert b.size == 200.0 and b.color == "#000000" and b.align == "left" and b.text == ""
    assert b.family == H.TEXT_FONT_DEFAULT
    assert H.clean_text_box(None) == H.clean_text_box({})
    big = H.clean_text_box({"text": "x" * (H.TEXT_BOX_MAX_CHARS + 50)})
    assert len(big.text) == H.TEXT_BOX_MAX_CHARS


@pytest.mark.parametrize("native", [0, 90, 180, 270])
def test_text_lands_where_the_box_says_on_every_native_rotation(app, tmp_path, native):
    src = blank(str(tmp_path / "p.pdf"), rotate=native)
    base = render(src)
    assert dark_bbox(base) is None                                  # control: the blank page has nothing dark
    assert H.apply_text_overlays(src, [(0, (BOX,))]) == 1
    im = render(src)
    bb = dark_bbox(im)
    assert bb is not None
    w, h = im.size
    assert abs(bb[0] / w - 0.5) < 0.03 and abs(bb[1] / h - 0.25) < 0.03     # the box's top-left, as a fraction of the page you see
    assert bb[2] / w < 0.95                                                 # and it stayed inside the box's width


@pytest.mark.parametrize("native,extra", [(0, 90), (0, 180), (0, 270), (90, 90), (90, 270)])
def test_text_turns_with_an_organizer_rotation(app, tmp_path, native, extra):
    a = blank(str(tmp_path / "a.pdf"), rotate=native)
    H.apply_text_overlays(a, [(0, (BOX,))])
    expected = render(a).rotate(-extra, expand=True)
    b = blank(str(tmp_path / "b.pdf"), rotate=(native + extra) % 360)
    H.apply_text_overlays(b, [(extra, (BOX,))])
    got = render(b)
    assert got.size == expected.size
    e, g = dark_bbox(expected), dark_bbox(got)
    assert e and g and all(abs(x - y) <= 3 for x, y in zip(e, g))


def test_overlay_text_is_real_searchable_text(app, tmp_path):
    src = blank(str(tmp_path / "p.pdf"))
    H.apply_text_overlays(src, [(0, (BOX,))])
    assert "HELLO" in page_text(src)


def test_no_boxes_means_the_file_is_not_touched(app, tmp_path):
    src = blank(str(tmp_path / "p.pdf"))
    before = sha(src)
    assert H.apply_text_overlays(src, [(0, ())]) == 0
    assert sha(src) == before


def test_existing_page_content_survives_the_overlay(app, tmp_path):
    from test_reader_engine import make_text_pdf
    src = str(tmp_path / "t.pdf")
    make_text_pdf(src, pages=1, text="UNDERNEATH", size=(400, 300), at=(60, 120))
    before = render(src)
    assert "UNDERNEATH" in page_text(src)
    H.apply_text_overlays(src, [(0, (H.TextBox(x=0.05, y=0.05, w=0.4, h=0.05, text="TOP", size=14),))])
    assert "UNDERNEATH" in page_text(src) and "TOP" in page_text(src)
    after = render(src)
    # the region of the original text is unchanged
    box = (50, 120, 380, 190)
    assert list(before.crop(box).getdata()) == list(after.crop(box).getdata())


def test_two_pages_get_their_own_boxes(app, tmp_path):
    src = blank(str(tmp_path / "p.pdf"), pages=3)
    H.apply_text_overlays(src, [(0, (H.TextBox(text="ONE"),)), (0, ()), (0, (H.TextBox(text="THREE"),))])
    assert "ONE" in page_text(src, 0) and page_text(src, 1).strip() == "" and "THREE" in page_text(src, 2)


def test_page_count_mismatch_is_refused(app, tmp_path):
    src = blank(str(tmp_path / "p.pdf"), pages=2)
    with pytest.raises(H.PdfOpError):
        H.apply_text_overlays(src, [(0, (BOX,))])


def test_wrapping_and_height(app):
    b = H.TextBox(w=0.2, text="word " * 40, size=12)
    tall = H.text_box_height_frac(b, 300, 400)
    short = H.text_box_height_frac(H.replace(b, w=0.9), 300, 400)
    assert tall > short * 3 and short > 0
    assert H.text_box_height_frac(H.TextBox(text=""), 300, 400) > 0


# ------------------------------------------------------------------ saving through organize_pdf
def test_organize_with_text_writes_a_new_edited_file_and_never_replaces(app, tmp_path):
    src = blank(str(tmp_path / "doc.pdf"), pages=2)
    before = sha(src)
    res = H.organize_pdf(src, [(0, 0), (1, 0)], replace=True, texts=[(BOX,), ()])
    assert res["edited"] is True and res["backup"] is None
    assert os.path.basename(res["path"]) == "doc_edited.pdf" and res["path"] != src
    assert sha(src) == before                                       # replace=True was ignored
    assert "HELLO" in page_text(res["path"], 0) and page_text(res["path"], 1).strip() == ""


def test_text_alone_counts_as_a_change(app, tmp_path):
    src = blank(str(tmp_path / "doc.pdf"))
    with pytest.raises(H.PdfOpError):
        H.organize_pdf(src, [(0, 0)])                               # control: no text, no change, refused
    assert H.organize_pdf(src, [(0, 0)], texts=[(BOX,)])["edited"]


def test_text_follows_its_page_through_a_reorder(app, tmp_path):
    src = blank(str(tmp_path / "doc.pdf"), pages=3)
    res = H.organize_pdf(src, [(2, 0), (0, 0), (1, 0)], texts=[(H.TextBox(text="WAS-THIRD"),), (), ()])
    assert "WAS-THIRD" in page_text(res["path"], 0)


def test_text_length_must_match_the_plan(app, tmp_path):
    src = blank(str(tmp_path / "doc.pdf"), pages=2)
    with pytest.raises(H.PdfOpError):
        H.organize_pdf(src, [(0, 0), (1, 0)], texts=[(BOX,)])


def test_saving_without_text_never_touches_the_overlay_code(app, tmp_path, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("the overlay engine ran for a save with no text")
    monkeypatch.setattr(H, "apply_text_overlays", boom)
    src = blank(str(tmp_path / "doc.pdf"), pages=2)
    assert H.organize_pdf(src, [(1, 0), (0, 0)])["edited"] is False


# ------------------------------------------------------------------ laziness
def test_the_style_bar_and_font_list_are_not_built_until_text_is_switched_on(app):
    r = H.PdfReaderView()
    assert r._fmt is None and not hasattr(r, "fmt_font")
    assert not r.text_btn.isChecked() and r.canvas._editor is None
    r.text_btn.setEnabled(True)
    r.text_btn.setChecked(True)
    assert r._fmt is not None and hasattr(r, "fmt_font")
    r.text_btn.setChecked(False)
    assert r._fmt.isHidden()
    r.deleteLater()


def test_style_bar_controls_all_have_tips_and_names(app):
    r = H.PdfReaderView()
    r.text_btn.setEnabled(True)
    r.text_btn.setChecked(True)
    from PySide6.QtWidgets import QPushButton, QComboBox
    for w in list(r._fmt.findChildren(QPushButton)) + list(r._fmt.findChildren(QComboBox)):
        if w.parent() is not None and w.parent().__class__.__name__ == "QComboBox":
            continue
        assert w.toolTip().strip(), w.text() if hasattr(w, "text") else w
        assert w.accessibleName().strip()
    assert r.text_btn.toolTip() and r.text_btn.accessibleName()
    r.deleteLater()


# ------------------------------------------------------------------ the tool, through real events
def mouse(widget, kind, pos, buttons=Qt.LeftButton, btn=Qt.LeftButton):
    p = QPointF(pos)
    ev = QMouseEvent(kind, p, widget.mapToGlobal(p), btn, buttons, Qt.NoModifier)
    QApplication.sendEvent(widget, ev)


def click(canvas, pos):
    vp = canvas.viewport()
    mouse(vp, QEvent.MouseButtonPress, pos)
    mouse(vp, QEvent.MouseButtonRelease, pos, buttons=Qt.NoButton)


def drag(canvas, a, b):
    vp = canvas.viewport()
    mouse(vp, QEvent.MouseButtonPress, a)
    mouse(vp, QEvent.MouseMove, b)
    mouse(vp, QEvent.MouseButtonRelease, b, buttons=Qt.NoButton)


def reader_with_text_tool(app, panel, row=0, pages=5):
    r = read_row(app, panel, row, pages)
    drawn(app, r)
    r.text_btn.setChecked(True)
    visible = [i for i, rot in r.canvas.pages()[:1]]
    assert pump(app, lambda: all(i in r.canvas._layouts for i in visible))   # the page's own words are read (a blink after the tool turns on)
    return r


def page_point(canvas, pos=0, fx=0.3, fy=0.3):
    rect = canvas._page_rect(pos)
    return QPoint(rect.x() + int(fx * rect.width()), rect.y() + int(fy * rect.height()))


def type_in(r, text):
    ed = r.canvas._editor
    assert ed is not None
    ed.setPlainText(text)
    assert r.handle_escape()                                         # Esc finishes the box


def make_box(app, panel, text="Served on 10/14", fx=0.3, fy=0.3):
    r = reader_with_text_tool(app, panel)
    click(r.canvas, page_point(r.canvas, 0, fx, fy))
    type_in(r, text)
    return r


def test_click_with_the_tool_on_makes_a_box_and_typing_saves_it(app, panel):
    r = make_box(app, panel)
    idx = r.canvas.pages()[0][0]
    boxes = panel._texts[idx]
    assert len(boxes) == 1 and boxes[0].text == "Served on 10/14"
    assert abs(boxes[0].x - 0.3) < 0.02
    assert panel._is_dirty() and len(panel._undo) == 1
    assert r.canvas._editor is None


def test_click_with_the_tool_off_makes_nothing(app, panel):
    r = read_row(app, panel, 0, 5)
    drawn(app, r)
    assert not r.text_btn.isChecked()
    click(r.canvas, page_point(r.canvas))
    assert r.canvas._editor is None and not panel._texts and not panel._is_dirty()


def test_an_empty_box_is_dropped_and_changes_nothing(app, panel):
    r = reader_with_text_tool(app, panel)
    click(r.canvas, page_point(r.canvas))
    assert r.canvas._editor is not None                              # control: a box was started
    r.handle_escape()
    assert not panel._texts and not panel._undo and not panel._is_dirty()


def test_dragging_a_box_moves_it_once(app, panel):
    r = make_box(app, panel)
    idx = r.canvas.pages()[0][0]
    r.canvas.text_escape()
    box = panel._texts[idx][0]
    rect = r.canvas._box_rect(0, box)
    a = rect.center()
    drag(r.canvas, a, a + QPoint(60, 40))
    moved = panel._texts[idx][0]
    pr = r.canvas._page_rect(0)
    assert abs((moved.x - box.x) - 60 / pr.width()) < 0.01
    assert abs((moved.y - box.y) - 40 / pr.height()) < 0.01
    assert len(panel._undo) == 2                                     # create, then the move: one entry each


def test_a_box_cannot_be_dragged_off_the_page(app, panel):
    r = make_box(app, panel)
    idx = r.canvas.pages()[0][0]
    r.canvas.text_escape()
    rect = r.canvas._box_rect(0, panel._texts[idx][0])
    drag(r.canvas, rect.center(), rect.center() + QPoint(5000, 5000))
    b = panel._texts[idx][0]
    assert b.x + b.w <= 1.0 + 1e-9 and b.y + b.h <= 1.0 + 1e-9 and b.x >= 0 and b.y >= 0


def test_the_side_handle_makes_a_box_wider(app, panel):
    r = make_box(app, panel)
    idx = r.canvas.pages()[0][0]
    box = panel._texts[idx][0]
    rect = r.canvas._box_rect(0, box)
    handle = QPoint(rect.right(), rect.center().y())
    drag(r.canvas, handle, handle + QPoint(30, 0))
    wider = panel._texts[idx][0]
    assert wider.w > box.w and wider.x == box.x


def test_delete_removes_the_box_and_undo_brings_it_back(app, panel):
    r = make_box(app, panel)
    idx = r.canvas.pages()[0][0]
    r.canvas.setFocus()
    key(r.canvas, Qt.Key_Delete)
    assert not panel._texts.get(idx)
    assert len(panel._plan()) == 5                                   # the key deleted the box, not the page
    panel.undo_pages()
    assert len(panel._texts[idx]) == 1 and r.canvas.texts()[idx]


def test_undo_walks_back_through_text_then_pages_in_order(app, panel):
    r = make_box(app, panel)
    idx = r.canvas.pages()[0][0]
    r.rotateRequested.emit(90)                                        # a page change after the text
    assert panel._plan()[0][1] == 90
    panel.undo_pages()
    assert panel._plan()[0][1] == 0 and panel._texts[idx]            # the rotation came off first, the text is still there
    panel.undo_pages()
    assert not panel._texts and not panel._is_dirty()


def test_arrow_keys_nudge_and_shift_moves_further(app, panel):
    r = make_box(app, panel)
    idx = r.canvas.pages()[0][0]
    bw, _bh = r.canvas._sizes[idx]
    x0 = panel._texts[idx][0].x
    key(r.canvas, Qt.Key_Right)
    assert abs(panel._texts[idx][0].x - (x0 + 1 / bw)) < 1e-6
    key(r.canvas, Qt.Key_Right, Qt.ShiftModifier)
    assert abs(panel._texts[idx][0].x - (x0 + 11 / bw)) < 1e-6


def test_ctrl_d_copies_the_box(app, panel):
    r = make_box(app, panel)
    idx = r.canvas.pages()[0][0]
    key(r.canvas, Qt.Key_D, Qt.ControlModifier)
    assert len(panel._texts[idx]) == 2 and panel._texts[idx][0].text == panel._texts[idx][1].text


def test_style_keys_and_the_bar_change_the_selected_box(app, panel):
    r = make_box(app, panel)
    idx = r.canvas.pages()[0][0]
    key(r.canvas, Qt.Key_B, Qt.ControlModifier)
    assert panel._texts[idx][0].bold is True
    key(r.canvas, Qt.Key_B, Qt.ControlModifier)
    assert panel._texts[idx][0].bold is False
    r.fmt_size.setCurrentText("30")
    r._fmt_size_picked()
    assert panel._texts[idx][0].size == 30.0
    r.fmt_align["center"].setChecked(True)
    assert panel._texts[idx][0].align == "center"
    r.fmt_btns["underline"].setChecked(True)
    assert panel._texts[idx][0].underline is True


def test_a_wrong_size_is_clamped_not_crashed(app, panel):
    r = make_box(app, panel)
    idx = r.canvas.pages()[0][0]
    r.fmt_size.setCurrentText("9999")
    r._fmt_size_picked()
    assert panel._texts[idx][0].size == 200.0
    r.fmt_size.setCurrentText("banana")
    r._fmt_size_picked()
    assert panel._texts[idx][0].size == 200.0                        # unchanged, bar put back in step


def test_colour_choice_applies(app, panel, monkeypatch):
    from PySide6.QtGui import QColor
    from PySide6.QtWidgets import QColorDialog
    r = make_box(app, panel)
    idx = r.canvas.pages()[0][0]
    monkeypatch.setattr(QColorDialog, "getColor", staticmethod(lambda *a, **k: QColor("#cc0000")))
    r._fmt_pick_color()
    assert panel._texts[idx][0].color == "#cc0000"


def test_typing_style_changes_keep_what_was_typed(app, panel):
    r = reader_with_text_tool(app, panel)
    click(r.canvas, page_point(r.canvas))
    r.canvas._editor.setPlainText("half typed")
    r.canvas.set_text_style(bold=True)
    assert r.canvas._editor is not None and r.canvas._editor.toPlainText() == "half typed"
    r.handle_escape()
    assert list(panel._texts.values())[0][0].text == "half typed" and list(panel._texts.values())[0][0].bold


def test_escape_finishes_then_releases_then_switches_the_tool_off(app, panel):
    r = reader_with_text_tool(app, panel)
    click(r.canvas, page_point(r.canvas))
    r.canvas._editor.setPlainText("x")
    assert r.handle_escape() and r.canvas._editor is None and r.canvas.selected_text() is not None
    assert r.handle_escape() and r.canvas.selected_text() is None and r.text_btn.isChecked()
    assert r.handle_escape() and not r.text_btn.isChecked()
    assert r.canvas.text_tool_on() is False


def test_t_key_toggles_the_tool(app, panel):
    r = read_row(app, panel, 0, 5)
    drawn(app, r)
    r.canvas.setFocus()
    key(r.canvas, Qt.Key_T)
    assert r.text_btn.isChecked()
    key(r.canvas, Qt.Key_T)
    assert not r.text_btn.isChecked()


def test_a_turned_page_refuses_new_text_and_says_why(app, panel):
    r = read_row(app, panel, 0, 5)
    drawn(app, r)
    r.rotateRequested.emit(90)
    assert pump(app, lambda: r.canvas.pages()[0][1] == 90)
    r.text_btn.setChecked(True)
    click(r.canvas, page_point(r.canvas))
    assert not panel._texts and r.canvas._editor is None
    assert "upright" in r.status.text()


def test_existing_text_still_draws_on_a_turned_page(app, panel):
    r = make_box(app, panel)
    r.rotateRequested.emit(90)
    assert pump(app, lambda: r.canvas.pages()[0][1] == 90)
    r.canvas.viewport().grab()                                        # paints through the rotated path without raising
    assert panel._texts


def test_reopening_the_reader_shows_the_text_again(app, panel):
    r = make_box(app, panel)
    idx = r.canvas.pages()[0][0]
    panel.close_reader()
    r2 = read_row(app, panel, 0, 5)
    assert r2.canvas.texts().get(idx) and r2.canvas.texts()[idx][0].text == "Served on 10/14"


def test_pasted_page_preview_cannot_take_text(app, panel):
    open_row(app, panel, 0, 5)
    panel._preview_path = "x"                                         # the read-only view of pasted pages is showing
    r = panel._ensure_reader()
    panel._reader_sync_state()
    assert not r.text_btn.isEnabled()
    panel._preview_path = ""


# ------------------------------------------------------------------ saving from the panel
def test_saving_text_from_the_panel_makes_edited_copy_and_keeps_the_original(app, panel, folder, monkeypatch):
    H.save_settings({"organizer_replace_original": True})
    try:
        src = str(folder / "a.pdf")
        before = sha(src)
        r = make_box(app, panel, text="DRAWN-BY-TOOL")
        panel.save_and_next()
        out = str(folder / "Reorganized" / "a_edited.pdf")
        assert pump(app, lambda: os.path.exists(out) and not panel._busy)
        assert sha(src) == before                                     # replace-original was on and was ignored
        assert "DRAWN-BY-TOOL" in page_text(out, 0)
        assert not os.path.exists(str(folder / "Reorganized" / "Originals"))
    finally:
        H.save_settings({"organizer_replace_original": False})


def test_text_on_a_deleted_page_is_not_written_and_does_not_break_the_save(app, panel, folder):
    r = make_box(app, panel, text="GONE")
    idx = r.canvas.pages()[0][0]
    panel.close_reader()
    select(panel, 0)
    panel.delete_pages()
    assert not panel._has_texts()                                     # the only text was on the page that was removed
    assert panel._texts_for_plan() is None


def test_a_text_move_of_the_page_carries_the_text(app, panel, folder):
    r = make_box(app, panel, text="MOVED-WITH-PAGE")
    panel.close_reader()
    select(panel, 0)
    panel.move_later()
    plan = panel._plan()
    texts = panel._texts_for_plan()
    k = next(i for i, t in enumerate(texts) if t)
    assert plan[k][0] == 0 and texts[k][0].text == "MOVED-WITH-PAGE"


# ------------------------------------------------------------------ right-click
def right_click(r, point):
    """A real right click on the canvas: press, release, and the context-menu request Qt sends with it."""
    vp = r.canvas.viewport()
    mouse(vp, QEvent.MouseButtonPress, point, buttons=Qt.RightButton, btn=Qt.RightButton)
    mouse(vp, QEvent.MouseButtonRelease, point, buttons=Qt.NoButton, btn=Qt.RightButton)
    r.canvas._on_context(point)


def menu_for(app, panel, r, point):
    got = {}
    r.canvas.contextRequested.disconnect()
    r.canvas.contextRequested.connect(lambda g: got.setdefault("menu", panel._build_reader_menu()))
    right_click(r, point)
    return got["menu"]


def labels(menu):
    return [a.text().split("\t")[0] for a in menu.actions() if not a.isSeparator()]


def find(menu, text):
    for a in menu.actions():
        if a.text().split("\t")[0] == text:
            return a
        if a.menu() is not None:
            hit = find(a.menu(), text)
            if hit is not None:
                return hit
    return None


def test_right_click_on_a_box_offers_the_text_actions_first(app, panel):
    r = make_box(app, panel)
    idx = r.canvas.pages()[0][0]
    pt = r.canvas._box_rect(0, panel._texts[idx][0]).center()
    m = menu_for(app, panel, r, pt)
    ls = labels(m)
    assert ls[:2] == ["Edit text", "Duplicate"] and "Style" in ls and "Arrange" in ls and "Delete text" in ls
    assert any(l.startswith("Rotate") for l in ls)                       # the page actions are still there, below
    assert ls.index("Delete text") < ls.index([l for l in ls if l.startswith("Rotate")][0])
    assert r.canvas.selected_text() == (idx, 0)                          # the right-click selected the box it hit


def test_right_click_on_empty_page_offers_add_text_here_and_it_works(app, panel):
    r = read_row(app, panel, 0, 5)
    drawn(app, r)
    assert not r.text_btn.isChecked()
    pt = page_point(r.canvas, 0, 0.4, 0.6)
    m = menu_for(app, panel, r, pt)
    assert "Add text here" in labels(m) and "Edit text" not in labels(m)
    find(m, "Add text here").trigger()
    assert r.text_btn.isChecked() and r.canvas._editor is not None       # the tool came on by itself and a box is waiting
    type_in(r, "from the menu")
    b = list(panel._texts.values())[0][0]
    assert b.text == "from the menu" and abs(b.x - 0.4) < 0.03


def test_edit_text_from_the_menu_reopens_the_box_even_with_the_tool_off(app, panel):
    r = make_box(app, panel, text="first")
    idx = r.canvas.pages()[0][0]
    r.text_btn.setChecked(False)
    assert not r.text_btn.isChecked()
    pt = r.canvas._box_rect(0, panel._texts[idx][0]).center()
    m = menu_for(app, panel, r, pt)
    find(m, "Edit text").trigger()
    assert r.text_btn.isChecked() and r.canvas._editor is not None and r.canvas._editor.toPlainText() == "first"
    r.canvas._editor.setPlainText("second")
    r.handle_escape()
    assert panel._texts[idx][0].text == "second"


def test_menu_style_actions_change_the_box_and_show_its_current_state(app, panel):
    r = make_box(app, panel)
    idx = r.canvas.pages()[0][0]
    pt = r.canvas._box_rect(0, panel._texts[idx][0]).center()
    m = menu_for(app, panel, r, pt)
    assert find(m, "Bold").isChecked() is False and find(m, "Align left").isChecked() is True
    find(m, "Bold").trigger()
    assert panel._texts[idx][0].bold is True
    m = menu_for(app, panel, r, r.canvas._box_rect(0, panel._texts[idx][0]).center())
    assert find(m, "Bold").isChecked() is True                           # control: the menu reflects the change
    find(m, "Align right").trigger()
    find(m, "36").trigger()
    b = panel._texts[idx][0]
    assert b.align == "right" and b.size == 36.0


def test_menu_delete_duplicate_and_arrange(app, panel):
    r = make_box(app, panel, text="one")
    idx = r.canvas.pages()[0][0]
    pt = r.canvas._box_rect(0, panel._texts[idx][0]).center()
    find(menu_for(app, panel, r, pt), "Duplicate").trigger()
    assert [b.text for b in panel._texts[idx]] == ["one", "one"]
    first = r.canvas._box_rect(0, panel._texts[idx][0]).center()
    r.canvas.set_texts({idx: (replace_text(panel._texts[idx][0], "A"), replace_text(panel._texts[idx][1], "B"))})
    m = menu_for(app, panel, r, first + QPoint(0, 0))
    assert [b.text for b in r.canvas.texts()[idx]] == ["A", "B"]
    find(m, "Bring to front").trigger()
    assert [b.text for b in r.canvas.texts()[idx]] == ["B", "A"]
    m = menu_for(app, panel, r, r.canvas._box_rect(0, r.canvas.texts()[idx][-1]).center())
    find(m, "Delete text").trigger()
    assert len(r.canvas.texts()[idx]) == 1


def replace_text(box, t):
    return H.replace(box, text=t)


def test_add_text_here_is_refused_on_a_turned_page(app, panel):
    r = read_row(app, panel, 0, 5)
    drawn(app, r)
    r.rotateRequested.emit(90)
    assert pump(app, lambda: r.canvas.pages()[0][1] == 90)
    m = menu_for(app, panel, r, page_point(r.canvas))
    act = [a for a in m.actions() if a.text().startswith("Add text here")][0]
    assert not act.isEnabled() and "upright" in act.text()


def test_the_editors_own_menu_does_not_end_the_edit(app, panel):
    from PySide6.QtCore import Qt as _Qt
    from PySide6.QtGui import QFocusEvent
    r = reader_with_text_tool(app, panel)
    click(r.canvas, page_point(r.canvas))
    ed = r.canvas._editor
    ed.setPlainText("keep me")
    QApplication.sendEvent(ed, QFocusEvent(QEvent.FocusOut, _Qt.PopupFocusReason))
    assert r.canvas._editor is ed and not panel._texts                   # popup focus loss: still editing
    QApplication.sendEvent(ed, QFocusEvent(QEvent.FocusOut, _Qt.MouseFocusReason))
    assert r.canvas._editor is None and list(panel._texts.values())[0][0].text == "keep me"


def test_no_text_items_when_nothing_text_was_hit_and_the_tool_is_unavailable(app, panel):
    r = read_row(app, panel, 0, 5)
    drawn(app, r)
    r.text_btn.setEnabled(False)
    m = menu_for(app, panel, r, page_point(r.canvas))
    assert not any("text" in l.lower() for l in labels(m))


# ------------------------------------------------------------------ hostile and damaged files
def _pk():
    import pikepdf
    return pikepdf


def diff_bbox(a, b, thresh=60):
    from PIL import ImageChops
    d = ImageChops.difference(a, b).point(lambda v: 255 if v > thresh else 0)
    return d.getbbox()


@pytest.mark.parametrize("box", [[0, 0, 1e12, 1e12], [0, 0, 0, 0], [0, 0, 0.5, 0.5], [0, 0, 20000, 300]])
def test_a_page_with_an_untrustworthy_size_refuses_the_save_instead_of_dropping_the_text(app, tmp_path, box):
    pk = _pk()
    pdf = pk.new()
    pdf.add_blank_page(page_size=(300, 400))
    pdf.pages[0].obj.MediaBox = pk.Array([float(v) for v in box])
    src = str(tmp_path / "bad.pdf")
    pdf.save(src)
    before = sha(src)
    with pytest.raises(H.PdfOpError, match="Page 1"):
        H.organize_pdf(src, [(0, 0)], texts=[(BOX,)])
    assert sha(src) == before
    out = tmp_path / "Reorganized"
    assert not out.exists() or [p.name for p in out.iterdir()] == []          # no half-written file, no .part, no reservation
    # control: the same page saved WITHOUT text is not refused by this rule
    H.organize_pdf(src, [(0, 90)])


def test_an_odd_rotate_value_is_treated_as_upright_like_the_viewers_do(app, tmp_path):
    pk = _pk()
    paths = {}
    for name, rot in (("odd", 45), ("zero", 0)):
        p = blank(str(tmp_path / f"{name}.pdf"))
        pdf = pk.open(p, allow_overwriting_input=True)
        pdf.pages[0].obj.Rotate = rot
        pdf.save(str(tmp_path / f"{name}2.pdf"))
        paths[name] = str(tmp_path / f"{name}2.pdf")
    H.apply_text_overlays(paths["odd"], [(0, (BOX,))])
    H.apply_text_overlays(paths["zero"], [(0, (BOX,))])
    a, b = dark_bbox(render(paths["odd"])), dark_bbox(render(paths["zero"]))
    assert a == b and a is not None


@pytest.mark.parametrize("content", [b"1 0 0 1 200 200 cm 0.5 g 0 0 10 10 re f", b"Q Q Q 0 0 5 5 re f",
                                     b"q q q 2 0 0 2 0 0 cm"])
def test_unbalanced_page_content_does_not_move_the_text(app, tmp_path, content):
    pk = _pk()
    pdf = pk.new()
    pdf.add_blank_page(page_size=(300, 400))
    pdf.pages[0].obj.Contents = pdf.make_stream(content)
    p = str(tmp_path / "u.pdf")
    pdf.save(p)
    ref = blank(str(tmp_path / "ref.pdf"))
    H.apply_text_overlays(ref, [(0, (BOX,))])
    want = dark_bbox(render(ref))
    before = render(p)
    H.apply_text_overlays(p, [(0, (BOX,))])
    got = diff_bbox(before, render(p))
    assert got is not None and all(abs(g - w) <= 3 for g, w in zip(got, want))


def test_a_content_stream_that_inflates_hugely_is_not_inflated_by_the_text_step(app, tmp_path):
    import zlib
    pk = _pk()
    pdf = pk.new()
    pdf.add_blank_page(page_size=(300, 400))
    raw = zlib.compress(b" " * (200 * 1024 * 1024), 9)                    # ~200 MB once decoded, ~200 KB on disk
    pdf.pages[0].obj.Contents = pdf.make_stream(raw, Filter=pk.Name.FlateDecode)
    p = str(tmp_path / "bomb.pdf")
    pdf.save(p)
    import resource
    H.apply_text_overlays(p, [(0, (BOX,))])
    assert os.path.getsize(p) < 2 * 1024 * 1024                            # output stays small
    with pk.open(p) as o:
        streams = o.pages[0].obj.Contents
        arr = list(streams) if isinstance(streams, pk.Array) else [streams]
        assert len(arr) >= 4                                               # q, the page's own stream, Q, our text: not merged
        assert max(len(s.read_raw_bytes()) for s in arr) < 1024 * 1024 and any(len(s.read_raw_bytes()) > 100_000 for s in arr)


def test_the_text_pass_never_calls_the_merging_overlay(app, tmp_path, monkeypatch):
    pk = _pk()
    def boom(*a, **k):
        raise AssertionError("Page.add_overlay merges and decodes every content stream")
    monkeypatch.setattr(pk.Page, "add_overlay", boom)
    p = blank(str(tmp_path / "p.pdf"))
    assert H.apply_text_overlays(p, [(0, (BOX,))]) == 1


def test_one_save_cannot_be_asked_to_hold_unbounded_text(app, tmp_path):
    src = blank(str(tmp_path / "p.pdf"), pages=2)
    many = tuple(H.TextBox(text="x") for _ in range(H.TEXT_BOXES_TOTAL_MAX // 2 + 1))
    with pytest.raises(H.PdfOpError, match="more text"):
        H.organize_pdf(src, [(0, 0), (1, 0)], texts=[many, many])
    huge = (H.TextBox(text="y" * H.TEXT_BOX_MAX_CHARS),) * 120
    with pytest.raises(H.PdfOpError, match="more text"):
        H.organize_pdf(src, [(0, 0), (1, 0)], texts=[huge, huge])


def test_an_unwritable_overlay_location_is_an_error_not_a_silent_no_text(app, tmp_path):
    with pytest.raises(H.PdfOpError):
        H.text_overlay_pdf([(300, 400, 0, (BOX,))], str(tmp_path / "no_such_dir" / "o.pdf"))


def test_damaged_and_locked_files_are_refused_cleanly_with_text_on_the_page(app, tmp_path):
    pk = _pk()
    good = blank(str(tmp_path / "g.pdf"), pages=3)
    data = open(good, "rb").read()
    (tmp_path / "cut.pdf").write_bytes(data[:len(data) // 2])
    (tmp_path / "junk.pdf").write_bytes(b"%PDF-1.7\n" + os.urandom(3000))
    pdf = pk.new()
    pdf.add_blank_page()
    pdf.save(str(tmp_path / "locked.pdf"), encryption=pk.Encryption(user="pw", owner="o"))
    for name in ("cut", "junk", "locked"):
        src = str(tmp_path / f"{name}.pdf")
        with pytest.raises(H.PdfOpError):
            H.organize_pdf(src, [(0, 0)], texts=[(BOX,)])
    assert not (tmp_path / "Reorganized").exists() or not any(p.suffix == ".pdf" for p in (tmp_path / "Reorganized").iterdir())


# ------------------------------------------------------------------ signed documents
def fake_sign(path, level=None, pages=2, rotate=0, xref_stream=False, size=(300, 400)):
    """A PDF whose structure says 'signed' (a signature field holding a /ByteRange), optionally certified at DocMDP
    `level`. It is enough for everything except cryptographic validation, which the optional pyHanko test covers."""
    pk = _pk()
    pdf = pk.new()
    for _ in range(pages):
        pdf.add_blank_page(page_size=size)
    if rotate:
        pdf.pages[0].obj.Rotate = rotate
    sigv = pdf.make_indirect(pk.Dictionary(Type=pk.Name.Sig, Filter=pk.Name("/Adobe.PPKLite"),
                                           SubFilter=pk.Name("/adbe.pkcs7.detached"),
                                           ByteRange=pk.Array([0, 10, 20, 30]), Contents=pk.String("00")))
    field = pdf.make_indirect(pk.Dictionary(Type=pk.Name.Annot, Subtype=pk.Name.Widget, FT=pk.Name.Sig, T=pk.String("Sig1"),
                                            Rect=pk.Array([0, 0, 0, 0]), V=sigv, F=132, P=pdf.pages[0].obj))
    pdf.pages[0].obj.Annots = pk.Array([field])
    pdf.Root.AcroForm = pk.Dictionary(Fields=pk.Array([field]), SigFlags=3)
    if level is not None:
        ref = pk.Dictionary(Type=pk.Name.SigRef, TransformMethod=pk.Name.DocMDP,
                            TransformParams=pk.Dictionary(Type=pk.Name.TransformParams, P=level, V=pk.Name("/1.2")))
        sigv.Reference = pk.Array([ref])
        pdf.Root.Perms = pk.Dictionary(DocMDP=sigv)
    pdf.save(path, object_stream_mode=pk.ObjectStreamMode.generate if xref_stream else pk.ObjectStreamMode.disable)
    return path


def test_signature_state_tells_signed_from_unsigned_from_an_empty_box(app, tmp_path):
    pk = _pk()
    assert H.file_signature_state(blank(str(tmp_path / "plain.pdf"))) == "none"
    assert H.file_signature_state(fake_sign(str(tmp_path / "s.pdf"))) == "signed"
    pdf = pk.new()
    pdf.add_blank_page()
    f = pdf.make_indirect(pk.Dictionary(FT=pk.Name.Sig, T=pk.String("x"), Subtype=pk.Name.Widget, Rect=pk.Array([0, 0, 1, 1])))
    pdf.Root.AcroForm = pk.Dictionary(Fields=pk.Array([f]), SigFlags=1)
    pdf.save(str(tmp_path / "empty.pdf"))
    assert H.file_signature_state(str(tmp_path / "empty.pdf")) == "placeholder"
    assert H.file_signature_state(str(tmp_path / "missing.pdf")) == "none"


def test_keeping_a_signature_needs_unchanged_pages_and_a_signer_who_allows_it(app, tmp_path):
    keep = H.signed_text_options(fake_sign(str(tmp_path / "a.pdf")), [(0, 0), (1, 0)], 2)
    assert keep == {"signed": True, "can_keep": True, "why_not": ""}
    moved = H.signed_text_options(str(tmp_path / "a.pdf"), [(1, 0), (0, 0)], 2)
    assert moved["signed"] and not moved["can_keep"] and "moved" in moved["why_not"]
    turned = H.signed_text_options(str(tmp_path / "a.pdf"), [(0, 90), (1, 0)], 2)
    assert not turned["can_keep"]
    dropped = H.signed_text_options(str(tmp_path / "a.pdf"), [(0, 0)], 2)
    assert not dropped["can_keep"]
    for level, ok in ((1, False), (2, False), (3, True)):
        p = fake_sign(str(tmp_path / f"c{level}.pdf"), level=level)
        got = H.signed_text_options(p, [(0, 0), (1, 0)], 2)
        assert got["can_keep"] is ok, level
        if not ok:
            assert "locked" in got["why_not"]
    plain = H.signed_text_options(blank(str(tmp_path / "p.pdf"), pages=2), [(0, 0), (1, 0)], 2)
    assert plain == {"signed": False, "can_keep": False, "why_not": ""}


@pytest.mark.parametrize("rotate", [0, 90, 180, 270])
@pytest.mark.parametrize("xref_stream", [False, True])
def test_text_appended_after_a_signature_leaves_the_signed_bytes_alone_and_lands_in_the_right_place(app, tmp_path, rotate, xref_stream):
    src = fake_sign(str(tmp_path / "s.pdf"), rotate=rotate, xref_stream=xref_stream)
    raw = open(src, "rb").read()
    assert (b"/Type /XRef" in raw or b"/Type/XRef" in raw) is xref_stream            # the fixture really has the xref kind asked for
    dest = str(tmp_path / "s_edited.pdf")
    assert H.append_text_update(src, dest, [(BOX,), ()], audit="unit test") == 1
    new = open(dest, "rb").read()
    assert new[:len(raw)] == raw and len(new) > len(raw)                              # the signed part is byte-identical
    assert open(src, "rb").read() == raw
    # reference: the same page, unsigned, text merged into its content (the proven path)
    ref = str(tmp_path / "ref.pdf")
    pk = _pk()
    with pk.open(src) as p:
        for k in ("/AcroForm", "/Perms"):
            if k in p.Root:
                del p.Root[k]
        p.save(ref)
    H.apply_text_overlays(ref, [(0, (BOX,)), (0, ())])
    a, b = dark_bbox(render(dest)), dark_bbox(render(ref))
    assert a is not None and b is not None and all(abs(x - y) <= 2 for x, y in zip(a, b))
    with pk.open(dest) as d:
        assert len(d.pages) == 2
        an = [x for x in d.pages[0].obj.Annots if str(x.get("/Subtype")) == "/FreeText"]
        assert len(an) == 1 and "HELLO" in str(an[0].Contents) and "unit test" in str(an[0].AcroDEdit)
        assert str(d.pages[0].obj.Annots[0].get("/FT")) == "/Sig"                      # the signature field is still there
        assert "/FreeText" not in [str(x.get("/Subtype")) for x in (d.pages[1].obj.get("/Annots") or [])]
        assert H.file_signature_state(dest) == "signed"
    assert not [p for p in os.listdir(tmp_path) if p.endswith((".over", ".sigpart"))]


def test_appending_works_when_the_page_already_has_comments_in_an_indirect_array(app, tmp_path):
    pk = _pk()
    src = fake_sign(str(tmp_path / "s.pdf"))
    with pk.open(src, allow_overwriting_input=True) as p:
        arr = p.make_indirect(pk.Array(list(p.pages[1].obj.get("/Annots") or [])))
        note = p.make_indirect(pk.Dictionary(Type=pk.Name.Annot, Subtype=pk.Name.Text, Rect=pk.Array([5, 5, 20, 20]), Contents=pk.String("kept")))
        arr.append(note)
        p.pages[1].obj.Annots = arr
        p.save(str(tmp_path / "s2.pdf"))
    src = str(tmp_path / "s2.pdf")
    H.append_text_update(src, str(tmp_path / "o.pdf"), [(), (BOX,)])
    with pk.open(str(tmp_path / "o.pdf")) as d:
        subs = [str(x.get("/Subtype")) for x in d.pages[1].obj.Annots]
        assert subs == ["/Text", "/FreeText"]


def test_appending_refuses_encrypted_mismatched_and_empty_requests(app, tmp_path):
    pk = _pk()
    src = fake_sign(str(tmp_path / "s.pdf"))
    with pytest.raises(H.PdfOpError):
        H.append_text_update(src, str(tmp_path / "o.pdf"), [(), ()])                  # nothing to add
    with pytest.raises(H.PdfOpError):
        H.append_text_update(src, str(tmp_path / "o.pdf"), [(BOX,)])                  # wrong page count
    pdf = pk.open(src)
    pdf.save(str(tmp_path / "enc.pdf"), encryption=pk.Encryption(user="", owner="o"))
    with pytest.raises(H.PdfOpError):
        H.append_text_update(str(tmp_path / "enc.pdf"), str(tmp_path / "o2.pdf"), [(BOX,), ()])
    assert not (tmp_path / "o.pdf").exists() and not (tmp_path / "o2.pdf").exists()


def test_organize_keeps_the_signature_only_when_asked_and_allowed(app, tmp_path):
    src = fake_sign(str(tmp_path / "doc.pdf"))
    before = sha(src)
    res = H.organize_pdf(src, [(0, 0), (1, 0)], texts=[(BOX,), ()], keep_signatures=True)
    assert res["edited"] and res["signature_kept"] and not res["signature_removed"]
    assert os.path.basename(res["path"]) == "doc_edited.pdf" and sha(src) == before
    assert open(res["path"], "rb").read().startswith(open(src, "rb").read())
    # the same request WITHOUT the keep flag rebuilds the file, so the signature is gone, and it says so
    res2 = H.organize_pdf(src, [(0, 0), (1, 0)], texts=[(BOX,), ()])
    assert res2["signature_removed"] and not res2["signature_kept"]
    assert H.file_signature_state(res2["path"]) != "signed" and "HELLO" in page_text(res2["path"], 0)   # field left empty, value gone
    # keeping is refused when the pages changed, with the reason
    with pytest.raises(H.PdfOpError, match="moved"):
        H.organize_pdf(src, [(1, 0), (0, 0)], texts=[(BOX,), ()], keep_signatures=True)
    # and on a file that has no signature to keep
    with pytest.raises(H.PdfOpError):
        H.organize_pdf(blank(str(tmp_path / "plain.pdf"), pages=2), [(0, 0), (1, 0)], texts=[(BOX,), ()], keep_signatures=True)
    assert sha(src) == before


def test_every_edited_file_records_what_it_was_edited_from(app, tmp_path):
    pk = _pk()
    src = blank(str(tmp_path / "doc.pdf"))
    res = H.organize_pdf(src, [(0, 0)], texts=[(BOX,)])
    with pk.open(res["path"]) as d:
        note = str(d.docinfo["/AcroDEdit"])
    assert "sha256 " + __import__("hashlib").sha256(open(src, "rb").read()).hexdigest() in note
    assert "doc.pdf" in note and "1 text box" in note and "Z" in note


@pytest.fixture
def signed_panel(app, tmp_path):
    d = tmp_path / "pdfs"
    d.mkdir()
    fake_sign(str(d / "a.pdf"), pages=5)
    blank(str(d / "b.pdf"), pages=3)
    p = H.OrganizerPanel()
    p.resize(1200, 800)
    p.show()
    p.folder_row.set_path(str(d))
    assert pump(app, lambda: p.file_list.count() == 2 and len(p._counts) == 2)
    yield p, d
    p.stop_for_shutdown()
    p.stop_background()
    p.close()


def _text_on_first_page(app, panel, row=0, pages=5):
    r = reader_with_text_tool(app, panel, row, pages)
    click(r.canvas, page_point(r.canvas))
    type_in(r, "approved item")
    return r


@pytest.mark.parametrize("answer,kept,removed", [("keep", True, False), ("remove", False, True)])
def test_saving_text_into_a_signed_file_asks_first_and_does_what_was_chosen(app, signed_panel, monkeypatch, answer, kept, removed):
    panel, d = signed_panel
    asked, said = [], []
    monkeypatch.setattr(H, "_ask_signed_choice", lambda parent, opts: asked.append(opts) or answer)
    orig_status = panel._set_status
    monkeypatch.setattr(panel, "_set_status", lambda text, kind="": (said.append(text), orig_status(text, kind))[1])
    src = str(d / "a.pdf")
    before = sha(src)
    _text_on_first_page(app, panel)
    panel.save_and_next()
    out = str(d / "Reorganized" / "a_edited.pdf")
    assert pump(app, lambda: os.path.exists(out) and not panel._busy)
    assert len(asked) == 1 and asked[0]["signed"] and asked[0]["can_keep"]
    assert (H.file_signature_state(out) == "signed") is kept
    assert sha(src) == before
    assert any("signature is kept" in t for t in said) is kept
    assert any("not in the saved copy" in t for t in said) is removed


def test_cancelling_the_signed_prompt_saves_nothing_and_keeps_the_text(app, signed_panel, monkeypatch):
    panel, d = signed_panel
    monkeypatch.setattr(H, "_ask_signed_choice", lambda parent, opts: "cancel")
    r = _text_on_first_page(app, panel)
    panel.save_and_next()
    assert not panel._busy and panel._texts and panel._is_dirty()
    assert not (d / "Reorganized").exists() or not list((d / "Reorganized").iterdir())


def test_the_prompt_is_not_shown_for_unsigned_files_or_for_saves_without_text(app, signed_panel, monkeypatch):
    panel, d = signed_panel
    monkeypatch.setattr(H, "_ask_signed_choice", lambda *a: (_ for _ in ()).throw(AssertionError("prompted")))
    _text_on_first_page(app, panel, row=1, pages=3)                       # b.pdf: not signed
    panel.close_reader()
    panel.save_and_next()
    assert pump(app, lambda: os.path.exists(str(d / "Reorganized" / "b_edited.pdf")) and not panel._busy)


def test_a_signed_file_saved_without_text_is_not_prompted(app, signed_panel, monkeypatch):
    panel, d = signed_panel
    monkeypatch.setattr(H, "_ask_signed_choice", lambda *a: (_ for _ in ()).throw(AssertionError("prompted")))
    panel.file_list.setCurrentRow(0)
    assert pump(app, lambda: len(panel._pages) == 5 and len(panel._thumbs) == 5)
    select(panel, 0)
    panel.delete_pages()
    panel.save_and_next()
    assert pump(app, lambda: os.path.exists(str(d / "Reorganized" / "a.pdf")) and not panel._busy)


def test_the_dialog_offers_keep_only_when_it_can_and_defaults_to_cancel(app, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    seen = {}

    def fake_exec(self):
        seen["buttons"] = [b.text() for b in self.buttons()]
        seen["default"] = self.defaultButton().text()
        seen["text"] = self.text()
        return 0
    monkeypatch.setattr(QMessageBox, "exec", fake_exec)
    assert H._ask_signed_choice(None, {"signed": True, "can_keep": True, "why_not": ""}) == "cancel"
    assert sorted(seen["buttons"]) == sorted(["Keep the signature", "Remove the signature and save", "Cancel"])
    assert seen["default"] == "Cancel" and "approved item" in seen["text"]
    H._ask_signed_choice(None, {"signed": True, "can_keep": False, "why_not": "The signer locked this document against added text and comments."})
    assert sorted(seen["buttons"]) == sorted(["Remove the signature and save", "Cancel"]) and "locked" in seen["text"]


def test_the_signature_still_validates_after_the_text_is_added(app, tmp_path):
    """Cryptographic check with pyHanko when it is installed: the signed bytes must still verify."""
    pytest.importorskip("pyhanko")
    pytest.importorskip("cryptography")
    import io as _io, datetime as _dt
    from cryptography import x509
    from cryptography.x509.oid import NameOID
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from pyhanko.sign import signers
    from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter
    from pyhanko.pdf_utils.reader import PdfFileReader
    from pyhanko.sign.validation import validate_pdf_signature
    from pyhanko_certvalidator import ValidationContext
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Test Signer")])
    now = _dt.datetime.now(_dt.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key()).serial_number(1)
            .not_valid_before(now - _dt.timedelta(days=1)).not_valid_after(now + _dt.timedelta(days=5))
            .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True).sign(key, hashes.SHA256()))
    (tmp_path / "k.key").write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    (tmp_path / "k.crt").write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    signer = signers.SimpleSigner.load(str(tmp_path / "k.key"), str(tmp_path / "k.crt"))
    pk = _pk()
    pdf = pk.new()
    for _ in range(2):
        pdf.add_blank_page(page_size=(300, 400))
    buf = _io.BytesIO()
    pdf.save(buf)
    buf.seek(0)
    signed = signers.sign_pdf(IncrementalPdfFileWriter(buf), signers.PdfSignatureMetadata(field_name="Sig1"), signer=signer)
    src = tmp_path / "signed.pdf"
    src.write_bytes(signed.getvalue())
    assert H.file_signature_state(str(src)) == "signed"
    dest = str(tmp_path / "signed_edited.pdf")
    H.append_text_update(str(src), dest, [(BOX,), ()])
    from pyhanko.keys import load_cert_from_pemder
    vc = ValidationContext(trust_roots=[load_cert_from_pemder(str(tmp_path / "k.crt"))])
    with open(dest, "rb") as f:
        st = validate_pdf_signature(PdfFileReader(f).embedded_signatures[0], vc)
    assert st.intact and st.valid and st.trusted                              # the signed bytes still verify
    # control: damage one signed byte and the same check fails
    bad = bytearray(open(dest, "rb").read())
    bad[300] ^= 0xFF
    (tmp_path / "bad.pdf").write_bytes(bytes(bad))
    with open(tmp_path / "bad.pdf", "rb") as f:
        try:
            st2 = validate_pdf_signature(PdfFileReader(f).embedded_signatures[0], vc)
            assert not (st2.intact and st2.valid)
        except Exception:
            pass
