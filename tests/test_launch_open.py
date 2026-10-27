"""PDFs launched from Explorer (double-click / Open with): the app opens the first one in the
Page Organizer's reader and keeps the rest in a queue; a second launch hands its PDF to the
window that is already open. The queue is memory-only and capped."""
import os, sys, time
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QMessageBox
import acrod as H
from test_organizer_panel import app, _iso, pump  # noqa: F401
from test_reader_engine import make_text_pdf


ORIG = {"max": H.LAUNCH_QUEUE_MAX, "burst": H.LAUNCH_BURST_S, "min": H.LAUNCH_UI_MIN}


@pytest.fixture(autouse=True)
def _no_burst(monkeypatch):
    monkeypatch.setattr(H, "LAUNCH_BURST_S", 0.0)      # tests opt in to batching explicitly


@pytest.fixture
def pdfs(tmp_path):
    d = tmp_path / "in"; d.mkdir()
    for n, pages in (("a", 3), ("b", 2), ("c", 4), ("d", 1)):
        make_text_pdf(str(d / f"{n}.pdf"), pages=pages, text=n.upper())
    e = tmp_path / "other"; e.mkdir()
    make_text_pdf(str(e / "z.pdf"), pages=5, text="ZULU")
    return d


@pytest.fixture
def win(app):
    w = H.MainWindow(); w.show(); app.processEvents()
    yield w
    for _i, p in w.panels():
        if isinstance(p, H.OrganizerPanel):
            p._launch_q.clear(); p._launch_state.clear()
            p._pages = []                       # a test may leave unsaved edits; do not prompt on close
            p.stop_for_shutdown()
    w.close()


def fill(win, paths, monkeypatch):
    """Launch several PDFs as one batch (the first is shown, the rest wait), then go back to
    one-launch-at-a-time timing."""
    monkeypatch.setattr(H, "LAUNCH_BURST_S", 60.0)
    for path in paths:
        win.open_pdf_from_launch(path)
    monkeypatch.setattr(H, "LAUNCH_BURST_S", 0.0)


def org(w):
    return next(p for _i, p in w.panels() if isinstance(p, H.OrganizerPanel))


def showing(app, p, path, pages=None):
    return pump(app, lambda: p._reader_active() and p._cur_path == str(path)
                and (pages is None or len(p._pages) == pages))


# ---------------------------------------------------------------- launch plumbing
def test_launch_arg_parsing(pdfs, tmp_path):
    exe = "C:/x/AcroD.exe"
    a = lambda *x: H.launch_pdf_args([exe, *map(str, x)])
    assert a() == []
    assert a(pdfs / "a.pdf") == [str(pdfs / "a.pdf")]
    assert a(pdfs / "a.pdf", pdfs / "b.pdf", pdfs / "c.pdf") == [str(pdfs / f"{n}.pdf") for n in "abc"]   # all of them, in order
    assert a(pdfs / "nope.pdf") == []
    (tmp_path / "t.txt").write_text("x")
    assert a(tmp_path / "t.txt") == [] and a("--cli") == []


def test_requests_come_back_in_arrival_order_not_pid_order(app, monkeypatch):
    for pid, name in ((9999, "first"), (123, "second"), (50000, "third")):
        monkeypatch.setattr(H.os, "getpid", lambda pid=pid: pid)
        assert H.post_open_request(f"/p/{name}.pdf")
    assert H.take_open_requests() == ["/p/first.pdf", "/p/second.pdf", "/p/third.pdf"]


def test_watcher_survives_a_failing_request(app, win, monkeypatch):
    seen = []
    def fake(path):
        seen.append(path)
        if path.endswith("bad.pdf"):
            raise RuntimeError("boom")
    monkeypatch.setattr(win, "open_pdf_from_launch", fake)
    for n in ("bad", "good1", "good2"):
        H.post_open_request(f"/p/{n}.pdf")
    t = H.start_open_watcher(win, interval_ms=10)
    try:
        assert pump(app, lambda: len(seen) == 3)
        assert [os.path.basename(x) for x in seen] == ["bad.pdf", "good1.pdf", "good2.pdf"]
    finally:
        t.stop()


