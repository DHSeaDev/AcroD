"""GC runs on the GUI thread only (intermittent native crash with worker threads)."""
import gc, os, sys, threading
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import pytest
from PySide6.QtWidgets import QApplication
import acrod as H


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


def test_gc_is_collected_by_a_gui_thread_timer_only(app):
    was = gc.isenabled()
    seen = []
    try:
        t = H.install_main_thread_gc(app, interval_ms=20)
        assert not gc.isenabled()
        gc.callbacks.append(lambda ph, info: seen.append(threading.current_thread()) if ph == "start" else None)
        junk = []
        for _ in range(3):
            a = []; a.append(a); junk.append(a)
        del junk, a
        import time
        end = time.time() + 1
        while time.time() < end and not seen:
            app.processEvents(); time.sleep(0.01)
        assert seen, "timer never collected"
        assert all(th is threading.main_thread() for th in seen)
        assert t.isActive()
        t.stop()
    finally:
        gc.callbacks.clear()
        if was:
            gc.enable()


def test_main_block_installs_it():
    src = open(os.path.join(os.path.dirname(__file__), "..", "acrod.py"), encoding="utf-8").read()
    assert "_gc_timer = install_main_thread_gc(app)" in src
