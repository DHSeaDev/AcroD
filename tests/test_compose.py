"""Saving a PDF built from pages of several files (the engine under cut/copy/paste and the
two-pane merge). Every page of the result is identified by the text it carries."""
import hashlib, io, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))
import pikepdf
import pytest
from pikepdf import Name, Dictionary, Array, String
import acrod as H
from test_reader_engine import make_text_pdf


@pytest.fixture
def two(tmp_path):
    a = make_text_pdf(str(tmp_path / "a.pdf"), pages=3, text="AAA")
    b = make_text_pdf(str(tmp_path / "b.pdf"), pages=2, text="BBB")
    return a, b


def texts(path):
    return [t.upper().replace(" ", "") for t in H.pdf_page_texts(path)]


def rots(path):
    with pikepdf.open(path) as p:
        return [int(pg.obj.get("/Rotate", 0)) for pg in p.pages]


def dump(path):
    """Every object of the file as text (object streams opened), so a name can be searched for."""
    with pikepdf.open(path) as p:
        return b"\n".join(o.unparse(resolved=True) for o in p.objects if not isinstance(o, pikepdf.Stream)
                         ) + b"\n" + b"\n".join(o.unparse(resolved=True) for o in p.objects if isinstance(o, pikepdf.Stream))


def sha(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


def test_mixed_order_and_rotation(two):
    a, b = two
    r = H.organize_pdf(a, [(0, 0, 0), (1, 1, 0), (0, 1, 0), (1, 0, 90)], extra=[b])
    assert texts(r["path"]) == ["AAA0", "BBB1", "AAA1", "BBB0"] and r["pages"] == 4
    assert rots(r["path"]) == [0, 0, 0, 90]


def test_a_page_can_be_copied_twice(two):
    a, _b = two
    r = H.organize_pdf(a, [(0, 0, 0), (0, 0, 0), (0, 1, 0), (0, 2, 0)], extra=[])
    assert texts(r["path"]) == ["AAA0", "AAA0", "AAA1", "AAA2"]


def test_originals_are_untouched(two):
    a, b = two
    ha, hb = sha(a), sha(b)
    H.organize_pdf(a, [(1, 0, 0), (0, 0, 0)], extra=[b])
    assert (sha(a), sha(b)) == (ha, hb)


def test_unchanged_mixed_plan_is_refused(two):
    a, b = two
    with pytest.raises(H.PdfOpError, match="Nothing was changed"):
        H.organize_pdf(a, [(0, 0, 0), (0, 1, 0), (0, 2, 0)], extra=[b])


def test_single_source_rules_are_unchanged(two):
    a, _b = two
    r = H.organize_pdf(a, [(2, 0), (0, 90)])                      # the old 2-tuple plan still works
    assert texts(r["path"]) == ["AAA2", "AAA0"]
    with pytest.raises(H.PdfOpError, match="appears twice"):
        H.organize_pdf(a, [(0, 0), (0, 0)])                       # and still refuses a duplicate


@pytest.mark.parametrize("bad", [
    [(2, 0, 0)],                 # no such source
    [(-1, 0, 0)], [(True, 0, 0)], [("0", 0, 0)],
    [(1, 2, 0)],                 # b has only 2 pages
    [(0, -1, 0)], [(0, True, 0)],
    [(0, 0, 45)], [(0, 0, True)],
    [(0, 0)] + [(1, 0, 0)],      # mixed item shapes
])
def test_bad_plans_write_nothing(two, bad):
    a, b = two
    before = set(os.listdir(os.path.dirname(a)))
    with pytest.raises(H.PdfOpError):
        H.organize_pdf(a, bad, extra=[b])
    assert set(os.listdir(os.path.dirname(a))) - before <= {"Reorganized"}
    out = os.path.join(os.path.dirname(a), "Reorganized")
    assert not os.path.isdir(out) or os.listdir(out) == []


def test_a_changed_other_file_is_refused(two):
    a, b = two
    stale = (os.stat(b).st_mtime_ns - 5, os.stat(b).st_size)
    with pytest.raises(H.PdfOpError, match="changed on disk"):
        H.organize_pdf(a, [(1, 0, 0), (0, 0, 0)], extra=[b], extra_expect=[stale])
    out = os.path.join(os.path.dirname(a), "Reorganized")
    assert not os.path.isdir(out) or os.listdir(out) == []


def test_matching_stamps_pass(two):
    a, b = two
    r = H.organize_pdf(a, [(1, 0, 0), (0, 0, 0)], expect=H.source_stamp(a), extra=[b], extra_expect=[H.source_stamp(b)])
    assert texts(r["path"]) == ["BBB0", "AAA0"]


def test_replace_keeps_a_backup_and_leaves_the_other_alone(two):
    a, b = two
    ha, hb = sha(a), sha(b)
    r = H.organize_pdf(a, [(0, 0, 0), (1, 0, 0), (0, 1, 0)], extra=[b], replace=True)
    assert r["path"] == a and sha(b) == hb and sha(r["backup"]) == ha
    assert texts(a) == ["AAA0", "BBB0", "AAA1"]


# ---------------------------------------------------------------- foreign files are not trusted
def hostile(path):
    pdf = pikepdf.new(); pdf.add_blank_page(page_size=(200, 200))
    page = pdf.pages[0].obj
    act = lambda s, **k: pdf.make_indirect(Dictionary(S=Name("/" + s), **{kk: vv for kk, vv in k.items()}))
    link = lambda a: pdf.make_indirect(Dictionary(Type=Name.Annot, Subtype=Name.Link, Rect=[0, 0, 9, 9], A=a))
    ann = [link(act("Launch", F=String("cmd.exe"))), link(act("JavaScript", JS=String("x"))),
           link(act("URI", URI=String("https://example.com"))),
           link(pdf.make_indirect(Dictionary(S=Name.GoTo, D=Array([page, Name("/Fit")]), Next=act("JavaScript", JS=String("y"))))),
           link(pdf.make_indirect(Dictionary(S=Name.GoTo, D=Array([page, Name("/Fit")])))),
           pdf.make_indirect(Dictionary(Type=Name.Annot, Subtype=Name.Screen, Rect=[0, 0, 9, 9])),
           pdf.make_indirect(Dictionary(Type=Name.Annot, Subtype=Name.FileAttachment, Rect=[0, 0, 9, 9], FS=String("x")))]
    page.Annots = pdf.make_indirect(Array(ann))
    page.AA = Dictionary(O=act("JavaScript", JS=String("z")))
    parent = pdf.make_indirect(Dictionary(FT=Name.Tx, T=String("f"), AA=Dictionary(K=act("JavaScript", JS=String("w")))))
    wid = pdf.make_indirect(Dictionary(Type=Name.Annot, Subtype=Name.Widget, Rect=[0, 0, 50, 20], FT=Name.Tx, T=String("kid"), Parent=parent))
    page.Annots.append(wid)
    pdf.Root.AcroForm = Dictionary(Fields=Array([wid]))
    pdf.save(path)
    return str(path)



def test_foreign_pages_lose_programs_but_keep_links(two, tmp_path):
    a, _b = two
    h = hostile(tmp_path / "h.pdf")
    r = H.organize_pdf(a, [(0, 0, 0), (1, 0, 0)], extra=[h])
    data = dump(r["path"])
    assert len(data) > 200
    for bad in (b"/Launch", b"/JS", b"/JavaScript", b"/Screen", b"/FileAttachment"):
        assert bad not in data, bad
    assert b"https://example.com" in data and b"/GoTo" in data


def test_own_pages_lose_launch_actions_too_unless_the_switch_is_off(tmp_path, monkeypatch):
    h = hostile(tmp_path / "h.pdf")
    other = make_text_pdf(str(tmp_path / "o.pdf"), pages=1, text="OOO")
    r = H.organize_pdf(h, [(0, 0, 0), (1, 0, 0)], extra=[other])
    assert b"/Launch" not in dump(r["path"])           # 1.10.1: a saved copy never carries programs, own pages included
    monkeypatch.setattr(H, "ORGANIZER_STRIP_ACTIVE", False)
    r = H.organize_pdf(h, [(0, 0, 0), (1, 0, 0)], extra=[other])
    assert b"/Launch" in dump(r["path"])


def test_pasted_form_fields_stay_fillable(two, tmp_path):
    a, _b = two
    h = hostile(tmp_path / "h.pdf")
    r = H.organize_pdf(a, [(1, 0, 0)], extra=[h])
    with pikepdf.open(r["path"]) as p:
        assert "/AcroForm" in p.Root and len(p.Root.AcroForm.Fields) >= 1


def test_foreign_file_that_is_not_a_pdf_is_refused(two, tmp_path):
    a, _b = two
    junk = tmp_path / "junk.pdf"; junk.write_bytes(b"not a pdf at all\n" * 50)
    with pytest.raises(H.PdfOpError, match="not a PDF"):
        H.organize_pdf(a, [(1, 0, 0), (0, 0, 0)], extra=[str(junk)])


def test_foreign_file_that_is_too_large_is_refused(two, monkeypatch):
    a, b = two
    monkeypatch.setattr(H, "PDF_FILE_MAX", os.path.getsize(b) - 1)
    with pytest.raises(H.PdfOpError, match="too large"):
        H.organize_pdf(a, [(1, 0, 0), (0, 0, 0)], extra=[b])


def test_password_protected_foreign_file_is_refused(two, tmp_path):
    a, _b = two
    enc = tmp_path / "enc.pdf"
    with pikepdf.open(a) as p:
        p.save(enc, encryption=pikepdf.Encryption(user="secret", owner="o"))
    with pytest.raises(H.PdfOpError, match="password"):
        H.organize_pdf(a, [(1, 0, 0), (0, 0, 0)], extra=[str(enc)])


def test_too_many_sources_is_refused(two, monkeypatch):
    a, b = two
    monkeypatch.setattr(H, "MULTI_SOURCES_MAX", 2)
    with pytest.raises(H.PdfOpError, match="more than 2"):
        H.organize_pdf(a, [(1, 0, 0), (2, 0, 0)], extra=[b, b])


def test_a_foreign_file_that_cannot_be_cleaned_is_not_saved(two, tmp_path, monkeypatch):
    a, b = two
    def boom(*a_, **k):
        raise RuntimeError("no")
    monkeypatch.setattr(H, "_strip_active_holder", boom)
    h = hostile(tmp_path / "h.pdf")
    with pytest.raises(H.PdfOpError, match="nothing was written"):
        H.organize_pdf(a, [(1, 0, 0), (0, 0, 0)], extra=[h])
    out = os.path.join(os.path.dirname(a), "Reorganized")
    assert os.path.isdir(out) and [f for f in os.listdir(out) if f.endswith(".pdf") or f.endswith(".part")] == []


def test_bookmarks_flag(two, tmp_path):
    a, b = two
    with pikepdf.open(b) as p:
        with p.open_outline() as o:
            o.root.append(pikepdf.OutlineItem("Chap", 0))
        p.save(tmp_path / "bm.pdf")
    r = H.organize_pdf(a, [(1, 0, 0), (0, 0, 0)], extra=[str(tmp_path / "bm.pdf")])
    assert r["dropped_bookmarks"] is True
    r2 = H.organize_pdf(a, [(1, 0, 0), (0, 0, 0)], extra=[b])
    assert r2["dropped_bookmarks"] is False


def test_foreign_decompression_bomb_is_refused(two, tmp_path, monkeypatch):
    import zlib
    a, _b = two
    pdf = pikepdf.new(); pdf.add_blank_page(page_size=(100, 100))
    pdf.pages[0].obj.Contents = pdf.make_stream(zlib.compress(b"q Q " * (2 << 20)), Filter=Name.FlateDecode)
    pdf.save(tmp_path / "bomb.pdf")
    monkeypatch.setattr(H, "BOMB_STREAM_MAX", 1 << 20)
    with pytest.raises(H.PdfOpError, match="expand"):
        H.organize_pdf(a, [(1, 0, 0), (0, 0, 0)], extra=[str(tmp_path / "bomb.pdf")])
