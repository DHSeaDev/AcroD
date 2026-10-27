"""Editing text that is already on a PDF page: reading a page's words as runs, building a box that sits on them,
taking the old words out of the saved page (checked by drawing it before and after) or covering them where that cannot be
trusted, scans, signed files, and the Text tool through real mouse and key events."""
import os, sys
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import pytest
from dataclasses import replace
from PySide6.QtCore import Qt, QPoint, QEvent, QRect
from PySide6.QtWidgets import QApplication
import acrod as H
from test_organizer_panel import app, pump, sha  # noqa: F401
from test_text_tool import (blank, render, dark_bbox, page_text, mouse, click, right_click, menu_for, labels,  # noqa: F401
                            find, page_point, type_in)


# ------------------------------------------------------------------ fixtures
LINES = [  # font key, size, x, y (PDF, from the bottom), text
    ("F2", 18, 72, 700, "Affidavit of Service"),
    ("F1", 12, 72, 660, "The undersigned states that on the 5th day of May,"),
    ("F1", 12, 72, 645.6, "2026, he served the summons upon the defendant"),
    ("F1", 12, 72, 631.2, "at the address stated below."),
    ("F3", 10, 72, 560, "Case No. CVCV123456"),
]
FONTS = {"F1": "Times-Roman", "F2": "Helvetica-Bold", "F3": "Courier"}


def text_pdf(path, lines=LINES, size=(612, 792), rotate=0, raw=None, pages=1):
    """A real text PDF: standard fonts, one Tj per line, so the words are page text, not pictures."""
    import pikepdf
    pdf = pikepdf.new()
    fonts = pikepdf.Dictionary({"/" + k: pdf.make_indirect(pikepdf.Dictionary(Type=pikepdf.Name.Font, Subtype=pikepdf.Name.Type1,
                                                                        BaseFont=pikepdf.Name("/" + v)))
                                for k, v in FONTS.items()})
    for _ in range(pages):
        if raw is None:
            body = "".join(f"BT /{f} {sz} Tf {x} {y} Td ({t}) Tj ET\n" for f, sz, x, y, t in lines)
        else:
            body = raw
        page = pdf.add_blank_page(page_size=size)
        page.obj["/Resources"] = pikepdf.Dictionary(Font=fonts)
        page.obj["/Contents"] = pdf.make_stream(body.encode("latin-1"))
        if rotate:
            page.obj["/Rotate"] = rotate
    pdf.save(path)
    return path


def runs_of(path, page=0):
    pdfium = H._pdfium()
    doc = pdfium.PdfDocument(path)
    try:
        return H.reader_text_layout(doc, page)
    finally:
        doc.close()


def edited_box(path, texts, para=True, which=1, size=(612, 792)):
    runs = runs_of(path)
    ids = H.text_paragraph(runs, which, *size) if para else [which]
    box = H.text_box_from_segments(runs, ids, size[0], size[1])
    return runs, ids, replace(box, text=texts)


def render_rgb(path, page=0, scale=1.0):
    import pypdfium2
    doc = pypdfium2.PdfDocument(path)
    try:
        return doc[page].render(scale=scale).to_pil().convert("RGB")
    finally:
        doc.close()


def copy(src, dst):
    import shutil
    shutil.copy(src, dst)
    return dst


# ------------------------------------------------------------------ reading a page's words
def test_runs_carry_text_place_size_and_style(app, tmp_path):
    runs = runs_of(text_pdf(str(tmp_path / "t.pdf")))
    assert [r["text"] for r in runs] == [l[4] for l in LINES]
    head, first, _second, _third, case = runs
    assert head["bold"] and head["family"] == "Arial" and abs(head["size"] - 18) < 0.2
    assert first["family"] == "Times New Roman" and not first["bold"] and abs(first["size"] - 12) < 0.2
    assert case["family"] == "Courier New" and abs(case["size"] - 10) < 0.2
    assert abs(first["by"] - (792 - 660) / 792) < 0.002                   # the baseline, as a fraction of the page from the top
    assert abs(first["x0"] - 72 / 612) < 0.003 and first["x1"] > first["x0"] and all(r["edit"] for r in runs)


def test_a_colour_is_read(app, tmp_path):
    p = text_pdf(str(tmp_path / "c.pdf"), raw="1 0 0 rg BT /F1 12 Tf 72 700 Td (RED WORDS) Tj ET")
    assert runs_of(p)[0]["color"] == "#ff0000"


def test_text_scaled_by_the_matrix_has_its_real_size(app, tmp_path):
    p = text_pdf(str(tmp_path / "m.pdf"), raw="BT /F1 1 Tf 11.04 0 0 11.04 72 700 Tm (SCALED) Tj ET\n"
                                              "q 0.5 0 0 0.5 0 0 cm BT /F1 24 Tf 144 1200 Td (HALF) Tj ET Q")
    sc, half = runs_of(p)
    assert abs(sc["size"] - 11.04) < 0.1 and abs(half["size"] - 12.0) < 0.1


def test_a_wide_gap_starts_a_new_run_and_a_new_line_does_too(app, tmp_path):
    p = text_pdf(str(tmp_path / "g.pdf"), raw="BT /F1 12 Tf 72 700 Td [(Name) -9000 (John Smith)] TJ ET")
    runs = runs_of(p)
    assert [r["text"] for r in runs] == ["Name", "John Smith"]


def test_text_on_a_turned_page_is_not_offered_for_editing_when_it_runs_sideways(app, tmp_path):
    p = text_pdf(str(tmp_path / "r.pdf"), rotate=90)
    runs = runs_of(p)
    assert runs and not any(r["edit"] for r in runs)                      # the words run top to bottom on screen
    assert runs_of(text_pdf(str(tmp_path / "r0.pdf")))[0]["edit"]         # control


def test_a_page_with_no_text_or_too_much_gives_nothing(app, tmp_path, monkeypatch):
    assert runs_of(blank(str(tmp_path / "b.pdf"))) == []
    p = text_pdf(str(tmp_path / "t.pdf"))
    monkeypatch.setattr(H, "TEXT_LAYOUT_MAX_CHARS", 20)
    assert runs_of(p) == []


@pytest.mark.parametrize("name,flags,want", [
    ("ABCDEF+Times-BoldItalic", 0, ("Times New Roman", True, True)),
    ("Helvetica-Oblique", 0, ("Arial", False, True)),
    ("CourierNewPSMT", 0, ("Courier New", False, False)),
    ("Calibri-Bold", 0, ("Arial", True, False)),
    ("Georgia", 0, ("Times New Roman", False, False)),
    ("SomeSerifFont", 2, ("Times New Roman", False, False)),
    ("Arial-BoldMT", 2, ("Arial", True, False)),
    ("Monotype Corsiva", 0, ("Arial", False, False)),
    ("Whatever", 1, ("Courier New", False, False)),
    ("", 0, ("Arial", False, False)),
])
def test_font_names_map_to_a_nearby_installed_family(name, flags, want):
    assert H.text_family_for_font(name, flags) == want


