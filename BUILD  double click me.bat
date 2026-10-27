@echo off
setlocal EnableExtensions EnableDelayedExpansion
title Acro-Data - Build
color 0F

REM ===================================================================
REM  Acro-Data - build a single standalone .exe
REM
REM  Double-click this file. It does everything.
REM
REM  Rules baked into this script, each one from a build that went wrong:
REM
REM   * It finds python.exe DIRECTLY. The "py" launcher was wrong at every
REM     step of the 2026-07-31 arc - it listed 3.14 while the exe was
REM     missing, then failed again after a clean reinstall.
REM   * It RUNS each candidate to prove it reports 3.14 before using it.
REM   * It ignores requirements.txt. A stale copy on the build machine
REM     pinned PySide6==6.9.1, whose metadata forbids 3.14, and that alone
REM     cost four failed builds.
REM   * NOTHING is redirected to nul. A diagnostic that hides the error is
REM     not a diagnostic.
REM   * No bare %% inside a python -c string. cmd expands it before Python
REM     ever sees it, which produced a SyntaxError once. Every one-liner
REM     below is built with + concatenation and contains no percent sign.
REM   * --onefile, because staff copy the exe alone onto network shares.
REM     A folder build is faster to launch and arrives broken.
REM ===================================================================

REM ---------------------------------------------------------------------
REM  Where things live (adjust to your own machine):
REM    source + final exe : the folder this .bat is in, currently
REM                         a folder of your choice, for example C:\Users\YOU\Documents\AcroD
REM    build environment, PyInstaller work files and the exe as first built:
REM                         %LOCALAPPDATA%\AcroD-Build   (NOT in OneDrive)
REM  The virtual environment alone is thousands of files. Inside a OneDrive
REM  folder every one of them is synced and scanned, which slows pip and
REM  PyInstaller and can lock a file mid-build. Only the finished exe is
REM  copied into the OneDrive folder, to dist\AcroD.exe, at the very end.
REM ---------------------------------------------------------------------
cd /d "%~dp0"
set "BUILDROOT=%LOCALAPPDATA%\AcroD-Build"
set "WORK=%BUILDROOT%\work"
set "OUTDIR=%BUILDROOT%\dist"
set "FINALDIR=%~dp0dist"

echo.
echo  ================================================================
echo   Acro-Data - Build
echo  ================================================================
echo.

if not exist "acrod.py" (
    echo  ERROR: acrod.py is not in this folder.
    echo  Put this .bat next to acrod.py and run it again.
    echo.
    pause
    exit /b 1
)

REM ---------------------------------------------------------------- 1
echo  [1/6] Looking for Python 3.14...
echo.

set "PY="
REM The build machine: Python 3.14 is the python install-manager build in
REM %LOCALAPPDATA%\Python\pythoncore-3.14-64 (verified present). It is tried
REM first. The only other Python here is 3.12, which is skipped by the version
REM check below - it can never be picked by mistake.
for %%D in (
    "%LOCALAPPDATA%\Python\pythoncore-3.14-64\python.exe"
    "%LOCALAPPDATA%\Programs\Python\Python314\python.exe"
    "%LOCALAPPDATA%\Python\pythoncore-3.14\python.exe"
    "%ProgramFiles%\Python314\python.exe"
    "%ProgramFiles(x86)%\Python314\python.exe"
    "C:\Python314\python.exe"
) do (
    if not defined PY if exist %%D (
        call :try_python %%D
    )
)

if not defined PY (
    echo    ...not in the usual places. Searching more widely.
    for /f "delims=" %%F in ('dir /b /s "%LOCALAPPDATA%\Programs\Python\python.exe" 2^>nul') do (
        if not defined PY call :try_python "%%F"
    )
)
if not defined PY (
    for /f "delims=" %%F in ('dir /b /s "%LOCALAPPDATA%\Python\python.exe" 2^>nul') do (
        if not defined PY call :try_python "%%F"
    )
)
if not defined PY (
    for /f "delims=" %%F in ('where python 2^>nul') do (
        if not defined PY call :try_python "%%F"
    )
)

if not defined PY goto :diagnose

echo.
echo    Using: !PY!
echo.

REM ---------------------------------------------------------------- 2
echo  [2/6] Preparing the build environment...
echo.

