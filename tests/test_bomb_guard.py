"""Decompression-bomb pre-flight: a small file that inflates to gigabytes is refused before pdfium, pikepdf or
pypdf is handed it (1.4.2: 15-22 s and 2-5 GB per file, and an OS kill on a 4 GiB one)."""
import os, signal, sys, time, tracemalloc, zlib
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import pytest
import acrod as H
from test_organizer_engine import make_pdf
from test_organizer_panel import app, _iso, pump  # noqa: F401


def pdf_with(streams, path):
    """streams: [(dict_body, raw_bytes_or_None)] -> a minimal PDF where object 4+ are those streams."""
    objs = [b"<</Type/Catalog/Pages 2 0 R>>", b"<</Type/Pages/Kids[3 0 R]/Count 1>>",
            b"<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]/Contents 4 0 R/Resources<<>>>>"]
    for item in streams:
        body, data = item[0], item[1]
        length = item[2] if len(item) > 2 else b"%d" % len(data)         # a lie is allowed: b"9 0 R", b"7"
        objs.append(b"<<" + body + b"/Length " + length + b">>\nstream\n" + data + b"\nendstream")
    out = bytearray(b"%PDF-1.5\n"); offs = []
    for i, o in enumerate(objs, 1):
        offs.append(len(out)); out += b"%d 0 obj\n" % i + o + b"\nendobj\n"
    x = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    for o in offs:
        out += b"%010d 00000 n \n" % o
    out += b"trailer\n<</Size %d/Root 1 0 R>>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, x)
    path.write_bytes(bytes(out)); return str(path)


def zeros(n):
    co, parts, chunk = zlib.compressobj(9), [], b" " * (1 << 20)
    for _ in range(n >> 20):
        parts.append(co.compress(chunk))
    parts.append(co.compress(b" " * (n & 0xFFFFF)))
    parts.append(co.flush()); return b"".join(parts)


FLATE = b"/Filter/FlateDecode"


@pytest.fixture(scope="module")
def bomb(tmp_path_factory):
    p = tmp_path_factory.mktemp("b") / "bomb.pdf"
    return pdf_with([(FLATE, zeros(300 << 20))], p)          # 300 MiB inflated: over the 128 MiB per-stream cap


@pytest.fixture
def ok_pdf(tmp_path):
    p = tmp_path / "ok.pdf"; make_pdf(str(p), 5); return str(p)


class Alarm:
    def __init__(self, s): self.s = s
    def __enter__(self):
        signal.signal(signal.SIGALRM, lambda *_: (_ for _ in ()).throw(TimeoutError("guard did not bound its own work")))
        signal.alarm(self.s)
    def __exit__(self, *a): signal.alarm(0)


def test_bomb_is_refused_quickly_and_in_bounded_memory(bomb):
    H._BOMB_CACHE.clear()
    tracemalloc.start()
    with Alarm(10):
        t = time.perf_counter(); msg = H.pdf_expansion_problem(bomb); dt = time.perf_counter() - t
    peak = tracemalloc.get_traced_memory()[1]; tracemalloc.stop()
    assert msg and "bomb.pdf" in msg and "safety check" in msg
    assert dt < 3.0, dt
    assert peak < 64 << 20, peak                                   # the file is ~0.25 MB; 1 MB steps, never the 256 MiB


def test_ordinary_pdfs_pass(ok_pdf, tmp_path):
    assert H.pdf_expansion_problem(ok_pdf) is None
    big = tmp_path / "big.pdf"; make_pdf(str(big), 300)
    t = time.perf_counter(); assert H.pdf_expansion_problem(str(big)) is None
    assert time.perf_counter() - t < 1.0


def test_a_stream_just_under_the_cap_passes(tmp_path):
    p = pdf_with([(FLATE, zeros(100 << 20))], tmp_path / "under.pdf")     # 100 MiB < 128 MiB
    assert H.pdf_expansion_problem(p) is None


