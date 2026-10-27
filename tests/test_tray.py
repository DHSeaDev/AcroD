"""Tray layout: planner, tray list, feeding tools, Classic switch, parity of tools."""
import os, sys, time
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import pytest
from pypdf import PdfWriter
from PySide6.QtWidgets import QMessageBox
import acrod as H
from test_organizer_panel import app, pump  # noqa: F401


@pytest.fixture(autouse=True)
def _iso(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "appdata"))
    H._reset_ui_state_cache()


def mk(path, pages=2):
    w = PdfWriter()
    for i in range(pages):
        w.add_blank_page(width=100 + i, height=100)
    with open(path, "wb") as f:
        w.write(f)
    return str(path)


def E(path, kind="pdf"):
    return {"path": str(path), "kind": kind}


# ---- planner
def test_merge_needs_exactly_two_pdfs():
    assert not H.plan_tray_feed("merge", [E("/a/1.pdf")])["ok"]
    assert not H.plan_tray_feed("merge", [E("/a/1.pdf"), E("/a/2.pdf"), E("/a/3.pdf")])["ok"]
    assert not H.plan_tray_feed("merge", [E("/a/1.pdf"), E("/a", "folder")])["ok"]
    p = H.plan_tray_feed("merge", [E("/a/1.pdf"), E("/a/2.pdf")])
    assert p["ok"] and p["pair"] == ("/a/1.pdf", "/a/2.pdf")


def test_empty_tray_explains():
    for v in ("merge", "split", "shrink", "pages", "rename", "move"):
        p = H.plan_tray_feed(v, [])
        assert not p["ok"] and "Tick" in p["problem"]


def test_split_shrink_expand_folders_and_dedupe(tmp_path):
    a, b = mk(tmp_path / "a.pdf"), mk(tmp_path / "B.pdf")
    (tmp_path / "note.txt").write_text("x")
    sub = tmp_path / "sub"; sub.mkdir(); mk(sub / "deep.pdf")
    p = H.plan_tray_feed("split", [E(a), E(tmp_path, "folder")])
    assert p["ok"] and p["paths"] == [a, b]          # folder expanded one level, a not doubled
    spelled = os.path.join(str(tmp_path), "sub", "..", "a.pdf")      # same file, different spelling
    p = H.plan_tray_feed("split", [E(spelled), E(tmp_path, "folder")])
    assert len(p["paths"]) == 2
    assert H.plan_tray_feed("shrink", [E(sub / "none", "folder")])["ok"] is False


def test_pages_single_folder_opens_folder(tmp_path):
    p = H.plan_tray_feed("pages", [E(tmp_path, "folder")])
    assert p["ok"] and p["folder"] == str(tmp_path)


def test_rename_move_one_folder_only(tmp_path):
    f1, f2 = mk(tmp_path / "a.pdf"), mk(tmp_path / "b.pdf")
    other = tmp_path / "o"; other.mkdir(); f3 = mk(other / "c.pdf")
    assert H.plan_tray_feed("rename", [E(f1), E(f2)])["folder"] == str(tmp_path)
    assert not H.plan_tray_feed("move", [E(f1), E(f3)])["ok"]
    assert not H.plan_tray_feed("move", [E(tmp_path, "folder"), E(other, "folder")])["ok"]
    assert H.plan_tray_feed("move", [E(tmp_path, "folder")])["folder"] == str(tmp_path)


# ---- tray list
@pytest.fixture
def tray(app):
    t = H.TrayPanel()
    yield t
    t.stop_for_shutdown()
    t.deleteLater(); app.processEvents()


def test_add_skips_non_pdf_and_duplicates(tray, tmp_path):
    a = mk(tmp_path / "a.pdf")
    txt = tmp_path / "x.txt"; txt.write_text("hi")
    msg = tray.add_paths([a, str(txt)])
    assert tray.paths() == [os.path.abspath(a)] and "Skipped 1" in msg
    msg = tray.add_paths([a])
    assert len(tray.paths()) == 1 and "already" in msg


