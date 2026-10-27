"""PDF reader engine (no Qt): zoom, layout, cache, queue, staleness, and
highlight geometry checked against the actual rendered pixels."""
import os, sys, threading, time
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import pytest
from pypdf import PdfWriter
from pypdf.generic import (DecodedStreamObject, DictionaryObject, NameObject)
import acrod as H


def make_text_pdf(path, pages=1, text="HELLOWORLD", rotate=None, size=(400, 300), at=(60, 120)):
    """Pages with one run of big black text at a known place (PDF space)."""
    w = PdfWriter()
    for i in range(pages):
        pg = w.add_blank_page(width=size[0], height=size[1])
        font = DictionaryObject({NameObject("/Type"): NameObject("/Font"),
                                 NameObject("/Subtype"): NameObject("/Type1"),
                                 NameObject("/BaseFont"): NameObject("/Helvetica")})
        pg[NameObject("/Resources")] = DictionaryObject({
            NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})})
        st = DecodedStreamObject()
        st.set_data(f"BT /F1 40 Tf {at[0]} {at[1]} Td ({text}{i}) Tj ET".encode())
        pg[NameObject("/Contents")] = w._add_object(st)
        if rotate and i in rotate:
            pg.rotate(rotate[i])
    with open(path, "wb") as f:
        w.write(f)
    return str(path)


# ------------------------------------------------------------------ zoom
def test_clamp_zoom_bounds_and_garbage():
    assert H.reader_clamp_zoom(0.01) == H.READER_ZOOM_MIN
    assert H.reader_clamp_zoom(99) == H.READER_ZOOM_MAX
    assert H.reader_clamp_zoom(1.5) == 1.5
    assert H.reader_clamp_zoom("x") == 1.0
    assert H.reader_clamp_zoom(None) == 1.0
    assert H.reader_clamp_zoom(float("nan")) == 1.0


def test_zoom_step_always_moves_and_stops_at_ends():
    assert H.reader_zoom_step(1.0, +1) == 1.25
    assert H.reader_zoom_step(1.0, -1) == 0.75
    assert H.reader_zoom_step(1.1, +1) == 1.25          # off-ladder snaps to the next stop
    assert H.reader_zoom_step(1.1, -1) == 1.0
    assert H.reader_zoom_step(4.0, +1) == 4.0
    assert H.reader_zoom_step(0.25, -1) == 0.25
    z = 0.25
    seen = [z]
    for _ in range(30):
        z = H.reader_zoom_step(z, +1)
        seen.append(z)
    assert seen == sorted(seen) and seen[-1] == 4.0


def test_fit_scale_modes_and_quarter_turn():
    # 600x800 page in a 624x824 view (24 margin each side): width fits at 1.0
    assert H.reader_fit_scale("width", 600, 800, 0, 648, 848) == pytest.approx(1.0)
    # a quarter turn swaps the sides: 800 wide now
    assert H.reader_fit_scale("width", 600, 800, 90, 848, 648) == pytest.approx(1.0)
    assert H.reader_fit_scale("width", 600, 800, 90, 648, 848) == pytest.approx(600 / 800)
    # fit-page takes the tighter side
    assert H.reader_fit_scale("page", 600, 800, 0, 2000, 448) == pytest.approx(400 / 800)
    assert H.reader_fit_scale(2.0, 600, 800, 0, 10, 10) == 2.0
    assert H.reader_fit_scale(50, 600, 800, 0, 10, 10) == H.READER_ZOOM_MAX
    # a tiny viewport never divides by zero or goes negative
    assert H.reader_fit_scale("width", 600, 800, 0, 0, 0) >= H.READER_ZOOM_MIN


# ------------------------------------------------------------------ layout
def test_layout_geometry_and_total():
    lay = H.ReaderLayout([(100, 200), (100, 200), (200, 100)], 1.0, gap=10, pad=20)
    assert lay.tops == [20, 230, 440]
    assert lay.heights == [200, 200, 100]
    assert lay.widths == [100, 100, 200]
    assert lay.total_height == 440 + 100 + 20
    assert lay.max_width == 200
    assert len(lay) == 3