def test_shipped_limits():
    assert ORIG["max"] == 200 and 3.0 <= ORIG["burst"] <= 15.0 and ORIG["min"] == 2


def test_request_roundtrip_is_ordered_and_consumed(app):
    assert H.post_open_request("/p/one.pdf") and H.post_open_request("/p/two.pdf")
    assert H.take_open_requests() == ["/p/one.pdf", "/p/two.pdf"]
    assert H.take_open_requests() == []


def test_stale_request_is_ignored(app):
    assert H.post_open_request("/p/old.pdf")
    d = H._log_dir()
    f = next(os.path.join(d, n) for n in os.listdir(d) if n.startswith("open-request-"))
    old = time.time() - 600
    os.utime(f, (old, old))
    assert H.take_open_requests() == []
    assert not [n for n in os.listdir(d) if n.startswith("open-request-")]


def test_watcher_delivers_second_launch(app, pdfs, win):
    t = H.start_open_watcher(win, interval_ms=20)
    try:
        assert H.post_open_request(str(pdfs / "a.pdf"))
        assert pump(app, lambda: any(isinstance(p, H.OrganizerPanel) for _i, p in win.panels()))
        assert showing(app, org(win), pdfs / "a.pdf")
    finally:
        t.stop()


def test_non_pdf_is_refused_without_crash(app, tmp_path, win):
    f = tmp_path / "x.txt"; f.write_text("x")
    assert win.open_pdf_from_launch(str(f)) is False


# ---------------------------------------------------------------- the queue
def test_single_launch_opens_reader_and_shows_no_queue_ui(app, pdfs, win):
    assert win.open_pdf_from_launch(str(pdfs / "b.pdf"))
    p = org(win)
    assert win.tabs.currentWidget().panel is p
    assert showing(app, p, pdfs / "b.pdf", 2)
    assert p._launch_q == [str(pdfs / "b.pdf")] and p._launch_state[str(pdfs / "b.pdf")] == "viewed"
    assert p.launch_panel.isHidden() and p.reader.q_next_btn.isHidden()


def test_launch_while_others_wait_is_queued_not_shown(app, pdfs, win):
    P = lambda n: str(pdfs / f"{n}.pdf")
    win.open_pdf_from_launch(P("a")); p = org(win)
    assert showing(app, p, P("a"), 3)
    p._launch_q.append(P("b")); p._launch_state[P("b")] = "waiting"      # a PDF is already waiting
    assert win.open_pdf_from_launch(P("c"))
    assert p._cur_path == P("a")                                            # still on the PDF being read
    assert p._launch_q == [P("a"), P("b"), P("c")]
    assert [p._launch_state[P(n)] for n in "abc"] == ["viewed", "waiting", "waiting"]
    assert not p.launch_panel.isHidden() and "1 of 3" in p.launch_label.text()
    assert p.reader.q_label.text() == "PDF 1 of 3 · 2 waiting" and p.reader.q_next_btn.isEnabled()
    assert not p.reader.q_prev_btn.isEnabled()


def test_burst_of_launches_shows_only_the_first(app, pdfs, win, monkeypatch):
    monkeypatch.setattr(H, "LAUNCH_BURST_S", 60.0)
    P = lambda n: str(pdfs / f"{n}.pdf")
    for n in "abcd":
        win.open_pdf_from_launch(P(n))
    p = org(win)
    assert showing(app, p, P("a"), 3)
    assert [p._launch_state[P(n)] for n in "abcd"] == ["viewed", "waiting", "waiting", "waiting"]
    assert p._cur_path == P("a")


def test_next_and_previous_walk_the_queue_across_folders(app, pdfs, win, tmp_path, monkeypatch):
    P = lambda n: str(pdfs / f"{n}.pdf"); Z = str(tmp_path / "other" / "z.pdf")
    fill(win, [P("a"), P("b"), Z], monkeypatch)
    p = org(win)
    assert showing(app, p, P("a"), 3)
    p._launch_next()
    assert showing(app, p, P("b"), 2) and p._launch_pos == 1 and p._launch_state[P("b")] == "viewed"
    p._launch_next()
    assert showing(app, p, Z, 5)                                              # different folder
    assert p._launch_state[Z] == "viewed" and not p.reader.q_next_btn.isEnabled()
    p._launch_next()
    assert "last PDF" in p.status.text()
    p._launch_prev()
    assert showing(app, p, P("b"), 2)
    assert [p._launch_state[x] for x in (P("a"), P("b"), Z)] == ["viewed"] * 3   # viewed stay marked