def test_add_folder_lists_its_pdfs(tray, tmp_path):
    mk(tmp_path / "a.pdf"); mk(tmp_path / "b.pdf")
    (tmp_path / "n.txt").write_text("x")
    msg = tray.add_paths([str(tmp_path)])
    assert tray.list.count() == 3
    kinds = [tray.list.item(i).data(H.ROLE_KIND) for i in range(3)]
    assert kinds == ["folder", "pdf", "pdf"]
    assert tray.list.item(1).data(H.ROLE_PARENT) == str(tmp_path)
    assert "2 PDFs" in tray.list.item(0).text() and "listed below" in tray.list.item(0).text()
    assert tray.entries()[0]["children"] == 2
    assert "1 folder with 2 PDFs" in msg
    assert "2 PDFs in 1 folder" in tray.count_label.text()


def test_folder_header_ticks_children_and_partial(tray, tmp_path):
    mk(tmp_path / "a.pdf"); mk(tmp_path / "b.pdf")
    tray.add_paths([str(tmp_path)])
    tray.list.item(1).setCheckState(H.Qt.Unchecked)
    assert tray.list.item(0).checkState() == H.Qt.PartiallyChecked
    assert [os.path.basename(e["path"]) for e in tray.ticked() if e["kind"] == "pdf"] == ["b.pdf"]
    tray.list.item(0).setCheckState(H.Qt.Unchecked)
    assert all(tray.list.item(i).checkState() == H.Qt.Unchecked for i in range(3))
    tray.list.item(0).setCheckState(H.Qt.Checked)
    assert all(tray.list.item(i).checkState() == H.Qt.Checked for i in range(3))


def test_removing_folder_row_removes_its_pdfs(tray, tmp_path):
    d = tmp_path / "d"; d.mkdir()
    mk(d / "a.pdf"); mk(d / "b.pdf")
    keep = mk(tmp_path / "keep.pdf")
    tray.add_paths([str(d), keep])
    tray.list.item(0).setSelected(True)
    tray.remove_selected()
    assert [os.path.basename(p) for p in tray.paths()] == ["keep.pdf"]


def test_removing_one_child_updates_header(tray, tmp_path):
    mk(tmp_path / "a.pdf"); mk(tmp_path / "b.pdf")
    tray.add_paths([str(tmp_path)])
    tray.list.item(1).setSelected(True)
    tray.remove_selected()
    assert tray.list.count() == 2 and "1 PDF " in tray.list.item(0).text()


def test_readd_folder_picks_up_new_files_without_dupes(tray, tmp_path):
    mk(tmp_path / "a.pdf")
    tray.add_paths([str(tmp_path)])
    mk(tmp_path / "b.pdf")
    msg = tray.add_paths([str(tmp_path)])
    assert tray.list.count() == 3 and [os.path.basename(p) for p in tray.paths()].count("a.pdf") == 1
    assert "already" in msg


def test_empty_and_subfolder_notes(tray, tmp_path):
    e = tmp_path / "empty"; e.mkdir()
    msg = tray.add_paths([str(e)])
    assert "No PDFs directly inside" in msg and tray.list.count() == 1
    d = tmp_path / "d"; d.mkdir(); mk(d / "a.pdf"); (d / "sub").mkdir()
    assert "sub-folder" in tray.add_paths([str(d)])


def test_planner_folder_with_listed_children():
    kids = [dict(E("/d/a.pdf"), parent="/d"), dict(E("/d/b.pdf"), parent="/d")]
    head = dict(E("/d", "folder"), children=2)
    p = H.plan_tray_feed("merge", [head] + kids)
    assert p["ok"] and p["pair"] == ("/d/a.pdf", "/d/b.pdf")
    p = H.plan_tray_feed("pages", [head] + kids)
    assert p["ok"] and p.get("folder") == "/d"
    p = H.plan_tray_feed("shrink", [head, kids[0]])
    assert p["ok"] and p["paths"] == ["/d/a.pdf"]


def test_page_counts_arrive(tray, tmp_path):
    a = mk(tmp_path / "a.pdf", pages=4)
    tray.add_paths([a])
    deadline = time.time() + 8
    while time.time() < deadline and tray.entries()[0]["pages"] is None:
        H.QApplication.processEvents(); time.sleep(0.02)
    assert tray.entries()[0]["pages"] == 4 and "4 pages" in tray.list.item(0).text()


