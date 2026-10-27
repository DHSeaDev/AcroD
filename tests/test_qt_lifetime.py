import os, subprocess, sys, textwrap
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
HERE = os.path.dirname(__file__)
sys.path.insert(0, HERE); sys.path.insert(0, os.path.join(HERE, ".."))
import gc, weakref
import qt_lifetime_scan as scan
import acrod as H

BAD = textwrap.dedent('''
    from PySide6.QtWidgets import QWidget, QPushButton
    class Keep(QWidget):
        def __init__(self, cb):
            super().__init__()
            self.cb = cb
    class Bad(QWidget):
        def __init__(self):
            super().__init__()
            self.hook = self.method
            b = QPushButton()
            b.clicked.connect(lambda: self.method())
            k = Keep(self.method)
        def method(self): pass
''')

GOOD = textwrap.dedent('''
    from PySide6.QtWidgets import QWidget, QPushButton
    class Good(QWidget):
        def __init__(self):
            super().__init__()
            b = QPushButton()
            b.clicked.connect(self.method)
            b.clicked.connect(lambda _c=False, p=3: print(p))
        def method(self): pass
''')


def test_scan_goes_red_on_each_known_bad_shape(tmp_path):
    f = tmp_path / "bad.py"; f.write_text(BAD)
    kinds = sorted(k for _l, k, _t in scan.scan(str(f)))
    assert kinds == ["A", "A", "B"]


def test_scan_is_quiet_on_the_safe_shapes(tmp_path):
    f = tmp_path / "good.py"; f.write_text(GOOD)
    assert scan.scan(str(f)) == []


def test_the_app_is_clean():
    assert scan.scan(os.path.join(HERE, "..", "acrod.py")) == []


def test_weak_callables_do_not_keep_their_owner_alive():
    class Owner:
        def __init__(self): self.n = 0
        def hit(self, k=1): self.n += k
    o = Owner()
    weak, later = H._weak(o.hit), H._later(o.hit, 5)
    weak(); later(True, "ignored trigger args")
    assert o.n == 6
    ref = weakref.ref(o); del o; gc.collect()
    assert ref() is None
    assert weak() is None and later() is None            # owner gone: a no-op, not an error
    assert H._weak(None) is None and H._weak(print) is print


def test_churn_probe_reports_survivors_and_passes_when_clean():
    probe = os.path.join(HERE, "churn_probe.py")
    ok = subprocess.run([sys.executable, probe, "4"], capture_output=True, text=True, timeout=120)
    assert ok.returncode == 0 and "alive=0" in ok.stdout, ok.stdout + ok.stderr
    bad = subprocess.run([sys.executable, probe, "4", "--inject-leak"], capture_output=True, text=True, timeout=120)
    assert bad.returncode == 1 and "alive=4" in bad.stdout, bad.stdout + bad.stderr
