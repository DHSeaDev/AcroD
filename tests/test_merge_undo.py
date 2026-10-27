"""Undo Last Merge: the file the merger overwrote comes back, and only when that is safe."""
import os, sys
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import pytest
from pypdf import PdfWriter, PdfReader
from PySide6.QtWidgets import QMessageBox
import acrod as H
from test_organizer_panel import app, pump  # noqa: F401


@pytest.fixture(autouse=True)
def _iso(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "appdata"))
    H._reset_ui_state_cache()


def mk(path, base, pages):
    w = PdfWriter()
    for i in range(pages):
        w.add_blank_page(width=base + i, height=100)
    with open(path, "wb") as f:
        w.write(f)
    return str(path)


def widths(p):
    return [int(float(x.mediabox.width)) for x in PdfReader(p).pages]


def test_undo_restores_the_original(tmp_path):
    base, ref = mk(tmp_path / "base.pdf", 100, 2), mk(tmp_path / "ref.pdf", 500, 1)
    before = open(base, "rb").read()
    H.merge_onto(base, ref)
    assert widths(base) == [100, 101, 500] and H.merge_undo_info()["original"] == os.path.abspath(base)
    assert H.undo_last_merge() == os.path.abspath(base)
    assert open(base, "rb").read() == before
    assert H.merge_undo_info() is None                              # one undo per merge
    with pytest.raises(H.PdfOpError, match="no merge to undo"):
        H.undo_last_merge()


def test_refuses_when_the_file_changed_since(tmp_path):
    base, ref = mk(tmp_path / "b.pdf", 100, 2), mk(tmp_path / "r.pdf", 500, 1)
    H.merge_onto(base, ref)
    mk(base, 700, 5)                                                # somebody worked on it afterwards
    with pytest.raises(H.PdfOpError, match="changed after the merge"):
        H.undo_last_merge()
    assert widths(base) == [700, 701, 702, 703, 704]                # untouched
    assert H.merge_undo_info() is not None                          # still undoable if they put it back


def test_refuses_when_the_file_is_gone_or_backup_damaged(tmp_path):
    base, ref = mk(tmp_path / "b.pdf", 100, 2), mk(tmp_path / "r.pdf", 500, 1)
    H.merge_onto(base, ref)
    with open(H.merge_backup_path(), "ab") as f:
        f.write(b"x")                                               # size no longer matches the record
    with pytest.raises(H.PdfOpError, match="missing or damaged"):
        H.undo_last_merge()
    os.remove(H.merge_backup_path())
    with pytest.raises(H.PdfOpError, match="missing or damaged"):
        H.undo_last_merge()
    base2 = mk(tmp_path / "b2.pdf", 100, 2)
    H.merge_onto(base2, ref)
    os.remove(base2)
    with pytest.raises(H.PdfOpError, match="no longer where the merge left it"):
        H.undo_last_merge()


def test_newest_merge_wins_and_failed_backup_clears_the_record(tmp_path, monkeypatch):
    a, b, ref = mk(tmp_path / "a.pdf", 100, 1), mk(tmp_path / "b.pdf", 200, 1), mk(tmp_path / "r.pdf", 500, 1)
    H.merge_onto(a, ref); H.merge_onto(b, ref)
    assert H.merge_undo_info()["original"] == os.path.abspath(b)
    monkeypatch.setattr(H.shutil, "copy2", lambda *x, **k: (_ for _ in ()).throw(OSError("disk full")))
    c = mk(tmp_path / "c.pdf", 300, 1)
    H.merge_onto(c, ref)                                            # still merges; no backup
    assert widths(c) == [300, 500] and H.merge_undo_info() is None  # and the old record is not left pointing at b


def test_corrupt_record_is_ignored(tmp_path):
    with open(H.merge_record_path(), "w") as f:
        f.write('{"original": 5}')
    assert H.merge_undo_info() is None
    with open(H.merge_record_path(), "w") as f:
        f.write("not json")
    assert H.merge_undo_info() is None


def test_panel_button_and_undo(app, tmp_path, monkeypatch):
    base, ref = mk(tmp_path / "base.pdf", 100, 2), mk(tmp_path / "ref.pdf", 500, 1)
    p = H.MergerPanel(); p.show()
    assert not p.undo_btn.isEnabled()
    monkeypatch.setattr(H.Msg, "question", lambda *a, **k: QMessageBox.Yes)
    monkeypatch.setattr(H.Msg, "information", lambda *a, **k: QMessageBox.Ok)
    monkeypatch.setattr(H.Msg, "critical", lambda *a, **k: QMessageBox.Ok)
    p.reference_path = ref
    p.handle_drop([base])
    assert pump(app, lambda: not p._busy and widths(base) == [100, 101, 500])
    assert p.undo_btn.isEnabled() and "Undo Last Merge" in p.status.text()
    p.undo_merge()
    assert pump(app, lambda: not p._busy and widths(base) == [100, 101])
    assert not p.undo_btn.isEnabled() and "Restored base.pdf" in p.status.text()
    p.undo_merge()
    assert "no merge to undo" in p.status.text()
    p._worker = None; p.stop_for_shutdown(); p.close()