def test_layout_visible_range_and_prefetch():
    lay = H.ReaderLayout([(100, 200)] * 10, 1.0, gap=10, pad=20)     # tops 20,230,440,...
    assert lay.visible(0, 100, prefetch=0) == (0, 0)
    assert lay.visible(0, 100, prefetch=1) == (0, 1)
    assert lay.visible(450, 100, prefetch=0) == (2, 2)
    assert lay.visible(450, 100, prefetch=1) == (1, 3)
    # a viewport spanning the gap between two pages shows both
    assert lay.visible(400, 100, prefetch=0) == (1, 2)
    # scrolled past the end clamps instead of raising
    assert lay.visible(10 ** 6, 100, prefetch=1)[1] == 9
    assert H.ReaderLayout([], 1.0).visible(0, 100) == (0, -1)


def test_layout_current_page_follows_upper_third():
    lay = H.ReaderLayout([(100, 200)] * 5, 1.0, gap=10, pad=20)
    assert lay.current(0, 300) == 0
    assert lay.current(lay.tops[3] - 100, 300) == 3     # page 3 top is exactly under the probe
    assert lay.current(lay.tops[3] - 101, 300) == 2
    assert lay.current(10 ** 6, 300) == 4
    assert lay.scroll_for(2) == lay.tops[2] - lay.gap
    assert lay.scroll_for(99) == lay.scroll_for(4)
    assert lay.scroll_for(-3) == lay.scroll_for(0)


def test_render_scale_respects_device_ratio_and_bomb_guard():
    assert H.reader_render_scale(600, 800, 1.0, 1.0) == pytest.approx(1.0)
    assert H.reader_render_scale(600, 800, 1.0, 2.0) == pytest.approx(2.0)
    s = H.reader_render_scale(2, 14400, 4.0, 2.0)                # the thumbnail-bomb shape
    assert 2 * s * 14400 * s <= H.READER_MAX_PX * 1.01 and 14400 * s <= H.READER_MAX_SIDE * 1.01
    s = H.reader_render_scale(600, 800, 4.0, 3.0)
    assert 600 * s * 800 * s <= H.READER_MAX_PX * 1.01


# ------------------------------------------------------------------ cache
def test_cache_evicts_oldest_by_pixels_and_get_refreshes():
    c = H.ReaderCache(100)
    c.put("a", 1, 40)
    c.put("b", 2, 40)
    assert c.get("a") == 1                      # a is now newest
    c.put("c", 3, 40)                           # 120 > 100: b (oldest) goes
    assert "b" not in c and "a" in c and "c" in c
    assert c.px == 80 and len(c) == 2


def test_cache_refuses_oversize_and_keeps_accounting():
    c = H.ReaderCache(100)
    c.put("a", 1, 60)
    assert c.put("big", 2, 101) is False
    assert "a" in c and c.px == 60
    c.put("a", 9, 30)                            # replace, not add
    assert c.px == 30 and c.get("a") == 9
    c.drop_where(lambda k: k == "a")
    assert c.px == 0 and len(c) == 0
    c.put("x", 1, 10)
    c.clear()
    assert c.px == 0 and c.get("x") is None


# ------------------------------------------------------------------ queue
def test_queue_priority_dedupe_and_generation():
    q = H.ReaderQueue()
    g = q.generation
    assert q.push(g, ("p", 5), 5, "five")
    assert q.push(g, ("p", 1), 1, "one")
    assert q.push(g, ("p", 5), 0, "five-urgent")         # repeat key: only re-prioritised
    assert q.pending() == 2
    assert q.pop(0.1)[2] == "five-urgent"
    assert q.pop(0.1)[2] == "one"
    assert q.pop(0.05) is None
    assert q.push(g, ("p", 2), 2, "two")
    g2 = q.new_generation()
    assert g2 == g + 1 and q.pending() == 0
    assert q.push(g, ("p", 3), 3, "stale") is False      # an older generation is refused
    assert q.push(g2, ("p", 3), 3, "fresh")


def test_queue_unique_payloads_are_never_dropped():
    q = H.ReaderQueue()
    g = q.generation
    for i in range(50):
        q.push(g, ("page", i), i, i)
    got = []
    while True:
        item = q.pop(0.01)
        if item is None:
            break
        got.append(item[2])
    assert got == list(range(50))


def test_queue_prune_and_close_unblocks_waiter():
    q = H.ReaderQueue()
    g = q.generation
    q.push(g, "a", 1, "a")
    q.push(g, "b", 2, "b")
    q.prune({"b"})
    assert q.pop(0.05)[1] == "b"
    out = []
    t = threading.Thread(target=lambda: out.append(q.pop(5)))
    t.start()
    time.sleep(0.1)
    q.close()
    t.join(2)
    assert not t.is_alive() and out == [None]
    assert q.push(g, "c", 1, "c") is False