def test_untick_remove_clear(tray, tmp_path):
    a, b = mk(tmp_path / "a.pdf"), mk(tmp_path / "b.pdf")
    tray.add_paths([a, b])
    tray.list.item(0).setCheckState(H.Qt.Unchecked)
    assert [os.path.basename(e["path"]) for e in tray.ticked()] == ["b.pdf"]
    tray.remove_ticked()
    assert [os.path.basename(p) for p in tray.paths()] == ["a.pdf"]
    assert os.path.exists(b)                         # files never touched
    tray.clear()
    assert tray.paths() == [] and os.path.exists(a)


def test_late_page_count_after_clear_is_dropped(tray, tmp_path):
    a = mk(tmp_path / "a.pdf")
    tray.add_paths([a]); old = tray._token; tray.clear()
    tray._on_pages(old, os.path.abspath(a), 9)       # gone path
    assert tray.paths() == []
    tray.add_paths([a])                              # same file back in the tray
    tray._on_pages(old, os.path.abspath(a), 9)       # a late answer from before the clear
    assert tray.entries()[0]["pages"] != 9


def test_cap(tray, tmp_path, monkeypatch):
    monkeypatch.setattr(H, "TRAY_MAX", 2)
    fs = [mk(tmp_path / f"{i}.pdf") for i in range(3)]
    msg = tray.add_paths(fs)
    assert len(tray.paths()) == 2 and "at most" in msg


# ---- window
@pytest.fixture
def win(app):
    w = H.MainWindow(); w.show()
    yield w
    for _i, p in w.panels():
        if hasattr(p, "stop_for_shutdown"):
            p.stop_for_shutdown()
    w.shell.tray.stop_for_shutdown()
    w.close()


def tab_title(win, i):
    return win.tabs.tabBar().title(i)


def test_default_is_tray_and_tabbar_hidden(win):
    assert win._layout == "tray" and win.centralWidget() is win.shell
    assert not win.tabs.tabBar().isVisible()


def test_classic_roundtrip_keeps_every_tab_and_state(win):
    titles = [tab_title(win, i) for i in range(win.tabs.count())]
    win.tabs.setCurrentIndex(3)
    win.apply_layout("classic")
    assert win.centralWidget() is win.shell and win.tabs.tabBar().isVisible() and not win.shell.tray.isVisible()
    assert not win.shell.verbs_w.isVisible() and not win.shell.top_bar.isVisible()
    assert [tab_title(win, i) for i in range(win.tabs.count())] == titles
    assert win.tabs.currentIndex() == 3
    win.apply_layout("tray")
    assert win.centralWidget() is win.shell and not win.tabs.tabBar().isVisible()
    assert win.tabs.currentIndex() == 3


def test_every_tool_reachable_from_tray_verbs(win):
    """Parity gate: each of the eight tabs is reachable from the tray window (six verbs + Settings + Help)."""
    reach = {t for *_x, t in H.TRAY_VERBS} | {"Settings", "Help"}
    have = {tab_title(win, i) for i in range(win.tabs.count())}
    assert reach == have
    for key, b in win.shell.verb_buttons.items():
        b.click()
        pump(app=None) if False else H.QApplication.processEvents()
        title = next(t for k, *_r, t in H.TRAY_VERBS if k == key)
        assert tab_title(win, win.tabs.currentIndex()) == title
        assert b.isChecked()


def test_settings_layout_applies_live(win):
    win.shell.settings_btn.click()
    sp = win.panel_at(win.tabs.currentIndex())
    idx = sp.adv["ui_layout"].findData("classic")
    sp.adv["ui_layout"].setCurrentIndex(idx)
    sp._save_advanced()
    assert win._layout == "classic" and H.setting("ui_layout") == "classic"
    sp._reset_advanced()
    assert win._layout == "tray"


