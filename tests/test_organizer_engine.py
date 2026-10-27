import hashlib, os, sys
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import pytest
from pypdf import PdfWriter, PdfReader
from pypdf.generic import DictionaryObject, NameObject, ArrayObject, FloatObject, TextStringObject
import acrod as H


@pytest.fixture(autouse=True)
def _iso(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "appdata"))
    H._reset_ui_state_cache()


def make_pdf(path, pages, rotate=None, outline=False, link_on=None, password=None):
    """Page i has width 100+i, so identity survives any reordering."""
    w = PdfWriter()
    for i in range(pages):
        pg = w.add_blank_page(width=100 + i, height=100)
        if rotate and i in rotate:
            pg.rotate(rotate[i])
    if link_on is not None:
        pg = w.pages[link_on]
        pg[NameObject("/Annots")] = ArrayObject([DictionaryObject({
            NameObject("/Type"): NameObject("/Annot"), NameObject("/Subtype"): NameObject("/Text"),
            NameObject("/Rect"): ArrayObject([FloatObject(1), FloatObject(1), FloatObject(9), FloatObject(9)]),
            NameObject("/Contents"): TextStringObject(f"note-on-{link_on}")})])
    if outline:
        for i in range(pages):
            w.add_outline_item(f"Part {i}", i)
    if password:
        w.encrypt(user_password="", owner_password=password)
    with open(path, "wb") as f:
        w.write(f)


def widths(path):
    return [int(float(p.mediabox.width)) for p in PdfReader(path).pages]


def rots(path):
    return [int(p.get("/Rotate", 0) or 0) for p in PdfReader(path).pages]