def test_paragraphs_group_stacked_lines_only(app, tmp_path):
    p = text_pdf(str(tmp_path / "t.pdf"))
    runs = runs_of(p)
    assert H.text_paragraph(runs, 2, 612, 792) == [1, 2, 3]
    assert H.text_paragraph(runs, 0, 612, 792) == [0]                      # the heading is a different size
    assert H.text_paragraph(runs, 4, 612, 792) == [4]                      # and so is the line far below
    assert H.text_paragraph(runs, 99, 612, 792) == [] and H.text_paragraph([], 0) == []


def test_a_heading_just_above_a_paragraph_is_not_part_of_it(app, tmp_path):
    lines = [("F2", 14, 72, 650, "AFFIDAVIT OF SERVICE"), ("F1", 12, 72, 620, "I, Jane Roe, being duly sworn,"),
             ("F1", 12, 72, 605.6, "state that on the 5th day of May,"), ("F1", 12, 72, 591.2, "I served the summons."),
             ("F1", 12, 72, 540, "A different paragraph.")]
    runs = runs_of(text_pdf(str(tmp_path / "h.pdf"), lines=lines))
    assert H.text_paragraph(runs, 2, 612, 792) == [1, 2, 3]                  # same font and size, close together
    assert H.text_paragraph(runs, 0, 612, 792) == [0]
    assert H.text_paragraph(runs, 4, 612, 792) == [4]                        # far below: its own paragraph


# ------------------------------------------------------------------ the box that sits on the old words
def test_a_box_from_a_paragraph_holds_its_words_and_their_places(app, tmp_path):
    p = text_pdf(str(tmp_path / "t.pdf"))
    runs, ids, box = edited_box(p, "x")
    assert ids == [1, 2, 3]
    full = H.text_box_from_segments(runs, ids, 612, 792)
    assert full.text.split("\n") == [l[4] for l in LINES[1:4]] and full.orig == full.text
    assert len(full.erase) == 3 and full.family == "Times New Roman" and abs(full.size - 12) < 0.2
    assert abs(full.x - runs[1]["x0"]) < 0.002 and 1.0 >= full.x + full.w
    for r, e in zip(runs[1:4], full.erase):
        assert e[0] <= r["x0"] and e[2] >= r["x1"] and e[1] <= r["y0"] and e[3] >= r["y1"]


def test_the_box_puts_new_words_on_the_old_baseline(app, tmp_path):
    p = text_pdf(str(tmp_path / "t.pdf"))
    runs = runs_of(p)
    box = H.text_box_from_segments(runs, [1], 612, 792)
    base = copy(p, str(tmp_path / "o.pdf"))
    rep = {}
    H.apply_text_overlays(base, [(0, (box,))], report=rep)               # same words, drawn by the box over the removed ones
    a, b = render(p, scale=2), render(base, scale=2)
    crop = (int(0.09 * a.width), int(0.15 * a.height), int(0.95 * a.width), int(0.185 * a.height))
    ba, bb = dark_bbox(a.crop(crop)), dark_bbox(b.crop(crop))
    assert ba and bb
    assert abs(ba[1] - bb[1]) <= 3 and abs(ba[3] - bb[3]) <= 3 and abs(ba[0] - bb[0]) <= 3     # top, bottom and left within 1.5 pt
    assert abs(ba[2] - bb[2]) <= 0.04 * a.width                                                 # right edge: a substitute font may differ a little


def test_clean_text_box_keeps_good_erase_rectangles_and_drops_bad_ones():
    b = H.clean_text_box({"erase": [(0.1, 0.1, 0.5, 0.2), (0.5, 0.5, 0.4, 0.6), ("x", 0, 1, 1), (0.1, 0.1, 0.1, 0.1)],
                          "cover": "nonsense", "orig": "was"})
    assert b.erase == ((0.1, 0.1, 0.5, 0.2),) and b.cover == "#ffffff" and b.orig == "was"
    assert H.clean_text_box({"erase": 5}).erase == ()
    assert len(H.clean_text_box({"erase": [(0.1, 0.1, 0.2, 0.2)] * 500}).erase) == H.TEXT_ERASE_MAX
    assert H.clean_text_box({"cover": "#ABCDEF"}).cover == "#abcdef"