def test_many_moderate_streams_trip_the_total_cap(tmp_path, monkeypatch):
    monkeypatch.setattr(H, "BOMB_TOTAL_MAX", 64 << 20)
    p = pdf_with([(FLATE, zeros(20 << 20)) for _ in range(5)], tmp_path / "many.pdf")   # 5 x 20 MiB, each under the stream cap
    assert H.pdf_expansion_problem(p)


def test_a_bomb_with_an_image_procset_is_not_waved_through(tmp_path):
    # the substring "/Image" appears in /ProcSet [/ImageC]; only /Subtype /Image makes a stream an image
    p = pdf_with([(b"/Subtype/Form/Resources<</ProcSet[/PDF/ImageC]>>" + FLATE, zeros(300 << 20))], tmp_path / "dressed.pdf")
    assert H.pdf_expansion_problem(p)




def test_garbage_truncated_and_missing_never_raise(tmp_path):
    g = pdf_with([(FLATE, b"not zlib at all")], tmp_path / "g.pdf")
    assert H.pdf_expansion_problem(g) is None
    t = pdf_with([(FLATE, zeros(1 << 20)[:100])], tmp_path / "t.pdf")
    assert H.pdf_expansion_problem(t) is None
    (tmp_path / "empty.pdf").write_bytes(b"")
    assert H.pdf_expansion_problem(str(tmp_path / "empty.pdf")) is None
    assert H.pdf_expansion_problem(str(tmp_path / "nope.pdf")) is None


def test_result_is_cached_and_invalidated_when_the_file_changes(ok_pdf, monkeypatch):
    H._BOMB_CACHE.clear()
    calls = []
    real = H._scan_for_bombs
    monkeypatch.setattr(H, "_scan_for_bombs", lambda d, n, *a: (calls.append(n), real(d, n, *a))[1])
    H.pdf_expansion_problem(ok_pdf); H.pdf_expansion_problem(ok_pdf)
    assert len(calls) == 1
    with open(ok_pdf, "ab") as f:
        f.write(b"\n% changed")
    H.pdf_expansion_problem(ok_pdf)
    assert len(calls) == 2


# ---- every site that makes a library inflate a stream must be guarded -------------------------------------
def test_search_refuses(bomb):
    with pytest.raises(H.PdfOpError, match="enormous size"):
        H.search_pdf_text(bomb, "abc")


def test_profile_and_inspect_refuse_from_path_and_from_bytes(bomb):
    with pytest.raises(H.PdfOpError, match="enormous size"):
        H.profile_pdf(bomb)
    with pytest.raises(H.PdfOpError, match="enormous size"):
        H.profile_pdf(bomb, data=open(bomb, "rb").read())
    with pytest.raises(H.PdfOpError, match="enormous size"):
        H.inspect_pdf(bomb)


def test_flatten_refuses(bomb, tmp_path):
    with pytest.raises(H.PdfOpError, match="enormous size"):
        H.flatten_to_images(bomb)


def test_peek_reports_instead_of_decoding(bomb):
    r = H.peek_file(bomb)
    assert r["text"] == [] and "enormous size" in r["error"]


def test_thumbnail_worker_fails_with_the_message(app, bomb):
    w = H._ThumbWorker(bomb, 120, 1)
    got = []
    w.failed.connect(lambda tok, msg: got.append(msg)); w.total.connect(lambda *_: got.append("TOTAL"))
    with Alarm(10):
        w.run()
    assert got and "TOTAL" not in got and "enormous size" in got[0]


def test_ordinary_files_still_work_everywhere(ok_pdf):
    assert H.search_pdf_text(ok_pdf, "x")["hits"] == 0
    assert H.profile_pdf(ok_pdf).pages == 5
    assert H.peek_file(ok_pdf)["pages"] == 5


