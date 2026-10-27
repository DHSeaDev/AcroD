"""PDF form fields must be drawn. pdfium paints widgets only after init_forms();
every render path (reader, grid thumbnails, Optimizer flatten) opens through
pdfium_open_for_render."""
import os, sys
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import pytest
from pypdf import PdfReader, PdfWriter
from pypdf.generic import NameObject, BooleanObject
import acrod as H


def _form(path, strip_ap):
    from reportlab.pdfgen import canvas
    c = canvas.Canvas(str(path), pagesize=(612, 792))
    c.drawString(72, 740, "CIVIL FILING INFORMATION SHEET")
    c.acroForm.textfield(name="party", value="HARDING VERSUS EXAMPLE CORP", x=72, y=680,
                         width=400, height=24, borderWidth=1, fontSize=12, forceBorder=True)
    c.acroForm.checkbox(name="agree", checked=True, x=72, y=640, size=20, forceBorder=True)
    c.save()
    if strip_ap:
        r = PdfReader(str(path)); w = PdfWriter(clone_from=r)
        for a in w.pages[0].get("/Annots", []):
            o = a.get_object()
            if "/AP" in o:
                del o["/AP"]
        w._root_object["/AcroForm"][NameObject("/NeedAppearances")] = BooleanObject(True)
        with open(path, "wb") as f:
            w.write(f)


def _ink(img):
    g = img.convert("L")
    return sum(1 for p in g.getdata() if p < 128)


def _render(path, forms):
    pdfium = H._pdfium()
    with H._PDFIUM_LOCK:
        doc = H.pdfium_open_for_render(pdfium, str(path)) if forms else pdfium.PdfDocument(str(path))
        try:
            return _ink(H.reader_render_page(doc, 0, 1.5, 0))
        finally:
            doc.close()


@pytest.mark.parametrize("strip", [False, True], ids=["with-AP", "no-AP"])
def test_form_fields_are_drawn(tmp_path, strip):
    p = tmp_path / "f.pdf"; _form(p, strip)
    plain, forms = _render(p, False), _render(p, True)
    assert forms > plain * 1.2, (plain, forms)


def test_thumbnail_and_flatten_paths_open_with_forms():
    import inspect
    for fn in (H._ThumbWorker.run, H._ReaderWorker.run, H._ReaderWorker._with_doc):
        assert "pdfium_open_for_render" in inspect.getsource(fn), fn
    src = inspect.getsource(H)
    assert "doc = pdfium_open_for_render(pdfium, source)" in src


def test_helper_survives_init_forms_failure():
    class D:
        def init_forms(self): raise RuntimeError("no form support")
    class P:
        @staticmethod
        def PdfDocument(src): return D()
    assert isinstance(H.pdfium_open_for_render(P, "x"), D)
