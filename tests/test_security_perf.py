"""Security and speed regressions found in the 1.4.1 audit."""
import os, sys, time
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import pytest
import acrod as H
from test_organizer_panel import app, _iso  # noqa: F401


@pytest.mark.parametrize("pat", [r"(a+)+$", r"(a*)*b", r"(a|aa)+$", r"(x+x+)+y", r"(\w+\s?)*$"])
def test_find_replace_refuses_exponential_patterns_fast(tmp_path, pat):
    f = tmp_path / ("a" * 40 + "!.pdf"); f.write_bytes(b"")
    import signal
    def _hang(*_):
        raise AssertionError("the pattern ran and did not return: the freeze is back")
    old = signal.signal(signal.SIGALRM, _hang); signal.alarm(5)
    try:
        t = time.perf_counter()
        with pytest.raises(H.RenameRuleError, match="freeze"):
            H.build_plan([str(f)], H.RenameRule(search=pat, replace="x", use_regex=True))
        assert time.perf_counter() - t < 0.5        # refused up front, never run
    finally:
        signal.alarm(0); signal.signal(signal.SIGALRM, old)


@pytest.mark.parametrize("pat", [r"(?<=_)\d+", r"^(\d+)-(\w+)", r"(foo|bar)", r"\d{4}-\d{2}", r"(.)\1", r"[ab]+$"])
def test_find_replace_keeps_ordinary_patterns(tmp_path, pat):
    f = tmp_path / "x_1234-ab foo.pdf"; f.write_bytes(b"")
    H.build_plan([str(f)], H.RenameRule(search=pat, replace="Z", use_regex=True))   # must not raise


def test_catastrophic_gate_can_pass_and_fail():
    assert H.catastrophic_regex(r"(a+)+") and H.catastrophic_regex(r"(a|b)*")
    assert H.catastrophic_regex(r"(a)+") is None and H.catastrophic_regex(r"a+b+") is None


@pytest.mark.parametrize("v,out", [("=HYPERLINK(\"x\")", "'=HYPERLINK(\"x\")"), ("@SUM(A1)", "'@SUM(A1)"),
                                   ("+1", "'+1"), ("-2", "'-2"), ("\tx", "'\tx"),
                                   ("normal.pdf", "normal.pdf"), ("", ""), ("a=b", "a=b")])
def test_csv_cells_cannot_start_a_formula(v, out):
    assert H.csv_safe(v) == out


def test_open_file_refuses_to_run_code(tmp_path, monkeypatch):
    opened, shown = [], []
    monkeypatch.setattr(H.QDesktopServices, "openUrl", lambda u: opened.append(u.toLocalFile()) or True)
    monkeypatch.setattr(H, "open_in_explorer", lambda p: shown.append(p) or True)
    for name in ("setup.EXE", "x.bat", "a.js", "l.lnk", "m.docm"):
        assert H.open_file(str(tmp_path / name)) is False
    assert not opened and len(shown) == 5                      # shown in the folder instead
    assert H.open_file(str(tmp_path / "scan.pdf")) and H.open_file(str(tmp_path / "pic.PNG"))
    assert [os.path.basename(o) for o in opened] == ["scan.pdf", "pic.PNG"]


# ---- page render size ----------------------------------------------------
def test_render_scale_caps_the_bitmap():
    import math
    s = H.render_scale(2, 14400, 150, H.THUMB_MAX_PX, H.THUMB_MAX_SIDE)
    assert 2 * s * 14400 * s <= H.THUMB_MAX_PX * 1.01 or 14400 * s <= H.THUMB_MAX_SIDE * 2
    assert 2 * s >= 0.99                                          # still at least one pixel wide
    assert H.render_scale(612, 792, 0.5, H.THUMB_MAX_PX, H.THUMB_MAX_SIDE) == 0.5   # normal page untouched
    big = H.render_scale(14400, 14400, 120 / 72, H.FLATTEN_MAX_PX, H.FLATTEN_MAX_SIDE)
    assert 14400 * big <= H.FLATTEN_MAX_SIDE and (14400 * big) ** 2 <= H.FLATTEN_MAX_PX * 1.01
    wide = H.render_scale(14400, 720, 120 / 72, H.FLATTEN_MAX_PX, H.FLATTEN_MAX_SIDE)   # the side cap binds, not pixels
    assert 14400 * wide <= H.FLATTEN_MAX_SIDE * 1.001


def test_a_hostile_page_shape_renders_small_and_fast(tmp_path):
    f = tmp_path / "tall.pdf"
    f.write_bytes(b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
                  b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
                  b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 2 14400]/Resources<<>>>>endobj\n"
                  b"trailer<</Root 1 0 R>>\n%%EOF")
    from PySide6.QtCore import QCoreApplication
    got = []
    w = H._ThumbWorker(str(f), 300, 1)
    w.thumb.connect(lambda t, i, img, blank: got.append(img))
    w.failed.connect(lambda t, m: got.append(m))
    import signal
    def _hang(*_):
        raise AssertionError("thumbnail asked for a giant bitmap")
    old = signal.signal(signal.SIGALRM, _hang); signal.alarm(8)
    try:
        t = time.perf_counter(); w.run()
    finally:
        signal.alarm(0); signal.signal(signal.SIGALRM, old)
    assert time.perf_counter() - t < 2
    assert got and not isinstance(got[0], str), got
    assert got[0].width() * got[0].height() <= H.THUMB_MAX_PX * 1.05


def test_saved_results_csv_neutralises_formula_names(app, _iso, tmp_path, monkeypatch):
    win = H.MainWindow(); win.show(); app.processEvents()
    i = next(i for i in range(win.tabs.count()) if win.tabs.tabToolTip(i) == "PDF Optimizer")
    win.tabs.setCurrentIndex(i); app.processEvents()
    p = win.tabs.widget(i).panel
    evil = str(tmp_path / "=1+1")
    p.table.setRowCount(1)
    it = H.QTableWidgetItem("x"); it.setData(H.Qt.UserRole, evil); p.table.setItem(0, 0, it)
    p._failed = {evil: "broken"}
    out = tmp_path / "r.csv"
    monkeypatch.setattr(H.QFileDialog, "getSaveFileName", lambda *a, **k: (str(out), ""))
    p._save_results()
    first = out.read_text(encoding="utf-8-sig").splitlines()[1]
    assert first.startswith("\"'=1+1\""), first
    win.close()