def test_panel_search_marks_the_bomb_failed_and_keeps_going(app, tmp_path, bomb):
    d = tmp_path / "mix"; d.mkdir()
    make_pdf(str(d / "a_ok.pdf"), 2)
    (d / "z_bomb.pdf").write_bytes(open(bomb, "rb").read())
    p = H.OrganizerPanel(); p.show()
    try:
        p.folder_row.set_path(str(d))
        assert pump(app, lambda: p.file_list.count() == 2 and len(p._counts) == 2)
        p.search_edit.setText("abc"); p.run_search()
        assert pump(app, lambda: len(p._matches) == 2, secs=20)
        by = {os.path.basename(k): v for k, v in p._matches.items()}
        assert by["z_bomb.pdf"].get("error") and not by["a_ok.pdf"].get("error")
        assert "search failed" in p.file_list.item(1).text()
    finally:
        p.stop_for_shutdown(); p.close()


def test_inflation_stops_one_step_past_the_cap_instead_of_finishing_the_lie(monkeypatch):
    produced = []
    real = zlib.decompressobj
    class Counting:
        def __init__(self): self.o = real()
        def decompress(self, b, n=0):
            out = self.o.decompress(b, n); produced.append(len(out)); return out
        @property
        def unconsumed_tail(self): return self.o.unconsumed_tail
        @property
        def eof(self): return self.o.eof
    monkeypatch.setattr(H.zlib, "decompressobj", Counting)
    n = H._inflate_capped(zeros(64 << 20), 0, 4 << 20)
    assert n > 4 << 20                                   # over the cap: reported as such
    assert sum(produced) <= (4 << 20) + (1 << 20)        # ...but only one 1 MB step past it was ever produced


# ---- bypasses found by the 1.4.3 review panel: each of these once returned None for a 256 MiB bomb ------------
import base64, binascii


def bomb_bytes(mb=300):
    return zeros(mb << 20)


@pytest.mark.parametrize("name,body", [
    ("obj_word_in_dict", b"/Filter/FlateDecode/X(obj)"),
    ("dict_over_4096_bytes", b"/Filter/FlateDecode/P(" + b"a" * 6000 + b")"),
    ("nested_subtype_image", b"/Filter/FlateDecode/Meta<</Subtype/Image>>"),
    ("nested_subtype_image_indirect_length", b"/Filter/FlateDecode/Meta<</Subtype/Image/Width 1/Height 1>>"),
    ("name_escape", b"/Filter/Flate#44ecode"),
    ("filter_array", b"/Filter[/FlateDecode]"),
    ("spaced_filter", b"/Filter  /FlateDecode"),
    ("abbreviation", b"/Filter/Fl"),
])
def test_dictionary_disguises_do_not_hide_a_bomb(tmp_path, name, body):
    length = b"9 0 R" if "indirect" in name or "nested" in name else None
    item = (body, bomb_bytes()) if length is None else (body, bomb_bytes(), length)
    assert H.pdf_expansion_problem(pdf_with([item], tmp_path / (name + ".pdf"))), name


def test_ascii85_and_hex_wrapped_bombs_are_unwrapped(tmp_path):
    z = bomb_bytes()
    a85 = base64.a85encode(z, adobe=True)                                  # <~ ... ~>
    assert H.pdf_expansion_problem(pdf_with([(b"/Filter[/ASCII85Decode/FlateDecode]", a85)], tmp_path / "a85.pdf"))
    hx = binascii.hexlify(z) + b">"
    assert H.pdf_expansion_problem(pdf_with([(b"/Filter[/ASCIIHexDecode/FlateDecode]", hx)], tmp_path / "hex.pdf"))


def test_a_forged_endstream_or_length_does_not_truncate_the_scan(tmp_path):
    z = bomb_bytes()
    # the real bomb is preceded by a decoy 'endstream' inside the data and /Length lies (indirect and too small)
    p = pdf_with([(b"/Filter/FlateDecode", b"endstream" * 0 + z)], tmp_path / "forged.pdf")
    raw = open(p, "rb").read().replace(b"/Length %d" % len(z), b"/Length 7 0 R")
    open(p, "wb").write(raw)
    assert H.pdf_expansion_problem(p)
    p2 = pdf_with([(FLATE, z)], tmp_path / "noend.pdf")
    raw2 = open(p2, "rb").read().replace(b"endstream", b"endstrXam")                  # no endstream at all
    open(p2, "wb").write(raw2)
    assert H.pdf_expansion_problem(p2)


