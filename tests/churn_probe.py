"""Construct / realise every tab / close / gc, N times. Reports how many
MainWindow objects are still alive afterwards (should be 0) and RSS growth.
Exit 1 if any survive or the process crashes (a segfault is the loud failure)."""
import gc, os, sys, weakref, resource
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import tempfile
os.environ["LOCALAPPDATA"] = tempfile.mkdtemp()
import acrod as H
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt, QEvent

N = int(sys.argv[1]) if len(sys.argv) > 1 else 25
app = QApplication([]); H.apply_theme(app)
refs = []
base = 0.0
keep = []
def rss(): return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0
for i in range(N):
    w = H.MainWindow(); w.setAttribute(Qt.WA_DeleteOnClose, True); w.show()
    for t in range(w.tabs.count()):
        w.tabs.setCurrentIndex(t); app.processEvents()
    refs.append(weakref.ref(w))
    if "--inject-leak" in sys.argv: keep.append(w)
    w.close(); w.deleteLater(); del w
    for _ in range(3): app.processEvents()
    app.sendPostedEvents(None, QEvent.DeferredDelete)
    app.processEvents()
    gc.collect()
    if i == min(4, N - 1): base = rss()
alive = sum(1 for r in refs if r() is not None)
print(f"N={N} alive={alive} rss_start={base:.0f}MB rss_end={rss():.0f}MB growth={rss()-base:.0f}MB")
sys.exit(1 if alive else 0)
