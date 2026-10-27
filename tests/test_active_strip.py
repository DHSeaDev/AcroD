"""Page Organizer saves leave scripts and launch actions out of the saved copy (ORGANIZER_STRIP_ACTIVE)."""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import pikepdf, pytest
from pikepdf import Dictionary, Array, Name, String, Pdf
import acrod as H
from test_organizer_panel import app  # noqa: F401


@pytest.fixture(autouse=True)
def _iso(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "appdata"))
    H._reset_ui_state_cache()


def js(code="app.alert(1)"):
    return Dictionary(S=Name.JavaScript, JS=String(code))


def launch():
    return Dictionary(S=Name.Launch, F=String("calc.exe"))


def hostile(path, pages=3, outline=False):
    pdf = Pdf.new()
    for i in range(pages):
        pdf.add_blank_page(page_size=(100 + i, 100))
    p0, p1 = pdf.pages[0].obj, pdf.pages[1].obj
    p0.AA = Dictionary(O=js())
    link = pdf.make_indirect(Dictionary(Type=Name.Annot, Subtype=Name.Link, Rect=[0, 0, 5, 5],
                                        A=Dictionary(S=Name.GoTo, D=Array([pdf.pages[2].obj, Name.Fit]))))
    uri = pdf.make_indirect(Dictionary(Type=Name.Annot, Subtype=Name.Link, Rect=[6, 0, 9, 5],
                                       A=Dictionary(S=Name.URI, URI=String("https://example.com"))))
    bad = pdf.make_indirect(Dictionary(Type=Name.Annot, Subtype=Name.Link, Rect=[0, 6, 5, 9], A=launch()))
    movie = pdf.make_indirect(Dictionary(Type=Name.Annot, Subtype=Name.Movie, Rect=[0, 0, 5, 5]))
    parent = pdf.make_indirect(Dictionary(FT=Name.Tx, T=String("calc"), AA=Dictionary(C=js("event.value=1"))))
    field = pdf.make_indirect(Dictionary(Type=Name.Annot, Subtype=Name.Widget, Rect=[10, 10, 50, 30],
                                         FT=Name.Tx, T=String("name"), Parent=parent))
    p0.Annots = Array([link, uri, bad, movie, field])
    p1.Annots = Array([pdf.make_indirect(Dictionary(Type=Name.Annot, Subtype=Name.Link, Rect=[0, 0, 5, 5], A=js()))])
    pdf.Root.OpenAction = Dictionary(S=Name.JavaScript, JS=String("app.alert(2)"))
    pdf.Root.AA = Dictionary(WC=js())
    pdf.Root.Names = Dictionary(JavaScript=Dictionary(Names=Array([String("x"), js()])))
    pdf.Root.AcroForm = Dictionary(Fields=Array([field]))
    if outline:
        a = pdf.make_indirect(Dictionary(Title=String("A"), Dest=Array([pdf.pages[0].obj, Name.Fit]), A=launch()))
        b = pdf.make_indirect(Dictionary(Title=String("B"), A=Dictionary(S=Name.GoTo, D=Array([pdf.pages[1].obj, Name.Fit]))))
        a.Next = b
        b.Prev = a
        pdf.Root.Outlines = pdf.make_indirect(Dictionary(Type=Name.Outlines, First=a, Last=b, Count=2))
        a.Parent = pdf.Root.Outlines
        b.Parent = pdf.Root.Outlines
    pdf.save(path)


def blob(path):
    return open(path, "rb").read()


def clean(path):
    raw = blob(path)
    return all(tok not in raw for tok in (b"/JavaScript", b"/Launch", b"app.alert", b"calc.exe", b"event.value"))


def run(tmp_path, plan=((0, 90), (1, 0), (2, 0)), **kw):
    s = str(tmp_path / "h.pdf")
    hostile(s, **kw.pop("mk", {}))
    return s, H.organize_pdf(s, list(plan), **kw)


def test_scripts_and_launch_actions_are_gone_from_an_own_file_save(tmp_path):
    _s, r = run(tmp_path)
    assert clean(r["path"])
    assert r["active_removed"]