def sha(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


def src(tmp_path, pages=5, **kw):
    p = tmp_path / "doc.pdf"
    make_pdf(str(p), pages, **kw)
    return str(p)


def test_reorder_delete_rotate_writes_exact_pages(tmp_path):
    s = src(tmp_path, 6)
    r = H.organize_pdf(s, [(4, 0), (0, 90), (2, 0), (5, 270)])
    assert widths(r["path"]) == [104, 100, 102, 105]
    assert rots(r["path"]) == [0, 90, 0, 270]
    assert r["pages"] == 4


def test_original_untouched_and_output_in_organized_folder(tmp_path):
    s = src(tmp_path, 4)
    before = sha(s)
    r = H.organize_pdf(s, [(3, 0), (2, 0), (1, 0), (0, 0)])
    assert sha(s) == before
    assert os.path.dirname(r["path"]) == str(tmp_path / H.setting("organized_dir_name"))
    assert os.path.basename(r["path"]) == "doc.pdf"


def test_second_save_never_overwrites_the_first(tmp_path):
    s = src(tmp_path, 4)
    a = H.organize_pdf(s, [(3, 0), (2, 0), (1, 0), (0, 0)])["path"]
    first = sha(a)
    b = H.organize_pdf(s, [(1, 0), (0, 0), (2, 0), (3, 0)])["path"]
    assert a != b and sha(a) == first and os.path.exists(b)


def test_rotation_adds_to_an_existing_page_rotation(tmp_path):
    s = src(tmp_path, 3, rotate={1: 90})
    r = H.organize_pdf(s, [(0, 0), (1, 90), (2, 0)])
    assert rots(r["path"]) == [0, 180, 0]


def test_unchanged_plan_is_refused(tmp_path):
    s = src(tmp_path, 3)
    with pytest.raises(H.PdfOpError, match="Nothing was changed"):
        H.organize_pdf(s, [(0, 0), (1, 0), (2, 0)])
    assert not (tmp_path / "Reorganized").exists() or not os.listdir(tmp_path / "Reorganized")


@pytest.mark.parametrize("plan,msg", [
    ([], "every page"),
    ([(0, 0), (0, 0)], "twice"),
    ([(9, 0)], "not in this"),
    ([(3, 0)], "not in this"),            # exactly one past the end
    ([(-1, 0)], "not in this"),
    ([(0, 45)], "quarter turn"),
    ([(True, 0)], "not in this"),
    (["x"], "damaged"),
])
def test_bad_plans_write_nothing(tmp_path, plan, msg):
    s = src(tmp_path, 3)
    with pytest.raises(H.PdfOpError, match=msg):
        H.organize_pdf(s, plan)
    assert not (tmp_path / "Reorganized").exists() or not os.listdir(tmp_path / "Reorganized")


def test_changed_source_is_refused(tmp_path):
    s = src(tmp_path, 4)
    stamp = H.source_stamp(s)
    make_pdf(s, 7)                                    # somebody else saved it
    with pytest.raises(H.PdfOpError, match="changed on disk"):
        H.organize_pdf(s, [(1, 0), (0, 0)], expect=stamp)


def test_replace_mode_keeps_an_identical_backup_first(tmp_path):
    s = src(tmp_path, 4)
    original = open(s, "rb").read()
    r = H.organize_pdf(s, [(3, 0), (0, 0)], replace=True)
    assert r["path"] == s and widths(s) == [103, 100]
    assert open(r["backup"], "rb").read() == original
    assert os.path.dirname(r["backup"]).endswith(os.path.join("Reorganized", "Originals"))


def test_replace_mode_failure_to_back_up_leaves_original_alone(tmp_path, monkeypatch):
    s = src(tmp_path, 4)
    before = sha(s)
    monkeypatch.setattr(H.shutil, "copy2", lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(OSError):
        H.organize_pdf(s, [(3, 0), (0, 0)], replace=True)
    assert sha(s) == before
    assert not [n for n in os.listdir(tmp_path) if n.endswith(".part")]


def test_bookmarks_are_dropped_and_reported(tmp_path):
    s = src(tmp_path, 4, outline=True)
    r = H.organize_pdf(s, [(3, 0), (0, 0), (1, 0), (2, 0)])
    assert r["dropped_bookmarks"] is True
    assert not PdfReader(r["path"]).outline
    s2 = tmp_path / "plain.pdf"; make_pdf(str(s2), 3)
    assert H.organize_pdf(str(s2), [(2, 0), (1, 0), (0, 0)])["dropped_bookmarks"] is False


def test_annotation_travels_with_its_page(tmp_path):
    s = src(tmp_path, 4, link_on=1)
    r = H.organize_pdf(s, [(3, 0), (1, 0), (0, 0)])
    pages = PdfReader(r["path"]).pages
    assert widths(r["path"]) == [103, 101, 100]
    annots = pages[1].get("/Annots")
    assert annots and annots[0].get_object()["/Contents"] == "note-on-1"
    assert not pages[0].get("/Annots") and not pages[2].get("/Annots")


def test_owner_password_file_still_organizes(tmp_path):
    s = src(tmp_path, 3, password="owner")
    r = H.organize_pdf(s, [(2, 0), (0, 0)])
    assert widths(r["path"]) == [102, 100]


def test_extract_keeps_order_and_leaves_source(tmp_path):
    s = src(tmp_path, 6)
    before = sha(s)
    r = H.extract_pages(s, [4, 1, 2])
    assert os.path.basename(r["path"]) == "doc_Extract.pdf"
    assert widths(r["path"]) == [104, 101, 102] and sha(s) == before
    r2 = H.extract_pages(s, [0])
    assert r2["path"] != r["path"]


def test_extract_validates(tmp_path):
    s = src(tmp_path, 3)
    with pytest.raises(H.PdfOpError):
        H.extract_pages(s, [])
    with pytest.raises(H.PdfOpError):
        H.extract_pages(s, [5])


def test_listing_is_natural_sorted_pdf_only_not_recursive(tmp_path):
    for n in ("b10.pdf", "b2.pdf", "A1.PDF", "note.txt"):
        (tmp_path / n).write_bytes(b"x")
    (tmp_path / "sub").mkdir(); (tmp_path / "sub" / "z.pdf").write_bytes(b"x")
    (tmp_path / "dir.pdf").mkdir()
    names = [d["name"] for d in H.pdf_folder_listing(str(tmp_path))]
    assert names == ["A1.PDF", "b2.pdf", "b10.pdf"]
    assert H.pdf_folder_listing(str(tmp_path / "missing")) == []


def text_pdf(path, pages):
    from reportlab.pdfgen import canvas
    c = canvas.Canvas(path)
    for t in pages:
        if t:
            c.drawString(72, 700, t)
        c.showPage()
    c.save()


def test_search_finds_pages_by_text_layer_only(tmp_path):
    p = str(tmp_path / "t.pdf")
    text_pdf(p, ["Summons for Case 2026-CV-0417", "", "see case 2026-cv-0417 again 2026-CV-0417"])
    r = H.search_pdf_text(p, "2026-cv-0417")
    assert r["pages"] == [0, 2] and r["hits"] == 3 and r["has_text"] is True
    assert H.search_pdf_text(p, "CASE 2026-CV-0417")["pages"] == [0, 2]   # needle case does not matter
    assert H.search_pdf_text(p, "nothing here")["pages"] == []
    assert H.search_pdf_text(p, "   ")["hits"] == 0


def test_search_reports_a_scan_with_no_text_layer(tmp_path):
    s = src(tmp_path, 3)                              # blank pages, no text
    r = H.search_pdf_text(s, "case")
    assert r == {"pages": [], "hits": 0, "has_text": False}
