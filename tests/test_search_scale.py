"""Page Organizer search results: O(1) per result, and exactly the same rows/colours/visibility as the
brute-force definition. (1.4.2 scanned the whole list twice per result: 12.7 s at 1,500 PDFs.)"""
import os, signal, sys, time
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import pytest
import acrod as H
from test_organizer_panel import app, _iso, pump  # noqa: F401


def make_panel(app, folder, n, tag="d"):
    folder.mkdir(exist_ok=True)
    for i in range(n):
        (folder / f"{tag}{i:05d}.pdf").write_bytes(b"x" * 2048)
    p = H.OrganizerPanel(); p.show()
    p.folder_row.set_path(str(folder))
    assert pump(app, lambda: p.file_list.count() == n and len(p._counts) == n, secs=60)
    return p


def paths_of(p):
    return [p._row_path(p.file_list.item(i)) for i in range(p.file_list.count())]


def res(i):
    return {"hits": (i % 3 == 0) * 2, "pages": [0, 1] if i % 3 == 0 else [], "has_text": i % 3 == 0 or i % 11 != 0, "error": None}


def start(p, gen=7):
    p._search_text, p._search_gen, p._matches = "abc", gen, {}


def expected(p):
    """The definition, brute force: what every row must show given p._matches."""
    only = p.only_matches.isChecked() and bool(p._search_text)
    out = []
    for i in range(p.file_list.count()):
        it = p.file_list.item(i)
        m = p._matches.get(p._row_path(it))
        out.append(bool(only and not (m and m["hits"])))
    return out


def hidden(p):
    return [p.file_list.item(i).isHidden() for i in range(p.file_list.count())]


def colours(p):
    return [p.file_list.item(i).foreground().color().name() for i in range(p.file_list.count())]


@pytest.fixture
def panel(app, tmp_path):
    p = make_panel(app, tmp_path / "f", 60)
    yield p
    p.stop_for_shutdown(); p.close()


@pytest.mark.parametrize("only", [True, False])
def test_every_row_matches_the_definition_after_results_in_any_order(panel, only):
    panel.only_matches.setChecked(only)
    start(panel)
    ps = paths_of(panel)
    order = list(range(len(ps)))[::-1]                        # reverse: not row order
    for i in order:
        panel._on_search_result(7, ps[i], res(i))
        assert hidden(panel) == expected(panel)               # after EVERY result, not just the last
    for i, it in enumerate(panel.file_list.item(k) for k in range(len(ps))):
        assert ("match" in it.text()) == bool(res(i)["hits"])
    acc = H.QColor(H.theme_color("accent")).name()
    assert [c == acc for c in colours(panel)] == [bool(res(i)["hits"]) for i in range(len(ps))]


def test_duplicate_result_for_one_path_is_harmless(panel):
    panel.only_matches.setChecked(True)
    start(panel)
    ps = paths_of(panel)
    panel._on_search_result(7, ps[3], res(3)); panel._on_search_result(7, ps[3], res(3))
    panel._on_search_result(7, ps[4], res(4)); panel._on_search_result(7, ps[3], res(3))
    assert hidden(panel) == expected(panel)


def test_stale_generation_is_ignored(panel):
    start(panel)
    ps = paths_of(panel)
    panel._on_search_result(6, ps[0], res(0))
    assert panel._matches == {} and not any("match" in panel.file_list.item(i).text() for i in range(60))


def test_toggling_show_only_matches_mid_search_then_more_results(panel):
    start(panel)
    ps = paths_of(panel)
    for i in range(20):
        panel._on_search_result(7, ps[i], res(i))
    panel.only_matches.setChecked(True)
    assert hidden(panel) == expected(panel)
    for i in range(20, 40):
        panel._on_search_result(7, ps[i], res(i))
        assert hidden(panel) == expected(panel)
    panel.only_matches.setChecked(False)
    assert not any(hidden(panel))


def test_index_is_rebuilt_for_a_new_folder(app, panel, tmp_path):
    panel.only_matches.setChecked(True)
    other = tmp_path / "g"; other.mkdir()
    for i in range(7):
        (other / f"z{i}.pdf").write_bytes(b"x" * 100)
    panel.folder_row.set_path(str(other))
    assert pump(app, lambda: panel.file_list.count() == 7 and len(panel._counts) == 7)
    assert set(panel._row_of) == set(paths_of(panel))             # no entries left from the old folder
    start(panel)
    panel._on_search_result(7, str(tmp_path / "f" / "d00001.pdf"), res(0))   # old folder: no row, no crash
    ps = paths_of(panel)
    for i, q in enumerate(ps):
        panel._on_search_result(7, q, res(i))
        assert hidden(panel) == expected(panel)
    assert "matches" in panel.file_list.item(0).text()


def test_1500_results_stay_linear(app, tmp_path):
    p = make_panel(app, tmp_path / "big", 1500)
    try:
        p.only_matches.setChecked(True)
        start(p)
        ps = paths_of(p)
        signal.signal(signal.SIGALRM, lambda *_: (_ for _ in ()).throw(TimeoutError("search handling is quadratic again")))
        signal.alarm(20)
        t0 = time.perf_counter(); worst = 0.0
        for i, q in enumerate(ps):
            t = time.perf_counter(); p._on_search_result(7, q, res(i)); worst = max(worst, time.perf_counter() - t)
        total = time.perf_counter() - t0
        signal.alarm(0)
        assert hidden(p) == expected(p)
        assert total < 2.0, total                              # 1.4.2: 12.7 s; 1.4.3: ~0.04 s
        assert worst < 0.25, worst                             # no single UI turn is a visible freeze
    finally:
        signal.alarm(0)
        p.stop_for_shutdown(); p.close()