def test_links_page_jumps_and_fields_survive(tmp_path):
    _s, r = run(tmp_path)
    with pikepdf.open(r["path"]) as out:
        subs = [str(a.get("/Subtype")) for a in out.pages[0].Annots]
        kinds = [str(a.A.S) for a in out.pages[0].Annots if "/A" in a]
        assert "/Widget" in subs and "/Movie" not in subs
        assert kinds.count("/GoTo") == 1 and kinds.count("/URI") == 1 and "/Launch" not in kinds
        assert "/AcroForm" in out.Root and len(out.Root.AcroForm.Fields) == 1
        assert "/AA" not in out.pages[0].obj


def test_outline_actions_are_stripped_but_titles_and_jumps_stay(tmp_path):
    s = str(tmp_path / "o.pdf")
    hostile(s, outline=True)
    r = H.organize_pdf(s, [(0, 90), (1, 0), (2, 0)])
    assert clean(r["path"])
    if not r["dropped_bookmarks"]:
        with pikepdf.open(r["path"]) as out:
            titles, n = [], out.Root.Outlines.First
            while n is not None:
                titles.append((str(n.Title), str(n.A.S) if "/A" in n else None))
                n = n.get("/Next")
            assert ("B", "/GoTo") in titles and all(k != "/Launch" for _t, k in titles)


def test_outline_walk_strips_the_file_itself(tmp_path):
    s = str(tmp_path / "o2.pdf")
    hostile(s, outline=True)
    gone = H._strip_foreign_pages(s, [0, 1, 2], document=True)
    assert gone.get("Annotation JavaScript", 0) >= 1 and clean(s)
    with pikepdf.open(s) as out:
        assert str(out.Root.Outlines.First.Title) == "A" and "/A" not in out.Root.Outlines.First
        assert str(out.Root.Outlines.First.Next.A.S) == "/GoTo"


def test_outline_loop_does_not_hang(tmp_path):
    s = str(tmp_path / "loop.pdf")
    hostile(s, outline=True)
    with pikepdf.open(s, allow_overwriting_input=True) as pdf:
        o = pdf.Root.Outlines
        o.First.Next.Next = o.First                      # cycle
        pdf.save(s)
    H._strip_foreign_pages(s, [0, 1, 2], document=True)


def test_clean_file_is_not_rewritten_by_the_strip(tmp_path, monkeypatch):
    s = str(tmp_path / "c.pdf")
    pdf = Pdf.new()
    for i in range(3):
        pdf.add_blank_page(page_size=(100 + i, 100))
    pdf.save(s)
    calls = []
    real = os.replace
    monkeypatch.setattr(os, "replace", lambda a, b: (calls.append(str(a)), real(a, b))[1])
    r = H.organize_pdf(s, [(0, 90), (1, 0), (2, 0)])
    assert r["active_removed"] == {}
    assert not [c for c in calls if c.endswith(".clean")]


def test_switch_off_keeps_the_old_behaviour(tmp_path, monkeypatch):
    monkeypatch.setattr(H, "ORGANIZER_STRIP_ACTIVE", False)
    _s, r = run(tmp_path)
    assert not clean(r["path"])


def test_fails_closed_when_the_file_cannot_be_checked(tmp_path, monkeypatch):
    s = str(tmp_path / "h.pdf")
    hostile(s)
    def boom(*a, **k):
        raise RuntimeError("damaged")
    monkeypatch.setattr(H, "_strip_outline_actions", boom)
    outdir = tmp_path / "Reorganized"
    with pytest.raises(H.PdfOpError, match="nothing was written"):
        H.organize_pdf(s, [(0, 90), (1, 0), (2, 0)])
    assert not outdir.exists() or not [f for f in os.listdir(outdir) if f.endswith(".pdf")]


def test_pasted_foreign_pages_are_still_stripped_and_own_pages_too(tmp_path):
    a = str(tmp_path / "a.pdf"); b = str(tmp_path / "b.pdf")
    hostile(a); hostile(b)
    ex = H.source_stamp(b)
    r = H.organize_pdf(a, [(0, 0, 0), (1, 1, 0), (0, 2, 0)], extra=[b], extra_expect=[ex])
    assert clean(r["path"])