def test_turning_a_fraction_rectangle_with_its_page():
    r = (0.1, 0.2, 0.4, 0.3)
    assert H.rotate_frac_rect(r, 0) == r
    for turn in (90, 180, 270):
        out = r
        for _ in range(turn // 90):
            out = H.rotate_frac_rect(out, 90)
        assert out == pytest.approx(H.rotate_frac_rect(r, turn))
    assert H.rotate_frac_rect(H.rotate_frac_rect(r, 90), 270) == pytest.approx(r)


# ------------------------------------------------------------------ taking the old words out of the saved page
def test_the_old_words_are_gone_from_the_page_and_the_new_ones_are_there(app, tmp_path):
    src = text_pdf(str(tmp_path / "t.pdf"))
    _runs, _ids, box = edited_box(src, "The undersigned certifies that on May 5,\n2026, service was made on the defendant\nas described below.")
    out = copy(src, str(tmp_path / "o.pdf"))
    rep = {}
    H.apply_text_overlays(out, [(0, (box,))], report=rep)
    assert rep == {"text_removed": 3, "text_covered": 0}
    txt = page_text(out)
    assert "undersigned states" not in txt and "certifies" in txt and "Affidavit of Service" in txt and "CVCV123456" in txt
    raw = open(out, "rb").read()
    assert b"undersigned states" not in raw                                # not hidden in an uncompressed stream either
    assert "undersigned states" in page_text(src)                          # the original file is as it was


def test_the_rest_of_the_page_looks_exactly_as_it_did(app, tmp_path):
    src = text_pdf(str(tmp_path / "t.pdf"))
    _runs, _ids, box = edited_box(src, "CHANGED", para=False)
    out = copy(src, str(tmp_path / "o.pdf"))
    H.apply_text_overlays(out, [(0, (box,))])
    a, b = render(src), render(out)
    w, h = a.size
    for crop in ((0, 0, w, int(0.14 * h)), (0, int(0.20 * h), w, h)):     # above and below the edited line
        assert list(a.crop(crop).getdata()) == list(b.crop(crop).getdata())


def test_deleting_a_line_leaves_nothing_where_it_was(app, tmp_path):
    src = text_pdf(str(tmp_path / "t.pdf"))
    _runs, _ids, box = edited_box(src, "", para=False)
    out = copy(src, str(tmp_path / "o.pdf"))
    rep = {}
    H.apply_text_overlays(out, [(0, (box,))], report=rep)
    assert rep["text_removed"] == 1 and "undersigned states" not in page_text(out)
    a = render(src)
    b = render(out)
    w, h = a.size
    gone = (0, int(0.15 * h), w, int(0.175 * h))
    assert dark_bbox(a.crop(gone)) and dark_bbox(b.crop(gone)) is None


def test_a_style_only_edit_replaces_the_line_with_the_same_words_in_the_new_style(app, tmp_path):
    src = text_pdf(str(tmp_path / "t.pdf"))
    runs = runs_of(src)
    box = replace(H.text_box_from_segments(runs, [1], 612, 792), bold=True, color="#cc0000")
    out = copy(src, str(tmp_path / "o.pdf"))
    H.apply_text_overlays(out, [(0, (box,))])
    assert page_text(out).count("The undersigned states") == 1


def test_old_words_that_cannot_be_removed_are_covered_and_said_so(app, tmp_path):
    # the words are inside a form XObject: they are not the page's own text objects
    import pikepdf
    src = text_pdf(str(tmp_path / "t.pdf"))
    with pikepdf.open(src, allow_overwriting_input=True) as pdf:
        page = pdf.pages[0]
        form = pdf.make_stream(b"BT /F1 12 Tf 72 700 Td (INSIDE A FORM) Tj ET")
        form["/Type"], form["/Subtype"] = pikepdf.Name.XObject, pikepdf.Name.Form
        form["/BBox"] = pikepdf.Array([0, 0, 612, 792])
        form["/Resources"] = page.obj["/Resources"]
        page.obj["/Resources"]["/XObject"] = pikepdf.Dictionary(Fm0=form)
        page.obj["/Contents"] = pdf.make_stream(b"/Fm0 Do")
        pdf.save(src)
    runs = runs_of(src)
    assert runs[0]["text"] == "INSIDE A FORM"
    box = replace(H.text_box_from_segments(runs, [0], 612, 792), text="REPLACED")
    out = copy(src, str(tmp_path / "o.pdf"))
    rep = {}
    H.apply_text_overlays(out, [(0, (box,))], report=rep)
    assert rep == {"text_removed": 0, "text_covered": 1}
    assert "INSIDE A FORM" in page_text(out)                              # said so: still in the file
    ref = text_pdf(str(tmp_path / "ref.pdf"), lines=[])
    H.apply_text_overlays(ref, [(0, (replace(box, erase=()),))])
    a, b = render(out), render(ref)
    assert list(a.getdata()) == list(b.getdata())                         # but it cannot be seen: the page reads as REPLACED only


def test_a_text_object_longer_than_the_edited_run_is_not_cut_in_half(app, tmp_path):
    src = text_pdf(str(tmp_path / "t.pdf"), raw="BT /F1 12 Tf 72 700 Td [(Name) -9000 (John Smith)] TJ ET")
    runs = runs_of(src)
    assert [r["text"] for r in runs] == ["Name", "John Smith"]
    box = replace(H.text_box_from_segments(runs, [0], 612, 792), text="Title")
    out = copy(src, str(tmp_path / "o.pdf"))
    rep = {}
    H.apply_text_overlays(out, [(0, (box,))], report=rep)
    assert rep == {"text_removed": 0, "text_covered": 1}
    assert "John Smith" in page_text(out)                                  # the neighbour was never touched
    assert dark_bbox(render(out).crop((0, 0, 100, 120))) is not None


def test_a_rewrite_that_changes_the_page_elsewhere_is_not_used(app, tmp_path, monkeypatch):
    src = text_pdf(str(tmp_path / "t.pdf"))
    _runs, _ids, box = edited_box(src, "CHANGED", para=False)
    monkeypatch.setattr(H, "TEXT_REMOVE_PAGE_DIFF_PX", -1.0)               # any difference at all counts as too much
    out = copy(src, str(tmp_path / "o.pdf"))
    rep = {}
    H.apply_text_overlays(out, [(0, (box,))], report=rep)
    assert rep == {"text_removed": 0, "text_covered": 1}
    assert "undersigned states" in page_text(out) and "CHANGED" in page_text(out)


def test_the_check_can_fail(app, tmp_path, monkeypatch):
    # instrument proof: with the verification threshold left alone the same edit IS removed (the test above is not vacuous)
    src = text_pdf(str(tmp_path / "t.pdf"))
    _runs, _ids, box = edited_box(src, "CHANGED", para=False)
    out = copy(src, str(tmp_path / "o.pdf"))
    rep = {}
    H.apply_text_overlays(out, [(0, (box,))], report=rep)
    assert rep["text_removed"] == 1


def test_a_cover_patch_uses_its_colour(app, tmp_path, monkeypatch):
    src = text_pdf(str(tmp_path / "t.pdf"), raw="0.8 0.9 1 rg 0 0 612 792 re f 0 g BT /F1 12 Tf 72 700 Td (ON BLUE) Tj ET")
    runs = runs_of(src)
    box = replace(H.text_box_from_segments(runs, [0], 612, 792, cover="#ccddff"), text="")
    monkeypatch.setattr(H, "_remove_text_on_page", lambda *a, **k: (None, [False] * len(a[2])))   # removal not possible: cover
    out = copy(src, str(tmp_path / "o.pdf"))
    rep = {}
    H.apply_text_overlays(out, [(0, (box,))], report=rep)
    assert rep["text_covered"] == 1
    im = render_rgb(out)
    e = box.erase[0]
    inner = [im.getpixel((x, y)) for x in range(int(e[0] * im.width) + 2, int(e[2] * im.width) - 2, 3)
             for y in range(int(e[1] * im.height) + 2, int(e[3] * im.height) - 2, 2)]
    assert inner and all(max(abs(c - d) for c, d in zip(px, (0xcc, 0xdd, 0xff))) <= 2 for px in inner)    # the paper's colour, no black left
    assert "ON BLUE" in page_text(out)                                                                      # (still inside the file: said so)


def test_a_scan_is_covered_and_retyped(app, tmp_path):
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (612, 792), "#f4efe0")
    ImageDraw.Draw(img).text((72, 100), "OLD SCANNED LINE", fill="black")
    src = str(tmp_path / "scan.pdf")
    img.save(src, "PDF", resolution=72)
    assert runs_of(src) == []                                              # nothing to read: it is a picture
    rect = (0.1, 0.11, 0.35, 0.17)
    out = copy(src, str(tmp_path / "o.pdf"))
    rep = {}
    H.apply_text_overlays(out, [(0, (H.clean_text_box({"x": 0.1, "y": 0.1, "w": 0.4, "text": "", "cover": "#f4efe0", "erase": [rect]}),))],
                          report=rep)
    assert rep == {"text_removed": 0, "text_covered": 1}
    im = render_rgb(out)
    w, h = im.size
    assert dark_bbox(render(src).crop((int(rect[0] * w), int(rect[1] * h), int(rect[2] * w), int(rect[3] * h)))) is not None   # control: it was there
    inner = {im.getpixel((x, y)) for x in range(int(rect[0] * w) + 2, int(rect[2] * w) - 2) for y in range(int(rect[1] * h) + 2, int(rect[3] * h) - 2)}
    assert all(max(abs(a - b) for a, b in zip(px, (0xf4, 0xef, 0xe0))) <= 2 for px in inner)
    out2 = copy(src, str(tmp_path / "o2.pdf"))
    H.apply_text_overlays(out2, [(0, (H.clean_text_box({"x": 0.1, "y": 0.1, "w": 0.4, "text": "NEW LINE", "size": 12,
                                                          "cover": "#f4efe0", "erase": [rect]}),))])
    assert "NEW LINE" in page_text(out2)