def test_buttons_and_shortcuts_drive_the_queue(app, pdfs, win, monkeypatch):
    P = lambda n: str(pdfs / f"{n}.pdf")
    fill(win, [P(n) for n in "abc"], monkeypatch)
    p = org(win); assert showing(app, p, P("a"), 3)
    p.reader.q_next_btn.click()
    assert showing(app, p, P("b"), 2)
    QTest.keyClick(p.reader.canvas, Qt.Key_PageDown, Qt.ControlModifier)
    assert showing(app, p, P("c"), 4)
    QTest.keyClick(p.reader.canvas, Qt.Key_PageUp, Qt.ControlModifier)
    assert showing(app, p, P("b"), 2)
    p.reader.q_prev_btn.click()
    assert showing(app, p, P("a"), 3)


def test_clicking_the_list_opens_that_pdf(app, pdfs, win, monkeypatch):
    P = lambda n: str(pdfs / f"{n}.pdf")
    fill(win, [P(n) for n in "abc"], monkeypatch)
    p = org(win); assert showing(app, p, P("a"), 3)
    p.close_reader()
    p._on_launch_clicked(p.launch_list.item(2))
    assert showing(app, p, P("c"), 4)


def test_relaunching_a_listed_pdf_jumps_without_duplicating(app, pdfs, win, monkeypatch):
    P = lambda n: str(pdfs / f"{n}.pdf")
    fill(win, [P(n) for n in "abc"], monkeypatch)
    p = org(win); assert showing(app, p, P("a"), 3)
    win.open_pdf_from_launch(P("c"))
    assert len(p._launch_q) == 3
    assert showing(app, p, P("c"), 4) and p._launch_pos == 2


def test_missing_file_is_marked_and_skipped(app, pdfs, win, monkeypatch):
    P = lambda n: str(pdfs / f"{n}.pdf")
    fill(win, [P(n) for n in "abc"], monkeypatch)
    p = org(win); assert showing(app, p, P("a"), 3)
    os.remove(P("b"))
    p._launch_next()
    assert showing(app, p, P("c"), 4)
    assert p._launch_state[P("b")] == "missing"
    assert "(missing)" in p.launch_list.item(1).text()
    assert "no longer there" in p.status.text()


def test_skip_follows_the_queue_and_stops_at_the_end(app, pdfs, win, monkeypatch):
    P = lambda n: str(pdfs / f"{n}.pdf")
    fill(win, [P(n) for n in "abc"], monkeypatch)
    p = org(win); assert showing(app, p, P("a"), 3)
    p.skip_next()
    assert showing(app, p, P("b"), 2)
    p.skip_next(); assert showing(app, p, P("c"), 4)
    p.skip_next()
    assert "last PDF" in p.status.text() and p._cur_path == P("c")


def test_unsaved_changes_prompt_before_switching_and_cancel_keeps_place(app, pdfs, win, monkeypatch):
    P = lambda n: str(pdfs / f"{n}.pdf")
    fill(win, [P(n) for n in "abc"], monkeypatch)
    p = org(win); assert showing(app, p, P("a"), 3)
    assert pump(app, lambda: len(p._thumbs) == 3)
    p.grid.item(0).setSelected(True)
    p.rotate_right()
    assert p._is_dirty()
    asked = []
    monkeypatch.setattr(p, "_ask_save", lambda name: asked.append(name) or QMessageBox.Cancel)
    p._launch_next()
    assert asked == ["a.pdf"] and p._cur_path == P("a") and p._is_dirty()
    assert p._launch_pos == 0 and p._launch_state[P("b")] == "waiting"
    monkeypatch.setattr(p, "_ask_save", lambda name: QMessageBox.Discard)
    p._launch_next()
    assert showing(app, p, P("b"), 2) and not p._is_dirty()