@pytest.mark.parametrize("eol", [b"\r\n", b"\r", b"\n", b" \n", b"  \r\n", b"\t\n", b" \r"])
def test_every_legal_line_ending_after_the_stream_keyword(tmp_path, eol):
    p = pdf_with([(FLATE, bomb_bytes())], tmp_path / "eol.pdf")
    raw = open(p, "rb").read().replace(b">>\nstream\n", b">>\nstream" + eol)
    open(p, "wb").write(raw)
    assert H.pdf_expansion_problem(p)


def test_stream_cap_boundary_is_128_mib(tmp_path):
    half = 512 << 10
    assert H.pdf_expansion_problem(pdf_with([(FLATE, zeros((127 << 20) + half))], tmp_path / "127h.pdf")) is None
    assert H.pdf_expansion_problem(pdf_with([(FLATE, zeros((128 << 20) + half))], tmp_path / "128h.pdf"))


def test_the_stream_cap_is_per_stream_not_per_file(tmp_path):
    p = pdf_with([(FLATE, zeros(100 << 20)) for _ in range(4)], tmp_path / "four100.pdf")      # 400 MiB in all, none over 128
    assert H.pdf_expansion_problem(p) is None


def test_bytes_read_count_toward_the_budget(tmp_path, monkeypatch):
    monkeypatch.setattr(H, "BOMB_TOTAL_MAX", 1 << 20)
    hostile = b"%PDF-1.7\n" + b"".join(b"stream\nx\x9c" + b"\xff" * 4096 for _ in range(300))   # valid zlib header, then junk
    (tmp_path / "reads.pdf").write_bytes(hostile)
    assert H.pdf_expansion_problem(str(tmp_path / "reads.pdf"))


def test_ascii_decoding_is_a_bounded_prefix(monkeypatch):
    import base64
    text = base64.a85encode(os.urandom(6 << 20), adobe=False) + b"~>"
    stats = H._BombStats()
    t = time.perf_counter(); body = H._ascii_to_bytes(text, 0, "a85", stats); dt = time.perf_counter() - t
    assert body is not None and len(text) - 0 > (6 << 20)
    assert stats.ascii_left >= (2 << 20) - (1 << 20)                  # one call may take at most 1 MiB of text
    assert dt < 2.0
    stats.ascii_left = 0
    assert H._ascii_to_bytes(text, 0, "a85", stats) is None           # budget spent: stop decoding


IMG = b"/Subtype/Image/Width %d/Height %d/BitsPerComponent 8/ColorSpace/DeviceRGB"


def test_a_big_honest_image_passes_and_a_huge_one_is_refused_by_what_it_inflates_to(tmp_path):
    ok = pdf_with([(IMG % (5100, 6600) + FLATE, zeros(100 << 20))], tmp_path / "scan600.pdf")   # a 600 dpi colour page (101 MB)
    assert H.pdf_expansion_problem(ok) is None
    big = pdf_with([(IMG % (30000, 30000) + FLATE, zeros(300 << 20))], tmp_path / "big.pdf")
    assert H.pdf_expansion_problem(big)


def test_a_content_stream_dressed_as_an_image_is_still_a_bomb(tmp_path):
    # the dictionary says Subtype/Image and claims 10000x10000; libraries ignore that on a page's Contents
    p = pdf_with([(IMG % (10000, 10000) + FLATE, bomb_bytes(), b"9 0 R")], tmp_path / "dressed_img.pdf")
    assert H.pdf_expansion_problem(p)