def test_words_left_under_a_rectangle_mean_it_is_reported_covered(app, tmp_path):
    # two words drawn on the same spot: one can be taken out, the other (part of a longer object) cannot
    src = text_pdf(str(tmp_path / "t.pdf"), raw="BT /F1 12 Tf 72 700 Td (AAAA) Tj ET\nBT /F1 12 Tf 72 700 Td [(AAAA) -9000 (tail)] TJ ET")
    runs = runs_of(src)
    box = replace(H.text_box_from_segments(runs, [0], 612, 792), text="")
    out = copy(src, str(tmp_path / "o.pdf"))
    rep = {}
    H.apply_text_overlays(out, [(0, (box,))], report=rep)
    assert rep["text_removed"] == 0 and rep["text_covered"] == len(box.erase)
    assert "tail" in page_text(out)


@pytest.mark.parametrize("turn", [90, 180, 270])
def test_edits_survive_an_organizer_rotation(app, tmp_path, turn):
    from PIL import ImageChops
    src = text_pdf(str(tmp_path / "t.pdf"))
    _runs, _ids, box = edited_box(src, "REPLACEMENT", para=False)
    flat = copy(src, str(tmp_path / "flat.pdf"))
    H.apply_text_overlays(flat, [(0, (box,))])                              # the same edit on the upright page
    out = text_pdf(str(tmp_path / "o.pdf"), rotate=turn)                   # the saved file: that page, turned by the Organizer
    rep = {}
    H.apply_text_overlays(out, [(turn, (box,))], report=rep)
    assert rep == {"text_removed": 1, "text_covered": 0}
    want = render(flat).rotate(-turn, expand=True)
    got = render(out)
    assert got.size == want.size
    off = ImageChops.difference(want, got).point(lambda v: 255 if v > 80 else 0)
    assert sum(1 for v in off.getdata() if v) < 0.004 * got.width * got.height      # it is the same page, turned


def test_edits_on_one_page_do_not_touch_another(app, tmp_path):
    src = text_pdf(str(tmp_path / "t.pdf"), pages=2)
    _runs, _ids, box = edited_box(src, "CHANGED", para=False)
    out = copy(src, str(tmp_path / "o.pdf"))
    H.apply_text_overlays(out, [(0, ()), (0, (box,))])
    assert "undersigned states" in page_text(out, 0) and "undersigned states" not in page_text(out, 1)
    assert list(render(src, 0).getdata()) == list(render(out, 0).getdata())


def test_an_edit_that_cannot_open_the_page_covers_instead_of_failing(app, tmp_path, monkeypatch):
    src = text_pdf(str(tmp_path / "t.pdf"))
    _runs, _ids, box = edited_box(src, "CHANGED", para=False)
    monkeypatch.setattr(H, "_remove_text_on_page", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("pdfium said no")))
    out = copy(src, str(tmp_path / "o.pdf"))
    rep = {}
    H.apply_text_overlays(out, [(0, (box,))], report=rep)
    assert rep["text_covered"] == 1 and "CHANGED" in page_text(out)


# ------------------------------------------------------------------ saving through organize_pdf
def test_organize_reports_how_the_old_words_were_dealt_with(app, tmp_path):
    src = text_pdf(str(tmp_path / "t.pdf"))
    _runs, _ids, box = edited_box(src, "CHANGED", para=False)
    before = sha(src)
    res = H.organize_pdf(src, [(0, 0)], texts=[(box,)])
    assert res["text_removed"] == 1 and res["text_covered"] == 0 and sha(src) == before
    assert os.path.basename(res["path"]) == "t_edited.pdf"
    assert "undersigned states" not in page_text(res["path"]) and "CHANGED" in page_text(res["path"])
    plain = H.organize_pdf(blank(str(tmp_path / "b.pdf")), [(0, 0)], texts=[(H.TextBox(text="HI"),)])
    assert plain["text_removed"] == 0 and plain["text_covered"] == 0


def test_signed_files_cannot_keep_their_signature_through_a_change_to_existing_text(app, tmp_path):
    from test_text_tool import fake_sign
    src = fake_sign(str(tmp_path / "s.pdf"), pages=1)
    _runs, _ids, box = edited_box(src, "x", para=False) if runs_of(src) else (None, None, H.TextBox(text="x", erase=((0.1, 0.1, 0.3, 0.2),)))
    opts = H.signed_text_options(src, [(0, 0)], 1, False, [(replace(H.TextBox(text="x"), erase=((0.1, 0.1, 0.3, 0.2),)),)])
    assert opts["signed"] and not opts["can_keep"] and "already on a page" in opts["why_not"]
    assert H.signed_text_options(src, [(0, 0)], 1, False, [(H.TextBox(text="x"),)])["can_keep"]      # control: added text may keep it
    with pytest.raises(H.PdfOpError):
        H.organize_pdf(src, [(0, 0)], texts=[(replace(H.TextBox(text="x"), erase=((0.1, 0.1, 0.3, 0.2),)),)], keep_signatures=True)


# ------------------------------------------------------------------ the tool, through real events
@pytest.fixture
def tpanel(app, tmp_path):
    d = tmp_path / "pdfs"
    d.mkdir()
    text_pdf(str(d / "a.pdf"), pages=2)
    blank(str(d / "b.pdf"), pages=2)
    p = H.OrganizerPanel()
    p.resize(1200, 800)
    p.show()
    p.folder_row.set_path(str(d))
    assert pump(app, lambda: p.file_list.count() == 2 and len(p._counts) == 2)
    yield p, d
    p.stop_for_shutdown()
    p.stop_background()
    p.close()


def text_reader(app, tpanel, row=0):
    from test_reader_panel import read_row, drawn
    panel, _d = tpanel
    r = read_row(app, panel, row, 2)
    drawn(app, r)
    r.text_btn.setChecked(True)
    idx = r.canvas.pages()[0][0]
    assert pump(app, lambda: idx in r.canvas._layouts)
    return panel, r, idx


def run_point(r, idx, n):
    run = r.canvas._layouts[idx][n]
    return page_point(r.canvas, 0, (run["x0"] + run["x1"]) / 2, (run["y0"] + run["y1"]) / 2)


def test_turning_the_tool_on_reads_the_page_text_once_and_not_before(app, tpanel):
    from test_reader_panel import read_row, drawn
    panel, _d = tpanel
    r = read_row(app, panel, 0, 2)
    drawn(app, r)
    assert r.canvas._layouts == {} and not r.canvas._layout_asked          # tool off: nothing read
    r.text_btn.setChecked(True)
    idx = r.canvas.pages()[0][0]
    assert pump(app, lambda: idx in r.canvas._layouts)
    assert [x["text"] for x in r.canvas._layouts[idx]] == [l[4] for l in LINES]


def test_hovering_page_text_marks_it_and_says_what_a_click_does(app, tpanel):
    panel, r, idx = text_reader(app, tpanel)
    hints = []
    r.canvas.textHint.connect(hints.append)
    mouse(r.canvas.viewport(), QEvent.MouseMove, run_point(r, idx, 1), buttons=Qt.NoButton)
    assert r.canvas._hover_seg == (idx, 1) and any("edit this line" in h.lower() for h in hints)
    mouse(r.canvas.viewport(), QEvent.MouseMove, page_point(r.canvas, 0, 0.9, 0.9), buttons=Qt.NoButton)
    assert r.canvas._hover_seg is None


