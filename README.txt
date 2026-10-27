Acro-Data (AcroD)
=================

A free Windows desktop toolbox for everyday PDF and file chores. Everything runs
on your computer; nothing is uploaded.

Tabs
  PDF Merger       combine PDFs
  PDF Splitter     split a PDF into parts
  PDF Optimizer    shrink PDFs and remove hidden information
  Page Organizer   reorder, rotate, cut/copy/paste pages between PDFs, import
                   pages, add text boxes, compare two PDFs side by side
  Bulk Renamer     rename many files by rules or by keyword tables
  Bulk File Mover  move files in bulk
  Settings, Help   in the app

Layout
  The Actions column holds each tool's buttons. It sits on the right by default;
  collapse it with the small button or Ctrl+J, and move it left or right in
  Settings. It folds itself in narrow windows. The file tray is hidden until
  you press Show file tray (Ctrl+T).

Install
  Just run AcroD.exe; copy it anywhere, including a network share. A first-start guide offers a look and
  the default PDF app. For Start Menu entries and an uninstaller, build installer\AcroD-Setup-1.12.1.exe by
  compiling AcroD.iss with Inno Setup 6 (per user, no administrator rights).

Command line
  AcroD.exe --cli optimize FILE_OR_FOLDER... [--ceiling MB] [--flatten]
  AcroD.exe --cli split PDF... [--after N]
  AcroD.exe --cli merge BASE.pdf REF.pdf
  AcroD.exe --cli diagnostics

Build it yourself
  Install Python 3.14 (64-bit), then double-click "BUILD  double click me.bat".
  It creates an isolated environment, installs the libraries, builds a single
  AcroD.exe and checks that it starts.

Settings and logs live in %LOCALAPPDATA%\AcroD.

Licence
  MIT (see LICENSE). Third-party libraries and their licences are listed in
  THIRD_PARTY_NOTICES.txt.

Tests
  python -m pytest tests