def test_launch_during_open_prompt_is_queued_not_lost(app, pdfs, win, monkeypatch):
    P = lambda n: str(pdfs / f"{n}.pdf")
    win.open_pdf_from_launch(P("a")); p = org(win)
    assert showing(app, p, P("a"), 3)

    def ask(name):
        assert win.open_pdf_from_launch(P("d"))          # a launch arrives while the prompt is open
        return QMessageBox.Cancel
    p._launch_q.append(P("b")); p._launch_state[P("b")] = "waiting"
    assert pump(app, lambda: len(p._thumbs) == 3)
    p.grid.item(0).setSelected(True); p.rotate_right()
    monkeypatch.setattr(p, "_ask_save", ask)
    p._launch_next()
    assert P("d") in p._launch_q and p._launch_state[P("d")] == "waiting" and p._cur_path == P("a")


def test_cap_refuses_and_clear_viewed_makes_room(app, pdfs, win, monkeypatch):
    monkeypatch.setattr(H, "LAUNCH_QUEUE_MAX", 3)
    P = lambda n: str(pdfs / f"{n}.pdf")
    fill(win, [P(n) for n in "abc"], monkeypatch)
    p = org(win); assert showing(app, p, P("a"), 3)
    assert win.open_pdf_from_launch(P("d")) is False
    assert "full" in p.status.text() and len(p._launch_q) == 3
    p.launch_clear_viewed()                              # keeps the one on screen and the waiting ones
    assert p._launch_q == [P("a"), P("b"), P("c")]
    p._launch_next(); assert showing(app, p, P("b"), 2)
    p._launch_next(); assert showing(app, p, P("c"), 4)
    p.launch_clear_viewed()
    assert p._launch_q == [P("c")] and p._launch_pos == 0
    assert win.open_pdf_from_launch(P("d"))


def test_queue_is_not_kept_across_windows(app, pdfs, win, monkeypatch):
    fill(win, [str(pdfs / f"{n}.pdf") for n in "abc"], monkeypatch)
    w2 = H.MainWindow(); w2.show(); app.processEvents()
    try:
        i = next(i for i in range(w2.tabs.count())
                 if getattr(w2.tabs.widget(i), "_factory", None) is H.OrganizerPanel)
        w2.tabs.setCurrentIndex(i); app.processEvents()
        assert org(w2)._launch_q == []
    finally:
        w2.close()


def test_closing_with_waiting_pdfs_asks_first(app, pdfs, monkeypatch):
    w = H.MainWindow(); w.show(); app.processEvents()
    fill(w, [str(pdfs / f"{n}.pdf") for n in "ab"], monkeypatch)
    p = org(w); assert showing(app, p, pdfs / "a.pdf", 3)
    seen = []
    monkeypatch.setattr(H.Msg, "question", staticmethod(lambda *a, **k: seen.append((a[1], a[2], a[4])) or QMessageBox.No))
    w.close()
    assert [x[0] for x in seen] == ["PDFs Still Waiting"] and w.isVisible()
    assert seen[0][1].startswith("1 PDF you opened from Explorer has not been looked at")
    assert seen[0][2] == QMessageBox.No
    monkeypatch.setattr(H.Msg, "question", staticmethod(lambda *a, **k: QMessageBox.Yes))
    p.stop_for_shutdown(); w.close()
    assert not w.isVisible()


def test_choosing_save_writes_the_pdf_then_moves_to_the_queued_one(app, pdfs, win, monkeypatch):
    P = lambda n: str(pdfs / f"{n}.pdf")
    fill(win, [P("a"), P("b")], monkeypatch)
    p = org(win); assert showing(app, p, P("a"), 3)
    assert pump(app, lambda: len(p._thumbs) == 3)
    p.grid.item(0).setSelected(True); p.rotate_right()
    monkeypatch.setattr(p, "_ask_save", lambda name: QMessageBox.Save)
    p._launch_next()
    assert showing(app, p, P("b"), 2)
    saved = pdfs / H.setting("organized_dir_name") / "a.pdf"
    assert saved.exists() and p._launch_state[P("b")] == "viewed"