def test_non_flate_images_are_never_judged_by_length(tmp_path):
    jpeg = pdf_with([(b"/Subtype/Image/Width 100/Height 100/BitsPerComponent 8/Filter/DCTDecode", b"\xff\xd8" + b"x" * 40000)],
                    tmp_path / "jpg.pdf")
    assert H.pdf_expansion_problem(jpeg) is None


def test_cache_key_has_size_and_mtime_and_is_bounded(tmp_path, ok_pdf, monkeypatch):
    H._BOMB_CACHE.clear()
    a = tmp_path / "a.pdf"; a.write_bytes(open(ok_pdf, "rb").read())
    assert H.pdf_expansion_problem(str(a)) is None
    st = os.stat(a)
    a.write_bytes(open(pdf_with([(FLATE, bomb_bytes())], tmp_path / "tmp.pdf"), "rb").read())
    os.utime(a, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))                            # new content, new mtime
    assert H.pdf_expansion_problem(str(a))                                                      # not served from the old verdict
    H._BOMB_CACHE.clear()
    for i in range(520):
        H._BOMB_CACHE[("k", i, 0)] = None
    H.pdf_expansion_problem(ok_pdf)
    assert len(H._BOMB_CACHE) <= 512


def encrypt(src, dst, pikepdf):
    with pikepdf.open(src) as pdf:
        pdf.save(dst, encryption=pikepdf.Encryption(owner="o", user="", R=4))


def test_an_encrypted_bomb_is_read_decrypted_and_refused(tmp_path, bomb):
    import pikepdf
    dst = str(tmp_path / "enc_bomb.pdf"); encrypt(bomb, dst, pikepdf)
    raw = open(dst, "rb").read()
    assert H.pdf_expansion_problem(dst)                      # ciphertext alone would have looked like noise
    with pytest.raises(H.PdfOpError, match="enormous size"):
        H.search_pdf_text(dst, "abc")


def test_an_ordinary_encrypted_pdf_still_works(tmp_path, ok_pdf):
    import pikepdf
    dst = str(tmp_path / "enc_ok.pdf"); encrypt(ok_pdf, dst, pikepdf)
    assert H.pdf_expansion_problem(dst) is None
    assert H.search_pdf_text(dst, "abc")["hits"] == 0


def page_with_image(path, img_dict, data):
    objs = [b"<</Type/Catalog/Pages 2 0 R>>", b"<</Type/Pages/Kids[3 0 R]/Count 1>>",
            b"<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]/Contents 5 0 R/Resources<</XObject<</Im0 4 0 R>>>>>>",
            b"<<" + img_dict + b"/Length %d>>\nstream\n" % len(data) + data + b"\nendstream",
            b"<</Length 27>>\nstream\nq 500 0 0 500 0 0 cm /Im0 Do Q\nendstream"]
    out = bytearray(b"%PDF-1.5\n"); offs = []
    for i, o in enumerate(objs, 1):
        offs.append(len(out)); out += b"%d 0 obj\n" % i + o + b"\nendobj\n"
    x = len(out); out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    for o in offs:
        out += b"%010d 00000 n \n" % o
    out += b"trailer\n<</Size %d/Root 1 0 R>>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, x)
    open(path, "wb").write(bytes(out)); return str(path)


def test_image_recompression_skips_an_image_that_lies_about_its_size(tmp_path, monkeypatch):
    import pikepdf
    # 101x101 RGB declares ~40 KB but carries 100 MiB: under the pre-flight's 256 MiB cap, so it is let through;
    # the step about to DECODE the image checks it against its own pixels first.
    liar = page_with_image(tmp_path / "liar.pdf",
                           b"/Type/XObject/Subtype/Image/Width 101/Height 101/BitsPerComponent 8/ColorSpace/DeviceRGB" + FLATE,
                           zeros(100 << 20))
    assert H.pdf_expansion_problem(liar) is None
    decoded = []
    real = pikepdf.PdfImage
    monkeypatch.setattr(pikepdf, "PdfImage", lambda o: (decoded.append(1), real(o))[1])
    notes = []
    with pikepdf.open(liar) as pdf, Alarm(60):
        H._recompress_images(pdf, {"dpi": 100, "quality": 40}, notes)
    assert decoded == []                                                  # never asked pikepdf to inflate it
    assert any("more data than its size allows" in n for n in notes), notes