def test_feed_merge_pair(win, tmp_path, monkeypatch):
    a, b = mk(tmp_path / "a.pdf"), mk(tmp_path / "b.pdf")
    win.shell._set_tray_wanted(True)
    win.shell.tray.add_paths([a, b])
    win.shell.verb_buttons["merge"].click()
    H.QApplication.processEvents()
    assert win.shell.feed_btn.isEnabled()
    asked = []
    monkeypatch.setattr(H.Msg, "question", lambda *a, **k: asked.append(a) or QMessageBox.No)
    before = open(a, "rb").read()
    ok, _m = win.feed_from_tray("merge")
    assert ok and asked                              # the Merger's own confirmation still appears
    assert open(a, "rb").read() == before            # declined: nothing written
    panel = win.panel_at(win.tabs.currentIndex())
    assert isinstance(panel, H.MergerPanel)
    assert os.path.normcase(os.path.abspath(panel.reference_path)) == os.path.normcase(os.path.abspath(b)) \
        if hasattr(panel, "reference_path") else True


def test_feed_merge_rejects_three(win, tmp_path):
    fs = [mk(tmp_path / f"{i}.pdf") for i in range(3)]
    win.shell._set_tray_wanted(True)
    win.shell.tray.add_paths(fs)
    win.shell.verb_buttons["merge"].click()
    H.QApplication.processEvents()
    assert not win.shell.feed_btn.isEnabled()
    ok, msg = win.feed_from_tray("merge")
    assert not ok and "two" in msg.lower()


def test_feed_folder_to_renamer_and_mover(win, tmp_path):
    mk(tmp_path / "a.pdf")
    win.shell.tray.add_paths([str(tmp_path)])
    for verb, cls, attr in (("rename", H.RenamerPanel, "folder_row"), ("move", H.MoverPanel, "source_row")):
        win.shell.verb_buttons[verb].click()
        ok, _m = win.feed_from_tray(verb)
        assert ok
        panel = win.panel_at(win.tabs.currentIndex())
        assert isinstance(panel, cls)
        assert os.path.normcase(getattr(panel, attr).path()) == os.path.normcase(str(tmp_path))


def test_feed_split_expands_folder(win, tmp_path):
    mk(tmp_path / "a.pdf"); mk(tmp_path / "b.pdf")
    win.shell.tray.add_paths([str(tmp_path)])
    win.shell.verb_buttons["split"].click()
    plan = H.plan_tray_feed("split", win.shell.tray.ticked())
    assert len(plan["paths"]) == 2


def test_busy_tool_refuses(win, tmp_path):
    a = mk(tmp_path / "a.pdf")
    win.shell.tray.add_paths([a])
    win.shell.verb_buttons["split"].click()
    panel = win.panel_at(win.tabs.currentIndex())
    panel.is_busy = lambda: True
    try:
        ok, msg = win.feed_from_tray("split")
    finally:
        del panel.is_busy
    assert not ok and "busy" in msg.lower()


def test_drop_on_shell_adds_to_tray(win, tmp_path):
    from PySide6.QtCore import QMimeData, QUrl, QPointF, Qt
    from PySide6.QtGui import QDropEvent
    a = mk(tmp_path / "a.pdf")
    md = QMimeData(); md.setUrls([QUrl.fromLocalFile(a)])
    ev = QDropEvent(QPointF(5, 5), Qt.CopyAction, md, Qt.LeftButton, Qt.NoModifier)
    win.shell.dropEvent(ev)
    assert win.shell.tray.paths() == [os.path.abspath(a)]


def test_classic_strip_keeps_its_density_after_a_roundtrip(win, app):
    """Regression: re-parenting a hidden tab strip lost its 'tight' padding, so it went icon-only too early."""
    H.apply_theme(app, "Periwinkle", "Normal")
    win.apply_layout("classic"); win.apply_layout("tray"); win.apply_layout("classic")
    for width in (1360, 1100, 720):
        win.resize(width, 640)
        for _ in range(3):
            H.QApplication.processEvents()
    win.resize(1100, 640)
    for _ in range(3):
        H.QApplication.processEvents()
    assert win.tabs.tabBar()._mode == "tight"