def test_progress_line_survives_a_reader_page_change(app, pdfs, win, monkeypatch):
    P = lambda n: str(pdfs / f"{n}.pdf")
    fill(win, [P("a"), P("b"), P("c")], monkeypatch)
    p = org(win); assert showing(app, p, P("a"), 3)
    p._launch_next(); assert showing(app, p, P("b"), 2)
    assert p.reader.q_label.text() == "PDF 2 of 3 · 1 waiting"
    assert p.reader.q_prev_btn.isEnabled() and p.reader.q_next_btn.isEnabled()


def test_clicking_a_missing_entry_marks_it(app, pdfs, win, monkeypatch):
    P = lambda n: str(pdfs / f"{n}.pdf")
    fill(win, [P("a"), P("b")], monkeypatch)
    p = org(win); assert showing(app, p, P("a"), 3)
    os.remove(P("b"))
    p._on_launch_clicked(p.launch_list.item(1))
    assert p._launch_state[P("b")] == "missing" and p._cur_path == P("a")
    assert "no longer there" in p.status.text() and "(missing)" in p.launch_list.item(1).text()


def test_default_burst_window_groups_close_launches_and_chains(app, pdfs, win, monkeypatch):
    monkeypatch.setattr(H, "LAUNCH_BURST_S", ORIG["burst"])
    P = lambda n: str(pdfs / f"{n}.pdf")
    win.open_pdf_from_launch(P("a")); p = org(win)
    t1 = p._launch_t
    win.open_pdf_from_launch(P("b"))                       # right behind the first: part of the batch
    assert p._launch_t >= t1 and p._launch_state[P("b")] == "waiting"
    p._launch_t -= 100                                     # a launch long after the last one
    win.open_pdf_from_launch(P("c"))
    assert p._launch_state[P("c")] == "waiting"            # b is still waiting, so c does not steal the view
    p._launch_state[P("b")] = p._launch_state[P("c")] = "viewed"
    p._launch_t -= 100
    win.open_pdf_from_launch(P("d"))
    assert showing(app, p, P("d"), 1)                      # nothing waiting, not a burst: shown at once


def test_clear_viewed_keeps_position_list_and_label_in_step(app, pdfs, win, monkeypatch):
    P = lambda n: str(pdfs / f"{n}.pdf")
    fill(win, [P(n) for n in "abcd"], monkeypatch)
    p = org(win); assert showing(app, p, P("a"), 3)
    p._on_launch_clicked(p.launch_list.item(2))
    assert showing(app, p, P("c"), 4)
    os.remove(P("d")); p._launch_state[P("d")] = "missing"
    p.launch_clear_viewed()
    assert p._launch_q == [P("b"), P("c")] and p._launch_pos == 1      # missing and a (viewed) gone; b waits; c is open
    assert p.launch_list.count() == 2 and "2 of 2" in p.launch_label.text() and "1 waiting" in p.launch_label.text()
    assert "Took 2 PDFs off the list" in p.status.text()


def test_save_and_next_and_skip_follow_the_queue_with_one_prompt(app, pdfs, win, monkeypatch):
    P = lambda n: str(pdfs / f"{n}.pdf")
    fill(win, [P("a"), P("b"), P("c")], monkeypatch)
    p = org(win); assert showing(app, p, P("a"), 3)
    assert pump(app, lambda: len(p._thumbs) == 3)
    p.grid.item(0).setSelected(True); p.rotate_right()
    p.save_and_next()                                     # Ctrl+S / Save & Next on a dirty queued PDF
    assert showing(app, p, P("b"), 2)
    assert (pdfs / H.setting("organized_dir_name") / "a.pdf").exists()
    assert pump(app, lambda: len(p._thumbs) == 2)
    p.grid.item(0).setSelected(True); p.rotate_right()
    asked = []
    monkeypatch.setattr(p, "_ask_save", lambda name: asked.append(name) or QMessageBox.Discard)
    p.skip_next()                                         # Skip + Discard must ask once, not twice
    assert asked == ["b.pdf"] and showing(app, p, P("c"), 4)