def test_the_size_check_itself(tmp_path):
    import pikepdf
    liar = page_with_image(tmp_path / "l2.pdf", b"/Type/XObject/Subtype/Image/Width 1000/Height 1000/BitsPerComponent 8/ColorSpace/DeviceRGB" + FLATE, zeros(64 << 20))
    honest = page_with_image(tmp_path / "h2.pdf", b"/Type/XObject/Subtype/Image/Width 1000/Height 1000/BitsPerComponent 8/ColorSpace/DeviceRGB" + FLATE,
                             zlib.compress(b"\x80" * (1000 * 1000 * 3)))
    for path, want in ((liar, False), (honest, True)):
        with pikepdf.open(path) as pdf:
            obj = pdf.objects[3]
            assert H._image_data_matches_its_size(obj, 1000, 1000) is want, path


def test_cache_key_needs_both_size_and_mtime(tmp_path, ok_pdf, monkeypatch):
    H._BOMB_CACHE.clear()
    calls = []
    real = H._scan_for_bombs
    monkeypatch.setattr(H, "_scan_for_bombs", lambda d, n, *a: (calls.append(n), real(d, n, *a))[1])
    f = tmp_path / "k.pdf"; raw = open(ok_pdf, "rb").read(); f.write_bytes(raw)
    H.pdf_expansion_problem(str(f)); H.pdf_expansion_problem(str(f))
    assert len(calls) == 1
    st = os.stat(f)
    f.write_bytes(raw[:-1] + b"X")                                       # same size, new content ...
    os.utime(f, ns=(st.st_atime_ns, st.st_mtime_ns + 7_000_000_000))     # ... and a new mtime
    H.pdf_expansion_problem(str(f))
    assert len(calls) == 2                                               # mtime alone must invalidate
    st = os.stat(f)
    f.write_bytes(raw + b"\n")                                           # new size ...
    os.utime(f, ns=(st.st_atime_ns, st.st_mtime_ns))                     # ... same mtime as before
    H.pdf_expansion_problem(str(f))
    assert len(calls) == 3                                               # size alone must invalidate


# ---- the second adversarial review: each of these returned None for a bomb costing libraries 3-20 s and 3-4 GB ---
def test_stacked_flate_layers_are_followed(tmp_path):
    inner = zeros(300 << 20)
    outer = zlib.compress(inner, 9)                       # a few KB; its first layer inflates to only ~0.3 MB
    assert len(outer) < 20000
    assert H.pdf_expansion_problem(pdf_with([(b"/Filter[/FlateDecode/FlateDecode]", outer)], tmp_path / "stack.pdf"))
    assert H.pdf_expansion_problem(pdf_with([(b"/Filter/FlateDecode", zlib.compress(outer, 9))], tmp_path / "stack3.pdf"))


@pytest.mark.parametrize("name,body", [
    ("decoy_filter_in_string_first", b"/Foo(/Filter /LZWDecode)/Filter/FlateDecode"),
    ("angle_brackets_in_string_after", b"/Filter/FlateDecode/Foo(<<)"),
    ("dictionary_over_64k", b"/Filter/FlateDecode/Pad[" + b"0 " * 40000 + b"]"),
    ("crypt_then_flate", b"/Filter[/Crypt/FlateDecode]"),
    ("indirect_filter", b"/Filter 7 0 R"),
])
def test_dictionary_tricks_cannot_hide_a_bomb(tmp_path, name, body):
    assert H.pdf_expansion_problem(pdf_with([(body, bomb_bytes(), b"9 0 R")], tmp_path / (name + ".pdf"))), name