set "VENV=%LOCALAPPDATA%\AcroD-Build\venv"
set "VPY=%VENV%\Scripts\python.exe"
if not exist "%BUILDROOT%" mkdir "%BUILDROOT%"

if exist "%~dp0.buildenv" (
    echo    NOTE: an old .buildenv folder is still in the project folder. It is no
    echo    longer used and OneDrive is syncing it. Delete it by hand to stop that.
    echo.
)

set "REUSE="
if exist "%VPY%" (
    "%VPY%" -c "import sys; sys.exit(0 if sys.version_info[:2]==(3,14) and sys.maxsize>2**32 else 1)"
    if !errorlevel! equ 0 (
        "%VPY%" -c "import PySide6, pypdf, pikepdf, PIL, pypdfium2, PyInstaller"
        if !errorlevel! equ 0 set "REUSE=1"
    )
)

if defined REUSE (
    echo    Existing build environment is Python 3.14 and has every package. Reusing it.
) else (
    if exist "%VENV%" (
        echo    Existing build environment is unusable. Removing it.
        rmdir /s /q "%VENV%"
    )
    echo    Creating a fresh build environment...
    "!PY!" -m venv "%VENV%"
    if !errorlevel! neq 0 (
        echo.
        echo  ERROR: could not create the build environment. The error is above.
        echo.
        pause
        exit /b 1
    )
)

REM ---------------------------------------------------------------- 3
echo.
echo  [3/6] Installing packages...
echo.

"%VPY%" -m pip install --upgrade pip
if !errorlevel! neq 0 (
    echo.
    echo  ERROR: pip could not update itself. The error is above.
    echo.
    pause
    exit /b 1
)

REM Explicit on the command line. requirements.txt is NOT read.
REM PySide6-Essentials, pypdf and pyinstaller are pinned because those exact
REM versions are known good on Python 3.14. pypdf is pinned at 6.20.1 (pure Python):
REM 5.9.0 carries 85 published denial-of-service advisories fixed in 6.19+.
REM pikepdf and Pillow are floor-constrained, not pinned: an exact pin is only
REM safe once you have confirmed that release ships a cp314 wheel, and an old
REM pin fails exactly the way PySide6==6.9.1 did. The resolved versions are
REM printed in step 4 - copy them into requirements.txt after a good build.
REM constraints.txt (written by a previous good build as constraints.new.txt,
REM then copied over by hand) freezes every transitive version, so two builds
REM of the same source get the same packages. Absent on the first build.
set "CONS="
if exist "constraints.txt" (
    set CONS=-c "constraints.txt"
    echo    Using constraints.txt - exact versions from the last good build.
) else (
    echo    No constraints.txt yet - resolving fresh. One is written after this build.
)
"%VPY%" -m pip install !CONS! "PySide6-Essentials==6.11.1" "pypdf==6.20.1" "pyinstaller==6.21.0" "pikepdf>=10.11" "Pillow>=12.3" "pypdfium2>=4.30"
if !errorlevel! neq 0 (
    echo.
    echo  ERROR: a package failed to install. The real error is above.
    echo.
    echo  If it mentions pikepdf or Pillow, no release of that package has a
    echo  wheel for your Python yet. Tell the maintainer which package and which
    echo  Python version - do not downgrade Python to work around it.
    echo.
    pause
    exit /b 1
)

if exist "requirements.txt" (
    findstr /c:"PySide6==" "requirements.txt"
    if !errorlevel! equ 0 (
        echo.
        echo  WARNING: requirements.txt in this folder pins plain "PySide6==".
        echo  That package forbids Python 3.14. This build ignored the file,
        echo  so the build is fine - but fix the file before anyone trusts it.
        echo.
    )
)

REM ---------------------------------------------------------------- 4
echo.
echo  [4/6] Checking the packages actually import...
echo.

"%VPY%" -c "import sys, PySide6, pypdf, pikepdf, PIL, pypdfium2; from PIL import features; print('  python      ' + sys.version.split()[0]); print('  PySide6     ' + PySide6.__version__); print('  pypdf       ' + pypdf.__version__); print('  pikepdf     ' + pikepdf.__version__); print('  Pillow      ' + PIL.__version__); print('  pypdfium2   ' + str(getattr(pypdfium2, 'V_PYPDFIUM2', 'ok'))); print('  libtiff     ' + str(features.check('libtiff')))"
if !errorlevel! neq 0 (
    echo.
    echo  ERROR: a package installed but will not import. The error is above.
    echo.
    pause
    exit /b 1
)

