"""The Optimizer promises JavaScript and automatic actions are gone. Every way a PDF can run code,
start a program, open another file or send form data must be gone too - not only /JavaScript
actions on page annotations. Plain links and page jumps must stay."""
import io, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import pikepdf
from pikepdf import Name, Dictionary, Array, String
import acrod as H


def _act(pdf, s, **kw):
    d = Dictionary(S=Name("/" + s)); [d.__setitem__("/" + k, v) for k, v in kw.items()]
    return pdf.make_indirect(d)


def _hostile():
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(200, 200))
    page = pdf.pages[0].obj
    js = lambda: _act(pdf, "JavaScript", JS=String("app.alert(1)"))
    annots = []
    for s, kw in (("Launch", dict(F=String("cmd.exe"))), ("GoToR", dict(F=String("x.pdf"), D=Array([0, Name("/Fit")]))),
                  ("GoToE", dict()), ("SubmitForm", dict(F=String("http://e/"))), ("ImportData", dict(F=String("a"))),
                  ("Rendition", dict()), ("JavaScript", dict(JS=String("x")))):
        annots.append(pdf.make_indirect(Dictionary(Type=Name.Annot, Subtype=Name.Link, Rect=[0, 0, 9, 9], A=_act(pdf, s, **kw))))
    # chained: harmless GoTo whose /Next runs a script
    chain = _act(pdf, "GoTo", D=Array([page, Name("/Fit")]), Next=js())
    annots.append(pdf.make_indirect(Dictionary(Type=Name.Annot, Subtype=Name.Link, Rect=[0, 0, 9, 9], A=chain)))
    # safe ones that must survive
    annots.append(pdf.make_indirect(Dictionary(Type=Name.Annot, Subtype=Name.Link, Rect=[0, 0, 9, 9], A=_act(pdf, "URI", URI=String("https://example.com")))))
    annots.append(pdf.make_indirect(Dictionary(Type=Name.Annot, Subtype=Name.Link, Rect=[0, 0, 9, 9], A=_act(pdf, "GoTo", D=Array([page, Name("/Fit")])))))
    annots.append(pdf.make_indirect(Dictionary(Type=Name.Annot, Subtype=Name.Screen, Rect=[0, 0, 9, 9], A=js())))
    annots.append(pdf.make_indirect(Dictionary(Type=Name.Annot, Subtype=Name.RichMedia, Rect=[0, 0, 9, 9])))
    page.Annots = pdf.make_indirect(Array(annots))
    # orphan field (not on any page) with calculate script, plus AcroForm /AA and XFA
    orphan = pdf.make_indirect(Dictionary(FT=Name.Tx, T=String("o"), AA=Dictionary(C=js()), A=js()))
    pdf.Root.AcroForm = pdf.make_indirect(Dictionary(Fields=Array([orphan]), XFA=pdf.make_stream(b"<xdp/>"), AA=Dictionary(WC=js())))
    pdf.Root.Outlines = pdf.make_indirect(Dictionary(Type=Name.Outlines, First=pdf.make_indirect(Dictionary(Title=String("b"), A=_act(pdf, "Launch", F=String("c"))))))
    pdf.Root.AF = Array([pdf.make_indirect(Dictionary(Type=Name.Filespec, F=String("e.exe")))])
    return pdf


def _dump(pdf):
    buf = io.BytesIO(); pdf.save(buf); return buf.getvalue()


def _sanitized(**over):
    pdf = _hostile()
    H.sanitize_pdf(pdf, H.SanitizeOptions(**over))
    return _dump(pdf)


def test_no_active_content_survives_defaults():
    out = _sanitized()
    for bad in (b"/Launch", b"/GoToR", b"/GoToE", b"/SubmitForm", b"/ImportData", b"/Rendition",
                b"/JavaScript", b"/JS", b"/XFA", b"/Screen", b"/RichMedia", b"/AA", b"/AF"):
        assert bad not in out, bad


def test_plain_links_and_jumps_stay():
    out = _sanitized()
    assert b"https://example.com" in out and b"/GoTo" in out


def test_outline_action_removed_but_bookmark_kept():
    out = _sanitized()
    assert b"/Outlines" in out and b"/Launch" not in out


def test_javascript_off_leaves_actions_alone():
    out = _sanitized(javascript=False, auto_actions=False)
    assert b"/Launch" in out


def test_malformed_action_is_treated_as_active():
    assert H._action_is_active(object()) is True
    assert H._action_is_active(None) is False


def test_chain_cycle_terminates():
    pdf = pikepdf.new(); a = pdf.make_indirect(Dictionary(S=Name.GoTo)); a.Next = a
    assert H._action_is_active(a) is False