def test_close_waits_for_the_tray_reader(win, monkeypatch):
    told = []
    monkeypatch.setattr(H.Msg, "information", lambda *a, **k: told.append(a))
    monkeypatch.setattr(win.shell.tray, "stop_for_shutdown", lambda *a, **k: False)
    from PySide6.QtGui import QCloseEvent
    ev = QCloseEvent()
    win.closeEvent(ev)
    assert not ev.isAccepted() and told


# ---- usability
def test_tray_is_hidden_by_default_and_the_choice_is_remembered(win, app):
    assert not win.shell.tray.isVisible() and win.shell.tray_toggle.text().startswith("Show file tray")
    win.shell.toggle_tray()
    assert win.shell.tray.isVisible() and win.shell.tray_toggle.text() == "Hide file tray"
    H._reset_ui_state_cache()
    w2 = H.MainWindow(); w2.show()
    try:
        assert w2.shell.tray.isVisible()             # remembered
    finally:
        w2.shell.tray.stop_for_shutdown(); w2.close()


def test_files_waiting_are_announced_on_the_hidden_toggle(win, tmp_path):
    win.shell.tray.add_paths([mk(tmp_path / "a.pdf")])
    win.shell.refresh()
    assert "1 waiting" in win.shell.tray_toggle.text()


def test_dropping_on_the_window_opens_the_tray(win, tmp_path):
    from PySide6.QtCore import QMimeData, QUrl, QPointF, Qt
    from PySide6.QtGui import QDropEvent
    a = mk(tmp_path / "a.pdf")
    md = QMimeData(); md.setUrls([QUrl.fromLocalFile(a)])
    win.shell.dropEvent(QDropEvent(QPointF(5, 5), Qt.CopyAction, md, Qt.LeftButton, Qt.NoModifier))
    assert win.shell.tray.isVisible() and len(win.shell.tray.paths()) == 1


def test_ctrl_t_toggles_the_tray(win):
    win.shell.toggle_tray()
    assert win.shell.tray.isVisible()
    win.shell.toggle_tray()
    assert not win.shell.tray.isVisible()


def test_empty_tray_says_what_to_do(win):
    win.shell.toggle_tray()
    t = win.shell.tray
    assert t.empty_box.isVisible() and not t.list.isVisible() and "drop" in t.empty_box.text().lower()


def test_double_click_opens_the_pdf_in_the_page_organizer(win, tmp_path, monkeypatch):
    a = mk(tmp_path / "a.pdf")
    win.shell.toggle_tray()
    win.shell.tray.add_paths([a])
    seen = []
    org = win.panel_at([i for i in range(win.tabs.count()) if tab_title(win, i) == "Page Organizer"][0]) \
        or win.tabs.widget([i for i in range(win.tabs.count()) if tab_title(win, i) == "Page Organizer"][0]).ensure()
    monkeypatch.setattr(org, "open_pdf_in_reader", lambda p: seen.append(p) or True)
    win.shell.tray.list.itemDoubleClicked.emit(win.shell.tray.list.item(0))
    assert seen == [os.path.abspath(a)]
    assert tab_title(win, win.tabs.currentIndex()) == "Page Organizer"
    assert "Opened" in win.shell.feed_label.text()


def test_double_click_on_a_folder_opens_it_in_the_organizer(win, tmp_path):
    mk(tmp_path / "a.pdf")
    win.shell.toggle_tray()
    win.shell.tray.add_paths([str(tmp_path)])
    win.shell.tray.list.itemDoubleClicked.emit(win.shell.tray.list.item(0))
    org = win.panel_at(win.tabs.currentIndex())
    assert isinstance(org, H.OrganizerPanel)
    assert os.path.normcase(org.folder_row.path()) == os.path.normcase(str(tmp_path))


def test_double_click_while_organizer_busy_says_so(win, tmp_path):
    a = mk(tmp_path / "a.pdf")
    win.shell.toggle_tray(); win.shell.tray.add_paths([a])
    idx = [i for i in range(win.tabs.count()) if tab_title(win, i) == "Page Organizer"][0]
    holder = win.tabs.widget(idx); org = holder.ensure()
    org.is_busy = lambda: True
    try:
        ok, msg = win.open_from_tray(a, "pdf")
    finally:
        del org.is_busy
    assert not ok and "busy" in msg.lower()