REM Group 4 is where the size reduction on black-and-white scans comes from.
REM Without libtiff the Optimizer still works, it just falls back to a larger
REM encoding - so this is a warning, not a failure.
"%VPY%" -c "from PIL import features; import sys; sys.exit(0 if features.check('libtiff') else 1)"
if !errorlevel! neq 0 (
    echo.
    echo  WARNING: this Pillow has no libtiff, so the PDF Optimizer cannot use
    echo  Group 4 compression. It will still work, but black-and-white output
    echo  will be larger than it should be. Worth reporting.
    echo.
)

"%VPY%" -c "import py_compile, sys; py_compile.compile('acrod.py', doraise=True); print('  acrod.py compiles cleanly')"
if !errorlevel! neq 0 (
    echo.
    echo  ERROR: acrod.py has a syntax error. The error is above.
    echo.
    pause
    exit /b 1
)

REM The version string is what the shared-folder updater compares against.
REM It is printed here so the person building can see it is not stale
REM before the exe exists - v8 shipped as "1.0.0" and the update note could
REM never fire.
"%VPY%" -c "import re; s = open('acrod.py', encoding='utf-8').read(); print('  APP_VERSION ' + re.search(r'APP_VERSION = .([0-9.]+).', s).group(1))"

REM Same version as the last good build means the shared-folder updater will
REM never show an update note for this exe. Warn, do not block: a rebuild of
REM the same version is sometimes deliberate.
set "THISVER="
"%VPY%" -c "import re; s = open('acrod.py', encoding='utf-8').read(); open('.this_version', 'w').write(re.search(r'APP_VERSION = .([0-9.]+).', s).group(1))"
if exist ".this_version" set /p THISVER=<".this_version"
set "LASTVER="
if exist ".last_built_version" set /p LASTVER=<".last_built_version"
if defined LASTVER if "!LASTVER!"=="!THISVER!" (
    echo.
    echo  WARNING: APP_VERSION !THISVER! is the same as the last good build.
    echo  Staff will not see an update note for this exe. Bump APP_VERSION
    echo  unless this is a deliberate rebuild.
    echo.
)
REM If the test suite is beside the source, run it. Not a hard gate: the
REM suite needs pytest, which the build environment does not otherwise
REM carry, so a missing pytest is reported and the build continues.
if exist "tests\test_smoke_ui.py" (
    echo.
    echo    tests\ folder found - running the suite...
    "%VPY%" -m pip install "pytest>=8"
    set "QT_QPA_PLATFORM=offscreen"
    "%VPY%" -m pytest tests -q -p no:cacheprovider
    if !errorlevel! neq 0 (
        echo.
        echo  ERROR: the test suite failed. The failures are above. Not building.
        echo.
        pause
        exit /b 1
    )
    set "QT_QPA_PLATFORM="
)

REM ---------------------------------------------------------------- 5
echo.
echo  [5/6] Building the .exe. This takes a few minutes - leave it alone.
echo.

REM Clean the work and output folders from the last build. They live outside
REM OneDrive, so this is quick and nothing syncs while PyInstaller writes.
if exist "!OUTDIR!" rmdir /s /q "!OUTDIR!"
if exist "!WORK!" rmdir /s /q "!WORK!"
if exist "%~dp0build" echo    NOTE: an old build folder is in the project folder. It is no longer used; delete it by hand.

