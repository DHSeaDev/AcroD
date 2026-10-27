"""Round 8: the flattener must accept the bytes the Optimizer hands it (it used to die with
'stat: embedded null character in path'), and the build script must stay lean."""
import os, re, sys
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import pytest
from pypdf import PdfReader
import io
import acrod as H
from test_organizer_engine import make_pdf
from test_bomb_guard import zeros, pdf_with, FLATE

ROOT = os.path.join(os.path.dirname(__file__), "..")


@pytest.fixture
def scan(tmp_path):
    from PIL import Image
    g = Image.effect_noise((1200, 1600), 60).convert("L")
    p = tmp_path / "scan.pdf"
    g.save(str(p), resolution=200)
    return str(p)


def test_flatten_accepts_bytes_and_keeps_page_count(tmp_path):
    p = tmp_path / "a.pdf"; make_pdf(str(p), 3)
    out = H.flatten_to_images(p.read_bytes(), dpi=72, quality=40)
    assert len(PdfReader(io.BytesIO(out)).pages) == 3


def test_flatten_bytes_still_refuses_a_bomb(tmp_path):
    bomb = pdf_with([(FLATE, zeros(300 << 20))], tmp_path / "bomb.pdf")
    with pytest.raises(H.PdfOpError, match="enormous size"):
        H.flatten_to_images(open(bomb, "rb").read())


def test_expansion_check_takes_bytes_without_raising(tmp_path):
    p = tmp_path / "a.pdf"; make_pdf(str(p), 1)
    assert H.pdf_expansion_problem(p.read_bytes()) is None
    assert H.pdf_expansion_problem(b"\x00\x00 not a pdf \x00") is None


def test_forced_flatten_through_the_optimizer_succeeds(scan):
    opts = H.OptimizeOptions(mode="smallest", out_dir_name="Optimized", ceiling_bytes=10_000_000,
                             allow_gray=True, allow_flatten=True, flatten_dpi=100, force_flatten=True,
                             sanitize=H.SanitizeOptions())
    res = H.optimize_pdf(scan, opts)
    assert res.ok and "Flattened" in res.tier, (res.ok, res.tier)


def test_last_resort_flatten_runs_when_nothing_else_fits(scan):
    opts = H.OptimizeOptions(mode="smallest", out_dir_name="Optimized", ceiling_bytes=20_000,
                             allow_gray=True, allow_flatten=True, flatten_dpi=100, force_flatten=False,
                             sanitize=H.SanitizeOptions())
    res = H.optimize_pdf(scan, opts)
    assert res.ok and "latten" in res.tier, (res.ok, res.tier)


# ---- build script: lean, and not pointed at OneDrive --------------------------
def bat():
    return open(os.path.join(ROOT, "BUILD  double click me.bat"), encoding="utf-8").read()


def app_imports_third_party():
    import ast
    t = ast.parse(open(os.path.join(ROOT, "acrod.py"), encoding="utf-8").read())
    found = set()
    for n in ast.walk(t):
        if isinstance(n, ast.Import):
            found |= {a.name.split(".")[0] for a in n.names}
        elif isinstance(n, ast.ImportFrom) and n.module and not n.level:
            found.add(n.module.split(".")[0])
    return found - set(sys.stdlib_module_names) - {"acrod", "pyi_splash"}


def test_app_uses_only_the_libraries_the_build_installs():
    assert app_imports_third_party() == {"PySide6", "pypdf", "pikepdf", "PIL", "pypdfium2"}


def test_build_installs_essentials_not_full_pyside6():
    line = next(l for l in bat().splitlines() if "-m pip install" in l and "PyInstaller".lower() in l.lower())
    assert '"PySide6-Essentials==' in line and not re.search(r'"PySide6[=>]', line)
    assert "pypdfium2" in line and "pikepdf" in line and "pypdf==" in line


def test_build_does_not_bundle_every_pil_submodule_or_unused_modules():
    b = "\n".join(l for l in bat().splitlines() if not l.lstrip().upper().startswith("REM"))
    assert "--collect-submodules PIL" not in b
    for mod in ("tkinter", "PIL.ImageTk", "PySide6.QtNetwork"):
        assert f"--exclude-module {mod}" in b, mod


def test_excluded_modules_are_really_unused_by_the_app():
    src = open(os.path.join(ROOT, "acrod.py"), encoding="utf-8").read()
    for needle in ("tkinter", "ImageTk", "ImageQt", "QtNetwork", "QNetwork"):
        assert needle not in src, needle


def test_build_environment_lives_outside_onedrive():
    b = bat()
    assert 'set "VENV=%LOCALAPPDATA%\\AcroD-Build\\venv"' in b
    assert "--workpath" in b and "--specpath" in b and "--distpath" in b
