"""Regression: Samba 'hide dot files' marks a dot-named temp file Hidden and the
bit survives the rename to the final name (Samba bug 14977). Explorer draws
Hidden items faded, like a cut file. The model below reproduces that on Linux:
a path is hidden iff it was ever created/renamed-to under a dot-name and the
bit was never cleared."""
import os, sys
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import pytest
import acrod as H


@pytest.fixture
def samba(monkeypatch, tmp_path):
    hidden = set()
    real_rename = os.rename

    def fake_rename(src, dst, *a, **k):
        real_rename(src, dst, *a, **k)
        was = src in hidden
        hidden.discard(src)
        if os.path.basename(dst).startswith(".") or was:
            hidden.add(dst)

    monkeypatch.setattr(os, "rename", fake_rename)
    monkeypatch.setattr(H, "file_is_hidden", lambda p: p in hidden)
    monkeypatch.setattr(H, "clear_hidden", lambda p: (hidden.discard(p) or True) if p in hidden else False)
    monkeypatch.setattr(H, "notify_shell_folder", lambda f: None)
    monkeypatch.setattr(H, "write_audit", lambda r: None)
    return hidden


def _plan(tmp_path, names):
    rows = []
    for a, b in names:
        src = tmp_path / a
        src.write_text("x")
        rows.append(H.RenameRow(src=str(src), dst=str(tmp_path / b), status=H.ST_OK))
    return H.RenamePlan(rows)


def test_renamed_files_are_not_left_hidden(samba, tmp_path):
    plan = _plan(tmp_path, [("a.pdf", "A1.pdf"), ("b.pdf", "B1.pdf")])
    n, fails, _ = H.execute_plan(plan, log_path=str(tmp_path / "u.jsonl"))
    assert n == 2 and not fails
    assert not any(str(tmp_path / x) in samba for x in ("A1.pdf", "B1.pdf"))


def test_temp_name_is_not_dot_prefixed():
    assert not H.TEMP_PREFIX.startswith(".")


def test_temp_prefix_recognised_as_temp():
    assert H.is_temp_name(H.TEMP_PREFIX + "abc")
    assert not H.is_temp_name("report.pdf")


def test_user_hidden_source_stays_hidden(samba, tmp_path):
    plan = _plan(tmp_path, [("a.pdf", "A1.pdf")])
    samba.add(str(tmp_path / "a.pdf"))          # the user hid it on purpose
    H.execute_plan(plan, log_path=str(tmp_path / "u.jsonl"))
    assert str(tmp_path / "A1.pdf") in samba


def test_undo_does_not_leave_hidden(samba, tmp_path):
    plan = _plan(tmp_path, [("a.pdf", "A1.pdf")])
    log = str(tmp_path / "u.jsonl")
    H.execute_plan(plan, log_path=log)
    restored, fails = H.undo(log)
    assert restored == 1 and not fails
    assert str(tmp_path / "a.pdf") not in samba


def test_orphan_scan_finds_temp_prefix(tmp_path):
    (tmp_path / (H.TEMP_PREFIX + "2")).write_text("x")
    (tmp_path / "real.pdf").write_text("x")
    names = sorted(os.path.basename(p) for p in H.find_orphan_temps(str(tmp_path), False))
    assert names == [H.TEMP_PREFIX + "2"]
    assert [os.path.basename(p) for p in H.collect_files(str(tmp_path), False)] == ["real.pdf"]


def test_fs_path_is_identity_unless_opted_in(monkeypatch):
    monkeypatch.setattr(H, "long_paths_allowed", lambda: False)
    assert H.fs_path("C:\\" + "a" * 300) == "C:\\" + "a" * 300
    assert H.max_path_len() == H.MAX_PATH_LEN


def test_long_paths_default_off_and_opt_in(monkeypatch):
    monkeypatch.setattr(H, "load_ui_state", lambda: {})
    assert H.long_paths_allowed() is False and H.max_path_len() == H.MAX_PATH_LEN
    monkeypatch.setattr(H, "load_ui_state", lambda: {"allow_long_paths": True})
    assert H.long_paths_allowed() is True and H.max_path_len() > 260