# ------------------------------------------------------------------ staleness
def test_reader_stale_detects_change_and_missing(tmp_path):
    p = tmp_path / "a.pdf"
    make_text_pdf(p)
    stamp = H.source_stamp(str(p))
    assert H.reader_stale(stamp, str(p)) is False
    time.sleep(0.02)
    with open(p, "ab") as f:
        f.write(b"\n%x")
    assert H.reader_stale(stamp, str(p)) is True
    os.remove(p)
    assert H.reader_stale(stamp, str(p)) is True
    assert H.reader_stale(None, str(p)) is True


def test_hit_fraction_orders_clips_and_rejects_empty():
    assert H.reader_hit_fraction((50, 10, 10, 30), 100, 100) == (0.1, 0.1, 0.5, 0.3)
    assert H.reader_hit_fraction((-20, -5, 30, 50), 100, 100) == (0.0, 0.0, 0.3, 0.5)
    assert H.reader_hit_fraction((10, 10, 10, 30), 100, 100) is None
    assert H.reader_hit_fraction((200, 10, 300, 30), 100, 100) is None


# ------------------------------------------------------------------ pdfium-backed
def test_page_sizes_apply_the_pages_own_rotate(tmp_path):
    p = tmp_path / "s.pdf"
    make_text_pdf(p, pages=2, rotate={1: 90}, size=(400, 300))
    assert H.pdf_page_sizes(str(p)) == [(400.0, 300.0), (300.0, 400.0)]


def _open(path):
    pdfium = H._pdfium()
    return pdfium.PdfDocument(path)


def test_render_page_size_follows_scale_and_extra_rotation(tmp_path):
    p = make_text_pdf(tmp_path / "r.pdf", size=(400, 300))
    doc = _open(p)
    try:
        assert H.reader_render_page(doc, 0, 1.0, 0).size == (400, 300)
        assert H.reader_render_page(doc, 0, 0.5, 0).size == (200, 150)
        assert H.reader_render_page(doc, 0, 1.0, 90).size == (300, 400)
        assert H.reader_render_page(doc, 0, 1.0, 180).size == (400, 300)
        assert H.reader_render_page(doc, 0, 1.0, 270).size == (300, 400)
    finally:
        doc.close()


def _ink_box(img):
    """Bounding box of dark pixels as fractions of the image."""
    g = img.convert("L").point(lambda v: 255 if v < 100 else 0)
    bb = g.getbbox()
    assert bb, "no ink rendered"
    return (bb[0] / img.width, bb[1] / img.height, bb[2] / img.width, bb[3] / img.height)


@pytest.mark.parametrize("own", [0, 90, 180, 270])
@pytest.mark.parametrize("extra", [0, 90, 180, 270])
def test_find_boxes_sit_on_the_ink_for_every_rotation(tmp_path, own, extra):
    p = make_text_pdf(tmp_path / "f.pdf", text="QZQZQZ", rotate={0: own} if own else None)
    doc = _open(p)
    try:
        boxes = H.reader_find_boxes(doc, 0, "qzqzqz0", extra)   # the whole run, so box == ink
        assert len(boxes) == 1
        ink = _ink_box(H.reader_render_page(doc, 0, 2.0, extra))
        b = boxes[0]
        # the text is the only ink, so the highlight must hug it
        for got, want in zip(b, ink):
            assert abs(got - want) < 0.04, (own, extra, b, ink)
    finally:
        doc.close()


def test_find_boxes_none_and_case_and_blank(tmp_path):
    p = make_text_pdf(tmp_path / "g.pdf", text="Alpha")
    doc = _open(p)
    try:
        assert H.reader_find_boxes(doc, 0, "zzz", 0) == []
        assert H.reader_find_boxes(doc, 0, "", 0) == []
        assert H.reader_find_boxes(doc, 0, "   ", 0) == []
        assert len(H.reader_find_boxes(doc, 0, "ALPHA", 0)) == 1
    finally:
        doc.close()


def test_cache_keeps_entries_that_exactly_fill_the_budget():
    c = H.ReaderCache(100)
    c.put("a", 1, 50)
    c.put("b", 2, 50)                         # exactly the budget: nothing is evicted
    assert "a" in c and "b" in c and c.px == 100
    c.put("c", 3, 1)                          # one over: the oldest goes
    assert "a" not in c and "b" in c and "c" in c