def test_launch_while_a_row_click_prompt_is_open_does_not_stack_a_second_prompt(app, pdfs, win, monkeypatch):
    P = lambda n: str(pdfs / f"{n}.pdf")
    fill(win, [P("a"), P("b")], monkeypatch)
    p = org(win); assert showing(app, p, P("a"), 3)
    assert pump(app, lambda: len(p._thumbs) == 3)
    p.grid.item(0).setSelected(True); p.rotate_right()
    p._launch_state[P("b")] = "viewed"                    # nothing else waits, so a stray launch WOULD try to show itself
    prompts = []
    def question(*a, **k):
        prompts.append(a[1])
        assert win.open_pdf_from_launch(P("d"))           # a launch arrives while THIS prompt is open
        return QMessageBox.Cancel
    monkeypatch.setattr(H.Msg, "question", staticmethod(question))
    p.skip_next()
    assert prompts == ["Unsaved Changes"]                 # no second prompt was stacked on the first
    assert P("d") in p._launch_q and p._launch_state[P("d")] == "waiting" and p._cur_path == P("a")
    assert p._launch_guard is False                       # the guard is released once the prompt closes


def test_opening_a_queued_pdf_by_hand_marks_it_viewed(app, pdfs, win, monkeypatch):
    P = lambda n: str(pdfs / f"{n}.pdf")
    fill(win, [P("a"), P("b"), P("c")], monkeypatch)
    p = org(win); assert showing(app, p, P("a"), 3)
    p.close_reader()
    row = next(i for i in range(p.file_list.count()) if p._row_path(p.file_list.item(i)) == P("c"))
    p.file_list.setCurrentRow(row)
    assert pump(app, lambda: p._cur_path == P("c") and len(p._pages) == 4)
    assert p._launch_state[P("c")] == "viewed" and p._launch_pos == 2 and p.launch_waiting() == 1


def test_clear_viewed_is_refused_while_a_save_runs(app, pdfs, win, monkeypatch):
    P = lambda n: str(pdfs / f"{n}.pdf")
    fill(win, [P("a"), P("b")], monkeypatch)
    p = org(win); assert showing(app, p, P("a"), 3)
    p._launch_state[P("b")] = "viewed"
    p._busy = True
    try:
        p.launch_clear_viewed()
        assert len(p._launch_q) == 2
    finally:
        p._busy = False


def test_shortcut_works_with_focus_in_the_page_box_and_find_box(app, pdfs, win, monkeypatch):
    P = lambda n: str(pdfs / f"{n}.pdf")
    fill(win, [P("a"), P("b"), P("c")], monkeypatch)
    p = org(win); assert showing(app, p, P("a"), 3)
    p.reader.page_spin.setFocus()
    QTest.keyClick(p.reader.page_spin, Qt.Key_PageDown, Qt.ControlModifier)
    assert showing(app, p, P("b"), 2)
    p.reader.focus_find()
    QTest.keyClick(p.reader.find_edit, Qt.Key_PageDown, Qt.ControlModifier)
    assert showing(app, p, P("c"), 4)


def test_entry_that_cannot_open_stays_waiting(app, pdfs, win, monkeypatch):
    P = lambda n: str(pdfs / f"{n}.pdf")
    fill(win, [P("a"), P("b")], monkeypatch)
    p = org(win); assert showing(app, p, P("a"), 3)
    monkeypatch.setattr(p, "_read_from_menu", lambda path: None)         # the list never got the file selected
    assert p._launch_next() is None and p._launch_state[P("b")] == "waiting" and p._launch_pos == 0
    assert "could not be opened" in p.status.text() and p.launch_waiting() == 1


# ---------------------------------------------------------------- a listed PDF that cannot open is skipped
def _bad(pdfs, name):
    f = pdfs / f"{name}.pdf"; f.write_bytes(b"this is not a pdf\n" * 20)
    return str(f)


def test_unopenable_pdf_is_skipped_with_the_reason(app, pdfs, win, monkeypatch):
    P = lambda n: str(pdfs / f"{n}.pdf")
    bad = _bad(pdfs, "bad")
    fill(win, [P("a"), bad, P("c")], monkeypatch)
    p = org(win); assert showing(app, p, P("a"), 3)
    p._launch_next()
    assert showing(app, p, P("c"), 4)                                   # went on by itself
    assert p._launch_state[bad] == "failed" and p._launch_state[P("c")] == "viewed"
    assert pump(app, lambda: "bad.pdf was skipped" in p.status.text() and "not a PDF" in p.status.text())
    assert "(could not be opened)" in p.launch_list.item(1).text()