def test_dragging_rows_out_carries_files_and_keeps_the_rows(tray, tmp_path):
    a, b = mk(tmp_path / "a.pdf"), mk(tmp_path / "b.pdf")
    tray.add_paths([a, b])
    md = tray.list.mimeData([tray.list.item(0), tray.list.item(1)])
    assert [u.toLocalFile() for u in md.urls()] == [os.path.abspath(a), os.path.abspath(b)]
    assert len(tray.paths()) == 2


def test_reordering_by_drag_changes_the_order_a_tool_receives(tray, tmp_path):
    from PySide6.QtCore import QMimeData, QPointF, Qt, QUrl
    from PySide6.QtGui import QDropEvent
    fs = [mk(tmp_path / n) for n in ("a.pdf", "b.pdf", "c.pdf")]
    tray.add_paths(fs); tray.show(); tray.resize(300, 500); H.QApplication.processEvents()
    lst = tray.list
    lst.clearSelection(); lst.item(0).setSelected(True)
    last = lst.visualItemRect(lst.item(2)).center()
    last.setY(last.y() + 20)                         # below the last row: drop at the end
    md = lst.mimeData([lst.item(0)])
    ev = QDropEvent(QPointF(last), Qt.MoveAction, md, Qt.LeftButton, Qt.NoModifier)
    ev.setDropAction(Qt.MoveAction)
    # a drop from this very list: simulate the source with a thin subclass
    class Ev:                                         # minimal stand-in carrying source()
        def __init__(s): s.p = last
        def source(s): return lst
        def position(s): return QPointF(s.p)
        def acceptProposedAction(s): pass
    lst.dropEvent(Ev())
    assert [os.path.basename(p) for p in tray.paths()] == ["b.pdf", "c.pdf", "a.pdf"]
    assert [os.path.basename(e["path"]) for e in tray.ticked()] == ["b.pdf", "c.pdf", "a.pdf"]


def test_delete_key_and_context_remove_only_from_the_tray(tray, tmp_path):
    a, b = mk(tmp_path / "a.pdf"), mk(tmp_path / "b.pdf")
    tray.add_paths([a, b])
    tray.list.clearSelection(); tray.list.item(0).setSelected(True)
    tray.remove_selected()
    assert [os.path.basename(p) for p in tray.paths()] == ["b.pdf"] and os.path.exists(a)


def test_tick_only_this_one(tray, tmp_path):
    a, b = mk(tmp_path / "a.pdf"), mk(tmp_path / "b.pdf")
    tray.add_paths([a, b])
    tray.tick_only(tray.list.item(1))
    assert [os.path.basename(e["path"]) for e in tray.ticked()] == ["b.pdf"]


def test_sending_to_a_tool_says_what_happens_next(win, tmp_path, monkeypatch):
    a, b = mk(tmp_path / "a.pdf"), mk(tmp_path / "b.pdf")
    monkeypatch.setattr(H.Msg, "question", lambda *x, **k: QMessageBox.No)
    win.shell.toggle_tray(); win.shell.tray.add_paths([a, b])
    win.shell.verb_buttons["merge"].click(); H.QApplication.processEvents()
    win.shell.feed_btn.click()
    assert "confirm before anything is written" in win.shell.feed_label.text()


def test_each_tool_button_says_in_words_what_it_does(win):
    win.shell.verb_buttons["split"].click(); H.QApplication.processEvents()
    assert "One PDF into many" in win.shell.heading.text()


def test_a_garbled_remembered_state_leaves_the_tray_hidden(app, monkeypatch, tmp_path):
    H.update_ui_state(tray_visible="yes")
    w = H.MainWindow(); w.show()
    try:
        assert not w.shell.tray.isVisible()
    finally:
        w.shell.tray.stop_for_shutdown(); w.close()


