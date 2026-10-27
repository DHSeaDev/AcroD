"""Open the reader on a PDF, draw, search, close the window - N times. Every
MainWindow must be gone afterwards and no renderer thread may still run.
Exit 1 on a survivor or a live thread; a segfault is the loud failure."""
import gc, os, sys, time, weakref, tempfile
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
HERE = os.path.dirname(__file__)
sys.path.insert(0, HERE); sys.path.insert(0, os.path.join(HERE, ".."))
os.environ["LOCALAPPDATA"] = tempfile.mkdtemp()
import acrod as H
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt, QEvent
from test_reader_engine import make_text_pdf

N = int(sys.argv[1]) if len(sys.argv) > 1 else 12
app = QApplication([]); H.apply_theme(app)
folder = tempfile.mkdtemp()
make_text_pdf(os.path.join(folder, "a.pdf"), pages=4, text="CHURN")
make_text_pdf(os.path.join(folder, "b.pdf"), pages=3, text="CHURN")


def pump(cond, secs=8.0):
    end = time.time() + secs
    while time.time() < end:
        app.processEvents()
        if cond():
            return True
        time.sleep(0.005)
    return False


refs = []
keep = []
for i in range(N):
    w = H.MainWindow(); w.setAttribute(Qt.WA_DeleteOnClose, True); w.show()
    tab = next(t for t in range(w.tabs.count()) if w.tabs.tabToolTip(t) == "Page Organizer")
    w.tabs.setCurrentIndex(tab); app.processEvents()
    p = w.tabs.widget(tab).panel
    p.folder_row.set_path(folder)
    assert pump(lambda: p.file_list.count() == 2 and len(p._counts) == 2), "list"
    p.file_list.setCurrentRow(0)
    assert pump(lambda: len(p._pages) == 4 and len(p._thumbs) == 4), "pages"
    p._on_file_double_clicked(p.file_list.item(0))
    assert pump(lambda: p.reader is not None and p.reader.is_loaded()), "reader"
    p.reader.find_edit.setText("churn"); p.reader._find_enter()
    for pos in range(4):
        p.reader.go_to(pos)
    if i % 3 == 0:
        p.skip_next()                                    # swap documents mid-flight
    if i % 3 == 1:
        p.close_reader()
    refs.append(weakref.ref(w))
    if "--inject-leak" in sys.argv:
        keep.append(w)
    w.close(); w.deleteLater(); del w, p
    for _ in range(3):
        app.processEvents()
    app.sendPostedEvents(None, QEvent.DeferredDelete)
    app.processEvents()
    gc.collect()
deadline = time.time() + 10
while time.time() < deadline and any(t.isRunning() for t in list(H._ALIVE_WORKERS)):
    app.processEvents(); time.sleep(0.01)
alive = sum(1 for r in refs if r() is not None)
threads = sum(1 for t in list(H._ALIVE_WORKERS) if t.isRunning())
print(f"N={N} alive={alive} running_workers={threads}")
sys.exit(1 if (alive or threads) else 0)
