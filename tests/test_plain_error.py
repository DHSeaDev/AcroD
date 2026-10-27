"""Errors nobody planned for reach the person in plain words; the detail goes to the log."""
import os, sys, errno
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import pytest
import acrod as H


@pytest.fixture(autouse=True)
def _iso(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "appdata"))
    H._reset_ui_state_cache()


@pytest.mark.parametrize("exc,needle", [
    (MemoryError(), "too big to process"),
    (FileNotFoundError(2, "No such file"), "could not be found"),
    (PermissionError(13, "denied"), "Windows refused access"),
    (OSError(errno.ENOSPC, "No space left on device"), "disk is full"),
    (OSError(errno.ENAMETOOLONG, "File name too long"), "path that is too long"),
    (OSError(errno.EIO, "Input/output error"), "disk or network problem"),
    (ValueError("secret internals 0x7f"), "unexpected problem (ValueError)"),
])
def test_messages(exc, needle):
    msg = H.plain_error(exc, "/x/Case 12.pdf")
    assert needle in msg
    if "disk is full" not in msg:
        assert "'Case 12.pdf'" in msg                      # says which file, except where the disk is the subject
    assert "0x7f" not in msg and "Traceback" not in msg


def test_pdf_library_errors_are_plain():
    from pypdf.errors import PdfReadError
    assert "could not be read as a PDF" in H.plain_error(PdfReadError("EOF marker not found"), "a.pdf")


def test_no_path_reads_naturally():
    assert H.plain_error(FileNotFoundError(), "").startswith("That file")


def test_detail_goes_to_the_log_and_the_log_is_bounded(monkeypatch):
    try:
        raise ValueError("needle-detail")
    except ValueError as e:
        H.plain_error(e, "a.pdf")
    log = os.path.join(H._log_dir(), "errors.log")
    assert "needle-detail" in open(log, encoding="utf-8").read()
    monkeypatch.setattr(H, "ERROR_LOG_MAX", 10)
    H.log_error_detail(ValueError("again"))
    assert os.path.exists(log + ".old")


def test_logging_never_raises(monkeypatch):
    monkeypatch.setattr(H, "_log_dir", lambda: (_ for _ in ()).throw(OSError("no")))
    assert "unexpected problem" in H.plain_error(RuntimeError("x"), "a.pdf")


def test_batch_worker_uses_it(qapp=None):
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    got = []
    w = H._BatchWorker(["/a/b.pdf"], lambda p: (_ for _ in ()).throw(ValueError("raw-secret")), None, path_of=lambda p: p)
    w.item_failed.connect(lambda i, it, m: got.append(m))
    w.run()
    app.processEvents()
    assert got and "unexpected problem (ValueError)" in got[0] and "raw-secret" not in got[0]