def test_a_click_on_page_text_makes_it_editable_in_place(app, tpanel):
    panel, r, idx = text_reader(app, tpanel)
    click(r.canvas, run_point(r, idx, 1))
    ed = r.canvas._editor
    assert ed is not None and ed.toPlainText() == LINES[1][4]
    box = panel._texts.get(idx) or r.canvas._texts[idx]
    assert len(box) == 1 and box[0].erase and box[0].orig == LINES[1][4]
    ed.setPlainText("Changed words")
    assert r.handle_escape()
    assert panel._texts[idx][0].text == "Changed words" and panel._is_dirty() and len(panel._undo) == 1


def test_opening_a_line_and_changing_nothing_is_not_an_edit(app, tpanel):
    panel, r, idx = text_reader(app, tpanel)
    click(r.canvas, run_point(r, idx, 1))
    assert r.canvas._editor is not None
    assert r.handle_escape()
    assert not panel._texts and not panel._undo and not panel._is_dirty() and not r.canvas._texts


def test_changing_only_the_style_of_a_line_is_an_edit(app, tpanel):
    panel, r, idx = text_reader(app, tpanel)
    click(r.canvas, run_point(r, idx, 1))
    r.canvas.toggle_text_style("bold")
    assert r.handle_escape()
    assert panel._texts[idx][0].bold and panel._texts[idx][0].text == LINES[1][4]


def test_alt_click_edits_the_whole_paragraph(app, tpanel):
    panel, r, idx = text_reader(app, tpanel)
    pt = run_point(r, idx, 2)
    vp = r.canvas.viewport()
    from PySide6.QtCore import QPointF
    from PySide6.QtGui import QMouseEvent
    for kind, btns in ((QEvent.MouseButtonPress, Qt.LeftButton), (QEvent.MouseButtonRelease, Qt.NoButton)):
        p = QPointF(pt)
        QApplication.sendEvent(vp, QMouseEvent(kind, p, vp.mapToGlobal(p), Qt.LeftButton, btns, Qt.AltModifier))
    ed = r.canvas._editor
    assert ed is not None and ed.toPlainText().split("\n") == [l[4] for l in LINES[1:4]]
    r.handle_escape()


def test_ctrl_click_on_page_text_still_adds_new_text_there(app, tpanel):
    panel, r, idx = text_reader(app, tpanel)
    pt = run_point(r, idx, 1)
    vp = r.canvas.viewport()
    r.canvas._text_click(pt, Qt.ControlModifier)
    ed = r.canvas._editor
    assert ed is not None and ed.toPlainText() == "" and not r.canvas._texts[idx][0].erase
    r.handle_escape()


def test_clearing_a_line_deletes_the_old_words_and_keeps_the_box(app, tpanel):
    panel, r, idx = text_reader(app, tpanel)
    click(r.canvas, run_point(r, idx, 1))
    r.canvas._editor.setPlainText("")
    assert r.handle_escape()
    boxes = panel._texts[idx]
    assert len(boxes) == 1 and boxes[0].text == "" and boxes[0].erase


def test_an_edited_line_is_not_offered_for_editing_again(app, tpanel):
    panel, r, idx = text_reader(app, tpanel)
    click(r.canvas, run_point(r, idx, 1))
    r.canvas._editor.setPlainText("New")
    r.handle_escape()
    assert r.canvas._hit_seg(run_point(r, idx, 1)) is None                  # that spot is now the edit box
    r.canvas.viewport().update()


def test_right_clicking_page_text_offers_edit_and_delete(app, tpanel):
    panel, r, idx = text_reader(app, tpanel)
    m = menu_for(app, panel, r, run_point(r, idx, 1))
    ls = labels(m)
    assert ls[:4] == ["Edit this line", "Edit this paragraph", "Delete this line", "Add new text here"]
    find(m, "Delete this line").trigger()
    assert pump(app, lambda: panel._texts.get(idx))
    assert panel._texts[idx][0].text == "" and panel._texts[idx][0].erase


def test_the_menu_on_an_edited_box_can_put_the_original_back(app, tpanel):
    panel, r, idx = text_reader(app, tpanel)
    click(r.canvas, run_point(r, idx, 1))
    r.canvas._editor.setPlainText("New")
    r.handle_escape()
    box = panel._texts[idx][0]
    m = menu_for(app, panel, r, r.canvas._box_rect(0, box).center())
    assert find(m, "Put the original text back") is not None and find(m, "Delete text") is None
    find(m, "Put the original text back").trigger()
    assert pump(app, lambda: not panel._texts.get(idx))


def test_a_click_that_arrives_before_the_page_is_read_is_answered_when_it_is(app, tpanel):
    panel, r, idx = text_reader(app, tpanel)
    pt = run_point(r, idx, 1)
    r.canvas._layouts.clear()
    r.canvas._layout_asked.clear()
    click(r.canvas, pt)
    assert r.canvas._pending_click is not None or r.canvas._editor is not None
    assert pump(app, lambda: r.canvas._editor is not None)
    assert r.canvas._editor.toPlainText() == LINES[1][4]