REM --onefile   : one standalone file. Staff copy the exe alone; a folder
REM               build arrives without its _internal folder and dies with
REM               "Failed to load Python DLL".
REM --windowed  : no console window behind the app.
REM --noupx     : UPX shrinks the file but slows the per-launch unpack, and
REM               unpack time is what onefile already pays for.
REM --collect-all pikepdf / pypdfium2 : both ship compiled libraries and data
REM               that PyInstaller's import scan cannot see; collecting them
REM               whole is cheaper than debugging a missing file inside a
REM               frozen exe. They are the only two collect-all entries.
REM Pillow is NOT collected whole any more. PyInstaller's own Pillow hook already
REM               adds every image-format plugin the app can use (JPEG, TIFF
REM               Group 4, PNG...). The old --collect-submodules PIL also pulled
REM               in ImageTk (and with it Tcl/Tk), ImageQt, fonts and filters
REM               that nothing here imports.
REM --exclude-module : modules the app never imports and that would otherwise be
REM               unpacked on every launch of a onefile exe. tkinter and
REM               PIL.ImageTk (Tcl/Tk), PIL.ImageQt, PySide6.QtNetwork (and the
REM               TLS and network plug-ins it drags in), unittest, pydoc, pytest.
REM               tests\test_round8.py proves the source really uses none of them.
REM --distpath/--workpath/--specpath : outside OneDrive, see the top of this file.
REM --icon        : the vermilion page mark (AcroD.ico), so Explorer, the taskbar and the title bar
REM               show it instead of the PyInstaller default. The .ico sits beside this file.
set "ICONARG="
if exist "%~dp0AcroD.ico" (
    set ICONARG=--icon "%~dp0AcroD.ico"
) else (
    echo  NOTE: AcroD.ico is missing, so the exe will have the default icon.
)
REM --splash      : the startup card (AcroD-splash.png) the bootloader paints before Python starts, so a cold
REM               launch from a network share shows something at once. PyInstaller's splash needs Tcl/Tk, so
REM               tkinter is NOT excluded when it is used; if this Python has no tkinter, or the PNG is missing,
REM               the build skips the splash and keeps tkinter out (smaller exe). Set USE_SPLASH=0 to opt out.
set "USE_SPLASH=1"
set "SPLASHARG="
set "TKARG=--exclude-module tkinter"
if "!USE_SPLASH!"=="1" if exist "%~dp0AcroD-splash.png" (
    "%VPY%" -c "import tkinter" >nul 2>&1
    if !errorlevel! equ 0 (
        set SPLASHARG=--splash "%~dp0AcroD-splash.png"
        set "TKARG="
    ) else (
        echo  NOTE: this Python has no tkinter, so the exe is built without the startup card.
    )
)
"%VPY%" -m PyInstaller ^
    !SPLASHARG! ^
    --onefile ^
    !ICONARG! ^
    --windowed ^
    --noupx ^
    --clean ^
    --name "AcroD" ^
    --distpath "!OUTDIR!" ^
    --workpath "!WORK!" ^
    --specpath "!WORK!" ^
    --collect-all pikepdf ^
    --collect-all pypdfium2 ^
    !TKARG! ^
    --exclude-module PIL.ImageTk ^
    --exclude-module PIL.ImageQt ^
    --exclude-module PySide6.QtNetwork ^
    --exclude-module unittest ^
    --exclude-module pydoc ^
    --exclude-module pytest ^
    "%~dp0acrod.py"

if !errorlevel! neq 0 (
    echo.
    echo  ERROR: PyInstaller failed. The real error is above.
    echo.
    pause
    exit /b 1
)

REM ---------------------------------------------------------------- 6
echo.
echo  [6/6] Checking the result...
echo.

if not exist "!OUTDIR!\AcroD.exe" (
    echo  ERROR: PyInstaller reported success but there is no .exe in !OUTDIR!.
    echo.
    pause
    exit /b 1
)

if exist "!OUTDIR!\_internal" (
    echo  ERROR: a folder-style build is also present in !OUTDIR!.
    echo  The .exe is NOT standalone. Delete that folder and run again.
    echo.
    pause
    exit /b 1
)

for %%A in ("!OUTDIR!\AcroD.exe") do set "EXESIZE=%%~zA"
echo    !OUTDIR!\AcroD.exe
echo    !EXESIZE! bytes
echo.