def lzw_bomb(total_mb):
    """PDF LZW (MSB first, EarlyChange 1) of zeros: code 0, then 258, 259, ... each one char longer, until the
    4096-entry table is full; the clear code is written at the CURRENT width; repeat."""
    acc, nb, out = 0, 0, bytearray()

    def put(code, width):
        nonlocal acc, nb
        acc = (acc << width) | code; nb += width
        while nb >= 8:
            out.append((acc >> (nb - 8)) & 255); nb -= 8; acc &= (1 << nb) - 1

    produced, width = 0, 9
    while produced < total_mb << 20:
        put(256, width); width = 9; table = 258
        put(0, width); produced += 1; length = 1
        while table < 4094:
            put(258 if table == 258 else table, width)         # the entry added by the previous code
            length += 1; produced += length; table += 1
            width = 9 if table + 1 < 512 else 10 if table + 1 < 1024 else 11 if table + 1 < 2048 else 12
    put(257, width); put(0, 7)
    return bytes(out)


def test_lzw_bombs_are_counted(tmp_path):
    z = lzw_bomb(300)
    assert len(z) < 1_000_000
    stats = H._BombStats(); stats.ascii_left = 0
    with pytest.raises(H._BombOver):
        H._lzw_count(z, 0, stats)
    assert H.pdf_expansion_problem(pdf_with([(b"/Filter/LZWDecode", z)], tmp_path / "lzw.pdf"))
    small = H._BombStats()
    H._lzw_count(lzw_bomb(20), 0, small)                          # pypdf's own decoder gives 22.1 MB for this stream
    assert (19 << 20) <= small.total <= (23 << 20), small.total


def test_encrypted_marker_far_from_the_file_ends(tmp_path, bomb):
    import pikepdf
    enc = str(tmp_path / "e.pdf"); encrypt(bomb, enc, pikepdf)
    raw = open(enc, "rb").read()
    i = raw.rindex(b"startxref")
    open(enc, "wb").write(raw[:i] + b"%" + b"j" * 100000 + b"\n" + raw[i:])      # /Encrypt now 100 KB from the end
    assert H.pdf_expansion_problem(enc)


def test_scan_time_stays_linear_on_hostile_structure(tmp_path):
    for name, blob in (("markers", b"%PDF-1.7\n" + b">>stream\n" * 100000),
                       ("lzw_hint", b"%PDF-1.7\n" + b"/LZWDecode" + b">>stream\n" * 100000)):
        (tmp_path / (name + ".pdf")).write_bytes(blob)
        with Alarm(20):
            t = time.perf_counter(); H.pdf_expansion_problem(str(tmp_path / (name + ".pdf"))); dt = time.perf_counter() - t
        assert dt < 5.0, (name, dt)
    many = b"%PDF-1.7\n" + b"".join(b"%d 0 obj\n<</Filter/ASCIIHexDecode>>\nstream\n" % i + b"00" * 2000 + b">\nendstream\nendobj\n" for i in range(3000))
    (tmp_path / "ahx.pdf").write_bytes(many)
    with Alarm(20):
        t = time.perf_counter(); H.pdf_expansion_problem(str(tmp_path / "ahx.pdf")); dt = time.perf_counter() - t
    assert dt < 5.0, dt


def test_an_ordinary_ascii85_scan_file_is_not_slow_or_refused():
    f = "/tmp/perf/scan20.pdf"
    if not os.path.exists(f):
        pytest.skip("needs the 52 MB perf fixture")
    with open(f, "rb") as fh:                   # warm the file cache: this times the scan, not a cold network disk
        while fh.read(1 << 22):
            pass
    H._BOMB_CACHE.clear()
    t = time.perf_counter(); assert H.pdf_expansion_problem(f) is None
    assert time.perf_counter() - t < 4.0


def pack_codes(pairs):
    acc, nb, out = 0, 0, bytearray()
    for code, width in pairs:
        acc = (acc << width) | code; nb += width
        while nb >= 8:
            out.append((acc >> (nb - 8)) & 255); nb -= 8; acc &= (1 << nb) - 1
    if nb:
        out.append((acc << (8 - nb)) & 255)
    return bytes(out)