def test_dropping_between_rows_lands_between_them(tray, tmp_path):
    from PySide6.QtCore import QPointF
    fs = [mk(tmp_path / n) for n in ("a.pdf", "b.pdf", "c.pdf")]
    tray.add_paths(fs); tray.show(); tray.resize(300, 500); H.QApplication.processEvents()
    lst = tray.list
    lst.clearSelection(); lst.item(0).setSelected(True)
    pt = lst.visualItemRect(lst.item(1)).center()
    pt.setY(lst.visualItemRect(lst.item(1)).bottom() - 1)      # lower half of b: after b, before c

    class Ev:
        def source(s): return lst
        def position(s): return QPointF(pt)
        def acceptProposedAction(s): pass
    lst.dropEvent(Ev())
    assert [os.path.basename(p) for p in tray.paths()] == ["b.pdf", "a.pdf", "c.pdf"]


def test_shrink_message_says_it_started_and_keeps_originals(win, tmp_path, monkeypatch):
    a = mk(tmp_path / "a.pdf")
    win.shell.toggle_tray(); win.shell.tray.add_paths([a])
    win.shell.verb_buttons["shrink"].click(); H.QApplication.processEvents()
    assert "now" in win.shell.feed_label.text() and "originals are kept" in win.shell.feed_label.text()
    panel = win.panel_at(win.tabs.currentIndex())
    monkeypatch.setattr(panel, "receive_paths", lambda p: True)
    ok, msg = win.feed_from_tray("shrink")
    assert ok and "has started" in msg and "originals are kept" in msg


def test_ctrl_t_shortcut_is_installed(win):
    acts = [a for a in win.actions() if a.shortcut().toString() == "Ctrl+T"]
    assert len(acts) == 1
    acts[0].trigger()
    assert win.shell.tray.isVisible()


def test_compare_from_the_tray_menu_puts_the_pdf_in_b(win, tmp_path):
    a, b = mk(tmp_path / "a.pdf"), mk(tmp_path / "b.pdf", pages=3)
    win.shell.toggle_tray(); win.shell.tray.add_paths([b])
    ok, msg = win.open_from_tray(b, "pdf", True)
    assert ok and "B" in msg
    org = win.panel_at(win.tabs.currentIndex())
    assert isinstance(org, H.OrganizerPanel) and org.b_pane.isVisible()
    assert os.path.normcase(org._b_path) == os.path.normcase(os.path.abspath(b))


def test_a_tray_row_dropped_on_b_works_because_it_carries_a_file_url(tray, tmp_path):
    from PySide6.QtCore import QUrl
    b = mk(tmp_path / "b.pdf")
    tray.add_paths([b])
    md = tray.list.mimeData([tray.list.item(0)])
    assert md.urls() == [QUrl.fromLocalFile(os.path.abspath(b))]


def test_send_to_menus_can_park_files_in_the_tray(win, tmp_path):
    f = mk(tmp_path / "a.pdf")
    assert H.add_to_tray_from(win.panel_at(0) or win, [f]) is True
    assert win.shell.tray.paths() == [os.path.abspath(f)] and win.shell.tray.isVisible()


def test_tray_row_menu_lists_compare_and_send_to(win, tmp_path):
    a = mk(tmp_path / "a.pdf")
    win.shell.toggle_tray(); tray = win.shell.tray; tray.add_paths([a])
    m = tray._build_row_menu(tray.list.visualItemRect(tray.list.item(0)).center())
    texts = [x.text() for x in m.actions()]
    assert any("show it in B" in t for t in texts) and "Send to" in texts
    sub = [x.menu() for x in m.actions() if x.text() == "Send to"][0]
    assert "PDF Merger" in [x.text() for x in sub.actions()]
    assert not any("File tray" in x.text() for x in sub.actions())


# ---- feed-bar wording (naive-user walk 2026-10-16)
def test_button_says_what_it_does():
    kids = [E("/d/a.pdf"), E("/d/b.pdf")]
    assert H.plan_tray_feed("merge", kids)["button"] == "Merge these two"
    assert H.plan_tray_feed("split", kids)["button"] == "Split them"
    assert H.plan_tray_feed("shrink", kids)["button"] == "Optimize now"
    assert H.plan_tray_feed("pages", kids)["button"] == "Open in Page Organizer"
    assert H.plan_tray_feed("rename", kids)["button"] == "Rename in this folder"
    assert H.plan_tray_feed("move", kids)["button"] == "Move from this folder"