REM Run the FROZEN exe once, headless, through its --cli entry. This proves
REM the bundle starts, imports every package and writes to the settings
REM folder - on the artifact that ships, not the source tree. start /wait
REM because a windowed exe detaches from the console otherwise.
echo    Running the built exe once ^(--cli diagnostics^)...
if exist "%LOCALAPPDATA%\AcroD\cli.log" del /q "%LOCALAPPDATA%\AcroD\cli.log"
start /wait "" "!OUTDIR!\AcroD.exe" --cli diagnostics
if not exist "%LOCALAPPDATA%\AcroD\cli.log" (
    echo.
    echo  ERROR: the built exe did not write cli.log. It did not start
    echo  correctly. Do not hand it out. Run it by double-click to see the
    echo  crash dialog, and include crash.log when you report it.
    echo.
    pause
    exit /b 1
)
findstr /c:"done: 0 failed" "%LOCALAPPDATA%\AcroD\cli.log"
if !errorlevel! neq 0 (
    echo.
    echo  ERROR: the built exe started but its diagnostics run did not finish.
    echo  cli.log is above. Do not hand it out.
    echo.
    pause
    exit /b 1
)
echo    The frozen exe started and wrote its diagnostics. Good.
echo.

REM Only a checked exe is copied into the OneDrive folder.
if not exist "!FINALDIR!" mkdir "!FINALDIR!"
copy /y "!OUTDIR!\AcroD.exe" "!FINALDIR!\AcroD.exe"
if !errorlevel! neq 0 (
    echo.
    echo  ERROR: the exe was built and checked but could not be copied to
    echo  !FINALDIR!. OneDrive may have the old one open. The good exe is still at
    echo  !OUTDIR!\AcroD.exe - copy it by hand.
    echo.
    pause
    exit /b 1
)