def test_lzw_counter_matches_an_independent_decoder_across_a_clear_code():
    import warnings
    import pypdf.filters as F
    # cycle 1 grows the table (258=2 bytes, 259=3, 260=4); after the clear, 259 means something different
    z = pack_codes([(256, 9), (0, 9), (258, 9), (259, 9), (260, 9),
                    (256, 9), (5, 9), (6, 9), (7, 9), (259, 9), (8, 9), (257, 9)])
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        want = len(F.LZWDecode.decode(z))
    stats = H._BombStats(); H._lzw_count(z, 0, stats)
    assert stats.stream == want, (stats.stream, want)


def test_ascii_budget_is_spent_across_a_file():
    import base64
    text = base64.a85encode(os.urandom(1 << 20), adobe=False) + b"~>"
    stats = H._BombStats()
    results = [H._ascii_to_bytes(text, 0, "a85", stats) is not None for _ in range(5)]
    assert results[0] and not results[-1], results                      # 2 MiB of budget, then it stops
    assert stats.ascii_left < 64


def shared_contents_pdf(path, pages, mib):
    """`pages` pages whose /Contents is ONE stream that inflates to `mib` MiB: a library inflates it once per page."""
    data = zeros(mib << 20)
    kids = b" ".join(b"%d 0 R" % (5 + i) for i in range(pages))
    objs = [b"<</Type/Catalog/Pages 2 0 R>>", b"<</Type/Pages/Kids[" + kids + b"]/Count %d>>" % pages, b"<<>>",
            b"<</Filter/FlateDecode/Length %d>>\nstream\n" % len(data) + data + b"\nendstream"]
    objs += [b"<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]/Contents 4 0 R/Resources<<>>>>"] * pages
    out = bytearray(b"%PDF-1.5\n"); offs = []
    for i, o in enumerate(objs, 1):
        offs.append(len(out)); out += b"%d 0 obj\n" % i + o + b"\nendobj\n"
    xr = len(out); out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    for o in offs:
        out += b"%010d 00000 n \n" % o
    out += b"trailer\n<</Size %d/Root 1 0 R>>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xr)
    open(path, "wb").write(bytes(out)); return str(path)


def test_one_big_stream_shared_by_many_pages_is_charged_per_page(tmp_path):
    assert H.pdf_expansion_problem(shared_contents_pdf(tmp_path / "s5.pdf", 5, 100)) is None        # 5 x 100 MiB: ~1 s of library work
    assert H.pdf_expansion_problem(shared_contents_pdf(tmp_path / "s40.pdf", 40, 100))              # 40 x 100 MiB: ~16 s
    assert H.pdf_expansion_problem(shared_contents_pdf(tmp_path / "small40.pdf", 40, 20)) is None   # small shared stream: fine


def test_many_distinct_big_streams_hit_the_two_gib_budget(tmp_path, monkeypatch):
    monkeypatch.setattr(H, "BOMB_TOTAL_MAX", 300 << 20)
    assert H.pdf_expansion_problem(pdf_with([(FLATE, zeros(100 << 20)) for _ in range(4)], tmp_path / "four.pdf"))


def _shared_check(biggest, data):
    st = H._BombStats()
    st.biggest = biggest
    try:
        H._check_shared(data, st)
        return False
    except H._BombOver:
        return True


def test_shared_charge_boundary_is_exactly_the_two_gib_constant():
    assert H.BOMB_TOTAL_MAX == 2 << 30
    mib = 100 << 20
    assert not _shared_check(mib, b"/Count 20")      # 2000 MiB < 2048 MiB
    assert _shared_check(mib, b"/Count 21")          # 2100 MiB > 2048 MiB


def test_absurd_count_is_ignored_not_trusted():
    assert not _shared_check(100 << 20, b"/Count 99999999999")
    assert _shared_check(100 << 20, b"/Count 999999")  # 6 digits is still believed