def test_folder_labels_name_the_action_and_the_preview():
    for verb, word in (("rename", "Rename"), ("move", "Move")):
        lab = H.plan_tray_feed(verb, [E("/d", "folder")])["label"]
        assert lab.startswith(word) and "Downloads" not in lab and "preview" in lab
        assert not lab.startswith("Use the folder")


def test_merge_problem_is_short():
    msg = H.plan_tray_feed("merge", [E("/a/1.pdf")])["problem"]
    assert "two" in msg and len(msg) < 110


def test_window_button_caption_follows_tool(win, tmp_path):
    a, b = mk(tmp_path / "a.pdf"), mk(tmp_path / "b.pdf")
    win.shell._set_tray_wanted(True)
    win.shell.tray.add_paths([a, b])
    for verb, cap in (("shrink", "Optimize now"), ("merge", "Merge these two"), ("rename", "Rename in this folder")):
        win.shell.verb_buttons[verb].click(); H.QApplication.processEvents()
        assert win.shell.feed_btn.text() == cap


def test_flash_clears_when_tool_changes(win, tmp_path):
    a, b = mk(tmp_path / "a.pdf"), mk(tmp_path / "b.pdf")
    win.shell._set_tray_wanted(True)
    win.shell.tray.add_paths([a, b])
    win.shell.verb_buttons["split"].click()
    win.shell._flash = "Sent to PDF Splitter. x"; win.shell._flash_verb = "split"; win.shell.refresh()
    assert not win.shell.feed_btn.isVisible() and "Sent" in win.shell.feed_label.text()
    win.shell.verb_buttons["merge"].click(); H.QApplication.processEvents()
    assert "Sent" not in win.shell.feed_label.text() and win.shell.feed_btn.isVisible()


# ---- drag carries every ticked row
def test_drag_carries_all_ticked_when_grabbing_a_ticked_row(tray, tmp_path):
    fs = [mk(tmp_path / f"{i}.pdf") for i in range(4)]
    tray.add_paths(fs)
    tray.list.item(3).setCheckState(H.Qt.Unchecked)
    got = tray.list._ticked_carry([tray.list.item(0)])
    assert [i.data(H.Qt.UserRole) for i in got] == fs[:3]
    got = tray.list._ticked_carry([tray.list.item(3)])           # unticked row: only what was grabbed
    assert [i.data(H.Qt.UserRole) for i in got] == [fs[3]]


def test_drag_of_ticked_folder_row_carries_pdfs_once(tray, tmp_path):
    mk(tmp_path / "a.pdf"); mk(tmp_path / "b.pdf")
    tray.add_paths([str(tmp_path)])
    got = tray.list._ticked_carry([tray.list.item(0)])
    assert [H.os.path.basename(i.data(H.Qt.UserRole)) for i in got] == ["a.pdf", "b.pdf"]


def test_startdrag_uses_ticked_rows(tray, tmp_path, monkeypatch):
    fs = [mk(tmp_path / f"{i}.pdf") for i in range(3)]
    tray.add_paths(fs)
    tray.list.item(0).setSelected(True)
    seen = {}

    class FakeDrag:
        def __init__(self, *_a): pass
        def setMimeData(self, md): seen["urls"] = [u.toLocalFile() for u in md.urls()]
        def exec(self, *_a): return 0
    monkeypatch.setattr(H, "QDrag", FakeDrag)
    tray.list.startDrag(H.Qt.CopyAction)
    assert [os.path.normcase(p) for p in seen["urls"]] == [os.path.normcase(p) for p in fs]


def test_verb_buttons_are_named_optimize_and_organize(win):
    assert win.shell.verb_buttons["shrink"].text().strip().endswith("Optimize")
    assert win.shell.verb_buttons["pages"].text().strip().endswith("Organize")
    assert not any("Shrink" in b.text() or "Fix pages" in b.text() for b in win.shell.verb_buttons.values())