REM Record what this good build resolved to. Copy constraints.new.txt over
REM constraints.txt once you have opened the exe and are happy with it.
"%VPY%" -m pip freeze --exclude-editable > "constraints.new.txt"
if defined THISVER >".last_built_version" echo !THISVER!
echo    Wrote constraints.new.txt ^(exact package versions of this build^).
echo.
echo  ================================================================
echo   BUILD COMPLETE
echo  ================================================================
echo.
echo   The file to hand out is:  !FINALDIR!\AcroD.exe
echo   It is standalone. Copy just that one file anywhere.
echo.
echo   Before handing it out, open it once and check all EIGHT tabs:
echo     PDF Merger, PDF Splitter, PDF Optimizer, Page Organizer,
echo     Bulk Renamer, Bulk File Mover, Settings, Help
echo   Then drop a real 20 MB scan on the PDF Optimizer and confirm the
echo   result opens, is readable, and is under the size limit.
echo   New in 1.12.1: a startup card, a first-start guide (look, Actions side, default PDF app), full-screen reader when a PDF is opened from Windows, and an optional installer script (AcroD.iss). If the guide does not appear, open %LOCALAPPDATA%\AcroD\startup.log and read the last line.
echo   New in 1.12.0: the app is now Acro-Data (AcroD): same tools, new name; its settings live in an AcroD folder, and the exe is AcroD.exe.
echo   New in 1.7.2: a PDF in the Explorer list that cannot be opened (too large, not a PDF, damaged) is marked, the reason is shown, and the next waiting PDF opens by itself.
echo   New in 1.8.0: select pages in the Page Organizer and press Ctrl+C or Ctrl+X, open another PDF, press Ctrl+V (Ctrl+Shift+V pastes before). Right-click a page for Cut, Copy and Paste. Pasted pages keep their form fields; scripts are removed.
echo   New in 1.10.0: a new Tray layout - a file tray on the left, one button per tool along the top, the same tools underneath. Settings, Advanced, Layout switches back to the classic tabs. Merge still joins two PDFs. Nothing in the Optimizer changed.
echo   New in 1.9.0: Compare in the Page Organizer shows a second PDF beside yours to drag pages from; Undo Last Merge in the PDF Merger; Send To between tabs; plain-language error messages; drop summaries; Help jumps to the tab. Nothing in the Optimizer changed.
echo   New in 1.7.1: security hardening - the Optimizer now also removes program-launch, form-submit, external-file and XFA actions (not only JavaScript); files over 1 GB or not starting like a PDF are refused unread; pypdf updated to 6.20.1 (fixes 85 published denial-of-service advisories).
echo   New in 1.7.0: PDFs you open from Explorer join a list (up to 200) - Next PDF / Previous PDF (Ctrl+Page Down / Page Up) step through them; the list is not kept after closing.
echo   New in 1.6.2: double-click a PDF (set Acro-Data as its default app, or Open with) and it opens full-size in the Page Organizer reader; a second PDF opens in the same window.
echo   New in 1.6.1: PDF form fields (typed text, checkboxes) now draw in the reader, page thumbnails and Optimizer output.
echo   New in 1.6.0: Acrobat-style reader polish - page arrows and a Page box in the top bar, drag to pan, Ctrl+0/1/2 fit page/100%%/fit width, Ctrl+G, Ctrl+S, Ctrl+Shift+R/L rotate,
echo   an 'Unsaved changes' marker, Find opens with Ctrl+F, Delete key only ^(Backspace no longer deletes^). Organizer toolbar no longer clips in a narrow window.
echo   Mover waits for its inputs, button text is Title case, Help answers open on click. Check on Windows at 125%% and 150%% scaling in all three themes.
echo   New in 1.5.0: the Page Organizer opens a PDF full size, like a PDF reader. Double-click a PDF in the list ^(or press Enter^), or double-click a page,
echo   and read it with zoom, a page strip, Find with highlights, and Rotate / Delete / Extract / Undo / Save ^& Next right there - it shows the pages as they will be saved.
echo   Settings ^> Advanced has the reader's starting zoom and memory. Check on Windows: double-click a PDF, Ctrl+wheel to zoom, rotate a page, press Esc, Save ^& Next.
echo   Also in 1.4.3: the Page Organizer list is live - an Extract into the folder on screen shows up at once ^(marked +^) and can be opened and
echo   extracted from again; the list re-reads the folder when you return to the window, and has a Refresh button and F5.
echo   Settings ^> Advanced ^> Page Organizer list refresh turns it off or adds a 10 second check. Check on Windows: extract into the same folder, then add a PDF in Explorer and alt-tab back.
echo   Also in 1.4.3: this build script keeps its work files outside OneDrive and bundles only the libraries the app uses ^(no Tcl/Tk, no
echo   extra Pillow modules, no QtNetwork^); the first build on this layout makes a fresh environment, so it takes a few minutes longer.
echo   Fixed: Flatten ^(forced, and the last-resort step when a file will not shrink^) failed with Could not flatten the file.
echo   Check on Windows: Optimize a scan with Flatten pages ticked, and note how long the exe takes to show its window.
echo   Also in 1.4.3: all Page Organizer buttons are on one row ^(Extract... and Reset are the short names^); Settings ^> Advanced has
echo   Page Organizer undo limit ^(5-200, default 100^) and a switch to start Extract with Delete-after-extracting ticked.
echo   Also in 1.4.3: Ctrl+Z undoes the last change on the tab ^(typing in a box still undoes typing^).
echo   Check on Windows: delete a page, click a page, press Ctrl+Z.
echo   Also in 1.4.3: Extract has a Delete-after-extracting box ^(off by default^); Undo brings the pages
echo   back and the file changes only when you Save. Check on Windows: extract 2 pages with it ticked.
echo   Also in 1.4.3: Page Organizer has a Delete page button ^(also right-click and the Delete key^);^
echo   page labels follow position ^(Page 1 is always first, moved pages say was page N^); Save writes a^
echo   copy into a Reorganized folder beside the PDF; the Bulk Renamer no longer has Number files from;^
echo   Help has a search box. Check on Windows: delete a page, Undo it, save, look in Reorganized,^
echo   type undo in the Help search box.^
echo   Also in 1.4.3: Page Organizer drag and drop no longer makes pages vanish ^(they reorder^),^
echo   there is an Undo button ^(Ctrl+Shift+Z, also in the right-click menu^), Pagey is gone from the^
echo   PDF Optimizer title, the exe and window icon is the vermilion page mark, and closing the app clears every^
echo   Recent list and remembered folder. Check on Windows: drag a page left then right, press Undo,^
echo   look at the exe icon in Explorer, close and reopen and confirm Recent is empty.^
echo   New in 1.4.3: Find text over a big folder no longer slows down ^(1,500 PDFs: 12.7 s to 0.04 s^),^
echo   and a PDF built to unpack into gigabytes is refused with a message instead of freezing or^
echo   closing the program. Nothing to check on Windows beyond the usual first-run walk.^
echo   New in 1.4.2 ^(security and speed^): Find and replace refuses patterns that freeze the^
echo   program, Open file will not start .exe/.bat/.js, results CSV is formula-safe, giant page^
echo   shapes no longer exhaust memory. Check on Windows: type ^(a+^)+$ in Find with regex on.^
echo   Also in 1.4.1: every button, box and table has a hover tip; Help is updated^
echo   ^(Pagey beside the Optimizer title was removed again in 1.4.3^). Hover a few controls.^
echo   New in 1.4.1: Page Organizer drag and drop ^(drag pages, gold bar shows the drop spot;^
echo   drop a folder or PDFs on the tab^), right-click menus on pages and on the PDF list,^
echo   and Extract now asks for a folder and file name. Check these on Windows.^
echo   New in 1.4.0: Page Organizer tab ^(choose a folder, click a PDF, reorder^
echo   or rotate or remove pages, Save and Next; output goes to an Organized^
echo   folder and the original is untouched; Extract copies chosen pages;^
echo   Find text searches every PDF in the folder, text layer only, no OCR^).^
echo   Check on Windows: drag a page to reorder, Save and Next on 3 PDFs,^
echo   Replace original in Settings ^(Advanced^) leaves a copy in Originals.^
echo   PDF Optimizer has Report only: inspect without writing anything.^
echo   Saved start-up tab positions shift by one for tabs after the Optimizer.^
echo   New in 1.3.1: PDF Splitter keeps the row you used last ^(Tab or a run no^
echo   longer flips it to Last^); Pagey sits centred in message boxes; the tab^
echo   row always fits: drag the window narrow and tabs tighten, then show only^
echo   icons except the open one ^(hover for the name^).
echo   New in 1.3.0: PDF Splitter is two rows, First and Last, each with a
echo   1 / 2 / 3 pages dropdown ^(pick a number and its row selects itself^).
echo   Split a 5 page PDF with Last 2 and check _Last2 holds pages 4 and 5,
echo   then First 3 and check _Cover holds pages 1 to 3. Confirm and result
echo   boxes show Pagey, the paper mascot. Settings has new Dialogs and PDF
echo   OPTIMIZER rows ^(open folder when done, flatten allowed, flatten DPI^);
echo   untick the mascot and check the plain Windows boxes come back. Check
echo   drop-downs, spin boxes and scrollbars look the same on every tab, in
echo   all three themes.
echo   In 1.2.0: drag the window smaller than before ^(it should resize
echo   freely and scroll inside a tab if needed^); buttons are rounded with
echo   icons.
echo   New in 1.1.1: rename a few files on the network share, then run
echo   attrib on one of them in cmd. It must NOT show an H. The icons must
echo   not look faded. Then press Undo and check again.
echo   Also in 1.1: press Cancel during a multi-file split, close the window
echo   during a move ^(it should finish the file it is on^), reopen and
echo   confirm the window size, tab and folders came back.
echo.
pause
exit /b 0