def test_saved_message_names_what_was_left_out(tmp_path):
    _s, r = run(tmp_path)
    assert all(isinstance(v, int) and v > 0 for v in r["active_removed"].values())


def test_damaged_page_tree_is_refused_in_plain_words_and_leaves_nothing(tmp_path, monkeypatch):
    s = str(tmp_path / "bad.pdf")
    hostile(s)
    def boom(*a, **k):
        raise ValueError("Invalid page object")
    monkeypatch.setattr(H, "_build_from_plan", boom)
    with pytest.raises(H.PdfOpError, match="damaged pages"):
        H.organize_pdf(s, [(0, 90), (1, 0), (2, 0)])
    out = tmp_path / "Reorganized"
    assert not out.exists() or not os.listdir(out)


def test_keep_scripts_setting_defaults_off_and_validates(tmp_path, monkeypatch):
    assert H.ADVANCED_DEFAULTS["organizer_keep_scripts"] is False
    assert H.setting("organizer_keep_scripts") is False
    assert H._valid_setting("organizer_keep_scripts", True) and not H._valid_setting("organizer_keep_scripts", "yes")


def test_strip_own_false_keeps_own_programs_but_pasted_pages_are_still_cleaned(tmp_path):
    a = str(tmp_path / "a.pdf"); b = str(tmp_path / "b.pdf")
    hostile(a); hostile(b)
    ex = H.source_stamp(b)
    r = H.organize_pdf(a, [(0, 0, 0), (1, 0, 0)], extra=[b], extra_expect=[ex], strip_own=False)
    with pikepdf.open(r["path"]) as out:
        own = [str(x.A.S) for x in out.pages[0].Annots if "/A" in x]
        pasted = [str(x.A.S) for x in out.pages[1].Annots if "/A" in x]
    assert "/Launch" in own                              # page 1 is the file's own: kept on request
    assert "/Launch" not in pasted and "/JavaScript" not in pasted     # page 2 came from another file: always cleaned


def test_settings_panel_saves_and_resets_the_keep_scripts_box(app):
    pnl = H.SettingsPanel()
    box = pnl.adv["organizer_keep_scripts"]
    assert not box.isChecked() and H.setting("organizer_keep_scripts") is False
    box.setChecked(True)
    assert H.setting("organizer_keep_scripts") is False           # nothing is stored until Save is pressed
    pnl._save_advanced()
    assert H.setting("organizer_keep_scripts") is True
    pnl._reset_advanced()
    assert not box.isChecked()
    pnl._save_advanced()
    assert H.setting("organizer_keep_scripts") is False
    assert H.save_settings({"organizer_keep_scripts": "yes"}) == ["organizer_keep_scripts"]


def _saved_by_panel(app, tmp_path, monkeypatch, keep):
    from test_page_clipboard import open_row, save
    from test_organizer_panel import select
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    d = tmp_path / "pdfs"; d.mkdir()
    hostile(str(d / "a.pdf"), pages=4)
    H.save_settings({"organizer_keep_scripts": keep})
    p = H.OrganizerPanel(); p.show()
    p.folder_row.set_path(str(d))
    from test_organizer_panel import pump
    assert pump(app, lambda: p.file_list.count() == 1 and len(p._counts) == 1)
    try:
        open_row(app, p, 0, 4)
        select(p, 3)
        p.grid.setFocus()
        QTest.keyClick(p.grid, Qt.Key_Delete)                     # a real page change so there is something to save
        assert len(p._pages) == 3
        return blob(save(app, p, monkeypatch))
    finally:
        p._pages = []
        p.stop_for_shutdown(); p.close(); p.deleteLater(); app.processEvents()


def test_a_real_panel_save_leaves_scripts_out_by_default(app, tmp_path, monkeypatch):
    raw = _saved_by_panel(app, tmp_path, monkeypatch, keep=False)
    assert b"/Launch" not in raw and b"app.alert" not in raw


def test_a_real_panel_save_keeps_the_files_own_scripts_when_asked(app, tmp_path, monkeypatch):
    raw = _saved_by_panel(app, tmp_path, monkeypatch, keep=True)
    assert b"/Launch" in raw