def test_two_bad_in_a_row_and_a_bad_last_one(app, pdfs, win, monkeypatch):
    P = lambda n: str(pdfs / f"{n}.pdf")
    b1, b2, b3 = _bad(pdfs, "bad1"), _bad(pdfs, "bad2"), _bad(pdfs, "bad3")
    fill(win, [P("a"), b1, b2, P("d"), b3], monkeypatch)
    p = org(win); assert showing(app, p, P("a"), 3)
    p._launch_next()
    assert showing(app, p, P("d"), 1)
    assert "bad1.pdf was skipped" in p.status.text() and "bad2.pdf was skipped" in p.status.text()
    p._launch_next()                                                     # last entry cannot open and nothing follows
    assert pump(app, lambda: p._launch_state[b3] == "failed")
    assert "could not be shown" in p.status.text()                       # the error stays up; no loop, no crash
    assert p.launch_waiting() == 0


def test_going_backwards_skips_backwards(app, pdfs, win, monkeypatch):
    P = lambda n: str(pdfs / f"{n}.pdf")
    bad = _bad(pdfs, "bad")
    fill(win, [P("a"), P("b"), bad, P("c")], monkeypatch)
    p = org(win); assert showing(app, p, P("a"), 3)
    p._launch_goto(3); assert showing(app, p, P("c"), 4)
    p._launch_step(-1)                                                   # lands on bad, then moves on backwards
    assert pump(app, lambda: p._launch_state[bad] == "failed")
    assert p._launch_dir == -1
    assert showing(app, p, P("b"), 2)                                    # b was still waiting, behind the bad one
    assert p._launch_state[P("b")] == "viewed"


def test_oversize_pdf_is_skipped_like_any_other(app, pdfs, win, monkeypatch):
    P = lambda n: str(pdfs / f"{n}.pdf")
    big = pdfs / "big.pdf"; make_text_pdf(str(big), pages=6, text="BIG")
    monkeypatch.setattr(H, "PDF_FILE_MAX", big.stat().st_size - 1)
    # a, b, d are smaller than big.pdf only if make_text_pdf makes them so; use size of the largest small one
    small = max(os.path.getsize(P(n)) for n in "abcd")
    if small >= big.stat().st_size:
        pytest.skip("fixture sizes do not separate")
    monkeypatch.setattr(H, "PDF_FILE_MAX", small)
    fill(win, [P("a"), str(big), P("b")], monkeypatch)
    p = org(win); assert showing(app, p, P("a"), 3)
    p._launch_next()
    assert showing(app, p, P("b"), 2) and p._launch_state[str(big)] == "failed"
    assert "too large" in p.status.text()


def test_failure_handler_leaves_alone_an_operator_who_moved_on(app, pdfs, win, monkeypatch):
    P = lambda n: str(pdfs / f"{n}.pdf")
    bad = _bad(pdfs, "bad")
    fill(win, [P("a"), bad, P("c"), P("d")], monkeypatch)
    p = org(win); assert showing(app, p, P("a"), 3)
    p._launch_goto(2); assert showing(app, p, P("c"), 4)                # c is on screen, d still waiting
    p._launch_fail = (bad, "late news")                                 # a stale failure report arrives
    p._launch_after_failure()
    assert p._cur_path == P("c") and p._launch_state[P("d")] == "waiting"


def test_previous_does_not_retry_a_failed_pdf(app, pdfs, win, monkeypatch):
    P = lambda n: str(pdfs / f"{n}.pdf")
    bad = _bad(pdfs, "bad")
    fill(win, [P("a"), bad, P("c")], monkeypatch)
    p = org(win); assert showing(app, p, P("a"), 3)
    p._launch_next(); assert showing(app, p, P("c"), 4)
    p._launch_step(-1)                                                   # steps over the failed one to a
    assert showing(app, p, P("a"), 3) and p._launch_state[bad] == "failed"