REM ===================================================================
:try_python
REM Prove the candidate reports 3.14 by RUNNING it. Do not trust a path.
set "CAND=%~1"
if not exist "!CAND!" goto :eof
"!CAND!" -c "import sys; sys.exit(0 if sys.version_info[:2]==(3,14) and sys.maxsize>2**32 else 1)"
if !errorlevel! equ 0 (
    set "PY=!CAND!"
    echo    found 3.14 at !CAND!
) else (
    echo    skipped ^(not 3.14^): !CAND!
)
goto :eof


REM ===================================================================
:diagnose
echo.
echo  ================================================================
echo   COULD NOT FIND PYTHON 3.14
echo  ================================================================
echo.
echo  Everything below is diagnostic output. Nothing is hidden. Send this
echo  whole window when you report it.
echo.
echo  --- py --list ---
py --list
echo.
echo  --- py --list-paths ---
py --list-paths
echo.
echo  --- py -3.14 -V (unhidden) ---
py -3.14 -V
echo.
echo  --- where python ---
where python
echo.
echo  --- where py ---
where py
echo.
echo  --- python -V ---
python -V
echo.
echo  --- %LOCALAPPDATA%\Programs\Python ---
dir /b "%LOCALAPPDATA%\Programs\Python"
echo.
echo  --- %LOCALAPPDATA%\Python ---
dir /b "%LOCALAPPDATA%\Python"
echo.
echo  --- PATH ---
echo %PATH%
echo.
echo  Install Python 3.14 for Windows from python.org, tick
echo  "Add python.exe to PATH", then run this file again.
echo.
pause
exit /b 1