def test_page_text_that_cannot_be_read_still_lets_a_click_add_new_text(app, tpanel, monkeypatch):
    monkeypatch.setattr(H, "reader_text_layout", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no")))
    from test_reader_panel import read_row, drawn
    panel, _d = tpanel
    r = read_row(app, panel, 0, 2)
    drawn(app, r)
    r.text_btn.setChecked(True)
    idx = r.canvas.pages()[0][0]
    assert pump(app, lambda: idx in r.canvas._layouts)
    assert r.canvas._layouts[idx] == []
    click(r.canvas, page_point(r.canvas, 0, 0.3, 0.3))
    assert r.canvas._editor is not None and r.canvas._editor.toPlainText() == ""
    r.handle_escape()


def test_a_blank_page_has_no_runs_and_clicking_adds_new_text(app, tpanel):
    from test_reader_panel import read_row, drawn
    panel, _d = tpanel
    r = read_row(app, panel, 1, 2)
    drawn(app, r)
    r.text_btn.setChecked(True)
    idx = r.canvas.pages()[0][0]
    assert pump(app, lambda: idx in r.canvas._layouts) and r.canvas._layouts[idx] == []
    click(r.canvas, page_point(r.canvas))
    assert r.canvas._editor is not None
    r.handle_escape()


def test_saving_an_edited_line_removes_the_old_words_and_says_so(app, tpanel):
    panel, r, idx = text_reader(app, tpanel)
    click(r.canvas, run_point(r, idx, 1))
    r.canvas._editor.setPlainText("The undersigned certifies service")
    r.handle_escape()
    notes = []
    orig = panel._set_status
    panel._set_status = lambda msg, kind="": (notes.append(msg), orig(msg, kind))[1]
    src = str(tpanel[1] / "a.pdf")
    before = sha(src)
    panel.save_and_next()
    out = str(tpanel[1] / "Reorganized" / "a_edited.pdf")
    assert pump(app, lambda: os.path.exists(out) and not panel._busy, 30)
    txt = page_text(out)
    assert "certifies service" in txt and "undersigned states" not in txt
    assert sha(src) == before
    assert any("gone from the page" in n for n in notes)


def test_nothing_in_the_new_code_runs_when_the_tool_is_never_used(app, tpanel, monkeypatch):
    calls = []
    monkeypatch.setattr(H, "reader_text_layout", lambda *a, **k: calls.append(1) or [])
    from test_reader_panel import read_row, drawn
    panel, _d = tpanel
    r = read_row(app, panel, 0, 2)
    drawn(app, r)
    pump(app, None, 0.5)
    assert calls == [] and r.canvas._layouts == {}


# ------------------------------------------------------------------ covering a picture of text (scans)
def drag_region(r, a, b):
    vp = r.canvas.viewport()
    mouse(vp, QEvent.MouseButtonPress, a)
    mouse(vp, QEvent.MouseMove, b)
    mouse(vp, QEvent.MouseButtonRelease, b, buttons=Qt.NoButton)


def test_the_menu_offers_to_cover_and_retype_a_region_on_page_text_and_on_blank_page(app, tpanel):
    panel, r, idx = text_reader(app, tpanel)
    assert "Cover and retype a region…" in labels(menu_for(app, panel, r, run_point(r, idx, 1)))
    assert "Cover and retype a region…" in labels(menu_for(app, panel, r, page_point(r.canvas, 0, 0.9, 0.9)))


def test_dragging_a_region_covers_it_and_opens_a_box_to_type_in(app, tpanel):
    panel, r, idx = text_reader(app, tpanel)
    hints = []
    r.canvas.textHint.connect(hints.append)
    find(menu_for(app, panel, r, page_point(r.canvas, 0, 0.9, 0.9)), "Cover and retype a region…").trigger()
    assert pump(app, lambda: r.canvas._region_armed) and any("Drag around" in h for h in hints)
    a, b = page_point(r.canvas, 0, 0.2, 0.5), page_point(r.canvas, 0, 0.6, 0.55)
    drag_region(r, a, b)
    assert not r.canvas._region_armed and r.canvas._editor is not None
    box = r.canvas._texts[idx][0]
    assert box.erase and abs(box.erase[0][0] - 0.2) < 0.01 and abs(box.erase[0][2] - 0.6) < 0.01 and box.text == ""
    r.canvas._editor.setPlainText("Replacement")
    r.handle_escape()
    assert panel._texts[idx][0].text == "Replacement" and panel._texts[idx][0].erase


def test_an_area_marked_on_purpose_stays_even_if_nothing_is_typed(app, tpanel):
    panel, r, idx = text_reader(app, tpanel)
    r.canvas.arm_region()
    drag_region(r, page_point(r.canvas, 0, 0.2, 0.5), page_point(r.canvas, 0, 0.6, 0.55))
    r.handle_escape()
    assert panel._texts[idx][0].erase and panel._texts[idx][0].text == ""


def test_escape_cancels_a_region_that_has_not_been_dragged(app, tpanel):
    panel, r, idx = text_reader(app, tpanel)
    r.canvas.arm_region()
    assert r.handle_escape() and not r.canvas._region_armed and not r.canvas._texts


def test_a_tiny_drag_makes_nothing(app, tpanel):
    panel, r, idx = text_reader(app, tpanel)
    r.canvas.arm_region()
    a = page_point(r.canvas, 0, 0.5, 0.5)
    drag_region(r, a, a + QPoint(2, 2))
    assert not r.canvas._texts and r.canvas._editor is None and not r.canvas._region_armed


def test_turning_the_tool_off_drops_a_pending_region(app, tpanel):
    panel, r, idx = text_reader(app, tpanel)
    r.canvas.arm_region()
    r.text_btn.setChecked(False)
    assert not r.canvas._region_armed


def test_a_region_on_a_scan_saves_as_cover_and_new_text(app, tmp_path):
    from PIL import Image, ImageDraw
    d = tmp_path / "pdfs"
    d.mkdir()
    img = Image.new("RGB", (612, 792), "#f4efe0")
    ImageDraw.Draw(img).text((72, 100), "OLD SCANNED LINE", fill="black")
    img.save(str(d / "scan.pdf"), "PDF", resolution=72)
    p = H.OrganizerPanel()
    p.resize(1200, 800)
    p.show()
    p.folder_row.set_path(str(d))
    try:
        assert pump(app, lambda: p.file_list.count() == 1 and len(p._counts) == 1)
        from test_reader_panel import read_row, drawn
        r = read_row(app, p, 0, 1)
        drawn(app, r)
        r.text_btn.setChecked(True)
        idx = r.canvas.pages()[0][0]
        assert pump(app, lambda: idx in r.canvas._layouts) and r.canvas._layouts[idx] == []
        r.canvas.arm_region()
        drag_region(r, page_point(r.canvas, 0, 0.1, 0.11), page_point(r.canvas, 0, 0.35, 0.17))
        cover = r.canvas._texts[idx][0].cover
        assert max(abs(int(cover[i:i + 2], 16) - v) for i, v in zip((1, 3, 5), (0xf4, 0xef, 0xe0))) <= 6     # sampled off the paper
        r.canvas._editor.setPlainText("NEW LINE")
        r.handle_escape()
        p.save_and_next()
        out = str(d / "Reorganized" / "scan_edited.pdf")
        assert pump(app, lambda: os.path.exists(out) and not p._busy, 30)
        words = page_text(out).split()
        assert "NEW" in words and "LINE" in words                                  # (the box is as wide as the area, so it may wrap)
    finally:
        p.stop_for_shutdown()
        p.stop_background()
        p.close()


# ------------------------------------------------------------------ hostile and oversized pages
def test_a_page_at_the_size_limit_is_checked_at_a_bounded_size(app, tmp_path):
    import time
    src = text_pdf(str(tmp_path / "huge.pdf"), size=(14400, 14400),
                   raw="BT /F1 200 Tf 1000 12000 Td (BIG WORDS) Tj ET")
    runs = runs_of(src)
    assert runs and runs[0]["text"] == "BIG WORDS"
    box = replace(H.text_box_from_segments(runs, [0], 14400, 14400), text="X")
    out = copy(src, str(tmp_path / "o.pdf"))
    rep = {}
    t0 = time.time()
    H.apply_text_overlays(out, [(0, (box,))], report=rep)
    assert time.time() - t0 < 30 and rep["text_removed"] + rep["text_covered"] == 1
    assert "BIG WORDS" not in page_text(out) or rep["text_covered"] == 1


def test_a_page_with_a_very_large_content_stream_is_covered_not_rewritten(app, tmp_path, monkeypatch):
    src = text_pdf(str(tmp_path / "t.pdf"))
    _runs, _ids, box = edited_box(src, "CHANGED", para=False)
    monkeypatch.setattr(H, "TEXT_REMOVE_CONTENT_MAX", 10)
    out = copy(src, str(tmp_path / "o.pdf"))
    rep = {}
    H.apply_text_overlays(out, [(0, (box,))], report=rep)
    assert rep == {"text_removed": 0, "text_covered": 1}


def test_the_signed_appender_refuses_edits_to_existing_text_even_if_asked_directly(app, tmp_path):
    from test_text_tool import fake_sign
    src = fake_sign(str(tmp_path / "s.pdf"), pages=1)
    with pytest.raises(H.PdfOpError):
        H.append_text_update(src, str(tmp_path / "o.pdf"), [(replace(H.TextBox(text="x"), erase=((0.1, 0.1, 0.3, 0.2),)),)])
    assert not os.path.exists(str(tmp_path / "o.pdf"))


def test_a_pile_of_overlapping_text_still_reads_in_bounded_runs(app, tmp_path):
    body = "".join(f"BT /F1 8 Tf {72 + (i % 7)} {700 - (i % 40) * 2} Td (overlapping words {i}) Tj ET\n" for i in range(600))
    runs = runs_of(text_pdf(str(tmp_path / "p.pdf"), raw=body))
    assert 0 < len(runs) <= H.TEXT_LAYOUT_MAX_SEGS


# ------------------------------------------------------------------ copy, cut, paste
def key_press(r, key, mods=Qt.NoModifier):
    from PySide6.QtGui import QKeyEvent
    for kind in (QEvent.KeyPress, QEvent.KeyRelease):
        QApplication.sendEvent(r.canvas, QKeyEvent(kind, key, mods))


def paper_point(r):
    """A point low on page 1 where there is no text, kept inside the visible area (the reader's height depends on the
    theme and text size another test file may have left on the shared QApplication)."""
    pt = page_point(r.canvas, 0, 0.3, 0.7)
    return QPoint(pt.x(), min(pt.y(), r.canvas.viewport().rect().bottom() - 20))


def one_box(app, tpanel):
    panel, r, idx = text_reader(app, tpanel)
    click(r.canvas, paper_point(r))                                         # empty paper: a new box
    type_in(r, "Copy me")
    r.canvas.setFocus()
    return panel, r, idx


def test_ctrl_c_then_ctrl_v_pastes_a_copy_with_its_look(app, tpanel):
    panel, r, idx = one_box(app, tpanel)
    r.canvas.toggle_text_style("bold")
    key_press(r, Qt.Key_C, Qt.ControlModifier)
    assert QApplication.clipboard().text() == "Copy me"                      # other programs get the words
    key_press(r, Qt.Key_V, Qt.ControlModifier)
    boxes = panel._texts[idx]
    assert len(boxes) == 2 and boxes[1].text == "Copy me" and boxes[1].bold and boxes[1] != boxes[0]
    assert r.canvas.selected_text() == (idx, 1)


def test_cut_removes_the_box_and_paste_brings_it_back(app, tpanel):
    panel, r, idx = one_box(app, tpanel)
    key_press(r, Qt.Key_X, Qt.ControlModifier)
    assert not panel._texts.get(idx)
    key_press(r, Qt.Key_V, Qt.ControlModifier)
    assert panel._texts[idx][0].text == "Copy me"


def test_pasting_plain_text_from_another_program_makes_a_box(app, tpanel):
    panel, r, idx = one_box(app, tpanel)
    QApplication.clipboard().setText("From elsewhere")
    r.canvas._tsel = None
    key_press(r, Qt.Key_V, Qt.ControlModifier)
    assert [b.text for b in panel._texts[idx]] == ["Copy me", "From elsewhere"]


def test_paste_with_nothing_to_paste_does_nothing(app, tpanel):
    panel, r, idx = one_box(app, tpanel)
    QApplication.clipboard().clear()
    n = len(panel._texts[idx])
    key_press(r, Qt.Key_V, Qt.ControlModifier)
    assert len(panel._texts[idx]) == n


def test_the_menu_pastes_where_you_right_clicked(app, tpanel):
    panel, r, idx = one_box(app, tpanel)
    key_press(r, Qt.Key_C, Qt.ControlModifier)
    where = page_point(r.canvas, 0, 0.5, 0.2)
    m = menu_for(app, panel, r, where)
    act = find(m, "Paste text here")
    assert act is not None and act.isEnabled()
    act.trigger()
    assert pump(app, lambda: len(panel._texts[idx]) == 2)
    assert abs(panel._texts[idx][1].x - 0.5) < 0.03 and abs(panel._texts[idx][1].y - 0.2) < 0.03


def test_an_edited_line_can_be_copied_but_not_cut_and_its_copy_does_not_erase_twice(app, tpanel):
    panel, r, idx = text_reader(app, tpanel)
    click(r.canvas, run_point(r, idx, 1))
    r.canvas._editor.setPlainText("Edited")
    r.handle_escape()
    r.canvas.setFocus()
    assert r.canvas.cut_selected_text() is False and len(panel._texts[idx]) == 1
    key_press(r, Qt.Key_C, Qt.ControlModifier)
    key_press(r, Qt.Key_V, Qt.ControlModifier)
    key_press(r, Qt.Key_D, Qt.ControlModifier)
    boxes = panel._texts[idx]
    assert len(boxes) == 3 and boxes[0].erase and not boxes[1].erase and not boxes[2].erase and boxes[1].orig == ""
    m = menu_for(app, panel, r, r.canvas._box_rect(0, boxes[0]).center())
    assert find(m, "Cut") is None and find(m, "Copy") is not None


def test_a_hostile_clipboard_payload_is_cleaned(app, tpanel):
    from PySide6.QtCore import QMimeData, QByteArray
    panel, r, idx = one_box(app, tpanel)
    md = QMimeData()
    md.setText("x")
    md.setData(H._TEXT_MIME, QByteArray(b'{"w": 99, "size": 1e9, "color": "red", "text": 5, "erase": [[0,0,1,1]], "x": -4}'))
    QApplication.clipboard().setMimeData(md)
    key_press(r, Qt.Key_V, Qt.ControlModifier)
    b = panel._texts[idx][-1]
    assert b.size == 200.0 and b.color == "#000000" and not b.erase and 0 <= b.x <= 1 and b.w <= 1.0
    md2 = QMimeData()
    md2.setText("fine")
    md2.setData(H._TEXT_MIME, QByteArray(b"not json at all"))
    QApplication.clipboard().setMimeData(md2)
    key_press(r, Qt.Key_V, Qt.ControlModifier)
    assert panel._texts[idx][-1].text == "fine"


# ------------------------------------------------------------------ what is actually on screen
def screen(r):
    """What the reader's page area shows right now, as an image in viewport coordinates."""
    for _ in range(5):
        QApplication.processEvents()
    return r.canvas.viewport().grab().toImage()


def shown_pixels(r, fx, fy):
    img = screen(r)
    pt = page_point(r.canvas, 0, fx, fy)
    c = img.pixelColor(pt.x(), pt.y())
    return c.red(), c.green(), c.blue()


def test_the_page_stays_white_with_a_box_selected_edited_or_hovered(app, tpanel):
    panel, r, idx = text_reader(app, tpanel)
    assert min(shown_pixels(r, 0.85, 0.5)) > 240                              # control: a white page
    click(r.canvas, paper_point(r))
    type_in(r, "A box")
    assert r.canvas.selected_text() is not None
    for _ in range(5):
        app.processEvents()
    assert min(shown_pixels(r, 0.85, 0.5)) > 240                              # box selected (handles showing): the page is not painted over
    click(r.canvas, run_point(r, idx, 1))
    r.canvas._editor.setPlainText("Edited")
    r.handle_escape()
    for _ in range(5):
        app.processEvents()
    assert min(shown_pixels(r, 0.85, 0.5)) > 240
    mouse(r.canvas.viewport(), QEvent.MouseMove, run_point(r, idx, 4), buttons=Qt.NoButton)
    assert min(shown_pixels(r, 0.85, 0.5)) > 240


def test_an_edited_line_looks_edited_on_screen_before_saving(app, tpanel):
    panel, r, idx = text_reader(app, tpanel)
    run = r.canvas._layouts[idx][1]
    spot = (run["x0"] + 0.01, (run["y0"] + run["y1"]) / 2)
    click(r.canvas, run_point(r, idx, 1))
    r.canvas._editor.setPlainText("")
    r.handle_escape()                                                          # deleted: the preview hides the old words
    for _ in range(5):
        app.processEvents()
    img = r.canvas.viewport().grab().toImage()
    rect = r.canvas._page_rect(0)
    x0, x1 = rect.x() + int(run["x0"] * rect.width()), rect.x() + int(run["x1"] * rect.width())
    y0, y1 = rect.y() + int(run["y0"] * rect.height()) + 1, rect.y() + int(run["y1"] * rect.height()) - 1
    dark = [1 for x in range(x0 + 2, x1 - 2, 2) for y in range(y0, y1, 2) if max(img.pixelColor(x, y).red(), img.pixelColor(x, y).green(), img.pixelColor(x, y).blue()) < 110]
    assert not dark                                                            # nothing dark left where the old words were


# ------------------------------------------------------------------ the text stays inside its frame (typing and after)
def _ink(img, rect, margin=40):
    from PySide6.QtGui import QColor  # noqa: F401
    xs, ys = [], []
    for y in range(max(0, rect.top() - margin), min(img.height(), rect.bottom() + margin)):
        for x in range(max(0, rect.left() - margin), min(img.width(), rect.right() + margin)):
            c = img.pixelColor(x, y)
            if c.red() < 90 and c.green() < 90 and c.blue() < 90:
                xs.append(x); ys.append(y)
    return (min(xs), min(ys), max(xs), max(ys)) if xs else None


_LOOKS = [("Arial", 12, False, False, 1.0), ("Arial", 12, True, True, 1.0), ("Times New Roman", 14, False, True, 1.0),
          ("Courier New", 10, True, False, 1.0), ("Arial", 24, False, False, 1.0), ("Arial", 12, False, False, 0.8),
          ("Arial", 12, False, False, 1.5), ("Times New Roman", 36, True, True, 1.0), ("Arial", 8, False, False, 1.0)]


def _typing_box(r, idx, look, text="Hjgpqy fl Wijk\nQuick brown fox\nSecond paragraph line"):
    fam, size, b, i, sp = look
    cv = r.canvas
    click(cv, page_point(cv, 0, 0.2, 0.55))
    cv._editor.setPlainText(text)
    cv._pull_editor_text()
    k = cv._edit[1]
    cv._texts[idx] = tuple(replace(bx, family=fam, size=size, bold=b, italic=i, spacing=sp) if n == k else bx
                           for n, bx in enumerate(cv._texts[idx]))
    cv._restyle_editor()
    for _ in range(3):
        QApplication.processEvents()
    return cv, k


@pytest.mark.parametrize("look", _LOOKS, ids=lambda l: f"{l[0]}-{l[1]}-b{int(l[2])}i{int(l[3])}-sp{l[4]}")
def test_the_typing_box_grows_to_hold_every_line_and_the_frame_follows(app, tpanel, look):
    panel, r, idx = text_reader(app, tpanel)
    cv, k = _typing_box(r, idx, look)
    box = cv._texts[idx][k]
    bw, bh = cv._sizes[idx]
    page = cv._page_rect(cv._pos_for(idx))
    painted_h = H.text_box_height_frac(box, bw, bh) * page.height()
    ed = cv._editor
    assert ed.height() >= painted_h                                   # nothing hidden below the typing box
    assert ed.verticalScrollBar().maximum() == 0                      # and it is not scrolled
    assert abs(box.h * page.height() - painted_h) < 1.5               # the dashed frame already wraps all of it
    fr = cv._box_rect(cv._pos_for(idx), box)
    assert ed.geometry().top() <= fr.top() and ed.geometry().bottom() >= fr.bottom()


@pytest.mark.parametrize("look", _LOOKS, ids=lambda l: f"{l[0]}-{l[1]}-b{int(l[2])}i{int(l[3])}-sp{l[4]}")
def test_finished_text_is_drawn_inside_its_frame(app, tpanel, look):
    panel, r, idx = text_reader(app, tpanel)
    cv, k = _typing_box(r, idx, look)
    assert r.handle_escape()
    for _ in range(3):
        QApplication.processEvents()
    fr = cv._box_rect(cv._pos_for(idx), cv._texts[idx][k])
    ink = _ink(screen(r), fr)
    assert ink is not None
    left, top, right, bottom = fr.left() - ink[0], fr.top() - ink[1], ink[2] - fr.right(), ink[3] - fr.bottom()
    assert max(left, top, right, bottom) <= 1, (left, top, right, bottom)    # one pixel of antialiasing is allowed


def test_typing_more_lines_keeps_growing_the_box(app, tpanel):
    panel, r, idx = text_reader(app, tpanel)
    cv, k = _typing_box(r, idx, _LOOKS[0], text="one")
    h1 = cv._texts[idx][k].h
    cv._editor.setPlainText("one\ntwo\nthree\nfour")
    for _ in range(3):
        QApplication.processEvents()
    h2 = cv._texts[idx][k].h
    assert h2 > 3 * h1 * 0.9 and cv._editor.verticalScrollBar().maximum() == 0
    cv._editor.setPlainText("one")
    for _ in range(3):
        QApplication.processEvents()
    assert abs(cv._texts[idx][k].h - h1) < 1e-6                         # and shrinks back


def test_typed_text_starts_where_the_finished_text_will(app, tpanel):
    panel, r, idx = text_reader(app, tpanel)
    cv, k = _typing_box(r, idx, ("Arial", 14, False, False, 1.0), text="Hjgpqy fl Wijk")
    ed = cv._editor
    e = ed.grab().toImage()
    eink = _ink(e, QRect(2, 2, e.width() - 4, e.height() - 4), margin=0)      # inside its 1 px border
    ex, ey = eink[0] + ed.x(), eink[1] + ed.y()
    assert r.handle_escape()
    for _ in range(3):
        QApplication.processEvents()
    fr = cv._box_rect(cv._pos_for(idx), cv._texts[idx][k])
    pink = _ink(screen(r), fr)
    assert abs(pink[0] - ex) <= 2 and abs(pink[1] - ey) <= 2, (pink[:2], (ex, ey))


def test_the_frame_line_does_not_cover_the_words_inside_it(app, tpanel):
    panel, r, idx = text_reader(app, tpanel)
    cv, k = _typing_box(r, idx, ("Arial", 14, False, False, 1.0), text="Hjgpqy fl Wijk")
    assert r.handle_escape()                                              # selected, not being typed in
    for _ in range(3):
        QApplication.processEvents()
    fr = cv._box_rect(cv._pos_for(idx), cv._texts[idx][k])
    img = screen(r)
    blue = [(x, y) for y in range(fr.top(), fr.bottom() + 1) for x in range(fr.left() + 1, fr.right() - 1)
            if (lambda c: c.blue() > 150 and c.red() < 70 and c.green() < 130)(img.pixelColor(x, y))]
    assert blue == []                                                     # no frame pixel inside the box (handles and line sit outside)
