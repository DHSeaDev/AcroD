"""A file named .pdf that is huge or not a PDF must be refused from its size and first KB,
before any library reads it into memory (a 2 GB file of zeros once cost 40-60 s and ~3 GB)."""
import os, sys, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import pytest
import acrod as H


def _pdf(tmp_path, name="a.pdf", pad=b""):
    from pypdf import PdfWriter
    w = PdfWriter(); w.add_blank_page(100, 100)
    p = tmp_path / name
    with open(p, "wb") as fh:
        fh.write(pad); w.write(fh)
    return str(p)


def test_too_large_is_refused_unread(tmp_path, monkeypatch):
    p = _pdf(tmp_path)
    monkeypatch.setattr(H, "PDF_FILE_MAX", 100)
    assert "too large" in H.pdf_file_problem(p)
    with pytest.raises(H.PdfOpError, match="too large"):
        H.pdf_page_count(p)
    with pytest.raises(H.PdfOpError, match="too large"):
        H.require_safe_to_expand(p)


def test_sparse_gigabytes_cost_nothing(tmp_path):
    p = tmp_path / "z.pdf"
    with open(p, "wb") as fh:
        fh.truncate(900 << 20)                 # under the cap: only the header check can save us
    t = time.time()
    with pytest.raises(H.PdfOpError, match="not a PDF"):
        H.pdf_page_count(str(p))
    with pytest.raises(H.PdfOpError, match="not a PDF"):
        H.require_safe_to_expand(str(p))
    assert time.time() - t < 2


def test_real_pdf_and_small_leading_junk_still_open(tmp_path):
    assert H.pdf_page_count(_pdf(tmp_path)) == 1
    assert H.pdf_page_count(_pdf(tmp_path, "j.pdf", pad=b"junk\n" * 50)) == 1   # under 1 KB of junk is tolerated


def test_empty_and_unreadable_are_not_misreported(tmp_path):
    e = tmp_path / "e.pdf"; e.write_bytes(b"")
    assert H.pdf_file_problem(str(e)) is None                 # the library's own message says "empty"
    assert H.pdf_file_problem(str(tmp_path / "gone.pdf")) is None
